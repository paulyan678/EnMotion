#!/usr/bin/env python3
"""Build launch-critical and on-demand PyInstaller binaries for Tauri."""

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
    binaries = DESKTOP_ROOT / "src-tauri" / "binaries"
    runtime = binaries / "enmotion-sidecar-runtime"
    final_worker = binaries / f"enmotion-demucs-worker-{target}{extension}"
    legacy_final = binaries / f"enmotion-sidecar-{target}{extension}"
    binaries.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="enmotion-sidecar-") as temporary_name:
        temporary = Path(temporary_name)
        work = temporary / "core-work"
        worker_work = temporary / "worker-work"
        dist = temporary / "dist"
        separator = ";" if os.name == "nt" else ":"
        signing_args: list[str] = []
        if platform.system() == "Darwin":
            signing_identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
            signing_required = os.environ.get("ENMOTION_REQUIRE_CODE_SIGNING", "").strip() == "1"
            if signing_required and not signing_identity:
                raise SystemExit("APPLE_SIGNING_IDENTITY is required for release sidecars")
            if signing_identity:
                signing_args = ["--codesign-identity", signing_identity]

        worker_command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--name",
            "enmotion-demucs-worker",
            "--distpath",
            str(dist),
            "--workpath",
            str(worker_work),
            "--specpath",
            str(temporary),
            "--paths",
            str(REPOSITORY_ROOT),
            "--hidden-import",
            "demucs.pretrained",
            "--hidden-import",
            "demucs.separate",
            "--hidden-import",
            "soundfile",
            "--collect-all",
            "demucs",
            *signing_args,
        ]
        if os.name == "nt":
            worker_command.extend(["--noconsole", "--exclude-module", "uvloop"])
        worker_command.append(str(DESKTOP_ROOT / "python" / "demucs_worker.py"))
        subprocess.run(worker_command, cwd=REPOSITORY_ROOT, check=True)
        built_worker = dist / f"enmotion-demucs-worker{extension}"
        if not built_worker.is_file():
            raise SystemExit(f"PyInstaller did not create {built_worker}")
        subprocess.run(
            [str(built_worker), "--verify-bundle"],
            cwd=temporary,
            check=True,
            timeout=120,
        )

        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onedir",
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
            "multipart",
            "--collect-all",
            "uvicorn",
            "--exclude-module",
            "demucs",
            "--exclude-module",
            "torch",
            "--exclude-module",
            "torchaudio",
            "--exclude-module",
            "torchcodec",
            "--exclude-module",
            "soundfile",
            *signing_args,
        ]
        if os.name == "nt":
            command.extend(["--noconsole", "--exclude-module", "uvloop"])
        command.append(str(DESKTOP_ROOT / "python" / "sidecar.py"))
        subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)
        built_runtime = dist / "enmotion-sidecar"
        built = built_runtime / f"enmotion-sidecar{extension}"
        if not built.is_file():
            raise SystemExit(f"PyInstaller did not create {built}")
        verification_environment = os.environ.copy()
        verification_environment["ENMOTION_DEMUCS_WORKER"] = str(built_worker)
        subprocess.run(
            [str(built), "--verify-bundle"],
            cwd=temporary,
            env=verification_environment,
            check=True,
            timeout=120,
        )

        temporary_runtime = binaries / ".enmotion-sidecar-runtime.partial"
        if temporary_runtime.exists():
            shutil.rmtree(temporary_runtime)
        shutil.copytree(built_runtime, temporary_runtime)
        if runtime.exists():
            shutil.rmtree(runtime)
        os.replace(temporary_runtime, runtime)

        temporary_worker = final_worker.with_suffix(final_worker.suffix + ".partial")
        shutil.copy2(built_worker, temporary_worker)
        if final_worker.exists():
            final_worker.unlink()
        os.replace(temporary_worker, final_worker)
        final_worker.chmod(final_worker.stat().st_mode | 0o100)

        if legacy_final.exists():
            legacy_final.unlink()
    final = runtime / f"enmotion-sidecar{extension}"
    print(f"built EnMotion launch runtime: {runtime}")
    print(f"built EnMotion on-demand Demucs worker: {final_worker}")
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    args = parser.parse_args()
    build(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
