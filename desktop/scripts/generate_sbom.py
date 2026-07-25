#!/usr/bin/env python3
"""Generate a compact CycloneDX SBOM for one EnMotion desktop target."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def component(component_type: str, name: str, version: str, purl: str) -> dict[str, str]:
    return {
        "type": component_type,
        "name": name,
        "version": version,
        "purl": purl,
    }


def node_components() -> list[dict[str, str]]:
    lock = json.loads((REPOSITORY_ROOT / "frontend/package-lock.json").read_text())
    result = []
    for path, payload in lock.get("packages", {}).items():
        if not path.startswith("node_modules/") or not payload.get("version"):
            continue
        name = path.removeprefix("node_modules/")
        result.append(
            component(
                "library",
                name,
                str(payload["version"]),
                f"pkg:npm/{name}@{payload['version']}",
            )
        )
    return result


def python_components() -> list[dict[str, str]]:
    result = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        result.append(
            component(
                "library",
                name,
                distribution.version,
                f"pkg:pypi/{name.lower().replace('_', '-')}@{distribution.version}",
            )
        )
    return result


def rust_components() -> list[dict[str, str]]:
    cargo_lock = REPOSITORY_ROOT / "desktop/src-tauri/Cargo.lock"
    if not cargo_lock.is_file():
        return []
    result = []
    for package in tomllib.loads(cargo_lock.read_text()).get("package", []):
        name = package["name"]
        version = str(package["version"])
        result.append(component("library", name, version, f"pkg:cargo/{name}@{version}"))
    return result


def ffmpeg_component(binary: Path) -> dict[str, str]:
    completed = subprocess.run(
        [str(binary), "-version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    first_line = completed.stdout.splitlines()[0].strip()
    prefix = "ffmpeg version "
    if not first_line.lower().startswith(prefix):
        raise SystemExit("could not identify the bundled FFmpeg version")
    version = first_line[len(prefix) :].split(maxsplit=1)[0]
    if not version:
        raise SystemExit("bundled FFmpeg reported an empty version")
    return component(
        "application",
        "FFmpeg",
        version,
        f"pkg:generic/ffmpeg@{quote(version, safe='.-_+')}",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.ffmpeg.is_file():
        raise SystemExit(f"bundled FFmpeg does not exist: {args.ffmpeg}")
    components = (
        node_components()
        + python_components()
        + rust_components()
        + [ffmpeg_component(args.ffmpeg)]
    )
    unique = {
        value["purl"]: value
        for value in sorted(components, key=lambda value: value["purl"].lower())
    }
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:"
        + str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"https://enmotion.invalid/sbom/{args.version}/{args.target}",
            )
        ),
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {
                "type": "application",
                "name": "EnMotion Desktop",
                "version": args.version,
                "properties": [{"name": "enmotion:target", "value": args.target}],
            },
        },
        "components": list(unique.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"generated SBOM: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
