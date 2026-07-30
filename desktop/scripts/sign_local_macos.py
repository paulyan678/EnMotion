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

APP_IDENTIFIER = "com.enmotion.desktop"
CORE_IDENTIFIER = f"{APP_IDENTIFIER}.sidecar"
WORKER_IDENTIFIER = f"{APP_IDENTIFIER}.demucs-worker"
CODESIGN = "/usr/bin/codesign"


def bundle_paths(app: Path) -> tuple[Path, Path]:
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

    core = app / "Contents" / "Resources" / "sidecar" / "enmotion-sidecar"
    worker = app / "Contents" / "MacOS" / "enmotion-demucs-worker"
    missing = [path for path in (core, worker) if not path.is_file()]
    if missing:
        raise SystemExit(f"packaged sidecar is missing: {missing[0]}")
    return core, worker


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


def sign_local_app(app: Path) -> None:
    app = app.expanduser().resolve()
    ensure_local_environment()
    core, worker = bundle_paths(app)
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

    print(f"local EnMotion bundle signed and smoke-tested: {app}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app",
        required=True,
        type=Path,
        help="path to the freshly built EnMotion.app",
    )
    args = parser.parse_args()
    sign_local_app(args.app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
