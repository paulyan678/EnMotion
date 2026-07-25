"""Transactional account and session operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import ServerSettings
from .models import LoginSession, User, Workspace, WorkspaceMembership, utc_now
from .security import (
    CredentialValidationError,
    digest_token,
    generate_opaque_token,
    hash_password,
    normalize_username,
    password_needs_rehash,
    verify_password,
)

VALID_USER_ROLES = {"user", "admin"}


class DuplicateUsernameError(ValueError):
    pass


class BootstrapAlreadyCompletedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedSession:
    record: LoginSession
    token: str
    csrf_token: str


def create_user_with_personal_workspace(
    db: Session,
    *,
    username: str,
    password: str,
    role: str = "user",
    workspace_name: str | None = None,
    storage_quota_bytes: int = 20 * 1024 * 1024 * 1024,
) -> tuple[User, Workspace]:
    normalized = normalize_username(username)
    display_username = username.strip()
    if role not in VALID_USER_ROLES:
        raise CredentialValidationError("Role must be 'user' or 'admin'")
    if storage_quota_bytes <= 0:
        raise CredentialValidationError("Storage quota must be greater than zero")
    resolved_workspace_name = (workspace_name or f"{display_username}'s workspace").strip()
    if not resolved_workspace_name:
        raise CredentialValidationError("Workspace name cannot be empty")

    if db.scalar(select(User.id).where(User.username_normalized == normalized)):
        raise DuplicateUsernameError("Username already exists")

    user = User(
        username=display_username,
        username_normalized=normalized,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()

    workspace = Workspace(
        name=resolved_workspace_name[:128],
        owner_user_id=user.id,
        storage_quota_bytes=storage_quota_bytes,
    )
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            user_id=user.id,
            workspace_id=workspace.id,
            role="owner",
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        raise DuplicateUsernameError("Username already exists") from exc
    return user, workspace


def bootstrap_first_admin(
    db: Session,
    *,
    username: str,
    password: str,
    workspace_name: str | None = None,
) -> tuple[User, Workspace]:
    """Create the initial admin, refusing to run once any account exists."""

    if db.scalar(select(func.count()).select_from(User)):
        raise BootstrapAlreadyCompletedError(
            "Bootstrap is disabled after the first account has been created"
        )
    user, workspace = create_user_with_personal_workspace(
        db,
        username=username,
        password=password,
        role="admin",
        workspace_name=workspace_name,
    )
    db.commit()
    return user, workspace


def authenticate_user(db: Session, *, username: str, password: str) -> User | None:
    try:
        normalized = normalize_username(username)
    except CredentialValidationError:
        normalized = ""

    user = db.scalar(select(User).where(User.username_normalized == normalized))
    if user is None:
        # Run the same expensive primitive for unknown users to reduce username
        # enumeration through response timing.
        verify_password(_dummy_password_hash(), password)
        return None
    password_matches = verify_password(user.password_hash, password)
    if not user.is_active or not password_matches:
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.flush()
    return user


_DUMMY_HASH: str | None = None


def _dummy_password_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-valid-user-password")
    return _DUMMY_HASH


def personal_workspace_for_user(db: Session, user_id: str) -> Workspace | None:
    return db.scalar(
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user_id)
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )


def issue_session(
    db: Session,
    *,
    user: User,
    workspace: Workspace,
    settings: ServerSettings,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedSession:
    token = generate_opaque_token()
    csrf_token = generate_opaque_token()
    now = utc_now()

    # Serialize successful logins for this account before pruning/inserting.
    # PostgreSQL's row lock makes the active-session cap reliable even when two
    # devices log in concurrently (SQLite safely ignores FOR UPDATE in tests).
    db.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()
    db.execute(
        delete(LoginSession)
        .where(LoginSession.user_id == user.id)
        .where(
            or_(
                LoginSession.revoked_at.is_not(None),
                LoginSession.expires_at <= now,
            )
        )
    )

    # Reserve one slot for the session created below. Removing surplus active
    # rows both invalidates the oldest devices and prevents the table from
    # growing without bound. The stable ID tie-breaker handles equal timestamps.
    active_conditions = (
        LoginSession.user_id == user.id,
        LoginSession.revoked_at.is_(None),
        LoginSession.expires_at > now,
    )
    keep_ids = (
        select(LoginSession.id)
        .where(*active_conditions)
        .order_by(LoginSession.created_at.desc(), LoginSession.id.desc())
        .limit(settings.max_active_sessions_per_user - 1)
    )
    db.execute(
        delete(LoginSession)
        .where(*active_conditions)
        .where(LoginSession.id.not_in(keep_ids))
    )

    record = LoginSession(
        user_id=user.id,
        workspace_id=workspace.id,
        token_hash=digest_token(token, settings.session_secret),
        csrf_hash=digest_token(csrf_token, settings.session_secret),
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        last_seen_at=now,
        ip_address=(ip_address or "")[:64] or None,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(record)
    db.commit()
    return IssuedSession(record=record, token=token, csrf_token=csrf_token)


def revoke_session(db: Session, session_id: str) -> None:
    record = db.get(LoginSession, session_id)
    if record and record.revoked_at is None:
        record.revoked_at = utc_now()
        db.commit()


def change_password(
    db: Session,
    *,
    user: User,
    current_session_id: str,
    current_password: str,
    new_password: str,
) -> bool:
    if not verify_password(user.password_hash, current_password):
        return False
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utc_now()
    # Revoke every other browser/device after a password change.
    db.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user.id)
        .where(LoginSession.id != current_session_id)
        .where(LoginSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    db.commit()
    return True


def datetime_is_expired(value: datetime, *, now: datetime | None = None) -> bool:
    """Compare timestamps consistently even when SQLite drops timezone metadata."""

    reference = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return value <= reference
