from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path

import sidecar
from starlette.requests import Request


def encode(payload: dict[str, object]) -> str:
    value = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class RuntimeContractTests(unittest.TestCase):
    def test_fastapi_request_annotation_resolves_at_module_scope(self) -> None:
        self.assertIs(sidecar.Request, Request)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.static_dir = root / "static"
        self.static_dir.mkdir()
        (self.static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        self.payload = {
            "schemaVersion": 1,
            "host": "127.0.0.1",
            "port": 24567,
            "nonce": "a" * 64,
            "staticDir": str(self.static_dir),
            "dataDir": str(root / "data"),
            "outputDir": str(root / "Documents" / "enmotion-output"),
            "currentVersion": "1.0.0",
            "controlPlaneUrl": "https://accounts.enmotion.example",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_runtime_contract_accepts_only_expected_loopback_shape(self) -> None:
        parsed = sidecar.parse_runtime_config(encode(self.payload))
        self.assertEqual(parsed.host, "127.0.0.1")
        self.assertEqual(parsed.port, 24567)
        self.assertEqual(parsed.output_dir.name, "enmotion-output")

    def test_runtime_contract_rejects_non_loopback_binding(self) -> None:
        self.payload["host"] = "0.0.0.0"
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            sidecar.parse_runtime_config(encode(self.payload))

    def test_runtime_contract_rejects_short_nonce(self) -> None:
        self.payload["nonce"] = "a" * 32
        with self.assertRaisesRegex(ValueError, "256-bit"):
            sidecar.parse_runtime_config(encode(self.payload))

    def test_runtime_contract_rejects_unknown_fields(self) -> None:
        self.payload["githubToken"] = "must-never-be-accepted"
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            sidecar.parse_runtime_config(encode(self.payload))

    def test_cookie_is_derived_and_does_not_reveal_nonce(self) -> None:
        nonce = "b" * 64
        cookie = sidecar.session_cookie_value(nonce)
        self.assertEqual(len(cookie), 64)
        self.assertNotEqual(cookie, nonce)
        self.assertNotEqual(sidecar.local_api_nonce(nonce), nonce)

    def test_bootstrap_cookie_survives_the_tauri_to_loopback_redirect(self) -> None:
        response = sidecar.desktop_session_response("c" * 64)
        cookie = response.headers["set-cookie"].lower()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/static/index.html")
        self.assertIn("enmotion_desktop_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/", cookie)

    def test_readiness_proof_is_bound_to_version_and_port(self) -> None:
        config = sidecar.parse_runtime_config(encode(self.payload))
        first = sidecar.readiness_proof(config)
        self.payload["currentVersion"] = "1.0.1"
        second = sidecar.readiness_proof(
            sidecar.parse_runtime_config(encode(self.payload))
        )
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)

    def test_static_csp_hashes_inline_next_scripts_without_unsafe_inline(self) -> None:
        (self.static_dir / "index.html").write_text(
            '<script>self.__next_f.push("ready")</script><script src="/app.js"></script>',
            encoding="utf-8",
        )
        policies = sidecar.static_content_security_policies(self.static_dir)
        policy = sidecar.csp_for_request("/static/index.html", policies)
        self.assertIn("script-src 'self' 'sha256-", policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", policy)
        self.assertIn(
            "connect-src 'self' ipc: http://ipc.localhost;",
            policy,
        )
        self.assertNotIn(
            "connect-src 'self' ipc: http://ipc.localhost https:",
            policy,
        )
        self.assertIn("media-src 'self' blob: https:;", policy)
        self.assertEqual(
            policy,
            sidecar.csp_for_request("/static/", policies),
        )

    def test_runtime_contract_rejects_insecure_remote_control_plane(self) -> None:
        self.payload["controlPlaneUrl"] = "http://accounts.enmotion.example"
        with self.assertRaisesRegex(ValueError, "secure absolute origin"):
            sidecar.parse_runtime_config(encode(self.payload))

    def test_update_manifest_capability_must_stay_on_control_plane(self) -> None:
        token = "A" * 48
        expected = (
            "https://accounts.enmotion.example"
            f"/api/v1/releases/session/{token}/manifest"
        )
        self.assertEqual(
            sidecar.validated_update_manifest_url(
                expected,
                "https://accounts.enmotion.example",
            ),
            expected,
        )
        for unsafe in (
            expected.replace("https://accounts", "https://attacker"),
            expected.replace("https://", "http://"),
            expected + "?access_token=secret",
            "https://accounts.enmotion.example/api/v1/releases/session/short/manifest",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaisesRegex(ValueError, "untrusted manifest"):
                    sidecar.validated_update_manifest_url(
                        unsafe,
                        "https://accounts.enmotion.example",
                    )

    def test_active_update_blockers_only_include_unfinished_work(self) -> None:
        class Pipeline:
            asset_generation_tasks = {
                "done": {"status": "completed"},
                "active": {"status": "processing"},
            }
            video_generation_tasks = {
                "failed": {"status": "failed"},
                "queued": {"status": "queued"},
            }
            scripts = {
                "project-1": {
                    "video_tasks": [
                        {"id": "clip-running", "status": "running"},
                        {"id": "clip-done", "status": "completed"},
                    ]
                }
            }

        self.assertEqual(
            sidecar.active_update_blockers(Pipeline()),
            [
                "asset_generation_tasks:active:processing",
                "scripts:project-1:video_tasks:clip-running:running",
                "video_generation_tasks:queued:queued",
            ],
        )

    def test_active_desktop_pipelines_uses_loaded_writers_only(self) -> None:
        writer = object()

        class Registry:
            _lock = threading.RLock()
            _writer_pipelines = {"employee-1": (writer, "fingerprint")}
            _reader_pipelines = {"employee-1": (object(), 1)}

        class ApiModule:
            _workspace_pipelines = Registry()

        self.assertEqual(sidecar.active_desktop_pipelines(ApiModule()), [writer])

    def test_playground_blocker_contract_is_wired_into_update_preparation(self) -> None:
        source = Path(sidecar.__file__).read_text(encoding="utf-8")
        self.assertIn("active_playground_generation_blockers", source)
        self.assertIn("blockers.extend(active_playground_blockers())", source)

    def test_update_barrier_blocks_new_mutations_and_recovers_after_cancel(self) -> None:
        barrier = sidecar.UpdateBarrier()
        mutation = barrier.enter_mutation()
        self.assertEqual(barrier.begin_prepare(), 1)
        with self.assertRaisesRegex(RuntimeError, "update is being installed"):
            barrier.enter_mutation()
        barrier.leave_mutation(mutation)
        barrier.cancel_prepare()
        next_mutation = barrier.enter_mutation()
        barrier.leave_mutation(next_mutation)

    def test_update_backup_is_idempotent_and_never_copies_output(self) -> None:
        config = sidecar.parse_runtime_config(encode(self.payload))
        config.data_dir.mkdir(parents=True)
        config.output_dir.mkdir(parents=True)
        (config.data_dir / "projects.json").write_text('{"ok": true}', encoding="utf-8")
        media = config.output_dir / "keep.mp4"
        media.write_bytes(b"generated-media")
        workspace = config.output_dir / "workspaces" / "employee-1" / "output"
        workspace.mkdir(parents=True)
        (workspace / "projects.json").write_text(
            '{"workspace": true}', encoding="utf-8"
        )
        (workspace / "playground_history.json").write_text(
            '[{"id":"generation-1"}]', encoding="utf-8"
        )

        first = sidecar.create_update_backup(config, target_version="1.0.1")
        second = sidecar.create_update_backup(config, target_version="1.0.1")

        self.assertEqual(first["transactionId"], second["transactionId"])
        backup = Path(first["backupDirectory"])
        self.assertTrue((backup / "projects.json").is_file())
        self.assertTrue(
            (backup / "workspaces/employee-1/projects.json").is_file()
        )
        self.assertTrue(
            (backup / "workspaces/employee-1/playground_history.json").is_file()
        )
        self.assertFalse((backup / "keep.mp4").exists())
        self.assertEqual(media.read_bytes(), b"generated-media")


if __name__ == "__main__":
    unittest.main()
