"""Authentication and account-administration HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import CSRF_COOKIE_NAME, ServerSettings, get_server_settings
from .context import Actor, get_current_actor, require_admin
from .database import get_db
from .models import User
from .rate_limit import login_rate_limiter
from .security import CredentialValidationError
from .service import (
    DuplicateUsernameError,
    authenticate_user,
    change_password,
    create_user_with_personal_workspace,
    issue_session,
    personal_workspace_for_user,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class AuthResponse(StrictModel):
    id: str
    username: str
    role: str
    workspace_id: str
    csrf_token: str


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class AdminCreateUserRequest(StrictModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=1024)
    role: str = Field(default="user", pattern="^(user|admin)$")
    workspace_name: str | None = Field(default=None, min_length=1, max_length=128)
    storage_quota_bytes: int = Field(default=20 * 1024 * 1024 * 1024, gt=0)


class UserResponse(StrictModel):
    id: str
    username: str
    role: str
    workspace_id: str
    is_active: bool


def _client_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For here. The production proxy should normalize
    # the ASGI client address using a restricted trusted-proxy configuration.
    return request.client.host if request.client else "unknown"


def _set_auth_cookies(
    response: Response,
    *,
    token: str,
    csrf_token: str,
    settings: ServerSettings,
) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        max_age=settings.session_ttl_seconds,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        max_age=settings.session_ttl_seconds,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_auth_cookies(response: Response, settings: ServerSettings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: ServerSettings = Depends(get_server_settings),
) -> AuthResponse:
    ip = _client_ip(request)
    normalized_hint = payload.username.strip().casefold()
    ip_key = f"login:ip:{ip}"
    account_key = f"login:account:{normalized_hint}"
    ip_decision = login_rate_limiter.consume(
        ip_key,
        limit=settings.login_attempts,
        window_seconds=settings.login_window_seconds,
    )
    if not ip_decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试次数过多，请稍后再试。",
            headers={"Retry-After": str(ip_decision.retry_after_seconds)},
        )
    account_decision = login_rate_limiter.consume(
        account_key,
        limit=settings.login_account_attempts,
        window_seconds=settings.login_window_seconds,
    )
    if not account_decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试次数过多，请稍后再试。",
            headers={"Retry-After": str(account_decision.retry_after_seconds)},
        )

    user = authenticate_user(db, username=payload.username, password=payload.password)
    if user is None:
        # The message intentionally does not disclose whether the username exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误。",
        )
    # Buckets represent failures, not routine use. Preserve other failed
    # usernames from this IP while removing this successful reservation, and
    # clear the proven account's distributed-failure bucket entirely.
    login_rate_limiter.refund(ip_key)
    login_rate_limiter.reset(account_key)
    workspace = personal_workspace_for_user(db, user.id)
    if workspace is None:
        raise HTTPException(
            status_code=500,
            detail="账号工作区暂时不可用，请联系管理员。",
        )

    issued = issue_session(
        db,
        user=user,
        workspace=workspace,
        settings=settings,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(
        response,
        token=issued.token,
        csrf_token=issued.csrf_token,
        settings=settings,
    )
    return AuthResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        workspace_id=workspace.id,
        csrf_token=issued.csrf_token,
    )


def _actor_response(actor: Actor, request: Request) -> AuthResponse:
    return AuthResponse(
        id=actor.user_id,
        username=actor.username,
        role=actor.role,
        workspace_id=actor.workspace_id,
        csrf_token=request.cookies.get(CSRF_COOKIE_NAME, ""),
    )


@router.get("/session", response_model=AuthResponse)
def current_session(
    request: Request,
    actor: Actor = Depends(get_current_actor),
) -> AuthResponse:
    return _actor_response(actor, request)


@router.get("/me", response_model=AuthResponse)
def current_user(
    request: Request,
    actor: Actor = Depends(get_current_actor),
) -> AuthResponse:
    return _actor_response(actor, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
    settings: ServerSettings = Depends(get_server_settings),
) -> None:
    revoke_session(db, actor.session_id)
    _clear_auth_cookies(response, settings)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def update_password(
    payload: ChangePasswordRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> None:
    user = db.get(User, actor.user_id)
    if user is None or not change_password(
        db,
        user=user,
        current_session_id=actor.session_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    ):
        raise HTTPException(status_code=400, detail="当前密码错误。")


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def admin_create_user(
    payload: AdminCreateUserRequest,
    _admin: Actor = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    try:
        user, workspace = create_user_with_personal_workspace(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            workspace_name=payload.workspace_name,
            storage_quota_bytes=payload.storage_quota_bytes,
        )
        db.commit()
    except DuplicateUsernameError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="此用户名已存在。") from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="此用户名已存在。") from exc
    except CredentialValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail="密码不符合安全要求，请使用更长且不易猜测的密码。",
        ) from exc
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        workspace_id=workspace.id,
        is_active=user.is_active,
    )


@router.get("/users", response_model=list[UserResponse])
def admin_list_users(
    _admin: Actor = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserResponse]:
    rows = db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.username_normalized.asc())
    ).scalars()
    result: list[UserResponse] = []
    for user in rows:
        workspace = personal_workspace_for_user(db, user.id)
        if workspace is not None:
            result.append(
                UserResponse(
                    id=user.id,
                    username=user.username,
                    role=user.role,
                    workspace_id=workspace.id,
                    is_active=user.is_active,
                )
            )
    return result
