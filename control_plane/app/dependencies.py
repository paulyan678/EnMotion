from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from .models import LoginSession, User, utcnow
from .security import secure_equal, token_digest


ACCESS_COOKIE = "enmotion_admin_session"
REFRESH_COOKIE = "enmotion_admin_refresh"
CSRF_COOKIE = "enmotion_admin_csrf"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class Principal:
    user_id: str
    session_id: str
    username: str
    role: str
    via_cookie: bool


def client_ip(request: Request) -> str | None:
    # Uvicorn accepts forwarding headers only from the explicitly trusted local
    # reverse proxy. Reading X-Forwarded-For here would make it client-spoofable
    # when the app is run directly.
    return request.client.host[:64] if request.client else None


def current_principal(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    bearer = ""
    via_cookie = False
    if authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if not bearer:
        bearer = request.cookies.get(ACCESS_COOKIE, "")
        via_cookie = bool(bearer)
    if not bearer:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")

    settings = request.app.state.settings
    digest = token_digest(settings.session_hmac_secret, bearer)
    with request.app.state.db.session() as session:
        row = session.execute(
            select(LoginSession, User)
            .join(User, User.id == LoginSession.user_id)
            .where(LoginSession.access_digest == digest)
        ).one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")
        login_session, user = row
        now = utcnow()
        if (
            login_session.revoked_at is not None
            or _aware(login_session.access_expires_at) <= now
            or not user.active
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired or revoked")
        if via_cookie and request.method.upper() in UNSAFE_METHODS:
            csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
            csrf_header = request.headers.get("x-csrf-token", "")
            if (
                not csrf_cookie
                or not csrf_header
                or not secure_equal(csrf_cookie, csrf_header)
                or not secure_equal(
                    token_digest(settings.session_hmac_secret, csrf_header),
                    login_session.csrf_digest,
                )
            ):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")
        if _aware(login_session.last_seen_at) < now - timedelta(minutes=5):
            login_session.last_seen_at = now
        return Principal(
            user_id=user.id,
            session_id=login_session.id,
            username=user.username,
            role=user.role,
            via_cookie=via_cookie,
        )


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def admin_principal(principal: CurrentPrincipal) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrator role required")
    return principal


AdminPrincipal = Annotated[Principal, Depends(admin_principal)]
