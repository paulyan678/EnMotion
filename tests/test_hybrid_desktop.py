from datetime import datetime, timedelta, timezone

import pytest

from src.apps.hybrid.client import ControlPlaneClient, ControlPlaneError, RemoteLogin
from src.apps.hybrid.config import (
    HybridConfigurationError,
    HybridSettings,
    hybrid_mode_enabled,
    workspace_isolation_enabled,
)
from src.apps.hybrid.provider import provider_gateway_base_url
from src.apps.hybrid.router import router as hybrid_router
from src.apps.hybrid.session import HybridUser, SessionVault


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


def test_account_switch_invalidates_previous_local_session(monkeypatch) -> None:
    vault = SessionVault()
    monkeypatch.setattr(vault, "_keyring", lambda: None)
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


def test_refresh_cannot_change_account_identity(monkeypatch) -> None:
    vault = SessionVault()
    monkeypatch.setattr(vault, "_keyring", lambda: None)
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
