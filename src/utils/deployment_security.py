"""Deployment-facing security defaults shared by the FastAPI application.

Local desktop and development traffic is allowed from loopback. Remote browser
origins and remote diagnostic access require explicit environment opt-ins.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Mapping, Optional
from urllib.parse import urlsplit

LOOPBACK_CORS_ORIGIN_REGEX = r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$"
REMOTE_CORS_ORIGINS_ENV = "ENMOTION_CORS_ORIGINS"
REMOTE_DIAGNOSTICS_ENV = "ENMOTION_ENABLE_REMOTE_DIAGNOSTICS"

DIAGNOSTIC_PATH_PREFIXES = (
    "/debug/config",
    "/diagnose",
    "/config/info",
    "/docs",
    "/redoc",
    "/openapi.json",
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _environment(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def remote_diagnostics_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    value = _environment(env).get(REMOTE_DIAGNOSTICS_ENV, "")
    return str(value).strip().lower() in _TRUE_VALUES


def is_loopback_host(host: Optional[str]) -> bool:
    """Return whether a request peer is unambiguously local to this machine."""

    normalized = (host or "").strip().lower()
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    # IPv6 scope identifiers are irrelevant to loopback classification.
    normalized = normalized.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def diagnostic_access_allowed(
    client_host: Optional[str],
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    return is_loopback_host(client_host) or remote_diagnostics_enabled(env)


def is_diagnostic_path(path: str) -> bool:
    return any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in DIAGNOSTIC_PATH_PREFIXES
    )


def configured_cors_origins(env: Optional[Mapping[str, str]] = None) -> list[str]:
    """Parse exact remote origins; wildcard credentials are never accepted."""

    active_env = _environment(env)
    raw = active_env.get(REMOTE_CORS_ORIGINS_ENV) or active_env.get("ENMOTION_ALLOWED_ORIGINS", "")
    origins: list[str] = []
    for candidate in str(raw).split(","):
        origin = candidate.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            raise ValueError(
                f"{REMOTE_CORS_ORIGINS_ENV} cannot contain '*'; list exact trusted origins"
            )

        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid CORS origin {origin!r}: {exc}") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(f"Invalid CORS origin {origin!r}; expected only scheme://host[:port]")
        if origin not in origins:
            origins.append(origin)
    return origins


def cors_middleware_options(env: Optional[Mapping[str, str]] = None) -> dict[str, object]:
    """Build Starlette CORS options with loopback-only defaults."""

    return {
        "allow_origins": configured_cors_origins(env),
        "allow_origin_regex": LOOPBACK_CORS_ORIGIN_REGEX,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": [
            "Content-Disposition",
            "ETag",
            "Retry-After",
            "X-EnMotion-Asset-Revision",
            "X-EnMotion-Workspace-ID",
            "X-Request-ID",
        ],
    }
