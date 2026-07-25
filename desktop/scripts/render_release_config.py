#!/usr/bin/env python3
"""Render an untracked Tauri updater override from release-only environment data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"required release value is missing: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    public_key = required_environment("ENMOTION_UPDATER_PUBLIC_KEY")
    if "REPLACE_" in public_key or len(public_key) < 32:
        raise SystemExit("ENMOTION_UPDATER_PUBLIC_KEY is not a usable public key")
    payload = {
        "bundle": {
            "createUpdaterArtifacts": True,
        },
        "plugins": {
            "updater": {
                "pubkey": public_key,
                "windows": {"installMode": "passive"},
            }
        }
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"rendered release-only Tauri config: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
