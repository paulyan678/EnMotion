import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop.scripts import run_qa_profile


class QaProfileTests(unittest.TestCase):
    def test_profile_names_are_strict_and_contained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with mock.patch.object(run_qa_profile, "profile_root", return_value=root):
                self.assertEqual(
                    run_qa_profile.profile_path("qa-20260802_ab12"),
                    root / "qa-20260802_ab12",
                )
                for invalid in ("", "../escape", "nested/profile", "with spaces", "é"):
                    with self.assertRaises(ValueError):
                        run_qa_profile.profile_path(invalid)

    def test_cleanup_requires_matching_manifest_and_stopped_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            profile = root / "qa-clean"
            profile.mkdir()
            manifest = {
                "bundle_id": run_qa_profile.BUNDLE_ID,
                "profile": "qa-clean",
                "pid": 100,
            }
            (profile / run_qa_profile.MANIFEST_NAME).write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            with (
                mock.patch.object(run_qa_profile, "profile_root", return_value=root),
                mock.patch.object(run_qa_profile, "process_is_running", return_value=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "still running"):
                    run_qa_profile.cleanup_profile("qa-clean")
            with (
                mock.patch.object(run_qa_profile, "profile_root", return_value=root),
                mock.patch.object(run_qa_profile, "process_is_running", return_value=False),
            ):
                self.assertEqual(run_qa_profile.cleanup_profile("qa-clean"), profile)
            self.assertFalse(profile.exists())

    def test_app_bundle_identifier_is_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "EnMotion.app"
            executable = app / "Contents" / "MacOS" / "EnMotion"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"#!/bin/sh\n")
            executable.chmod(0o755)
            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": run_qa_profile.BUNDLE_ID,
                        "CFBundleExecutable": "EnMotion",
                    },
                    handle,
                )

            self.assertEqual(run_qa_profile.app_executable(app), executable.resolve())


if __name__ == "__main__":
    unittest.main()
