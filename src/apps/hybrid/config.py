"""Validated configuration for EnMotion's managed desktop mode."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


class HybridConfigurationError(RuntimeError):
    """Raised when managed desktop mode is configured unsafely."""


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HybridConfigurationError(f"布尔配置值无效：{value!r}")


def hybrid_mode_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether this process is a centrally managed EnMotion desktop."""

    env = os.environ if environ is None else environ
    explicit = env.get("ENMOTION_HYBRID_MODE")
    if explicit is not None:
        return _as_bool(explicit)
    return env.get("ENMOTION_DEPLOYMENT_MODE", "desktop").strip().lower() in {
        "hybrid",
        "managed-desktop",
    }


def workspace_isolation_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether API state must be resolved through an account workspace."""

    from ..server.config import server_mode_enabled

    env = os.environ if environ is None else environ
    return server_mode_enabled(env) or hybrid_mode_enabled(env)


def _validated_origin(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise HybridConfigurationError("ENMOTION_CONTROL_PLANE_URL 必须是完整的 HTTP(S) 服务地址")
    hostname = parsed.hostname.lower()
    if parsed.path not in {"", "/"}:
        raise HybridConfigurationError("ENMOTION_CONTROL_PLANE_URL 不能包含路径")
    if parsed.scheme != "https" and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HybridConfigurationError(
            "除本机回环地址外，ENMOTION_CONTROL_PLANE_URL 必须使用 HTTPS"
        )
    return raw


@dataclass(frozen=True, slots=True)
class HybridSettings:
    enabled: bool
    control_plane_url: str
    request_timeout_seconds: float = 30.0
    session_cookie_name: str = "enmotion_session"
    csrf_cookie_name: str = "enmotion_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    local_nonce: str = ""

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        require_enabled: bool = True,
    ) -> "HybridSettings":
        env = os.environ if environ is None else environ
        enabled = hybrid_mode_enabled(env)
        if require_enabled and not enabled:
            raise HybridConfigurationError("EnMotion 混合模式尚未启用")
        raw_url = env.get("ENMOTION_CONTROL_PLANE_URL", "").strip()
        if enabled and not raw_url:
            raise HybridConfigurationError("混合模式必须配置 ENMOTION_CONTROL_PLANE_URL")
        timeout_raw = env.get("ENMOTION_CONTROL_PLANE_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise HybridConfigurationError(
                "ENMOTION_CONTROL_PLANE_TIMEOUT_SECONDS 必须是数字"
            ) from exc
        if timeout <= 0 or timeout > 300:
            raise HybridConfigurationError(
                "ENMOTION_CONTROL_PLANE_TIMEOUT_SECONDS 必须大于 0 且不超过 300"
            )
        return cls(
            enabled=enabled,
            control_plane_url=_validated_origin(raw_url) if raw_url else "",
            request_timeout_seconds=timeout,
            local_nonce=env.get("ENMOTION_SIDECAR_NONCE", "").strip(),
        )
