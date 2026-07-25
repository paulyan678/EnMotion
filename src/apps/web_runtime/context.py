"""Authenticated tenant context shared by API handlers and runtime proxies."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RequestTenant:
    """Identity and workspace selected by the authenticated server session."""

    user_id: str
    workspace_id: str
    role: str = "user"


_CURRENT_TENANT: ContextVar[Optional[RequestTenant]] = ContextVar(
    "enmotion_current_tenant", default=None
)


def bind_tenant(
    user_id: str, workspace_id: str, role: str = "user"
) -> Token[Optional[RequestTenant]]:
    """Bind a tenant to the current request/task context and return its token."""

    if not user_id or not workspace_id:
        raise ValueError("user_id and workspace_id are required")
    return _CURRENT_TENANT.set(
        RequestTenant(user_id=str(user_id), workspace_id=str(workspace_id), role=role)
    )


def reset_tenant(token: Token[Optional[RequestTenant]]) -> None:
    """Restore the tenant context that existed before :func:`bind_tenant`."""

    _CURRENT_TENANT.reset(token)


def get_tenant(*, required: bool = True) -> Optional[RequestTenant]:
    """Return the bound tenant or fail closed when one is required."""

    tenant = _CURRENT_TENANT.get()
    if required and tenant is None:
        raise RuntimeError("No authenticated workspace is bound to this request")
    return tenant
