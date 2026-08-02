#!/usr/bin/env python3
"""Launch the packaged macOS app with isolated, safely removable QA data."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


BUNDLE_ID = "com.enmotion.desktop"
PROFILE_ENV = "ENMOTION_QA_PROFILE"
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MANIFEST_NAME = ".enmotion-qa-profile.json"


def profile_root() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / BUNDLE_ID
        / "qa-profiles"
    ).resolve()


def validate_profile_name(value: str) -> str:
    if not PROFILE_PATTERN.fullmatch(value):
        raise ValueError(
            "QA profile names must contain 1-64 ASCII letters, numbers, hyphens, or underscores"
        )
    return value


def profile_path(value: str) -> Path:
    name = validate_profile_name(value)
    root = profile_root()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        raise ValueError("QA profile path escaped the private app-data directory")
    return candidate


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_profile(name: str) -> Path:
    target = profile_path(name)
    if target.is_symlink() or not target.is_dir():
        raise RuntimeError(f"QA profile does not exist as a regular directory: {target}")
    manifest_path = target / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("Refusing cleanup because the QA profile manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Refusing cleanup because the QA profile manifest is invalid") from exc
    if manifest.get("bundle_id") != BUNDLE_ID or manifest.get("profile") != name:
        raise RuntimeError("Refusing cleanup because the QA profile manifest does not match")
    pid = manifest.get("pid")
    if isinstance(pid, int) and process_is_running(pid):
        raise RuntimeError(f"Refusing cleanup while EnMotion QA process {pid} is still running")
    shutil.rmtree(target)
    return target


def default_profile_name() -> str:
    stamp = datetime.now(UTC).strftime("qa-%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def app_executable(app_path: Path) -> Path:
    app_path = app_path.resolve()
    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise RuntimeError(f"Not a macOS application bundle: {app_path}")
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise RuntimeError(f"Application bundle identifier is not {BUNDLE_ID}: {app_path}")
    executable_name = info.get("CFBundleExecutable")
    if not isinstance(executable_name, str) or not executable_name:
        raise RuntimeError("Application bundle does not declare an executable")
    executable = app_path / "Contents" / "MacOS" / executable_name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"Application executable is missing: {executable}")
    return executable


def launch(app_path: Path, name: str, *, detach: bool, keep: bool) -> int:
    executable = app_executable(app_path)
    target = profile_path(name)
    target.mkdir(parents=True, exist_ok=False)
    manifest_path = target / MANIFEST_NAME
    manifest = {
        "schema_version": 1,
        "bundle_id": BUNDLE_ID,
        "profile": name,
        "app": str(app_path.resolve()),
        "created_at": datetime.now(UTC).isoformat(),
        "pid": None,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment[PROFILE_ENV] = name
    stdout_path = target / "launcher.stdout.log"
    stderr_path = target / "launcher.stderr.log"
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [str(executable)],
            cwd=target,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    manifest["pid"] = process.pid
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "profile": name,
                "profile_path": str(target),
                "pid": process.pid,
                "detached": detach,
            }
        ),
        flush=True,
    )
    if detach:
        return 0
    return_code = process.wait()
    if not keep:
        cleanup_profile(name)
    return return_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/Applications/EnMotion.app"))
    parser.add_argument("--profile", default=default_profile_name())
    parser.add_argument("--detach", action="store_true", help="Return after launch; clean later")
    parser.add_argument("--keep", action="store_true", help="Keep profile data after app exit")
    parser.add_argument("--cleanup", metavar="PROFILE", help="Remove one stopped, verified QA profile")
    return parser.parse_args()


def main() -> int:
    if sys.platform != "darwin":
        raise RuntimeError("The packaged QA profile runner currently supports macOS only")
    args = parse_args()
    if args.cleanup:
        removed = cleanup_profile(args.cleanup)
        print(json.dumps({"removed_profile_path": str(removed)}))
        return 0
    if args.detach and not args.keep:
        args.keep = True
    return launch(args.app, validate_profile_name(args.profile), detach=args.detach, keep=args.keep)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"QA profile error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
