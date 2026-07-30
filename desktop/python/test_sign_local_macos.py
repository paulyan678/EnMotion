from __future__ import annotations

import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "desktop" / "scripts" / "sign_local_macos.py"
SPEC = importlib.util.spec_from_file_location("sign_local_macos", SCRIPT)
assert SPEC and SPEC.loader
sign_local_macos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sign_local_macos)


class SignLocalMacOSTests(unittest.TestCase):
    def make_app(self, bundle_identifier: str = "com.enmotion.desktop") -> Path:
        root = Path(self.temporary.name)
        app = root / "EnMotion.app"
        (app / "Contents" / "Resources" / "sidecar").mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleIdentifier": bundle_identifier}, handle)
        (app / "Contents" / "Resources" / "sidecar" / "enmotion-sidecar").touch()
        (app / "Contents" / "MacOS" / "enmotion-demucs-worker").touch()
        (app / "Contents" / "MacOS" / "enmotion").touch()
        return app

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @mock.patch.object(sign_local_macos.platform, "system", return_value="Darwin")
    def test_bundle_paths_accept_only_enmotion(self, _system: mock.Mock) -> None:
        app = self.make_app()
        main, core, worker = sign_local_macos.bundle_paths(app)
        self.assertEqual(main.name, "enmotion")
        self.assertEqual(core.name, "enmotion-sidecar")
        self.assertEqual(worker.name, "enmotion-demucs-worker")

        wrong = self.make_app("com.example.other")
        with self.assertRaisesRegex(SystemExit, "refusing to sign bundle identifier"):
            sign_local_macos.bundle_paths(wrong)

    def test_embedded_control_plane_must_match_expected_origin(self) -> None:
        main = Path(self.temporary.name) / "enmotion"
        main.write_bytes(b"prefix-http://127.0.0.1:18787-suffix")
        self.assertEqual(
            sign_local_macos.ensure_embedded_control_plane(main, "http://127.0.0.1:18787/"),
            "http://127.0.0.1:18787",
        )
        with self.assertRaisesRegex(SystemExit, "is not embedded"):
            sign_local_macos.ensure_embedded_control_plane(main, "https://accounts.example.com")

    def test_control_plane_origin_rejects_credentials_and_public_http(self) -> None:
        with self.assertRaisesRegex(SystemExit, "credential-free origin"):
            sign_local_macos.normalize_control_plane_url("https://user:secret@example.com")
        with self.assertRaisesRegex(SystemExit, "HTTPS or loopback HTTP"):
            sign_local_macos.normalize_control_plane_url("http://accounts.example.com")

    def test_only_outer_app_keeps_hardened_runtime(self) -> None:
        app = Path("/tmp/EnMotion.app")
        core = app / "Contents/Resources/sidecar/enmotion-sidecar"
        worker = app / "Contents/MacOS/enmotion-demucs-worker"
        core_command, worker_command, app_command = sign_local_macos.signing_commands(
            app, core, worker
        )

        self.assertNotIn("--options", core_command)
        self.assertNotIn("--options", worker_command)
        self.assertEqual(app_command[app_command.index("--options") + 1], "runtime")
        self.assertIn("com.enmotion.desktop.sidecar", core_command)
        self.assertIn("com.enmotion.desktop.demucs-worker", worker_command)
        self.assertEqual(core_command[-1], str(core))
        self.assertEqual(worker_command[-1], str(worker))
        self.assertEqual(app_command[-1], str(app))

    def test_refuses_release_signing_environment(self) -> None:
        with mock.patch.dict(
            sign_local_macos.os.environ,
            {"ENMOTION_REQUIRE_CODE_SIGNING": "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(SystemExit, "release signing is configured"):
                sign_local_macos.ensure_local_environment()

        with mock.patch.dict(
            sign_local_macos.os.environ,
            {"APPLE_SIGNING_IDENTITY": "Developer ID Application: Example"},
            clear=True,
        ):
            with self.assertRaisesRegex(SystemExit, "release signing is configured"):
                sign_local_macos.ensure_local_environment()

    @mock.patch.object(sign_local_macos, "signature_details")
    def test_signature_flags_are_parsed(self, details: mock.Mock) -> None:
        details.return_value = mock.Mock(
            stdout="",
            stderr=(
                "CodeDirectory v=20500 size=10 "
                "flags=0x10002(adhoc,runtime) hashes=1+0 location=embedded\n"
            ),
        )
        self.assertEqual(
            sign_local_macos.signature_flags(Path("/tmp/EnMotion.app")),
            {"adhoc", "runtime"},
        )

    @mock.patch.object(sign_local_macos, "signature_details")
    def test_signature_identifier_is_parsed(self, details: mock.Mock) -> None:
        details.return_value = mock.Mock(
            stdout="",
            stderr="Identifier=com.enmotion.desktop.sidecar\n",
        )
        self.assertEqual(
            sign_local_macos.signature_identifier(Path("/tmp/enmotion-sidecar")),
            "com.enmotion.desktop.sidecar",
        )

    @mock.patch.object(sign_local_macos.subprocess, "run")
    def test_refuses_to_overwrite_distribution_signature(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(
            returncode=0,
            stdout="",
            stderr="Authority=Developer ID Application: Example\n",
        )
        with self.assertRaisesRegex(SystemExit, "non-ad-hoc"):
            sign_local_macos.ensure_local_signature(Path("/tmp/EnMotion.app"))


if __name__ == "__main__":
    unittest.main()
