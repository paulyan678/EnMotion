from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from desktop.scripts.generate_update_manifest import (
    parse_source,
    validate_release_identity,
)
from desktop.scripts.validate import is_https_origin

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPOSITORY_ROOT / "desktop/scripts/generate_update_manifest.py"
PLATFORMS = {
    "macos-arm64": "mac-arm.tar.gz",
    "macos-x86_64": "mac-intel.tar.gz",
    "windows-x86_64": "EnMotion-Setup-1.2.3-Windows-x64.exe",
}
SOURCE_URLS = {
    platform: f"https://github.com/acme/EnMotion/releases/download/desktop-v1.2.3/{filename}"
    for platform, filename in PLATFORMS.items()
}


class ReleaseManifestTests(unittest.TestCase):
    def test_release_control_plane_must_be_a_credential_free_https_origin(
        self,
    ) -> None:
        self.assertTrue(is_https_origin("https://accounts.enmotion.example"))
        self.assertTrue(is_https_origin("https://accounts.enmotion.example:8443/"))
        for unsafe in (
            "http://accounts.enmotion.example",
            "https://accounts.enmotion.example/path",
            "https://accounts.enmotion.example?token=secret",
            "https://user@accounts.enmotion.example",
            "https://accounts.enmotion.example.invalid",
            "https://accounts.enmotion.example:invalid",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertFalse(is_https_origin(unsafe))

    def test_sources_must_be_public_github_release_download_urls(self) -> None:
        self.assertEqual(
            parse_source(
                "macos-arm64=https://github.com/acme/EnMotion/releases/download/"
                "desktop-v1.2.3/mac-arm.tar.gz"
            )[0],
            "macos-arm64",
        )
        for unsafe in (
            "https://api.github.com/repos/acme/EnMotion/releases/assets/123",
            "https://github.com/acme/EnMotion/releases/latest/download/app.zip",
            "https://api.github.com/repos/acme/enmotion/releases/assets/not-numeric",
            "https://github.com/acme/EnMotion/releases/download/"
            "desktop-v1.2.3/app.zip?token=secret",
            "https://token@github.com/acme/EnMotion/releases/download/desktop-v1.2.3/app.zip",
            "https://github.com/acme/EnMotion/releases/download/../app.zip",
            "https://github.com/acme/EnMotion/releases/download/desktop-v1.2.3/%2Fapp.zip",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_source(f"macos-arm64={unsafe}")

    def test_release_identity_is_version_repo_and_asset_bound(self) -> None:
        assets = {platform: Path(filename) for platform, filename in PLATFORMS.items()}
        validate_release_identity("1.2.3", assets, SOURCE_URLS)

        mismatches = (
            {
                **SOURCE_URLS,
                "macos-arm64": SOURCE_URLS["macos-arm64"].replace(
                    "desktop-v1.2.3", "desktop-v1.2.4"
                ),
            },
            {
                **SOURCE_URLS,
                "macos-arm64": SOURCE_URLS["macos-arm64"].replace(
                    "github.com/acme/", "github.com/other/"
                ),
            },
            {
                **SOURCE_URLS,
                "macos-arm64": SOURCE_URLS["macos-arm64"].replace(
                    "mac-arm.tar.gz", "different.tar.gz"
                ),
            },
        )
        for sources in mismatches:
            with self.subTest(sources=sources):
                with self.assertRaises(SystemExit):
                    validate_release_identity("1.2.3", assets, sources)
        with self.assertRaises(SystemExit):
            validate_release_identity("not-semver", assets, SOURCE_URLS)

    def test_generator_emits_signed_control_plane_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = [
                sys.executable,
                str(GENERATOR),
                "--version",
                "1.2.3",
            ]
            expected_hashes: dict[str, str] = {}
            for platform, filename in PLATFORMS.items():
                archive = root / filename
                content = f"signed archive for {platform}".encode()
                archive.write_bytes(content)
                Path(f"{archive}.sig").write_text(
                    f"signature-{platform}\n",
                    encoding="utf-8",
                )
                arguments.extend(["--asset", f"{platform}={archive}"])
                arguments.extend(["--source", f"{platform}={SOURCE_URLS[platform]}"])
                expected_hashes[platform] = hashlib.sha256(content).hexdigest()
            output = root / "control-plane-releases.json"
            arguments.extend(["--notes", "Test release", "--output", str(output)])

            completed = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["contract_version"], 1)
            self.assertEqual(len(payload["releases"]), 3)
            for release in payload["releases"]:
                platform = release["platform"]
                self.assertEqual(release["version"], "1.2.3")
                self.assertEqual(release["channel"], "stable")
                self.assertEqual(release["sha256"], expected_hashes[platform])
                self.assertEqual(
                    release["signature"],
                    f"signature-{platform}",
                )
                self.assertEqual(release["source_url"], SOURCE_URLS[platform])
                self.assertGreater(release["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
