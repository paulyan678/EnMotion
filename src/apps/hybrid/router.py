"""Local same-origin facade over EnMotion's remote account control plane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .activity import backfill_asset_activity, list_activity
from .client import ControlPlaneClient, ControlPlaneError
from .config import HybridSettings
from .session import (
    HybridUser,
    LocalSession,
    RemoteSession,
    StalePersistedCredentialError,
    session_vault,
)

router = APIRouter()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


def _settings() -> HybridSettings:
    return HybridSettings.from_env()


def _client() -> ControlPlaneClient:
    return ControlPlaneClient(_settings())


def _raise_control_error(exc: ControlPlaneError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _auth_response(user: HybridUser, csrf_token: str) -> dict[str, str]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "workspace_id": user.workspace_id,
        "csrf_token": csrf_token,
    }


def _set_local_cookies(response: Response, local: LocalSession) -> None:
    settings = _settings()
    response.set_cookie(
        settings.session_cookie_name,
        local.token,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        local.csrf_token,
        httponly=False,
        secure=False,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_local_cookies(response: Response) -> None:
    settings = _settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    response.headers["Cache-Control"] = "no-store"


def _local_session(request: Request) -> LocalSession | None:
    return session_vault.get_local(request.cookies.get(_settings().session_cookie_name))


def _required_session(request: Request) -> LocalSession:
    local = _local_session(request)
    if local is None:
        raise HTTPException(status_code=401, detail="请先登录。")
    return local


def _remote_session(local: LocalSession) -> RemoteSession:
    try:
        return session_vault.ensure_fresh(local.user.id, _client().refresh)
    except ControlPlaneError as exc:
        _raise_control_error(exc)


def _required_admin(request: Request) -> tuple[LocalSession, RemoteSession]:
    local = _required_session(request)
    if local.user.role != "admin":
        raise HTTPException(status_code=403, detail="此操作需要管理员权限。")
    return local, _remote_session(local)


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response) -> dict[str, str]:
    try:
        remote = _client().login(payload.username.strip(), payload.password)
        local = session_vault.start(
            user=remote.user,
            access_token=remote.access_token,
            refresh_token=remote.refresh_token,
            expires_in=remote.expires_in,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)
    _set_local_cookies(response, local)
    return _auth_response(local.user, local.csrf_token)


def _restore_session(response: Response) -> LocalSession | None:
    persisted = session_vault.persisted_refresh_token_snapshot()
    if persisted is None:
        return None
    try:
        remote = _client().refresh(persisted.value)
        local = session_vault.start(
            user=remote.user,
            access_token=remote.access_token,
            refresh_token=remote.refresh_token,
            expires_in=remote.expires_in,
            expected_credential_generation=persisted.generation,
        )
    except StalePersistedCredentialError:
        return None
    except ControlPlaneError as exc:
        if exc.status_code in {401, 403}:
            session_vault.clear_if_credential_generation(persisted.generation)
            return None
        _raise_control_error(exc)
    _set_local_cookies(response, local)
    return local


@router.get("/auth/session")
@router.get("/auth/me")
def current_session(request: Request, response: Response) -> dict[str, str]:
    local = _local_session(request) or _restore_session(response)
    if local is None:
        raise HTTPException(status_code=401, detail="请先登录。")
    return _auth_response(local.user, local.csrf_token)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    local = _required_session(request)
    try:
        remote = _remote_session(local)
        _client().logout(remote)
    except ControlPlaneError:
        # Local revocation must succeed even while the account service is down.
        pass
    finally:
        session_vault.revoke_local(local.token)
        _clear_local_cookies(response)


@router.post("/auth/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
) -> None:
    local = _required_session(request)
    try:
        _client().change_password(
            _remote_session(local),
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)
    session_vault.revoke_local(local.token)
    _clear_local_cookies(response)


@router.get("/account/balance")
def account_balance(request: Request) -> Any:
    local = _required_session(request)
    try:
        return _client().get_json(
            "/api/v1/account/balance",
            _remote_session(local),
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.get("/account/usage")
def account_usage(
    request: Request,
    limit: int = Query(default=30, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
) -> Any:
    local = _required_session(request)
    try:
        return _client().get_json(
            "/api/v1/account/usage",
            _remote_session(local),
            params={
                "limit": limit,
                **({"cursor": cursor} if cursor else {}),
            },
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.get("/activity/history")
def activity_history(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Return local hybrid generation lifecycles without the workspace writer lock."""

    local = _required_session(request)
    backfill_asset_activity(local.user.workspace_id)
    return list_activity(local.user.workspace_id, limit=limit)


@router.get("/auth/users")
def list_users(request: Request) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().get_json(
            "/api/v1/admin/users",
            remote,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.post("/auth/users")
def create_user(payload: dict[str, Any], request: Request) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().post_json(
            "/api/v1/admin/users",
            remote,
            payload,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.get("/admin/users")
def list_admin_users(request: Request) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().get_json("/api/v1/admin/users", remote)
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.post("/admin/users/{user_id}/credits")
def adjust_user_credits(
    user_id: str,
    payload: dict[str, Any],
    request: Request,
) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().post_json(
            f"/api/v1/admin/users/{user_id}/credits",
            remote,
            payload,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.patch("/admin/users/{user_id}/status")
def set_user_status(
    user_id: str,
    payload: dict[str, Any],
    request: Request,
) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().patch_json(
            f"/api/v1/admin/users/{user_id}/status",
            remote,
            payload,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.post("/admin/users/{user_id}/password")
def reset_user_password(
    user_id: str,
    payload: dict[str, Any],
    request: Request,
) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().post_json(
            f"/api/v1/admin/users/{user_id}/password",
            remote,
            payload,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)


@router.post("/admin/users/{user_id}/sessions/revoke")
def revoke_user_sessions(
    user_id: str,
    payload: dict[str, Any],
    request: Request,
) -> Any:
    _, remote = _required_admin(request)
    try:
        return _client().post_json(
            f"/api/v1/admin/users/{user_id}/sessions/revoke",
            remote,
            payload,
        )
    except ControlPlaneError as exc:
        _raise_control_error(exc)
