from __future__ import annotations

import base64
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select

from ..dependencies import CurrentPrincipal
from ..http_status import UNPROCESSABLE_CONTENT
from ..models import UsageRequest, User
from ..schemas import BalanceResponse, UsagePage, UsagePublic, UserPublic

router = APIRouter(prefix="/account", tags=["account"])


def _encode_cursor(usage: UsageRequest) -> str:
    raw = json.dumps([usage.created_at.isoformat(), usage.id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, usage_id = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(timestamp), str(usage_id)
    except Exception as exc:
        raise HTTPException(UNPROCESSABLE_CONTENT, "invalid cursor") from exc


@router.get("/me", response_model=UserPublic)
def account_me(principal: CurrentPrincipal, request: Request) -> UserPublic:
    with request.app.state.db.session() as session:
        user = session.get(User, principal.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        return UserPublic.model_validate(user)


@router.get("/balance", response_model=BalanceResponse)
def balance(principal: CurrentPrincipal, request: Request) -> BalanceResponse:
    with request.app.state.db.session() as session:
        user = session.get(User, principal.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        return BalanceResponse(
            available_credits=user.available_credits,
            reserved_credits=user.reserved_credits,
            total_credits=user.available_credits + user.reserved_credits,
        )


@router.get("/usage", response_model=UsagePage)
def usage_history(
    principal: CurrentPrincipal,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> UsagePage:
    statement = (
        select(UsageRequest)
        .where(UsageRequest.user_id == principal.user_id)
        .order_by(UsageRequest.created_at.desc(), UsageRequest.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        timestamp, usage_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                UsageRequest.created_at < timestamp,
                and_(UsageRequest.created_at == timestamp, UsageRequest.id < usage_id),
            )
        )
    with request.app.state.db.session() as session:
        rows = list(session.scalars(statement).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    return UsagePage(
        items=[UsagePublic.model_validate(item) for item in page],
        next_cursor=_encode_cursor(page[-1]) if has_more and page else None,
    )
