from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database import begin_immediate
from ..models import CreditLedger, RateCard, UsageRequest, User, utcnow


class LedgerError(RuntimeError):
    pass


class InsufficientCredits(LedgerError):
    pass


class IdempotencyConflict(LedgerError):
    pass


class RateCardNotFound(LedgerError):
    pass


class InvalidLedgerTransition(LedgerError):
    pass


@dataclass(frozen=True)
class ReserveOutcome:
    usage: UsageRequest
    replay: bool


@dataclass(frozen=True)
class AdjustmentOutcome:
    entry: CreditLedger
    replay: bool


@dataclass(frozen=True)
class TransitionOutcome:
    usage: UsageRequest
    replay: bool


def _matching_rate_card(
    session: Session,
    *,
    operation: str,
    model: str,
    context: dict[str, object],
) -> RateCard:
    candidates = session.scalars(
        select(RateCard)
        .where(RateCard.operation == operation)
        .where(RateCard.model == model)
        .where(RateCard.active.is_(True))
        .order_by(RateCard.priority.desc(), RateCard.version.desc())
    ).all()
    for card in candidates:
        if all(context.get(key) == expected for key, expected in (card.selectors or {}).items()):
            return card
    raise RateCardNotFound(f"no active rate card for {operation}/{model}")


def _append(
    session: Session,
    *,
    user: User,
    entry_type: str,
    delta_available: int,
    delta_reserved: int,
    reason: str,
    idempotency_key: str,
    actor_user_id: str | None = None,
    usage_request_id: str | None = None,
) -> CreditLedger:
    if user.available_credits < 0 or user.reserved_credits < 0:
        raise InvalidLedgerTransition("credit balances cannot be negative")
    entry = CreditLedger(
        user_id=user.id,
        actor_user_id=actor_user_id,
        usage_request_id=usage_request_id,
        entry_type=entry_type,
        delta_available=delta_available,
        delta_reserved=delta_reserved,
        available_after=user.available_credits,
        reserved_after=user.reserved_credits,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    session.add(entry)
    session.flush()
    return entry


def adjust_credits(
    session: Session,
    *,
    user_id: str,
    actor_user_id: str,
    delta: int,
    reason: str,
    idempotency_key: str,
) -> AdjustmentOutcome:
    begin_immediate(session)
    existing = session.scalar(
        select(CreditLedger)
        .where(CreditLedger.user_id == user_id)
        .where(CreditLedger.idempotency_key == f"adjust:{idempotency_key}")
    )
    if existing:
        if existing.delta_available != delta or existing.reason != reason:
            raise IdempotencyConflict("credit adjustment key was reused with different values")
        return AdjustmentOutcome(existing, replay=True)
    user = session.get(User, user_id)
    if user is None:
        raise LookupError("user not found")
    if user.available_credits + delta < 0:
        raise InsufficientCredits("adjustment would make the available balance negative")
    user.available_credits += delta
    user.updated_at = utcnow()
    return AdjustmentOutcome(
        _append(
            session,
            user=user,
            actor_user_id=actor_user_id,
            entry_type="adjustment",
            delta_available=delta,
            delta_reserved=0,
            reason=reason,
            idempotency_key=f"adjust:{idempotency_key}",
        ),
        replay=False,
    )


def reserve_usage(
    session: Session,
    *,
    user_id: str,
    operation: str,
    model: str,
    idempotency_key: str,
    request_fingerprint: str,
    rate_context: dict[str, object],
) -> ReserveOutcome:
    begin_immediate(session)
    existing = session.scalar(
        select(UsageRequest)
        .where(UsageRequest.user_id == user_id)
        .where(UsageRequest.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.request_fingerprint != request_fingerprint:
            raise IdempotencyConflict("idempotency key was reused for a different request")
        return ReserveOutcome(existing, replay=True)
    user = session.get(User, user_id)
    if user is None or not user.active:
        raise PermissionError("account is inactive")
    card = _matching_rate_card(
        session,
        operation=operation,
        model=model,
        context=rate_context,
    )
    cost = card.unit_cost
    if user.available_credits < cost:
        raise InsufficientCredits("insufficient available credits")
    usage = UsageRequest(
        user_id=user.id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        operation=operation,
        model=model,
        rate_card_id=card.id,
        status="reserved",
        reserved_units=cost,
    )
    session.add(usage)
    session.flush()
    user.available_credits -= cost
    user.reserved_credits += cost
    user.updated_at = utcnow()
    _append(
        session,
        user=user,
        entry_type="reserve",
        delta_available=-cost,
        delta_reserved=cost,
        reason=f"reserved for {operation}",
        idempotency_key=f"usage:{usage.id}:reserve",
        usage_request_id=usage.id,
    )
    return ReserveOutcome(usage, replay=False)


def settle_usage(
    session: Session,
    *,
    usage_id: str,
    charged_units: int | None = None,
    upstream_status: int | None = None,
    upstream_task_id: str | None = None,
    reason: str = "provider accepted request",
) -> TransitionOutcome:
    begin_immediate(session)
    usage = session.get(UsageRequest, usage_id)
    if usage is None:
        raise LookupError("usage request not found")
    if usage.status == "settled":
        return TransitionOutcome(usage, replay=True)
    if usage.status == "refunded":
        raise InvalidLedgerTransition("refunded usage cannot be settled")
    if usage.status not in {"reserved", "pending_reconciliation"}:
        raise InvalidLedgerTransition(f"cannot settle usage in state {usage.status}")
    charge = usage.reserved_units if charged_units is None else charged_units
    if charge < 0 or charge > usage.reserved_units:
        raise InvalidLedgerTransition("charged units must fit inside the reservation")
    user = session.get(User, usage.user_id)
    if user is None or user.reserved_credits < usage.reserved_units:
        raise InvalidLedgerTransition("reserved balance invariant failed")
    release = usage.reserved_units - charge
    user.available_credits += release
    user.reserved_credits -= usage.reserved_units
    user.updated_at = utcnow()
    usage.status = "settled"
    usage.settled_units = charge
    usage.upstream_status = upstream_status
    usage.upstream_task_id = upstream_task_id or usage.upstream_task_id
    usage.settled_at = utcnow()
    usage.updated_at = utcnow()
    _append(
        session,
        user=user,
        entry_type="settle",
        delta_available=release,
        delta_reserved=-usage.reserved_units,
        reason=reason,
        idempotency_key=f"usage:{usage.id}:settle",
        usage_request_id=usage.id,
    )
    return TransitionOutcome(usage, replay=False)


def refund_usage(
    session: Session,
    *,
    usage_id: str,
    reason: str,
    upstream_status: int | None = None,
    error_code: str | None = None,
) -> TransitionOutcome:
    begin_immediate(session)
    usage = session.get(UsageRequest, usage_id)
    if usage is None:
        raise LookupError("usage request not found")
    if usage.status == "refunded":
        return TransitionOutcome(usage, replay=True)
    if usage.status == "settled":
        raise InvalidLedgerTransition("settled usage cannot be refunded")
    if usage.status not in {"reserved", "pending_reconciliation"}:
        raise InvalidLedgerTransition(f"cannot refund usage in state {usage.status}")
    user = session.get(User, usage.user_id)
    if user is None or user.reserved_credits < usage.reserved_units:
        raise InvalidLedgerTransition("reserved balance invariant failed")
    user.available_credits += usage.reserved_units
    user.reserved_credits -= usage.reserved_units
    user.updated_at = utcnow()
    usage.status = "refunded"
    usage.upstream_status = upstream_status
    usage.error_code = error_code
    usage.updated_at = utcnow()
    usage.settled_at = utcnow()
    _append(
        session,
        user=user,
        entry_type="refund",
        delta_available=usage.reserved_units,
        delta_reserved=-usage.reserved_units,
        reason=reason,
        idempotency_key=f"usage:{usage.id}:refund",
        usage_request_id=usage.id,
    )
    return TransitionOutcome(usage, replay=False)


def mark_pending(
    session: Session,
    *,
    usage_id: str,
    reason_code: str,
    upstream_status: int | None = None,
) -> UsageRequest:
    begin_immediate(session)
    usage = session.get(UsageRequest, usage_id)
    if usage is None:
        raise LookupError("usage request not found")
    if usage.status in {"settled", "refunded", "pending_reconciliation"}:
        return usage
    usage.status = "pending_reconciliation"
    usage.error_code = reason_code
    usage.upstream_status = upstream_status
    usage.updated_at = utcnow()
    return usage


def recover_interrupted_reservations(session: Session) -> int:
    """Expose requests left in-flight by a prior single-worker process."""

    begin_immediate(session)
    now = utcnow()
    result = session.execute(
        update(UsageRequest)
        .where(UsageRequest.status == "reserved")
        .values(
            status="pending_reconciliation",
            error_code="control_plane_restart",
            updated_at=now,
        )
    )
    return int(result.rowcount or 0)
