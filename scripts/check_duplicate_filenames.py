#!/usr/bin/env python3

"""Reject tracked conflict-copy filenames such as ``module 2.py``."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def tracked_conflict_copies() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    return sorted(
        path
        for path in paths
        if path and (REPO_ROOT / path).exists() and re.search(r"(?:^|/)[^/]* 2(?:\.|$)", path)
    )


def main() -> int:
    conflicts = tracked_conflict_copies()
    if conflicts:
        print("Tracked conflict-copy filenames are not allowed:")
        for path in conflicts:
            print(f"- {path}")
        return 1
    print("No tracked conflict-copy filenames found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
