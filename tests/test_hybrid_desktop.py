import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.hybrid.client import ControlPlaneClient, ControlPlaneError, RemoteLogin
from src.apps.hybrid.config import (
    HybridConfigurationError,
    HybridSettings,
    hybrid_mode_enabled,
    workspace_isolation_enabled,
)
from src.apps.hybrid.middleware import HybridAuthMiddleware
from src.apps.hybrid.provider import provider_gateway_base_url
from src.apps.hybrid.router import router as hybrid_router
from src.apps.hybrid.session import (
    HybridUser,
    LocalSession,
    SessionVault,
    StalePersistedCredentialError,
    session_vault,
)
from src.apps.playground.models import PlaygroundGeneration, PlaygroundMode
from src.apps.playground.storage import PlaygroundStorage
from src.apps.web_runtime.file_lock import (
    acquire_lock_file,
    interprocess_lock,
    release_lock_file,
)
from src.apps.web_runtime.pipeline_registry import WorkspacePipelineRegistry


def _user(identifier: str) -> HybridUser:
    return HybridUser(
        id=identifier,
        username=identifier,
        role="user",
        workspace_id=f"workspace-{identifier}",
    )


def test_hybrid_mode_enables_workspace_isolation_without_server_mode() -> None:
    environment = {
        "ENMOTION_HYBRID_MODE": "true",
        "ENMOTION_SERVER_MODE": "false",
    }

    assert hybrid_mode_enabled(environment)
    assert workspace_isolation_enabled(environment)


def test_authenticated_hybrid_read_does_not_wait_for_busy_workspace_writer(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))
    settings = HybridSettings(
        enabled=True,
        control_plane_url="http://127.0.0.1:8123",
    )
    local = LocalSession(
        token="local-session",
        csrf_token="csrf-token",
        user=_user("nonblocking-read"),
    )
    monkeypatch.setattr(session_vault, "get_local", lambda _token: local)
    app = FastAPI()
    app.add_middleware(HybridAuthMiddleware, settings=settings)

    @app.get("/protected/atomic-snapshot")
    def atomic_snapshot():
        lock_path = workspace_root / local.user.workspace_id / ".workspace.lock"
        with interprocess_lock(lock_path):
            return {"workspace_id": local.user.workspace_id}

    lock_path = workspace_root / local.user.workspace_id / ".workspace.lock"
    descriptor, _canonical = acquire_lock_file(lock_path)
    try:
        with TestClient(app, base_url="http://testserver") as client:
            client.cookies.set(settings.session_cookie_name, local.token)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    client.get,
                    "/protected/atomic-snapshot",
                )
                try:
                    response = future.result(timeout=1.0)
                except TimeoutError:  # pragma: no cover - regression guard
                    pytest.fail("authenticated hybrid GET blocked on the workspace writer")
    finally:
        release_lock_file(descriptor)

    assert response.status_code == 200
    assert response.json() == {"workspace_id": local.user.workspace_id}
    assert response.headers["x-enmotion-workspace-id"] == local.user.workspace_id


def test_playground_admission_does_not_wait_for_busy_workspace_writer(tmp_path) -> None:
    output_root = tmp_path / "workspaces" / "workspace-alice" / "output"
    storage = PlaygroundStorage(
        output_root=str(output_root),
        shared_workspace=True,
    )
    generation = PlaygroundGeneration(
        id="generation-admitted",
        mode=PlaygroundMode.T2I,
        model_id="gpt-image-2",
        prompt="Dog",
        status="pending",
        created_at="2026-08-01T00:57:00+00:00",
    )

    descriptor, _canonical = acquire_lock_file(storage.workspace_lock_path)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(storage.add_generation, generation)
    try:
        try:
            future.result(timeout=1.0)
        except TimeoutError:  # pragma: no cover - regression guard
            pytest.fail("Playground admission waited for an unrelated workspace writer")
    finally:
        release_lock_file(descriptor)
        executor.shutdown(wait=True)

    assert storage.get_generation(generation.id) == generation


