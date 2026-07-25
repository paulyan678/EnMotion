from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..audit import record_audit
from ..config import MODEL_CAPABILITIES
from ..database import begin_immediate
from ..dependencies import AdminPrincipal, client_ip
from ..models import (
    AuditEvent,
    CreditLedger,
    LoginSession,
    RateCard,
    UsageRequest,
    User,
    utcnow,
)
from ..schemas import (
    AdminUsagePublic,
    AuditPublic,
    CreateUserRequest,
    CreditAdjustmentRequest,
    LedgerPublic,
    MessageResponse,
    RateCardCreate,
    RateCardPublic,
    RateCardUpdate,
    ReconcileUsageRequest,
    ResetPasswordRequest,
    RevokeSessionsRequest,
    SessionPublic,
    UsagePublic,
    UserPublic,
    UserStatusRequest,
)
from ..security import (
    PasswordWorkUnavailable,
    hash_password,
    normalize_username,
    password_work_slot,
)
from ..services.auth import revoke_all_sessions
from ..services.ledger import (
    IdempotencyConflict,
    InsufficientCredits,
    InvalidLedgerTransition,
    adjust_credits,
    refund_usage,
    settle_usage,
)


router = APIRouter(prefix="/admin", tags=["administration"])

_OPERATION_CAPABILITY = {
    "chat.completions": "chat",
    "images.generations": "image",
    "images.edits": "image",
    "video.generations": "video",
}


@router.get("/users", response_model=list[UserPublic])
def list_users(
    _principal: AdminPrincipal,
    request: Request,
    include_inactive: bool = True,
) -> list[UserPublic]:
    statement = select(User).order_by(User.created_at.asc())
    if not include_inactive:
        statement = statement.where(User.active.is_(True))
    with request.app.state.db.session() as session:
        return [UserPublic.model_validate(user) for user in session.scalars(statement).all()]


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    principal: AdminPrincipal,
    request: Request,
) -> UserPublic:
    try:
        normalized = normalize_username(payload.username)
        with password_work_slot(request.app.state.password_hash_slots):
            password_hash = hash_password(payload.password)
    except PasswordWorkUnavailable as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "password hashing capacity is busy; retry shortly",
            headers={"Retry-After": "2"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        with request.app.state.db.session() as session:
            begin_immediate(session)
            if session.scalar(
                select(User).where(User.normalized_username == normalized)
            ):
                raise HTTPException(status.HTTP_409_CONFLICT, "username already exists")
            user = User(
                username=payload.username.strip(),
                normalized_username=normalized,
                password_hash=password_hash,
                role=payload.role,
                available_credits=payload.initial_credits,
            )
            session.add(user)
            session.flush()
            if payload.initial_credits:
                session.add(
                    CreditLedger(
                        user_id=user.id,
                        actor_user_id=principal.user_id,
                        entry_type="adjustment",
                        delta_available=payload.initial_credits,
                        delta_reserved=0,
                        available_after=payload.initial_credits,
                        reserved_after=0,
                        reason="initial account credit",
                        idempotency_key=f"initial:{user.id}",
                    )
                )
            record_audit(
                session,
                actor_user_id=principal.user_id,
                action="admin.user_created",
                target_type="user",
                target_id=user.id,
                detail={
                    "username": user.username,
                    "role": user.role,
                    "initial_credits": payload.initial_credits,
                },
                ip_address=client_ip(request),
            )
            result = UserPublic.model_validate(user)
        return result
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "username already exists") from exc


@router.patch("/users/{user_id}/status", response_model=UserPublic)
def set_user_status(
    user_id: str,
    payload: UserStatusRequest,
    principal: AdminPrincipal,
    request: Request,
) -> UserPublic:
    if user_id == principal.user_id and not payload.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot deactivate your own account")
    with request.app.state.db.session() as session:
        begin_immediate(session)
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        changed = user.active != payload.active
        user.active = payload.active
        user.updated_at = utcnow()
        revoked = 0
        if changed and not payload.active:
            revoked = revoke_all_sessions(
                session,
                user_id=user.id,
                reason="account_deactivated",
            )
        record_audit(
            session,
            actor_user_id=principal.user_id,
            action="admin.user_status_changed",
            target_type="user",
            target_id=user.id,
            detail={"active": payload.active, "sessions_revoked": revoked},
            ip_address=client_ip(request),
        )
        return UserPublic.model_validate(user)


