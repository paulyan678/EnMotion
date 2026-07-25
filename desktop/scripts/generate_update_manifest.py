#!/usr/bin/env python3
"""Create the signed release inventory consumed by EnMotion's control plane."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


PLATFORMS = {
    "macos-arm64",
    "macos-x86_64",
    "windows-x86_64",
}
_SAFE_RELEASE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_asset(value: str) -> tuple[str, Path]:
    platform_name, separator, filename = value.partition("=")
    if separator != "=" or platform_name not in PLATFORMS:
        raise argparse.ArgumentTypeError(
            "--asset must be platform=/path using " + ", ".join(sorted(PLATFORMS))
        )
    return platform_name, Path(filename)


def _release_url_parts(source_url: str) -> list[str]:
    parsed = urlparse(source_url)
    path_parts = parsed.path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
        or len(path_parts) != 7
        or path_parts[0] != ""
        or not path_parts[1]
        or not path_parts[2]
        or path_parts[3:5] != ["releases", "download"]
        or any(
            not _SAFE_RELEASE_SEGMENT.fullmatch(segment)
            for segment in (path_parts[1], path_parts[2], path_parts[5], path_parts[6])
        )
        or path_parts[5] in {".", ".."}
        or path_parts[6] in {".", ".."}
    ):
        raise argparse.ArgumentTypeError(
            "--source values must be public GitHub release download URLs"
        )
    return path_parts


def parse_source(value: str) -> tuple[str, str]:
    platform_name, separator, source_url = value.partition("=")
    if separator != "=" or platform_name not in PLATFORMS:
        raise argparse.ArgumentTypeError(
            "--source must be platform=https://... using "
            + ", ".join(sorted(PLATFORMS))
        )
    _release_url_parts(source_url)
    return platform_name, source_url


def validate_release_identity(
    version: str,
    assets: dict[str, Path],
    sources: dict[str, str],
) -> None:
    if not _SEMVER.fullmatch(version):
        raise SystemExit("release version must be valid SemVer without a leading v")
    expected_tag = f"desktop-v{version}"
    parsed_sources = {
        platform_name: _release_url_parts(source_url)
        for platform_name, source_url in sources.items()
    }
    repositories = {
        (parts[1].lower(), parts[2].lower()) for parts in parsed_sources.values()
    }
    if len(repositories) != 1:
        raise SystemExit("all updater sources must belong to the same GitHub repository")
    for platform_name in PLATFORMS:
        parts = parsed_sources[platform_name]
        if parts[5] != expected_tag:
            raise SystemExit(
                f"updater source tag for {platform_name} must be {expected_tag}"
            )
        if parts[6] != assets[platform_name].name:
            raise SystemExit(
                f"updater source asset for {platform_name} does not match the local artifact"
            )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--asset", action="append", type=parse_asset, required=True)
    parser.add_argument("--source", action="append", type=parse_source, required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assets = dict(args.asset)
    sources = dict(args.source)
    if len(args.asset) != len(PLATFORMS) or set(assets) != PLATFORMS:
        raise SystemExit(f"exactly one updater asset is required for {sorted(PLATFORMS)}")
    if len(args.source) != len(PLATFORMS) or set(sources) != PLATFORMS:
        raise SystemExit(f"exactly one updater source is required for {sorted(PLATFORMS)}")
    validate_release_identity(args.version, assets, sources)
    published_at = datetime.now(timezone.utc).isoformat()
    releases: list[dict[str, str | int]] = []
    for platform_name, asset in assets.items():
        signature_path = Path(str(asset) + ".sig")
        if not asset.is_file() or not signature_path.is_file():
            raise SystemExit(f"missing updater archive or signature for {platform_name}")
        signature = signature_path.read_text(encoding="utf-8").strip()
        if not signature:
            raise SystemExit(f"empty updater signature: {signature_path}")
        releases.append(
            {
                "version": args.version,
                "platform": platform_name,
                "channel": "stable",
                "sha256": sha256_file(asset),
                "size_bytes": asset.stat().st_size,
                "published_at": published_at,
                "source_url": sources[platform_name],
                "signature": signature,
                "notes": args.notes,
            }
        )
    payload = {
        "contract_version": 1,
        "releases": sorted(releases, key=lambda release: str(release["platform"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"generated updater metadata: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
