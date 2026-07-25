#!/usr/bin/env python3
"""Build the native PyInstaller sidecar expected by Tauri externalBin."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = REPOSITORY_ROOT / "desktop"
TARGETS = {
    "aarch64-apple-darwin": ("Darwin", "arm64"),
    "x86_64-apple-darwin": ("Darwin", "x86_64"),
    "x86_64-pc-windows-msvc": ("Windows", "AMD64"),
}


def native_identity() -> tuple[str, str]:
    machine = platform.machine()
    normalized = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "x86_64",
        "AMD64": "AMD64",
    }.get(machine, machine)
    if platform.system() == "Windows" and normalized == "x86_64":
        normalized = "AMD64"
    return platform.system(), normalized


def ffmpeg_binary() -> Path:
    configured = os.environ.get("ENMOTION_FFMPEG_BINARY", "").strip()
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidate = Path(configured) if configured else Path(shutil.which(name) or "")
    if not candidate.is_file():
        raise SystemExit("a native FFmpeg is required; install it or set ENMOTION_FFMPEG_BINARY")
    subprocess.run(
        [str(candidate), "-version"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return candidate.resolve()


def build(target: str) -> Path:
    expected = TARGETS[target]
    actual = native_identity()
    if actual != expected:
        raise SystemExit(
            f"PyInstaller cannot cross-compile: target {target} needs {expected}, "
            f"runner is {actual}"
        )
    if not (REPOSITORY_ROOT / "config/model_catalog/generated/model_catalog.json").is_file():
        raise SystemExit("generated model catalog is missing")
    ffmpeg = ffmpeg_binary()
    extension = ".exe" if target.endswith("windows-msvc") else ""
    final = DESKTOP_ROOT / "src-tauri" / "binaries" / f"enmotion-sidecar-{target}{extension}"
    final.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="enmotion-sidecar-") as temporary_name:
        temporary = Path(temporary_name)
        work = temporary / "work"
        dist = temporary / "dist"
        separator = ";" if os.name == "nt" else ":"
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--name",
            "enmotion-sidecar",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            "--specpath",
            str(temporary),
            "--paths",
            str(REPOSITORY_ROOT),
            "--additional-hooks-dir",
            str(REPOSITORY_ROOT / ".pyinstaller-hooks"),
            "--add-data",
            f"{REPOSITORY_ROOT / 'src/apps/comic_gen/style_presets.json'}"
            f"{separator}src/apps/comic_gen",
            "--add-data",
            f"{REPOSITORY_ROOT / 'config/model_catalog/generated/model_catalog.json'}"
            f"{separator}config/model_catalog/generated",
            "--add-binary",
            f"{ffmpeg}{separator}.",
            "--hidden-import",
            "src.apps.comic_gen.api",
            "--hidden-import",
            "uvicorn.logging",
            "--hidden-import",
            "uvicorn.loops.auto",
            "--hidden-import",
            "uvicorn.protocols.http.auto",
            "--hidden-import",
            "uvicorn.protocols.websockets.auto",
            "--hidden-import",
            "uvicorn.lifespan.on",
            "--hidden-import",
            "openai",
            "--hidden-import",
            "oss2",
            "--hidden-import",
            "demucs.pretrained",
            "--hidden-import",
            "demucs.separate",
            "--hidden-import",
            "soundfile",
            "--hidden-import",
            "multipart",
            "--hidden-import",
            "keyring",
            "--collect-all",
            "uvicorn",
            "--collect-all",
            "keyring",
            "--collect-all",
            "demucs",
        ]
        if os.name == "nt":
            command.extend(["--noconsole", "--exclude-module", "uvloop"])
        if platform.system() == "Darwin":
            signing_identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
            signing_required = os.environ.get("ENMOTION_REQUIRE_CODE_SIGNING", "").strip() == "1"
            if signing_required and not signing_identity:
                raise SystemExit("APPLE_SIGNING_IDENTITY is required for a release sidecar")
            if signing_identity:
                command.extend(["--codesign-identity", signing_identity])
        command.append(str(DESKTOP_ROOT / "python" / "sidecar.py"))
        subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)
        built = dist / f"enmotion-sidecar{extension}"
        if not built.is_file():
            raise SystemExit(f"PyInstaller did not create {built}")
        subprocess.run(
            [str(built), "--verify-bundle"],
            cwd=temporary,
            check=True,
            timeout=120,
        )
        temporary_final = final.with_suffix(final.suffix + ".partial")
        shutil.copy2(built, temporary_final)
        if final.exists():
            final.unlink()
        os.replace(temporary_final, final)
        final.chmod(final.stat().st_mode | 0o100)
    print(f"built EnMotion sidecar: {final}")
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    args = parser.parse_args()
    build(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
