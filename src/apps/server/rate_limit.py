"""Small, replaceable rate-limit abstraction.

The in-memory implementation is suitable for the single API process used in
Mac testing.  A Redis implementation can satisfy the same protocol when the API
is scaled horizontally.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    def consume(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision: ...

    def refund(self, key: str) -> None: ...

    def reset(self, key: str) -> None: ...


class InMemoryRateLimiter:
    def __init__(self, *, clock=time.monotonic, max_keys: int = 10_000) -> None:
        if max_keys <= 0:
            raise ValueError("max_keys must be greater than zero")
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be greater than zero")
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                # Bound attacker-controlled account/IP keys so random usernames
                # cannot grow this single-process implementation indefinitely.
                if len(self._events) >= self._max_keys:
                    self._events.pop(next(iter(self._events)))
                events = deque()
                self._events[key] = events
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0]) + 0.999))
                return RateLimitDecision(False, retry_after)
            events.append(now)
            return RateLimitDecision(True)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def refund(self, key: str) -> None:
        """Remove the most recently reserved attempt for a successful request."""

        with self._lock:
            events = self._events.get(key)
            if not events:
                return
            events.pop()
            if not events:
                self._events.pop(key, None)

    def reset(self, key: str) -> None:
        """Clear one bucket after the account proves knowledge of its password."""

        with self._lock:
            self._events.pop(key, None)


login_rate_limiter: RateLimiter = InMemoryRateLimiter()
