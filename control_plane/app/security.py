from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Iterator

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
DEFAULT_PASSWORD_MIN_LENGTH = 12
ADMIN_RESET_PASSWORD_MIN_LENGTH = 6
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


def normalize_username(username: str) -> str:
    normalized = username.strip().casefold()
    if not _USERNAME.fullmatch(username.strip()):
        raise ValueError(
            "username must be 3-64 characters using letters, numbers, '.', '_' or '-'"
        )
    return normalized


def validate_password(
    password: str,
    *,
    minimum_length: int = DEFAULT_PASSWORD_MIN_LENGTH,
) -> None:
    if len(password) < minimum_length:
        raise ValueError(f"password must contain at least {minimum_length} characters")
    if len(password) > 256:
        raise ValueError("password must not exceed 256 characters")
    if password.isspace():
        raise ValueError("password must not be only whitespace")


def hash_password(
    password: str,
    *,
    minimum_length: int = DEFAULT_PASSWORD_MIN_LENGTH,
) -> str:
    validate_password(password, minimum_length=minimum_length)
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, candidate: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def new_token() -> str:
    return secrets.token_urlsafe(48)


def token_digest(secret: str, token: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class PasswordWorkUnavailable(RuntimeError):
    pass


@contextmanager
def password_work_slot(semaphore: threading.BoundedSemaphore) -> Iterator[None]:
    if not semaphore.acquire(blocking=False):
        raise PasswordWorkUnavailable("password verification capacity is busy")
    try:
        yield
    finally:
        semaphore.release()


class SlidingWindowLimiter:
    """Small in-process limiter suitable for the single-worker deployment."""

    def __init__(self, attempts: int, window_seconds: int = 60):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._next_sweep = 0.0

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            if now >= self._next_sweep:
                stale = [
                    bucket_key
                    for bucket_key, bucket in self._events.items()
                    if not bucket or bucket[-1] < cutoff
                ]
                for bucket_key in stale:
                    self._events.pop(bucket_key, None)
                self._next_sweep = now + self.window_seconds
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.attempts:
                return False
            events.append(now)
            return True


class ConcurrentKeyLimiter:
    """Bound concurrent work globally and per authenticated account."""

    def __init__(self, *, global_limit: int, per_key_limit: int):
        self.global_limit = global_limit
        self.per_key_limit = per_key_limit
        self._global_count = 0
        self._key_counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, key: str) -> bool:
        with self._lock:
            if (
                self._global_count >= self.global_limit
                or self._key_counts[key] >= self.per_key_limit
            ):
                return False
            self._global_count += 1
            self._key_counts[key] += 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            current = self._key_counts.get(key, 0)
            if current <= 0 or self._global_count <= 0:
                raise RuntimeError("concurrency limiter released without an acquisition")
            if current == 1:
                self._key_counts.pop(key, None)
            else:
                self._key_counts[key] = current - 1
            self._global_count -= 1
