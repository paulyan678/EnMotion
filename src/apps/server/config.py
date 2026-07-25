"""Configuration for EnMotion's opt-in multi-user server mode.

Nothing in this module mutates process state or opens a database connection.  The
desktop application therefore keeps its existing behaviour unless server mode is
explicitly enabled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Mapping, cast
from urllib.parse import urlparse

SERVER_MODE_VALUES = {"server", "web", "production"}
CSRF_COOKIE_NAME = "enmotion_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"


class ServerConfigurationError(RuntimeError):
    """Raised when server mode is enabled with an unsafe/incomplete config."""


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ServerConfigurationError(f"Invalid boolean value: {value!r}")


def server_mode_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the process should enable multi-user server behaviour."""

    env = os.environ if environ is None else environ
    explicit = env.get("ENMOTION_SERVER_MODE")
    if explicit is not None:
        return _as_bool(explicit)
    return env.get("ENMOTION_DEPLOYMENT_MODE", "desktop").strip().lower() in SERVER_MODE_VALUES


def _split_origins(value: str) -> tuple[str, ...]:
    origins: list[str] = []
    for origin in value.split(","):
        normalized = origin.strip().rstrip("/")
        if not normalized:
            continue
        parsed = urlparse(normalized)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ServerConfigurationError(
                f"ENMOTION_ALLOWED_ORIGINS contains an invalid origin: {origin!r}"
            ) from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ServerConfigurationError(
                f"ENMOTION_ALLOWED_ORIGINS contains an invalid origin: {origin!r}"
            )
        origins.append(normalized)
    return tuple(dict.fromkeys(origins))


def _public_base_url(value: str | None) -> str | None:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ServerConfigurationError("ENMOTION_PUBLIC_BASE_URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ServerConfigurationError(
            "ENMOTION_PUBLIC_BASE_URL must be a bare absolute HTTP(S) origin"
        )
    if parsed.scheme != "https" and parsed.hostname.lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ServerConfigurationError(
            "ENMOTION_PUBLIC_BASE_URL must use HTTPS unless it is a loopback URL"
        )
    return raw


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Validated server-mode settings.

    ``database_url`` can be SQLite in tests and local development.  The portable
    deployment supplies PostgreSQL via ``DATABASE_URL``.
    """

    enabled: bool
    database_url: str
    session_secret: str
    session_cookie_name: str = "enmotion_session"
    session_ttl_seconds: int = 7 * 24 * 60 * 60
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    public_base_url: str | None = None
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3008",
        "http://127.0.0.1:3008",
    )
    login_attempts: int = 5
    login_account_attempts: int = 25
    login_window_seconds: int = 5 * 60
    max_active_sessions_per_user: int = 20
    # The largest complete HTTP request accepted by the ASGI application.
    # This sits above the individual 5/10 MiB upload policies so multipart
    # framing has room, while still placing a hard ceiling on JSON and
    # chunked bodies that do not carry Content-Length.
    max_request_body_bytes: int = 16 * 1024 * 1024

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        require_enabled: bool = True,
    ) -> "ServerSettings":
        env = os.environ if environ is None else environ
        enabled = server_mode_enabled(env)
        if require_enabled and not enabled:
            raise ServerConfigurationError("EnMotion server mode is not enabled")

        database_url = env.get("DATABASE_URL", "").strip()
        session_secret = env.get("ENMOTION_SESSION_SECRET", "")
        if enabled and not database_url:
            raise ServerConfigurationError("DATABASE_URL is required in server mode")
        if enabled and len(session_secret) < 32:
            raise ServerConfigurationError(
                "ENMOTION_SESSION_SECRET must contain at least 32 characters"
            )

        ttl = _positive_int(env, "ENMOTION_SESSION_TTL_SECONDS", 7 * 24 * 60 * 60)
        attempts = _positive_int(env, "ENMOTION_LOGIN_ATTEMPTS", 5)
        account_attempts = _positive_int(env, "ENMOTION_LOGIN_ACCOUNT_ATTEMPTS", 25)
        window = _positive_int(env, "ENMOTION_LOGIN_WINDOW_SECONDS", 5 * 60)
        max_active_sessions_per_user = _positive_int(
            env, "ENMOTION_MAX_ACTIVE_SESSIONS_PER_USER", 20
        )
        max_request_body_bytes = _positive_int(
            env, "ENMOTION_MAX_REQUEST_BODY_BYTES", 16 * 1024 * 1024
        )
        same_site = env.get("ENMOTION_COOKIE_SAMESITE", "lax").strip().lower()
        if same_site not in {"lax", "strict", "none"}:
            raise ServerConfigurationError(
                "ENMOTION_COOKIE_SAMESITE must be one of lax, strict, or none"
            )
        secure = _as_bool(env.get("ENMOTION_COOKIE_SECURE"), default=False)
        if same_site == "none" and not secure:
            raise ServerConfigurationError("SameSite=None cookies must also be Secure")

        allowed = _split_origins(
            env.get(
                "ENMOTION_ALLOWED_ORIGINS",
                "http://localhost:3008,http://127.0.0.1:3008",
            )
        )
        if enabled and not allowed:
            raise ServerConfigurationError("At least one ENMOTION_ALLOWED_ORIGINS value is required")
        public_base_url = _public_base_url(env.get("ENMOTION_PUBLIC_BASE_URL"))
        if public_base_url and public_base_url not in allowed:
            raise ServerConfigurationError(
                "ENMOTION_PUBLIC_BASE_URL must also appear in ENMOTION_ALLOWED_ORIGINS"
            )

        session_cookie_name = env.get("ENMOTION_SESSION_COOKIE_NAME", "enmotion_session").strip()
        if not session_cookie_name:
            raise ServerConfigurationError("Session cookie name cannot be empty")
        _validate_fixed_name(env, "ENMOTION_CSRF_COOKIE_NAME", CSRF_COOKIE_NAME)
        _validate_fixed_name(env, "ENMOTION_CSRF_HEADER_NAME", CSRF_HEADER_NAME)

        return cls(
            enabled=enabled,
            database_url=database_url,
            session_secret=session_secret,
            session_cookie_name=session_cookie_name,
            session_ttl_seconds=ttl,
            cookie_secure=secure,
            cookie_samesite=cast(Literal["lax", "strict", "none"], same_site),
            public_base_url=public_base_url,
            allowed_origins=allowed,
            login_attempts=attempts,
            login_account_attempts=account_attempts,
            login_window_seconds=window,
            max_active_sessions_per_user=max_active_sessions_per_user,
            max_request_body_bytes=max_request_body_bytes,
        )


def _validate_fixed_name(env: Mapping[str, str], name: str, expected: str) -> None:
    configured = env.get(name)
    if configured is not None and configured.strip() != expected:
        raise ServerConfigurationError(
            f"{name} is fixed to {expected!r} by the browser/API protocol"
        )


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ServerConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ServerConfigurationError(f"{name} must be greater than zero")
    return value


@lru_cache(maxsize=1)
def get_server_settings() -> ServerSettings:
    return ServerSettings.from_env()


def clear_server_settings_cache() -> None:
    get_server_settings.cache_clear()
