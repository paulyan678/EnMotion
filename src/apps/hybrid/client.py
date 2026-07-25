"""Strict HTTPS client for the lightweight EnMotion control plane."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from ...utils.newapi_models import redact_newapi_secrets
from .config import HybridSettings
from .session import HybridUser, RemoteSession

logger = logging.getLogger(__name__)


_PUBLIC_CONTROL_PLANE_ERRORS = {
    400: "请求内容无效，请检查后重试。",
    401: "登录状态已失效，请重新登录。",
    403: "当前账号无权执行此操作。",
    404: "请求的内容不存在。",
    409: "当前操作与现有状态冲突，请刷新后重试。",
    413: "提交内容过大，请缩小后重试。",
    422: "提交内容不符合要求，请检查后重试。",
    429: "请求过于频繁，请稍后重试。",
}


def _public_control_plane_error(status_code: int) -> str:
    if status_code >= 500:
        return "EnMotion 账号服务暂时不可用，请稍后重试。"
    return _PUBLIC_CONTROL_PLANE_ERRORS.get(
        status_code,
        "EnMotion 账号请求失败，请稍后重试。",
    )


class ControlPlaneError(RuntimeError):
    def __init__(self, status_code: int, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RemoteLogin:
    user: HybridUser
    access_token: str
    refresh_token: str
    expires_in: int


class ControlPlaneClient:
    def __init__(self, settings: HybridSettings | None = None) -> None:
        self.settings = settings or HybridSettings.from_env()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("账号服务 API 路径必须以 / 开头")
        return f"{self.settings.control_plane_url}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Accept", "application/json")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.request(
                method,
                self._url(path),
                headers=headers,
                timeout=timeout or self.settings.request_timeout_seconds,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("账号服务连接失败：%s", type(exc).__name__)
            raise ControlPlaneError(
                503,
                "EnMotion 账号服务暂时不可用，请稍后重试。",
                retryable=True,
            ) from exc
        if 200 <= response.status_code < 300:
            return response
        diagnostic = ""
        try:
            payload = response.json()
            candidate = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                diagnostic = redact_newapi_secrets(candidate.strip())[:300]
        except Exception:
            pass
        logger.warning(
            "账号服务请求失败：状态=%s 路径=%s 详情=%s",
            response.status_code,
            path,
            diagnostic or "无",
        )
        raise ControlPlaneError(
            response.status_code,
            _public_control_plane_error(response.status_code),
            retryable=response.status_code in {408, 429, 502, 503, 504},
        )

    @staticmethod
    def _login_payload(payload: dict[str, Any]) -> RemoteLogin:
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        expires_in_value = payload.get("expires_in")
        if expires_in_value is None and payload.get("access_expires_at"):
            try:
                expires_at = datetime.fromisoformat(
                    str(payload["access_expires_at"]).replace("Z", "+00:00")
                )
                expires_in_value = max(
                    30,
                    int((expires_at - datetime.now(timezone.utc)).total_seconds()),
                )
            except (TypeError, ValueError):
                expires_in_value = None
        try:
            expires_in = int(expires_in_value or 900)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneError(
                502,
                "账号服务返回的会话无效，请重新登录。",
            ) from exc
        if not access_token or not refresh_token:
            raise ControlPlaneError(
                502,
                "账号服务返回的会话不完整，请重新登录。",
            )
        try:
            user = HybridUser.from_payload(payload)
        except ValueError as exc:
            logger.warning("账号服务返回的用户资料无效：%s", type(exc).__name__)
            raise ControlPlaneError(
                502,
                "账号服务返回的用户资料无效，请重新登录。",
            ) from exc
        return RemoteLogin(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=max(expires_in, 30),
        )

    def login(self, username: str, password: str) -> RemoteLogin:
        response = self._request(
            "POST",
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        return self._login_payload(response.json())

    def refresh(self, refresh_token: str) -> RemoteLogin:
        response = self._request(
            "POST",
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        return self._login_payload(response.json())

    def logout(self, remote: RemoteSession) -> None:
        self._request(
            "POST",
            "/api/v1/auth/logout",
            token=remote.access_token,
            json={"refresh_token": remote.refresh_token},
        )

    def change_password(
        self,
        remote: RemoteSession,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        self._request(
            "POST",
            "/api/v1/auth/change-password",
            token=remote.access_token,
            json={
                "current_password": current_password,
                "new_password": new_password,
            },
        )

    def get_json(
        self,
        path: str,
        remote: RemoteSession,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request(
            "GET",
            path,
            token=remote.access_token,
            params=params,
        ).json()

    def post_json(self, path: str, remote: RemoteSession, payload: Any) -> Any:
        response = self._request(
            "POST",
            path,
            token=remote.access_token,
            json=payload,
        )
        return response.json() if response.content else None

    def patch_json(self, path: str, remote: RemoteSession, payload: Any) -> Any:
        response = self._request(
            "PATCH",
            path,
            token=remote.access_token,
            json=payload,
        )
        return response.json() if response.content else None
