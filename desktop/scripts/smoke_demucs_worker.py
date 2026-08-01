#!/usr/bin/env python3
"""Run one real separation through a frozen EnMotion Demucs worker."""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def write_fixture(path: Path) -> None:
    sample_rate = 44_100
    frames = bytearray()
    for index in range(sample_rate):
        sample = int(8_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        encoded = struct.pack("<h", sample)
        frames.extend(encoded)
        frames.extend(encoded)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def smoke(worker: Path) -> None:
    resolved_worker = worker.expanduser().resolve()
    if not resolved_worker.is_file():
        raise SystemExit(f"Demucs worker is unavailable: {resolved_worker}")
    with tempfile.TemporaryDirectory(prefix="enmotion-demucs-smoke-") as name:
        root = Path(name)
        source = root / "input.wav"
        output = root / "output"
        write_fixture(source)
        subprocess.run(
            [str(resolved_worker), "--input", str(source), "--output", str(output)],
            check=True,
            timeout=10 * 60,
        )
        stem_root = output / "htdemucs" / source.stem
        expected = (stem_root / "vocals.wav", stem_root / "no_vocals.wav")
        if any(not path.is_file() or path.stat().st_size <= 44 for path in expected):
            raise RuntimeError("frozen Demucs worker did not create both audio stems")
    print("EnMotion frozen Demucs separation smoke passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True, type=Path)
    args = parser.parse_args()
    smoke(args.worker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
