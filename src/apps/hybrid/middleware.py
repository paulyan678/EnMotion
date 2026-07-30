"""Fail-closed local authentication and workspace binding for managed desktops."""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from ..web_runtime.context import bind_tenant, reset_tenant
from ..web_runtime.file_lock import bind_nonblocking_read, reset_nonblocking_read
from .config import HybridSettings
from .session import LocalSession, session_vault

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS = {
    "/health",
    "/ready",
    "/runtime-config.js",
    "/auth/login",
    "/auth/session",
    "/auth/me",
}
_PUBLIC_PREFIXES = ("/static/", "/docs", "/openapi.json")
_BLOCKED_PREFIXES = ("/config/api-keys", "/config/env")
_NONCE_BOOTSTRAP_PATHS = {"/health", "/ready", "/runtime-config.js"}


class HybridAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: HybridSettings) -> None:
        super().__init__(app)
        self.settings = settings
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _write_lock(self, workspace_id: str) -> asyncio.Lock:
        return self._write_locks.setdefault(workspace_id, asyncio.Lock())

    def _nonce_valid(self, request: Request) -> bool:
        expected = self.settings.local_nonce
        if not expected:
            return True
        supplied = request.headers.get("X-EnMotion-Local-Nonce", "")
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    @staticmethod
    def _workspace_lock_path(workspace_id: str) -> Path:
        workspace_root = Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser()
        return workspace_root / workspace_id / ".workspace.lock"

    def _session(self, request: Request) -> LocalSession | None:
        return session_vault.get_local(request.cookies.get(self.settings.session_cookie_name))

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        if path.startswith(_BLOCKED_PREFIXES):
            return JSONResponse(status_code=404, content={"detail": "请求的内容不存在。"})
        if (
            not self._nonce_valid(request)
            and not path.startswith("/static/")
            and path not in _NONCE_BOOTSTRAP_PATHS
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "本机客户端验证失败。"},
            )

        is_public = (
            request.method == "OPTIONS"
            or path in _PUBLIC_PATHS
            or path.startswith(_PUBLIC_PREFIXES)
        )
        if is_public:
            return await call_next(request)

        local = self._session(request)
        if local is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "请先登录。"},
                headers={"Cache-Control": "no-store"},
            )

        if request.method not in _SAFE_METHODS:
            supplied = request.headers.get(self.settings.csrf_header_name, "")
            if not supplied or not secrets.compare_digest(supplied, local.csrf_token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "安全校验已失效，请刷新页面后重试。"},
                    headers={"Cache-Control": "no-store"},
                )

        request.state.hybrid_user = local.user
        token = bind_tenant(
            local.user.id,
            local.user.workspace_id,
            local.user.role,
        )
        try:
            if request.method in _SAFE_METHODS:
                # Workspace JSON writers use atomic replacement. Read routes can
                # therefore consume the latest complete snapshot without waiting
                # minutes for an image/video provider call holding the writer
                # lock. Transient /tasks polling has its own lock-free writer
                # accessor and must retain the in-memory task map.
                if path.startswith("/tasks/"):
                    response = await call_next(request)
                else:
                    read_token = bind_nonblocking_read(
                        self._workspace_lock_path(local.user.workspace_id)
                    )
                    try:
                        response = await call_next(request)
                    finally:
                        reset_nonblocking_read(read_token)
            else:
                async with self._write_lock(local.user.workspace_id):
                    response = await call_next(request)
        finally:
            reset_tenant(token)
        response.headers["X-EnMotion-Workspace-ID"] = local.user.workspace_id
        response.headers.setdefault("Cache-Control", "no-store")
        return response
