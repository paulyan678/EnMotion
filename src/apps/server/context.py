"""Authenticated request identity exposed to tenant-aware application code."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: str
    username: str
    role: str
    workspace_id: str
    membership_role: str
    session_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True, slots=True)
class RequestContext:
    actor: Actor
    workspace_id: str


def get_current_actor(request: Request) -> Actor:
    """FastAPI dependency returning the actor authenticated by server middleware."""

    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, Actor):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录。",
        )
    return actor


def get_request_context(actor: Actor = Depends(get_current_actor)) -> RequestContext:
    return RequestContext(actor=actor, workspace_id=actor.workspace_id)


def require_admin(actor: Actor = Depends(get_current_actor)) -> Actor:
    if not actor.is_admin:
        # Deliberately avoid confirming that an admin capability exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="请求的内容不存在。",
        )
    return actor
