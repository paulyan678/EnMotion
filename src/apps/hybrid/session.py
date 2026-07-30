"""In-memory desktop sessions with refresh credentials in the OS keychain."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "com.enmotion.desktop"
_KEYRING_ACCOUNT = "control-plane-refresh-token"
_KEYRING_READ_TIMEOUT_SECONDS = 2.0
_NO_PENDING_KEYRING_WRITE = object()


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


@dataclass(slots=True)
class _KeyringReadTask:
    completed: threading.Event
    started_at: float
    generation: int
    value: str | None = None
    error: BaseException | None = None
    timeout_reported: bool = False


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
        keyring_timeout_seconds: float = _KEYRING_READ_TIMEOUT_SECONDS,
    ) -> None:
        if keyring_timeout_seconds <= 0:
            raise ValueError("credential-store timeout must be positive")
        self._lock = threading.RLock()
        self._local_sessions: dict[str, LocalSession] = {}
        self._remote: RemoteSession | None = None
        self._credential_generation = 0
        self._keyring_timeout_seconds = keyring_timeout_seconds
        self._keyring_read_lock = threading.Lock()
        self._keyring_read_task: _KeyringReadTask | None = None
        self._keyring_write_lock = threading.Lock()
        self._pending_keyring_write: tuple[Any, str | None, int] | object = (
            _NO_PENDING_KEYRING_WRITE
        )
        self._keyring_persisted_generation = 0
        self._keyring_writer_running = False

    @staticmethod
    def _keyring():
        try:
            import keyring
        except ImportError:
            return None
        return keyring

    def _start_keyring_read(
        self,
        keyring: Any,
        generation: int,
    ) -> _KeyringReadTask:
        """Return the single in-flight credential read, starting it if needed."""

        with self._keyring_read_lock:
            current = self._keyring_read_task
            if current is not None and not current.completed.is_set():
                return current

            task = _KeyringReadTask(
                completed=threading.Event(),
                started_at=time.monotonic(),
                generation=generation,
            )
            self._keyring_read_task = task

            def read() -> None:
                try:
                    # A macOS Security.framework read can remain blocked after
                    # our bounded wait returns (for example while Keychain
                    # authorization is unresolved). Keep reads single-flight,
                    # but never let that uninterruptible call own a lock needed
                    # by later credential mutations.
                    task.value = keyring.get_password(
                        _KEYRING_SERVICE,
                        _KEYRING_ACCOUNT,
                    )
                except BaseException as exc:
                    task.error = exc
                    logger.warning(
                        "Credential store read failed (%s)",
                        type(exc).__name__,
                    )
                finally:
                    task.completed.set()
                    with self._keyring_read_lock:
                        if self._keyring_read_task is task:
                            self._keyring_read_task = None

            threading.Thread(
                target=read,
                name="enmotion-keyring-read",
                daemon=True,
            ).start()
            return task

    def persisted_refresh_token_snapshot(self) -> PersistedRefreshToken | None:
        keyring = self._keyring()
        if keyring is None:
            return None
        with self._lock:
            generation = self._credential_generation
        with self._keyring_write_lock:
            # Do not read an older Keychain value while a newer login/logout
            # mutation is still queued or blocked in Security.framework.
            if self._keyring_persisted_generation != generation:
                return None
        task = self._start_keyring_read(keyring, generation)
        remaining = max(
            0.0,
            self._keyring_timeout_seconds - (time.monotonic() - task.started_at),
        )
        if not task.completed.wait(remaining):
            with self._keyring_read_lock:
                if not task.timeout_reported:
                    task.timeout_reported = True
                    logger.warning(
                        "Credential store read timed out after %.1f seconds",
                        self._keyring_timeout_seconds,
                    )
            return None
        if task.error is not None:
            return None
        value = task.value
        normalized = value.strip() if value and value.strip() else None
        if normalized is None:
            return None
        with self._lock:
            if task.generation != self._credential_generation:
                logger.info("Discarded a stale credential-store read")
                return None
        return PersistedRefreshToken(
            value=normalized,
            generation=task.generation,
        )

    def persisted_refresh_token(self) -> str | None:
        snapshot = self.persisted_refresh_token_snapshot()
        return snapshot.value if snapshot is not None else None

    def _drain_keyring_writes(self) -> None:
        """Persist only the latest desired credential state off the request path."""

        while True:
            with self._keyring_write_lock:
                pending = self._pending_keyring_write
                if pending is _NO_PENDING_KEYRING_WRITE:
                    self._keyring_writer_running = False
                    return
                self._pending_keyring_write = _NO_PENDING_KEYRING_WRITE

            assert isinstance(pending, tuple)
            keyring, value, generation = pending
            try:
                if value:
                    keyring.set_password(
                        _KEYRING_SERVICE,
                        _KEYRING_ACCOUNT,
                        value,
                    )
                else:
                    keyring.delete_password(
                        _KEYRING_SERVICE,
                        _KEYRING_ACCOUNT,
                    )
                with self._keyring_write_lock:
                    self._keyring_persisted_generation = max(
                        self._keyring_persisted_generation,
                        generation,
                    )
            except BaseException as exc:
                logger.warning(
                    "Credential store update failed (%s)",
                    type(exc).__name__,
                )

    def _persist_refresh_token(self, value: str | None, *, generation: int) -> None:
        keyring = self._keyring()
        if keyring is None:
            if value:
                logger.warning(
                    "No OS credential-store backend is available; login will not persist"
                )
            return

        with self._keyring_write_lock:
            pending = self._pending_keyring_write
            pending_generation = pending[2] if isinstance(pending, tuple) else -1
            if generation < max(
                self._keyring_persisted_generation,
                pending_generation,
            ):
                return
            self._pending_keyring_write = (keyring, value, generation)
            if self._keyring_writer_running:
                return
            self._keyring_writer_running = True
        threading.Thread(
            target=self._drain_keyring_writes,
            name="enmotion-keyring-writer",
            daemon=True,
        ).start()

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
            credential_generation = self._credential_generation
        self._persist_refresh_token(
            refresh_token,
            generation=credential_generation,
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
                self._credential_generation += 1
                credential_generation = self._credential_generation
                self._local_sessions.clear()
                self._remote = None
                self._persist_refresh_token(
                    None,
                    generation=credential_generation,
                )
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
            self._credential_generation += 1
            credential_generation = self._credential_generation
            self._persist_refresh_token(
                updated.refresh_token,
                generation=credential_generation,
            )
            return updated

    def revoke_local(self, token: str | None) -> None:
        with self._lock:
            self._credential_generation += 1
            credential_generation = self._credential_generation
            if token:
                self._local_sessions.pop(token, None)
            if not self._local_sessions:
                self._remote = None
        self._persist_refresh_token(None, generation=credential_generation)

    def clear(self) -> None:
        with self._lock:
            self._credential_generation += 1
            credential_generation = self._credential_generation
            self._local_sessions.clear()
            self._remote = None
        self._persist_refresh_token(None, generation=credential_generation)

    def clear_if_credential_generation(self, expected_generation: int) -> bool:
        """Clear an invalid restored token without erasing a newer login."""

        with self._lock:
            if expected_generation != self._credential_generation:
                return False
            self._credential_generation += 1
            credential_generation = self._credential_generation
            self._local_sessions.clear()
            self._remote = None
        self._persist_refresh_token(None, generation=credential_generation)
        return True


session_vault = SessionVault()
