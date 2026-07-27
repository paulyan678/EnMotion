from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..audit import record_audit
from ..database import begin_immediate
from ..dependencies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    CurrentPrincipal,
    client_ip,
)
from ..models import LoginSession, User, utcnow
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    SessionPublic,
    SessionResponse,
    TokenResponse,
    UserPublic,
)
from ..security import (
    ADMIN_RESET_PASSWORD_MIN_LENGTH,
    hash_password,
    normalize_username,
    password_work_slot,
    password_needs_rehash,
    PasswordWorkUnavailable,
    secure_equal,
    token_digest,
    verify_password,
)
from ..services.auth import issue_session, revoke_all_sessions, rotate_refresh_token


router = APIRouter(prefix="/auth", tags=["authentication"])


def _set_session_cookies(response: Response, request: Request, token_response: TokenResponse) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        ACCESS_COOKIE,
        token_response.access_token,
        max_age=settings.access_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        token_response.refresh_token,
        max_age=settings.refresh_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE,
        token_response.csrf_token,
        # The double-submit value is also required to rotate the refresh cookie,
        # so it must remain available for the refresh session's lifetime.
        max_age=settings.refresh_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _token_response(tokens, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_expires_at=tokens.session.access_expires_at,
        refresh_expires_at=tokens.session.refresh_expires_at,
        csrf_token=tokens.csrf_token,
        user=UserPublic.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> TokenResponse:
    ip = client_ip(request) or "unknown"
    normalized_attempt = payload.username.strip().casefold()[:64]
    if (
        not request.app.state.login_global_limiter.allow("all")
        or not request.app.state.login_ip_limiter.allow(ip)
        or not request.app.state.login_account_limiter.allow(normalized_attempt)
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")
    try:
        normalized = normalize_username(payload.username)
    except ValueError:
        normalized = ""

    # Argon2 is intentionally performed before acquiring SQLite's write lock.
    # Re-check the cheap immutable hash value inside the transaction to avoid
    # issuing a session if an administrator changed the account concurrently.
    with request.app.state.db.session() as session:
        candidate = session.scalar(
            select(User).where(User.normalized_username == normalized)
        )
        stored_hash = (
            candidate.password_hash
            if candidate is not None
            else request.app.state.dummy_password_hash
        )
        candidate_id = candidate.id if candidate is not None else None
        candidate_active = bool(candidate and candidate.active)
    try:
        with password_work_slot(request.app.state.password_hash_slots):
            valid = verify_password(stored_hash, payload.password)
            replacement_hash = (
                hash_password(
                    payload.password,
                    minimum_length=ADMIN_RESET_PASSWORD_MIN_LENGTH,
                )
                if candidate_active and valid and password_needs_rehash(stored_hash)
                else None
            )
    except PasswordWorkUnavailable as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "password verification capacity is busy; retry shortly",
            headers={"Retry-After": "2"},
        ) from exc

    with request.app.state.db.session() as session:
        begin_immediate(session)
        user = session.get(User, candidate_id) if candidate_id else None
        accepted = bool(
            candidate_active
            and valid
            and user is not None
            and user.active
            and secure_equal(user.password_hash, stored_hash)
        )
        if not accepted:
            record_audit(
                session,
                actor_user_id=None,
                action="auth.login_failed",
                target_type="user",
                target_id=user.id if user else None,
                detail={"username": normalized_attempt},
                ip_address=ip,
            )
            login_failed = True
        else:
            login_failed = False
            if replacement_hash is not None:
                user.password_hash = replacement_hash
            tokens = issue_session(
                session,
                user=user,
                settings=request.app.state.settings,
                device_label=payload.device_label,
            )
            record_audit(
                session,
                actor_user_id=user.id,
                action="auth.login",
                target_type="session",
                target_id=tokens.session.id,
                detail={"device_label": tokens.session.device_label},
                ip_address=ip,
            )
            result = _token_response(tokens, user)
    if login_failed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    _set_session_cookies(response, request, result)
    return result


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
) -> TokenResponse | Response:
    supplied_token = payload.refresh_token or request.cookies.get(REFRESH_COOKIE, "")
    if not supplied_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token required")
    if payload.refresh_token is None:
        csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if not csrf_cookie or not csrf_header or not secure_equal(csrf_cookie, csrf_header):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")
        refresh_digest = token_digest(
            request.app.state.settings.session_hmac_secret,
            supplied_token,
        )
        with request.app.state.db.session() as validation_session:
            source_session = validation_session.scalar(
                select(LoginSession).where(LoginSession.refresh_digest == refresh_digest)
            )
            if (
                source_session is None
                or source_session.csrf_digest
                != token_digest(
                    request.app.state.settings.session_hmac_secret,
                    csrf_header,
                )
            ):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")
    with request.app.state.db.session() as session:
        begin_immediate(session)
        result = rotate_refresh_token(
            session,
            refresh_token=supplied_token,
            settings=request.app.state.settings,
        )
        if result.reuse_detected:
            record_audit(
                session,
                actor_user_id=result.user.id if result.user else None,
                action="auth.refresh_reuse_detected",
                target_type="user",
                target_id=result.user.id if result.user else None,
                ip_address=client_ip(request),
            )
        if result.tokens is None or result.user is None:
            failure = True
        else:
            failure = False
            record_audit(
                session,
                actor_user_id=result.user.id,
                action="auth.refresh_rotated",
                target_type="session",
                target_id=result.tokens.session.id,
                ip_address=client_ip(request),
            )
            response_payload = _token_response(result.tokens, result.user)
    if failure:
        failure_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "invalid or expired refresh token"},
        )
        _clear_session_cookies(failure_response)
        return failure_response
    _set_session_cookies(response, request, response_payload)
    return response_payload


