"""Provider-gateway routing that never exposes company credentials locally."""

from __future__ import annotations

from ..web_runtime.context import get_tenant
from .config import HybridSettings, hybrid_mode_enabled
from .session import session_vault


def provider_gateway_base_url() -> str:
    settings = HybridSettings.from_env()
    return f"{settings.control_plane_url}/api/v1/gateway"


def provider_gateway_token() -> str:
    from .client import ControlPlaneClient

    tenant = get_tenant(required=True)
    assert tenant is not None
    remote = session_vault.ensure_fresh(
        tenant.user_id,
        ControlPlaneClient().refresh,
    )
    return remote.access_token


__all__ = [
    "hybrid_mode_enabled",
    "provider_gateway_base_url",
    "provider_gateway_token",
]
