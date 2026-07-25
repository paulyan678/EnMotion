from __future__ import annotations

import pytest
from fastapi import FastAPI

from src.apps.server import include_server_mode
from src.apps.server.config import (
    ServerConfigurationError,
    ServerSettings,
    server_mode_enabled,
)


def test_server_mode_is_opt_in_and_desktop_integration_is_noop(monkeypatch):
    monkeypatch.delenv("ENMOTION_SERVER_MODE", raising=False)
    monkeypatch.delenv("ENMOTION_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENMOTION_SESSION_SECRET", raising=False)

    app = FastAPI()
    assert server_mode_enabled() is False
    assert include_server_mode(app) is False
    assert not any(route.path.startswith("/auth") for route in app.routes)


@pytest.mark.parametrize(
    "environment",
    [
        {"ENMOTION_SERVER_MODE": "true"},
        {"ENMOTION_DEPLOYMENT_MODE": "server"},
        {"ENMOTION_DEPLOYMENT_MODE": "production"},
    ],
)
def test_server_mode_aliases(environment):
    assert server_mode_enabled(environment) is True


def test_enabled_server_requires_database_and_strong_session_secret():
    with pytest.raises(ServerConfigurationError, match="DATABASE_URL"):
        ServerSettings.from_env({"ENMOTION_SERVER_MODE": "true"})

    with pytest.raises(ServerConfigurationError, match="at least 32"):
        ServerSettings.from_env(
            {
                "ENMOTION_SERVER_MODE": "true",
                "DATABASE_URL": "sqlite://",
                "ENMOTION_SESSION_SECRET": "short",
            }
        )


def test_settings_parse_cookie_and_origin_policy():
    settings = ServerSettings.from_env(
        {
            "ENMOTION_DEPLOYMENT_MODE": "server",
            "DATABASE_URL": "postgresql+psycopg://db/enmotion",
            "ENMOTION_SESSION_SECRET": "x" * 32,
            "ENMOTION_COOKIE_SECURE": "true",
            "ENMOTION_COOKIE_SAMESITE": "none",
            "ENMOTION_ALLOWED_ORIGINS": "https://example.test/, https://admin.example.test",
            "ENMOTION_PUBLIC_BASE_URL": "https://example.test/",
            "ENMOTION_MAX_REQUEST_BODY_BYTES": "12345",
            "ENMOTION_LOGIN_ACCOUNT_ATTEMPTS": "17",
            "ENMOTION_MAX_ACTIVE_SESSIONS_PER_USER": "9",
        }
    )
    assert settings.cookie_secure is True
    assert settings.cookie_samesite == "none"
    assert settings.allowed_origins == (
        "https://example.test",
        "https://admin.example.test",
    )
    assert settings.public_base_url == "https://example.test"
    assert settings.max_request_body_bytes == 12345
    assert settings.login_account_attempts == 17
    assert settings.max_active_sessions_per_user == 9


def test_request_body_limit_must_be_positive_integer():
    base = {
        "ENMOTION_SERVER_MODE": "true",
        "DATABASE_URL": "sqlite://",
        "ENMOTION_SESSION_SECRET": "x" * 32,
    }
    for value in ("0", "-1", "not-a-number"):
        with pytest.raises(ServerConfigurationError, match="ENMOTION_MAX_REQUEST_BODY_BYTES"):
            ServerSettings.from_env({**base, "ENMOTION_MAX_REQUEST_BODY_BYTES": value})


def test_account_login_limit_must_be_positive_integer():
    base = {
        "ENMOTION_SERVER_MODE": "true",
        "DATABASE_URL": "sqlite://",
        "ENMOTION_SESSION_SECRET": "x" * 32,
    }
    for value in ("0", "-1", "not-a-number"):
        with pytest.raises(ServerConfigurationError, match="ENMOTION_LOGIN_ACCOUNT_ATTEMPTS"):
            ServerSettings.from_env({**base, "ENMOTION_LOGIN_ACCOUNT_ATTEMPTS": value})


def test_active_session_limit_must_be_positive_integer():
    base = {
        "ENMOTION_SERVER_MODE": "true",
        "DATABASE_URL": "sqlite://",
        "ENMOTION_SESSION_SECRET": "x" * 32,
    }
    for value in ("0", "-1", "not-a-number"):
        with pytest.raises(
            ServerConfigurationError,
            match="ENMOTION_MAX_ACTIVE_SESSIONS_PER_USER",
        ):
            ServerSettings.from_env(
                {**base, "ENMOTION_MAX_ACTIVE_SESSIONS_PER_USER": value}
            )


def test_same_site_none_requires_secure_cookie():
    with pytest.raises(ServerConfigurationError, match="must also be Secure"):
        ServerSettings.from_env(
            {
                "ENMOTION_SERVER_MODE": "true",
                "DATABASE_URL": "sqlite://",
                "ENMOTION_SESSION_SECRET": "x" * 32,
                "ENMOTION_COOKIE_SAMESITE": "none",
            }
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENMOTION_CSRF_COOKIE_NAME", "custom_csrf"),
        ("ENMOTION_CSRF_HEADER_NAME", "X-Custom-CSRF"),
    ],
)
def test_csrf_protocol_names_cannot_be_overridden(name, value):
    with pytest.raises(ServerConfigurationError, match="fixed to"):
        ServerSettings.from_env(
            {
                "ENMOTION_SERVER_MODE": "true",
                "DATABASE_URL": "sqlite://",
                "ENMOTION_SESSION_SECRET": "x" * 32,
                name: value,
            }
        )


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test/path",
        "https://user@example.test",
        "javascript://example.test",
        "https://example.test?next=evil",
    ],
)
def test_allowed_origins_must_be_bare_http_origins(origin):
    with pytest.raises(ServerConfigurationError, match="invalid origin"):
        ServerSettings.from_env(
            {
                "ENMOTION_SERVER_MODE": "true",
                "DATABASE_URL": "sqlite://",
                "ENMOTION_SESSION_SECRET": "x" * 32,
                "ENMOTION_ALLOWED_ORIGINS": origin,
            }
        )


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://example.test",
        "https://user@example.test",
        "https://example.test/path",
        "https://example.test?next=evil",
    ],
)
def test_public_base_url_must_be_a_safe_origin(public_base_url):
    with pytest.raises(ServerConfigurationError, match="ENMOTION_PUBLIC_BASE_URL"):
        ServerSettings.from_env(
            {
                "ENMOTION_SERVER_MODE": "true",
                "DATABASE_URL": "sqlite://",
                "ENMOTION_SESSION_SECRET": "x" * 32,
                "ENMOTION_ALLOWED_ORIGINS": "https://example.test",
                "ENMOTION_PUBLIC_BASE_URL": public_base_url,
            }
        )


def test_public_base_url_must_be_an_allowed_origin():
    with pytest.raises(ServerConfigurationError, match="must also appear"):
        ServerSettings.from_env(
            {
                "ENMOTION_SERVER_MODE": "true",
                "DATABASE_URL": "sqlite://",
                "ENMOTION_SESSION_SECRET": "x" * 32,
                "ENMOTION_ALLOWED_ORIGINS": "https://example.test",
                "ENMOTION_PUBLIC_BASE_URL": "https://other.example.test",
            }
        )
