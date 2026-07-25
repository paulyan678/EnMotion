#!/usr/bin/env python3
"""On-demand Demucs runner kept out of EnMotion's launch-critical sidecar."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def verify_bundle() -> None:
    import demucs.pretrained  # noqa: F401
    import demucs.separate  # noqa: F401
    import soundfile  # noqa: F401
    import torch  # noqa: F401


def separate(input_path: Path, output_dir: Path) -> None:
    if not input_path.is_file():
        raise ValueError("input audio is unavailable")
    output_dir.mkdir(parents=True, exist_ok=True)

    import demucs.separate

    demucs.separate.main(
        [
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "--out",
            str(output_dir),
            str(input_path),
        ]
    )
    expected = output_dir / "htdemucs" / input_path.stem / "no_vocals.wav"
    if not expected.is_file():
        matches = list(output_dir.rglob("no_vocals.wav"))
        if not matches:
            raise RuntimeError("Demucs did not create a background-audio stem")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--verify-bundle", action="store_true")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.verify_bundle:
        verify_bundle()
        return 0
    if args.input is None or args.output is None:
        parser.error("--input and --output are required")
    # Do not inherit Python path injection into the isolated model worker.
    os.environ.pop("PYTHONPATH", None)
    separate(args.input.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Demucs worker failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
