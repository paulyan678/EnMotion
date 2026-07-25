"""In-memory desktop sessions with refresh credentials in the OS keychain."""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "com.enmotion.desktop"
_KEYRING_ACCOUNT = "control-plane-refresh-token"


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


class SessionVault:
    """Own one active employee identity and fail closed across account switches."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local_sessions: dict[str, LocalSession] = {}
        self._remote: RemoteSession | None = None

    @staticmethod
    def _keyring():
        try:
            import keyring
        except ImportError:
            return None
        return keyring

    def persisted_refresh_token(self) -> str | None:
        keyring = self._keyring()
        if keyring is None:
            return None
        try:
            value = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
        except Exception as exc:
            logger.warning("Credential store read failed (%s)", type(exc).__name__)
            return None
        return value.strip() if value and value.strip() else None

    def _persist_refresh_token(self, value: str | None) -> None:
        keyring = self._keyring()
        if keyring is None:
            if value:
                logger.warning(
                    "No OS credential-store backend is available; login will not persist"
                )
            return
        try:
            if value:
                keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, value)
            else:
                try:
                    keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Credential store update failed (%s)", type(exc).__name__)

    def start(
        self,
        *,
        user: HybridUser,
        access_token: str,
        refresh_token: str,
        expires_in: int,
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
            # A desktop owns one active account. Clearing the old identity makes
            # account switching fail closed instead of billing a new user for an
            # old background task.
            self._local_sessions.clear()
            self._local_sessions[local.token] = local
            self._remote = RemoteSession(
                user=user,
                access_token=access_token,
                refresh_token=refresh_token,
                access_expires_at=expires_at,
            )
        self._persist_refresh_token(refresh_token)
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
            self._remote = updated
            self._persist_refresh_token(updated.refresh_token)
            return updated

    def revoke_local(self, token: str | None) -> None:
        with self._lock:
            if token:
                self._local_sessions.pop(token, None)
            if not self._local_sessions:
                self._remote = None
        self._persist_refresh_token(None)

    def clear(self) -> None:
        with self._lock:
            self._local_sessions.clear()
            self._remote = None
        self._persist_refresh_token(None)


session_vault = SessionVault()