def test_playground_generate_does_not_wait_for_unrelated_hybrid_mutation(
    monkeypatch,
) -> None:
    settings = HybridSettings(
        enabled=True,
        control_plane_url="http://127.0.0.1:8123",
    )
    local = LocalSession(
        token="local-session",
        csrf_token="csrf-token",
        user=_user("playground-admission"),
    )
    monkeypatch.setattr(session_vault, "get_local", lambda _token: local)
    app = FastAPI()
    app.add_middleware(HybridAuthMiddleware, settings=settings)
    mutation_started = threading.Event()
    release_mutation = threading.Event()

    @app.post("/protected/slow-mutation")
    async def slow_mutation():
        mutation_started.set()
        await asyncio.to_thread(release_mutation.wait, 5)
        return {"ok": True}

    @app.post("/playground/generate")
    async def admit_playground_generation():
        return {"id": "generation-admitted", "status": "pending"}

    with TestClient(app, base_url="http://testserver") as client:
        client.cookies.set(settings.session_cookie_name, local.token)
        headers = {settings.csrf_header_name: local.csrf_token}
        executor = ThreadPoolExecutor(max_workers=2)
        mutation = executor.submit(
            client.post,
            "/protected/slow-mutation",
            headers=headers,
        )
        assert mutation_started.wait(1.0)
        admission = executor.submit(
            client.post,
            "/playground/generate",
            headers=headers,
        )
        try:
            try:
                response = admission.result(timeout=1.0)
            except TimeoutError:  # pragma: no cover - regression guard
                pytest.fail("Playground POST waited for an unrelated hybrid mutation")
        finally:
            release_mutation.set()
            mutation.result(timeout=2.0)
            admission.result(timeout=2.0)
            executor.shutdown(wait=True)

    assert response.status_code == 200
    assert response.json() == {"id": "generation-admitted", "status": "pending"}


def test_hybrid_task_status_reads_the_cached_writer_without_its_provider_lock(
    tmp_path,
) -> None:
    class Pipeline:
        @staticmethod
        def get_asset_generation_task_status(task_id: str):
            return {"task_id": task_id, "status": "processing", "progress": 50}

    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    registry._writer_pipelines["workspace-alice"] = (Pipeline(), ())

    assert registry.transient_task_status("workspace-alice", "task-1") == {
        "task_id": "task-1",
        "status": "processing",
        "progress": 50,
    }


def test_remote_control_plane_requires_https() -> None:
    with pytest.raises(HybridConfigurationError):
        HybridSettings.from_env(
            {
                "ENMOTION_HYBRID_MODE": "true",
                "ENMOTION_CONTROL_PLANE_URL": "http://accounts.example.com",
            }
        )

    settings = HybridSettings.from_env(
        {
            "ENMOTION_HYBRID_MODE": "true",
            "ENMOTION_CONTROL_PLANE_URL": "http://127.0.0.1:8123",
        }
    )
    assert settings.control_plane_url == "http://127.0.0.1:8123"


def test_account_switch_invalidates_previous_local_session(tmp_path) -> None:
    vault = SessionVault(credential_path=tmp_path / "refresh-token")
    first = vault.start(
        user=_user("first"),
        access_token="access-first",
        refresh_token="refresh-first",
        expires_in=900,
    )
    second = vault.start(
        user=_user("second"),
        access_token="access-second",
        refresh_token="refresh-second",
        expires_in=900,
    )

    assert vault.get_local(first.token) is None
    assert vault.get_local(second.token) == second
    with pytest.raises(RuntimeError):
        vault.remote_for_user("first")


def test_refresh_cannot_change_account_identity(tmp_path) -> None:
    vault = SessionVault(credential_path=tmp_path / "refresh-token")
    vault.start(
        user=_user("first"),
        access_token="expired",
        refresh_token="refresh-first",
        expires_in=30,
    )
    vault._remote.access_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    replacement = RemoteLogin(
        user=_user("second"),
        access_token="new-access",
        refresh_token="new-refresh",
        expires_in=900,
    )
    with pytest.raises(RuntimeError):
        vault.ensure_fresh("first", lambda _token: replacement)
    with pytest.raises(RuntimeError):
        vault.remote_for_user("first")


def test_refresh_token_persists_across_vault_instances(tmp_path) -> None:
    path = tmp_path / "refresh-token"
    first = SessionVault(credential_path=path)
    first.start(
        user=_user("persisted-session"),
        access_token="access",
        refresh_token="persisted-refresh",
        expires_in=900,
    )

    restored = SessionVault(credential_path=path).persisted_refresh_token_snapshot()

    assert restored is not None
    assert restored.value == "persisted-refresh"
    assert restored.generation == 0


def test_credential_write_failure_prevents_session_commit(monkeypatch) -> None:
    class FailingStore:
        @staticmethod
        def write(_value: str) -> None:
            raise RuntimeError("credential write failed")

    vault = SessionVault()
    monkeypatch.setattr(vault, "_credential_store", lambda: FailingStore)

    with pytest.raises(RuntimeError, match="credential write failed"):
        vault.start(
            user=_user("failed-login"),
            access_token="access",
            refresh_token="refresh",
            expires_in=900,
        )
    with pytest.raises(RuntimeError):
        vault.remote_for_user("failed-login")


