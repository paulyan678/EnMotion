from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.models import CreditLedger, RateCard, UsageRequest, User
from app.security import hash_password, normalize_username
from app.services.ledger import (
    InsufficientCredits,
    recover_interrupted_reservations,
    reserve_usage,
)


def test_concurrent_reservations_cannot_overspend(app_env):
    _client, app = app_env
    with app.state.db.session() as session:
        user = User(
            username="concurrent",
            normalized_username=normalize_username("concurrent"),
            password_hash=hash_password("Concurrent-password-123"),
            role="user",
            available_credits=10,
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(
            CreditLedger(
                user_id=user.id,
                entry_type="adjustment",
                delta_available=10,
                delta_reserved=0,
                available_after=10,
                reserved_after=0,
                reason="concurrency seed",
                idempotency_key=f"seed:{user.id}",
            )
        )
        session.add(
            RateCard(
                operation="chat.completions",
                model="deepseek-v4-pro",
                unit_cost=3,
                priority=100,
            )
        )

    def reserve(index: int) -> str:
        try:
            with app.state.db.session() as session:
                result = reserve_usage(
                    session,
                    user_id=user_id,
                    operation="chat.completions",
                    model="deepseek-v4-pro",
                    idempotency_key=f"concurrent-{index}",
                    request_fingerprint=f"{index:064d}",
                    rate_context={},
                )
                return result.usage.id
        except InsufficientCredits:
            return "insufficient"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(reserve, range(10)))
    assert len([item for item in results if item != "insufficient"]) == 3
    with app.state.db.session() as session:
        user = session.get(User, user_id)
        assert user.available_credits == 1
        assert user.reserved_credits == 9
        assert (
            len(
                session.scalars(
                    select(UsageRequest).where(UsageRequest.user_id == user_id)
                ).all()
            )
            == 3
        )


def test_concurrent_duplicate_key_charges_once(app_env):
    _client, app = app_env
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.username == "other"))
        user_id = user.id

    def reserve(_index: int) -> tuple[str, bool]:
        with app.state.db.session() as session:
            result = reserve_usage(
                session,
                user_id=user_id,
                operation="chat.completions",
                model="deepseek-v4-flash",
                idempotency_key="one-logical-request",
                request_fingerprint="f" * 64,
                rate_context={},
            )
            return result.usage.id, result.replay

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert len({item[0] for item in results}) == 1
    assert sum(not item[1] for item in results) == 1
    with app.state.db.session() as session:
        user = session.get(User, user_id)
        assert user.available_credits == 93
        assert user.reserved_credits == 7


def test_interrupted_reservations_become_visible_for_reconciliation(app_env):
    _client, app = app_env
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.username == "other"))
        outcome = reserve_usage(
            session,
            user_id=user.id,
            operation="chat.completions",
            model="deepseek-v4-flash",
            idempotency_key="interrupted-request",
            request_fingerprint="a" * 64,
            rate_context={},
        )
        usage_id = outcome.usage.id

    with app.state.db.session() as session:
        assert recover_interrupted_reservations(session) == 1

    with app.state.db.session() as session:
        usage = session.get(UsageRequest, usage_id)
        user = session.scalar(select(User).where(User.username == "other"))
        assert usage.status == "pending_reconciliation"
        assert usage.error_code == "control_plane_restart"
        assert user.available_credits == 93
        assert user.reserved_credits == 7