@router.post("/users/{user_id}/password", response_model=MessageResponse)
def reset_user_password(
    user_id: str,
    payload: ResetPasswordRequest,
    principal: AdminPrincipal,
    request: Request,
) -> MessageResponse:
    try:
        with password_work_slot(request.app.state.password_hash_slots):
            password_hash = hash_password(payload.new_password)
    except PasswordWorkUnavailable as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "password hashing capacity is busy; retry shortly",
            headers={"Retry-After": "2"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    with request.app.state.db.session() as session:
        begin_immediate(session)
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        user.password_hash = password_hash
        user.updated_at = utcnow()
        revoked = revoke_all_sessions(
            session,
            user_id=user.id,
            reason="admin_password_reset",
        )
        record_audit(
            session,
            actor_user_id=principal.user_id,
            action="admin.password_reset",
            target_type="user",
            target_id=user.id,
            detail={"sessions_revoked": revoked},
            ip_address=client_ip(request),
        )
    return MessageResponse(message="password reset; all sessions revoked")


@router.get("/users/{user_id}/sessions", response_model=list[SessionPublic])
def list_user_sessions(
    user_id: str,
    _principal: AdminPrincipal,
    request: Request,
) -> list[SessionPublic]:
    with request.app.state.db.session() as session:
        if session.get(User, user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        rows = session.scalars(
            select(LoginSession)
            .where(LoginSession.user_id == user_id)
            .order_by(LoginSession.created_at.desc())
        ).all()
        return [SessionPublic.model_validate(item) for item in rows]


@router.post("/users/{user_id}/sessions/revoke", response_model=MessageResponse)
def revoke_user_sessions(
    user_id: str,
    payload: RevokeSessionsRequest,
    principal: AdminPrincipal,
    request: Request,
) -> MessageResponse:
    with request.app.state.db.session() as session:
        begin_immediate(session)
        if session.get(User, user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        revoked = revoke_all_sessions(
            session,
            user_id=user_id,
            reason="admin_revoked",
            except_session_id=payload.except_session_id,
        )
        record_audit(
            session,
            actor_user_id=principal.user_id,
            action="admin.sessions_revoked",
            target_type="user",
            target_id=user_id,
            detail={"count": revoked, "except_session_id": payload.except_session_id},
            ip_address=client_ip(request),
        )
    return MessageResponse(message=f"revoked {revoked} session(s)")


@router.post("/users/{user_id}/credits", response_model=LedgerPublic)
def adjust_user_credits(
    user_id: str,
    payload: CreditAdjustmentRequest,
    principal: AdminPrincipal,
    request: Request,
) -> LedgerPublic:
    try:
        with request.app.state.db.session() as session:
            outcome = adjust_credits(
                session,
                user_id=user_id,
                actor_user_id=principal.user_id,
                delta=payload.delta,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
            )
            entry = outcome.entry
            record_audit(
                session,
                actor_user_id=principal.user_id,
                action=(
                    "admin.credit_adjustment_replayed"
                    if outcome.replay
                    else "admin.credit_adjusted"
                ),
                target_type="user",
                target_id=user_id,
                detail={
                    "delta": payload.delta,
                    "ledger_entry_id": entry.id,
                    "idempotency_key": payload.idempotency_key,
                    "replay": outcome.replay,
                },
                ip_address=client_ip(request),
            )
            return LedgerPublic.model_validate(entry)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InsufficientCredits as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/ledger", response_model=list[LedgerPublic])
def ledger_history(
    _principal: AdminPrincipal,
    request: Request,
    user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[LedgerPublic]:
    statement = select(CreditLedger).order_by(CreditLedger.created_at.desc()).limit(limit)
    if user_id:
        statement = statement.where(CreditLedger.user_id == user_id)
    with request.app.state.db.session() as session:
        return [
            LedgerPublic.model_validate(entry) for entry in session.scalars(statement).all()
        ]


@router.get("/usage", response_model=list[AdminUsagePublic])
def usage_history(
    _principal: AdminPrincipal,
    request: Request,
    user_id: str | None = None,
    usage_status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AdminUsagePublic]:
    allowed_statuses = {
        "reserved",
        "pending_reconciliation",
        "settled",
        "refunded",
    }
    if usage_status is not None and usage_status not in allowed_statuses:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unsupported usage status",
        )
    statement = select(UsageRequest).order_by(UsageRequest.created_at.desc()).limit(limit)
    if user_id:
        statement = statement.where(UsageRequest.user_id == user_id)
    if usage_status:
        statement = statement.where(UsageRequest.status == usage_status)
    with request.app.state.db.session() as session:
        return [
            AdminUsagePublic.model_validate(usage)
            for usage in session.scalars(statement).all()
        ]


@router.get("/rate-cards", response_model=list[RateCardPublic])
def list_rate_cards(
    _principal: AdminPrincipal,
    request: Request,
) -> list[RateCardPublic]:
    with request.app.state.db.session() as session:
        rows = session.scalars(
            select(RateCard).order_by(
                RateCard.operation.asc(), RateCard.model.asc(), RateCard.priority.desc()
            )
        ).all()
        return [RateCardPublic.model_validate(card) for card in rows]


def _validate_rate_capability(operation: str, model: str) -> None:
    if MODEL_CAPABILITIES[model] != _OPERATION_CAPABILITY[operation]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "model capability does not match the operation",
        )


@router.post(
    "/rate-cards",
    response_model=RateCardPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_rate_card(
    payload: RateCardCreate,
    principal: AdminPrincipal,
    request: Request,
) -> RateCardPublic:
    _validate_rate_capability(payload.operation, payload.model)
    with request.app.state.db.session() as session:
        begin_immediate(session)
        latest = session.scalar(
            select(RateCard)
            .where(RateCard.operation == payload.operation)
            .where(RateCard.model == payload.model)
            .order_by(RateCard.version.desc())
        )
        card = RateCard(
            **payload.model_dump(),
            version=(latest.version + 1) if latest else 1,
        )
        session.add(card)
        session.flush()
        record_audit(
            session,
            actor_user_id=principal.user_id,
            action="admin.rate_card_created",
            target_type="rate_card",
            target_id=card.id,
            detail={
                "operation": card.operation,
                "model": card.model,
                "unit_cost": card.unit_cost,
                "selectors": card.selectors,
            },
            ip_address=client_ip(request),
        )
        return RateCardPublic.model_validate(card)


@router.patch("/rate-cards/{card_id}", response_model=RateCardPublic)
def update_rate_card(
    card_id: str,
    payload: RateCardUpdate,
    principal: AdminPrincipal,
    request: Request,
) -> RateCardPublic:
    with request.app.state.db.session() as session:
        begin_immediate(session)
        card = session.get(RateCard, card_id)
        if card is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "rate card not found")
        changes = payload.model_dump(exclude_none=True)
        for key, value in changes.items():
            setattr(card, key, value)
        latest_version = session.scalar(
            select(func.max(RateCard.version))
            .where(RateCard.operation == card.operation)
            .where(RateCard.model == card.model)
        )
        card.version = int(latest_version or 0) + 1
        card.updated_at = utcnow()
        record_audit(
            session,
            actor_user_id=principal.user_id,
            action="admin.rate_card_updated",
            target_type="rate_card",
            target_id=card.id,
            detail={"changed_fields": sorted(changes)},
            ip_address=client_ip(request),
        )
        return RateCardPublic.model_validate(card)


@router.post("/usage/{usage_id}/settle", response_model=UsagePublic)
def reconcile_settle(
    usage_id: str,
    payload: ReconcileUsageRequest,
    principal: AdminPrincipal,
    request: Request,
) -> UsagePublic:
    try:
        with request.app.state.db.session() as session:
            outcome = settle_usage(
                session,
                usage_id=usage_id,
                charged_units=payload.charged_units,
                reason=f"admin reconciliation: {payload.reason}",
            )
            usage = outcome.usage
            record_audit(
                session,
                actor_user_id=principal.user_id,
                action=(
                    "admin.usage_settlement_replayed"
                    if outcome.replay
                    else "admin.usage_settled"
                ),
                target_type="usage_request",
                target_id=usage.id,
                detail={
                    "charged_units": usage.settled_units,
                    "replay": outcome.replay,
                },
                ip_address=client_ip(request),
            )
            return UsagePublic.model_validate(usage)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidLedgerTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/usage/{usage_id}/refund", response_model=UsagePublic)
def reconcile_refund(
    usage_id: str,
    payload: ReconcileUsageRequest,
    principal: AdminPrincipal,
    request: Request,
) -> UsagePublic:
    try:
        with request.app.state.db.session() as session:
            outcome = refund_usage(
                session,
                usage_id=usage_id,
                reason=f"admin reconciliation: {payload.reason}",
            )
            usage = outcome.usage
            record_audit(
                session,
                actor_user_id=principal.user_id,
                action=(
                    "admin.usage_refund_replayed"
                    if outcome.replay
                    else "admin.usage_refunded"
                ),
                target_type="usage_request",
                target_id=usage.id,
                detail={
                    "reserved_units": usage.reserved_units,
                    "replay": outcome.replay,
                },
                ip_address=client_ip(request),
            )
            return UsagePublic.model_validate(usage)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidLedgerTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/audit", response_model=list[AuditPublic])
def audit_history(
    _principal: AdminPrincipal,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditPublic]:
    with request.app.state.db.session() as session:
        rows = session.scalars(
            select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
        ).all()
        return [AuditPublic.model_validate(event) for event in rows]