@router.get("/session", response_model=SessionResponse)
@router.get("/me", response_model=SessionResponse)
def session_info(principal: CurrentPrincipal, request: Request) -> SessionResponse:
    with request.app.state.db.session() as session:
        user = session.get(User, principal.user_id)
        login_session = session.get(LoginSession, principal.session_id)
        if user is None or login_session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session unavailable")
        return SessionResponse(
            user=UserPublic.model_validate(user),
            session=SessionPublic.model_validate(login_session),
        )


@router.post("/logout", response_model=MessageResponse)
def logout(
    payload: LogoutRequest,
    principal: CurrentPrincipal,
    request: Request,
    response: Response,
) -> MessageResponse:
    now = utcnow()
    with request.app.state.db.session() as session:
        begin_immediate(session)
        current = session.get(LoginSession, principal.session_id)
        if current and current.revoked_at is None:
            current.revoked_at = now
            current.revoked_reason = "logout"
        if payload.refresh_token:
            digest = token_digest(
                request.app.state.settings.session_hmac_secret,
                payload.refresh_token,
            )
            matched = session.scalar(
                select(LoginSession)
                .where(LoginSession.user_id == principal.user_id)
                .where(LoginSession.refresh_digest == digest)
            )
            if matched and matched.revoked_at is None:
                matched.revoked_at = now
                matched.revoked_reason = "logout"
        record_audit(
            session,
            actor_user_id=principal.user_id,
            action="auth.logout",
            target_type="session",
            target_id=principal.session_id,
            ip_address=client_ip(request),
        )
    _clear_session_cookies(response)
    return MessageResponse(message="signed out")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    principal: CurrentPrincipal,
    request: Request,
    response: Response,
) -> MessageResponse:
    with request.app.state.db.session() as read_session:
        existing = read_session.get(User, principal.user_id)
        stored_hash = existing.password_hash if existing is not None else ""
    try:
        with password_work_slot(request.app.state.password_hash_slots):
            current_valid = bool(
                stored_hash
                and verify_password(stored_hash, payload.current_password)
            )
            replacement_hash = (
                hash_password(payload.new_password) if current_valid else ""
            )
    except PasswordWorkUnavailable as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "password verification capacity is busy; retry shortly",
            headers={"Retry-After": "2"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not current_valid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is incorrect")

    with request.app.state.db.session() as session:
        begin_immediate(session)
        user = session.get(User, principal.user_id)
        if (
            user is None
            or not user.active
            or not secure_equal(user.password_hash, stored_hash)
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "account credentials changed; sign in again",
            )
        user.password_hash = replacement_hash
        user.updated_at = utcnow()
        revoke_all_sessions(
            session,
            user_id=user.id,
            reason="password_changed",
        )
        record_audit(
            session,
            actor_user_id=user.id,
            action="auth.password_changed",
            target_type="user",
            target_id=user.id,
            ip_address=client_ip(request),
        )
    _clear_session_cookies(response)
    return MessageResponse(message="password changed", reauthentication_required=True)
