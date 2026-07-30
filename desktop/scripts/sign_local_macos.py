#!/usr/bin/env python3
"""Ad-hoc sign and smoke-test a local EnMotion macOS application bundle.

This helper is intentionally for local validation builds only. Official release
artifacts continue to use Developer ID signing and notarization in GitHub
Actions.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

APP_IDENTIFIER = "com.enmotion.desktop"
CORE_IDENTIFIER = f"{APP_IDENTIFIER}.sidecar"
WORKER_IDENTIFIER = f"{APP_IDENTIFIER}.demucs-worker"
CODESIGN = "/usr/bin/codesign"


def bundle_paths(app: Path) -> tuple[Path, Path, Path]:
    if platform.system() != "Darwin":
        raise SystemExit("local ad-hoc application signing is supported only on macOS")
    if app.suffix != ".app" or not app.is_dir():
        raise SystemExit(f"expected an application bundle, got: {app}")

    info_plist = app / "Contents" / "Info.plist"
    try:
        with info_plist.open("rb") as handle:
            bundle_identifier = plistlib.load(handle).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException) as error:
        raise SystemExit(f"cannot read application metadata at {info_plist}: {error}")
    if bundle_identifier != APP_IDENTIFIER:
        raise SystemExit(
            f"refusing to sign bundle identifier {bundle_identifier!r}; "
            f"expected {APP_IDENTIFIER!r}"
        )

    main = app / "Contents" / "MacOS" / "enmotion"
    core = app / "Contents" / "Resources" / "sidecar" / "enmotion-sidecar"
    worker = app / "Contents" / "MacOS" / "enmotion-demucs-worker"
    missing = [path for path in (main, core, worker) if not path.is_file()]
    if missing:
        raise SystemExit(f"packaged executable is missing: {missing[0]}")
    return main, core, worker


def normalize_control_plane_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise SystemExit("expected control-plane URL must use HTTPS or loopback HTTP")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("expected control-plane URL must be a credential-free origin")
    return normalized


def ensure_embedded_control_plane(main: Path, expected_url: str) -> str:
    normalized = normalize_control_plane_url(expected_url)
    try:
        executable = main.read_bytes()
    except OSError as error:
        raise SystemExit(f"cannot inspect packaged executable {main}: {error}")
    if normalized.encode("utf-8") not in executable:
        raise SystemExit(
            "expected control-plane origin is not embedded in the application; "
            "rebuild with ENMOTION_CONTROL_PLANE_URL set"
        )
    return normalized


def signing_commands(app: Path, core: Path, worker: Path) -> list[list[str]]:
    preserve_entitlements = "--preserve-metadata=entitlements"
    return [
        [
            CODESIGN,
            "--force",
            "--sign",
            "-",
            "--identifier",
            CORE_IDENTIFIER,
            preserve_entitlements,
            str(core),
        ],
        [
            CODESIGN,
            "--force",
            "--sign",
            "-",
            "--identifier",
            WORKER_IDENTIFIER,
            preserve_entitlements,
            str(worker),
        ],
        [
            CODESIGN,
            "--force",
            "--sign",
            "-",
            "--identifier",
            APP_IDENTIFIER,
            "--options",
            "runtime",
            preserve_entitlements,
            str(app),
        ],
    ]


def ensure_local_environment() -> None:
    release_required = os.environ.get("ENMOTION_REQUIRE_CODE_SIGNING", "").strip()
    signing_identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    if release_required == "1" or signing_identity:
        raise SystemExit("refusing local ad-hoc signing while release signing is configured")


def signature_details(path: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [CODESIGN, "--display", "--verbose=4", str(path)],
        check=check,
        capture_output=True,
        text=True,
    )


def signature_flags(path: Path) -> set[str]:
    inspection = signature_details(path, check=True)
    details = f"{inspection.stdout}\n{inspection.stderr}"
    code_directory = next(
        (line for line in details.splitlines() if line.startswith("CodeDirectory ")),
        "",
    )
    if "(" not in code_directory or ")" not in code_directory:
        raise SystemExit(f"cannot determine code-signing flags for {path}")
    values = code_directory.rsplit("(", 1)[1].split(")", 1)[0]
    return {value.strip() for value in values.split(",") if value.strip()}


def signature_identifier(path: Path) -> str:
    inspection = signature_details(path, check=True)
    details = f"{inspection.stdout}\n{inspection.stderr}"
    identifier = next(
        (
            line.removeprefix("Identifier=")
            for line in details.splitlines()
            if line.startswith("Identifier=")
        ),
        "",
    )
    if not identifier:
        raise SystemExit(f"cannot determine code-signing identifier for {path}")
    return identifier


def ensure_local_signature(app: Path) -> None:
    inspection = signature_details(app, check=False)
    details = f"{inspection.stdout}\n{inspection.stderr}"
    if inspection.returncode == 0 and "Signature=adhoc" not in details:
        raise SystemExit(
            "refusing to replace a non-ad-hoc application signature; "
            "build a fresh local bundle instead"
        )


def sign_local_app(app: Path, expected_control_plane_url: str) -> None:
    app = app.expanduser().resolve()
    ensure_local_environment()
    main, core, worker = bundle_paths(app)
    control_plane_url = ensure_embedded_control_plane(main, expected_control_plane_url)
    ensure_local_signature(app)

    # PyInstaller's embedded Python is ad-hoc signed. A Hardened Runtime
    # signature on either Python launcher would enable library validation and
    # macOS would reject the embedded runtime because it has no Developer ID
    # Team ID. Keep only the outer Tauri shell on Hardened Runtime locally.
    for command in signing_commands(app, core, worker):
        subprocess.run(command, check=True)

    if "runtime" in signature_flags(core) or "runtime" in signature_flags(worker):
        raise SystemExit("local PyInstaller launchers unexpectedly use Hardened Runtime")
    if "runtime" not in signature_flags(app):
        raise SystemExit("local Tauri application is missing Hardened Runtime")
    identifiers = {
        app: APP_IDENTIFIER,
        core: CORE_IDENTIFIER,
        worker: WORKER_IDENTIFIER,
    }
    for path, expected in identifiers.items():
        if signature_identifier(path) != expected:
            raise SystemExit(f"unexpected code-signing identifier for {path}")

    subprocess.run(
        [CODESIGN, "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        check=True,
    )

    with tempfile.TemporaryDirectory(prefix="enmotion-local-sign-smoke-") as name:
        smoke_root = Path(name)
        environment = os.environ.copy()
        environment.update(
            {
                "ENMOTION_DATA_DIR": str(smoke_root / "data"),
                "ENMOTION_LOG_DIR": str(smoke_root / "logs"),
                "ENMOTION_DOCUMENTS_DIR": str(smoke_root / "Documents"),
                "ENMOTION_OUTPUT_DIR": str(smoke_root / "Documents" / "enmotion-output"),
                "ENMOTION_DEMUCS_WORKER": str(worker),
            }
        )
        subprocess.run(
            [str(core), "--verify-bundle"],
            cwd=smoke_root,
            env=environment,
            check=True,
            timeout=180,
        )

    print(
        f"local EnMotion bundle signed and smoke-tested: {app} "
        f"(control plane: {control_plane_url})"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app",
        required=True,
        type=Path,
        help="path to the freshly built EnMotion.app",
    )
    parser.add_argument(
        "--expected-control-plane-url",
        required=True,
        help="compile-time account/control-plane origin expected in the app",
    )
    args = parser.parse_args()
    sign_local_app(args.app, args.expected_control_plane_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