def test_restore_cannot_overwrite_a_newer_credential_state(monkeypatch) -> None:
    class ImmediateStore:
        @staticmethod
        def read() -> str:
            return "persisted-refresh"

        @staticmethod
        def write(_value: str) -> None:
            return None

        @staticmethod
        def delete() -> None:
            return None

    vault = SessionVault()
    monkeypatch.setattr(vault, "_credential_store", lambda: ImmediateStore)
    persisted = vault.persisted_refresh_token_snapshot()
    assert persisted is not None

    vault.clear()

    with pytest.raises(StalePersistedCredentialError):
        vault.start(
            user=_user("stale-restore"),
            access_token="stale-access",
            refresh_token="stale-refresh",
            expires_in=900,
            expected_credential_generation=persisted.generation,
        )
    with pytest.raises(RuntimeError):
        vault.remote_for_user("stale-restore")


def test_invalid_restore_cannot_clear_a_newer_login(monkeypatch) -> None:
    class ImmediateStore:
        @staticmethod
        def read() -> str:
            return "persisted-refresh"

        @staticmethod
        def write(_value: str) -> None:
            return None

    vault = SessionVault()
    monkeypatch.setattr(vault, "_credential_store", lambda: ImmediateStore)
    persisted = vault.persisted_refresh_token_snapshot()
    assert persisted is not None

    local = vault.start(
        user=_user("newer-login"),
        access_token="newer-access",
        refresh_token="newer-refresh",
        expires_in=900,
    )

    assert not vault.clear_if_credential_generation(persisted.generation)
    assert vault.get_local(local.token) == local
    assert vault.remote_for_user("newer-login").refresh_token == "newer-refresh"


def test_control_plane_accepts_iso_expiry_payload() -> None:
    payload = {
        "access_token": "access",
        "refresh_token": "refresh",
        "access_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "user": {
            "id": "user-1",
            "username": "employee",
            "role": "user",
            "workspace_id": "workspace-1",
        },
    }

    parsed = ControlPlaneClient._login_payload(payload)
    assert parsed.user.id == "user-1"
    assert 500 <= parsed.expires_in <= 600


def test_control_plane_does_not_relay_remote_error_detail(monkeypatch) -> None:
    leaked_detail = "upstream rejected bearer secret-that-must-not-reach-the-ui"

    class RejectedResponse:
        status_code = 400

        @staticmethod
        def json():
            return {"detail": leaked_detail}

    monkeypatch.setattr(
        "src.apps.hybrid.client.requests.request",
        lambda *_args, **_kwargs: RejectedResponse(),
    )
    client = ControlPlaneClient(
        HybridSettings(
            enabled=True,
            control_plane_url="http://127.0.0.1:18787",
        )
    )

    with pytest.raises(ControlPlaneError) as exc_info:
        client._request("POST", "/api/v1/auth/login")

    assert exc_info.value.detail == "请求内容无效，请检查后重试。"
    assert leaked_detail not in exc_info.value.detail


def test_control_plane_retries_transient_proxy_and_tls_failures(monkeypatch) -> None:
    attempts: list[tuple[float, float]] = []
    sleeps: list[float] = []

    class HealthyResponse:
        status_code = 200

    def request(*_args, **kwargs):
        attempts.append(kwargs["timeout"])
        if len(attempts) == 1:
            raise requests.exceptions.ProxyError("proxy tunnel unavailable")
        if len(attempts) == 2:
            raise requests.exceptions.SSLError("TLS handshake interrupted")
        return HealthyResponse()

    monkeypatch.setattr("src.apps.hybrid.client.requests.request", request)
    monkeypatch.setattr("src.apps.hybrid.client.time.sleep", sleeps.append)
    client = ControlPlaneClient(
        HybridSettings(
            enabled=True,
            control_plane_url="https://accounts.example.com",
        )
    )

    response = client._request("GET", "/health/live")

    assert response.status_code == 200
    assert attempts == [(8.0, 30.0), (8.0, 30.0), (8.0, 30.0)]
    assert sleeps == [0.25, 0.5]


def test_provider_gateway_matches_control_plane_contract(monkeypatch) -> None:
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")
    monkeypatch.setenv("ENMOTION_CONTROL_PLANE_URL", "http://127.0.0.1:18787")

    assert provider_gateway_base_url() == "http://127.0.0.1:18787/api/v1/gateway"


def test_hybrid_router_exposes_all_admin_account_actions() -> None:
    routes = {
        (method, route.path)
        for route in hybrid_router.routes
        for method in (route.methods or set())
    }

    assert ("GET", "/admin/users") in routes
    assert ("POST", "/admin/users/{user_id}/credits") in routes
    assert ("PATCH", "/admin/users/{user_id}/status") in routes
    assert ("POST", "/admin/users/{user_id}/password") in routes
    assert ("POST", "/admin/users/{user_id}/sessions/revoke") in routes
