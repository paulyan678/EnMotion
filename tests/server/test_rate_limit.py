from __future__ import annotations

from src.apps.server.rate_limit import InMemoryRateLimiter


def test_rate_limiter_enforces_sliding_window():
    now = [10.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    assert limiter.consume("account", limit=2, window_seconds=10).allowed is True
    assert limiter.consume("account", limit=2, window_seconds=10).allowed is True
    denied = limiter.consume("account", limit=2, window_seconds=10)
    assert denied.allowed is False
    assert denied.retry_after_seconds == 10

    now[0] = 20.1
    assert limiter.consume("account", limit=2, window_seconds=10).allowed is True


def test_rate_limiter_bounds_attacker_controlled_keys():
    limiter = InMemoryRateLimiter(max_keys=2)
    limiter.consume("first", limit=1, window_seconds=60)
    limiter.consume("second", limit=1, window_seconds=60)
    limiter.consume("third", limit=1, window_seconds=60)

    # The oldest key was evicted rather than retaining unbounded empty buckets.
    assert limiter.consume("first", limit=1, window_seconds=60).allowed is True


def test_rate_limiter_can_refund_success_and_reset_proven_account():
    limiter = InMemoryRateLimiter()
    assert limiter.consume("ip", limit=1, window_seconds=60).allowed is True
    limiter.refund("ip")
    assert limiter.consume("ip", limit=1, window_seconds=60).allowed is True

    assert limiter.consume("account", limit=1, window_seconds=60).allowed is True
    assert limiter.consume("account", limit=1, window_seconds=60).allowed is False
    limiter.reset("account")
    assert limiter.consume("account", limit=1, window_seconds=60).allowed is True
