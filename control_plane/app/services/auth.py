from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import LoginSession, User, utcnow
from ..security import new_token, token_digest


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    csrf_token: str
    session: LoginSession


@dataclass(frozen=True)
class RefreshResult:
    tokens: IssuedTokens | None
    user: User | None
    reuse_detected: bool = False


def issue_session(
    session: Session,
    *,
    user: User,
    settings: Settings,
    device_label: str | None,
) -> IssuedTokens:
    now = utcnow()
    access_token = new_token()
    refresh_token = new_token()
    csrf_token = new_token()
    record = LoginSession(
        user_id=user.id,
        access_digest=token_digest(settings.session_hmac_secret, access_token),
        refresh_digest=token_digest(settings.session_hmac_secret, refresh_token),
        csrf_digest=token_digest(settings.session_hmac_secret, csrf_token),
        device_label=(device_label or "").strip() or None,
        access_expires_at=now + timedelta(seconds=settings.access_ttl_seconds),
        refresh_expires_at=now + timedelta(seconds=settings.refresh_ttl_seconds),
    )
    session.add(record)
    session.flush()
    return IssuedTokens(access_token, refresh_token, csrf_token, record)


def rotate_refresh_token(
    session: Session,
    *,
    refresh_token: str,
    settings: Settings,
) -> RefreshResult:
    digest = token_digest(settings.session_hmac_secret, refresh_token)
    current = session.scalar(
        select(LoginSession).where(LoginSession.refresh_digest == digest)
    )
    if current is None:
        return RefreshResult(None, None)
    user = session.get(User, current.user_id)
    now = utcnow()
    if current.revoked_at is not None:
        if current.revoked_reason == "rotated":
            session.execute(
                update(LoginSession)
                .where(LoginSession.user_id == current.user_id)
                .where(LoginSession.revoked_at.is_(None))
                .values(revoked_at=now, revoked_reason="refresh_token_reuse")
            )
            return RefreshResult(None, user, reuse_detected=True)
        return RefreshResult(None, user)
    if _aware(current.refresh_expires_at) <= now or user is None or not user.active:
        current.revoked_at = now
        current.revoked_reason = "expired_or_inactive"
        return RefreshResult(None, user)
    current.revoked_at = now
    current.revoked_reason = "rotated"
    tokens = issue_session(
        session,
        user=user,
        settings=settings,
        device_label=current.device_label,
    )
    current.rotated_to_id = tokens.session.id
    return RefreshResult(tokens, user)


def revoke_all_sessions(
    session: Session,
    *,
    user_id: str,
    reason: str,
    except_session_id: str | None = None,
) -> int:
    statement = (
        update(LoginSession)
        .where(LoginSession.user_id == user_id)
        .where(LoginSession.revoked_at.is_(None))
    )
    if except_session_id:
        statement = statement.where(LoginSession.id != except_session_id)
    result = session.execute(statement.values(revoked_at=utcnow(), revoked_reason=reason))
    return int(result.rowcount or 0)
