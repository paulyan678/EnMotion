#!/usr/bin/env python3
"""Atomically stage the complete Next.js export for the Tauri resource bundle."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPOSITORY_ROOT / "frontend" / "out"
DEFAULT_DESTINATION = REPOSITORY_ROOT / "desktop" / "web" / "static"


def validate_export(source: Path) -> None:
    if not (source / "index.html").is_file():
        raise SystemExit(f"frontend export is missing {source / 'index.html'}")
    if not (source / "_next" / "static").is_dir():
        raise SystemExit(f"frontend export is missing {source / '_next/static'}")
    for item in source.rglob("*"):
        if item.is_symlink():
            resolved = item.resolve()
            if not resolved.is_relative_to(source.resolve()):
                raise SystemExit(f"frontend export contains an escaping symlink: {item}")


def stage(source: Path, destination: Path) -> None:
    validate_export(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".static-staging-", dir=destination.parent)
    )
    backup = destination.parent / ".static-previous"
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True, symlinks=False)
        validate_export(temporary)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(temporary, destination)
        except Exception:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    stage(args.source.resolve(), args.destination.resolve())
    print(f"staged EnMotion frontend: {args.destination.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
