"""In-memory desktop sessions with an owner-only persisted refresh token."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .session_store import LocalCredentialStore


@dataclass(frozen=True, slots=True)
class HybridUser:
    id: str
    username: str
    role: str
    workspace_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HybridUser":
        nested = payload.get("user")
        source = nested if isinstance(nested, dict) else payload
        user_id = str(source.get("id") or "").strip()
        username = str(source.get("username") or "").strip()
        role = str(source.get("role") or "user").strip()
        workspace_id = str(source.get("workspace_id") or user_id).strip()
        if not user_id or not username or not workspace_id:
            raise ValueError("账号服务返回的用户资料不完整")
        if role not in {"user", "admin"}:
            raise ValueError("账号服务返回的账号角色无效")
        return cls(
            id=user_id,
            username=username,
            role=role,
            workspace_id=workspace_id,
        )


@dataclass(slots=True)
class RemoteSession:
    user: HybridUser
    access_token: str
    refresh_token: str
    access_expires_at: datetime


@dataclass(frozen=True, slots=True)
class LocalSession:
    token: str
    csrf_token: str
    user: HybridUser


@dataclass(frozen=True, slots=True)
class PersistedRefreshToken:
    value: str
    generation: int


class StalePersistedCredentialError(RuntimeError):
    """Raised when a restore races with login, logout, or credential rotation."""


class SessionVault:
    """Own one active employee identity and fail closed across account switches."""

    def __init__(
        self,
        *,
        credential_path: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._local_sessions: dict[str, LocalSession] = {}
        self._remote: RemoteSession | None = None
        self._credential_generation = 0
        self._credential_path = credential_path

    def _credential_store(self) -> LocalCredentialStore:
        return LocalCredentialStore(self._credential_path)

    def persisted_refresh_token_snapshot(self) -> PersistedRefreshToken | None:
        with self._lock:
            generation = self._credential_generation
            value = self._credential_store().read()
            normalized = value.strip() if value and value.strip() else None
            if normalized is None:
                return None
            return PersistedRefreshToken(
                value=normalized,
                generation=generation,
            )

    def persisted_refresh_token(self) -> str | None:
        snapshot = self.persisted_refresh_token_snapshot()
        return snapshot.value if snapshot is not None else None

    def start(
        self,
        *,
        user: HybridUser,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        expected_credential_generation: int | None = None,
    ) -> LocalSession:
        if not access_token or not refresh_token:
            raise ValueError("账号服务令牌不能为空")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 30))
        local = LocalSession(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            user=user,
        )
        with self._lock:
            if (
                expected_credential_generation is not None
                and expected_credential_generation != self._credential_generation
            ):
                raise StalePersistedCredentialError(
                    "credential state changed while restoring the desktop session"
                )
            self._credential_store().write(refresh_token)
            # A desktop owns one active account. Clearing the old identity makes
            # account switching fail closed instead of billing a new user for an
            # old background task.
            self._credential_generation += 1
            self._local_sessions.clear()
            self._local_sessions[local.token] = local
            self._remote = RemoteSession(
                user=user,
                access_token=access_token,
                refresh_token=refresh_token,
                access_expires_at=expires_at,
            )
        return local

    def get_local(self, token: str | None) -> LocalSession | None:
        if not token:
            return None
        with self._lock:
            return self._local_sessions.get(token)

    def remote_for_user(self, user_id: str) -> RemoteSession:
        with self._lock:
            remote = self._remote
            if remote is None or remote.user.id != user_id:
                raise RuntimeError("当前账号没有可用的账号服务会话")
            return remote

    def needs_refresh(self, user_id: str, *, leeway_seconds: int = 60) -> bool:
        remote = self.remote_for_user(user_id)
        return remote.access_expires_at <= datetime.now(timezone.utc) + timedelta(
            seconds=leeway_seconds
        )

    def ensure_fresh(
        self,
        user_id: str,
        refresh: Callable[[str], Any],
        *,
        leeway_seconds: int = 60,
    ) -> RemoteSession:
        """Rotate an expiring remote token once while preserving local cookies."""

        with self._lock:
            remote = self._remote
            if remote is None or remote.user.id != user_id:
                raise RuntimeError("当前账号没有可用的账号服务会话")
            if remote.access_expires_at > datetime.now(timezone.utc) + timedelta(
                seconds=leeway_seconds
            ):
                return remote
            replacement = refresh(remote.refresh_token)
            if replacement.user.id != user_id:
                self._credential_store().delete()
                self._credential_generation += 1
                self._local_sessions.clear()
                self._remote = None
                raise RuntimeError("账号服务刷新后返回了不同的账号身份")
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=max(int(replacement.expires_in), 30)
            )
            updated = RemoteSession(
                user=replacement.user,
                access_token=replacement.access_token,
                refresh_token=replacement.refresh_token,
                access_expires_at=expires_at,
            )
            self._credential_store().write(updated.refresh_token)
            self._remote = updated
            self._credential_generation += 1
            return updated

    def revoke_local(self, token: str | None) -> None:
        with self._lock:
            if token:
                self._local_sessions.pop(token, None)
            if not self._local_sessions:
                self._credential_store().delete()
                self._credential_generation += 1
                self._remote = None

    def clear(self) -> None:
        with self._lock:
            self._credential_store().delete()
            self._credential_generation += 1
            self._local_sessions.clear()
            self._remote = None

    def clear_if_credential_generation(self, expected_generation: int) -> bool:
        """Clear an invalid restored token without erasing a newer login."""

        with self._lock:
            if expected_generation != self._credential_generation:
                return False
            self._credential_store().delete()
            self._credential_generation += 1
            self._local_sessions.clear()
            self._remote = None
        return True


session_vault = SessionVault()
