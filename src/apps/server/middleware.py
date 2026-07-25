"""Fail-closed authentication, CSRF, and tenant binding for server mode."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from pathlib import Path

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..web_runtime.context import bind_tenant, reset_tenant
from ..web_runtime.file_lock import (
    acquire_lock_file,
    bind_external_lock,
    bind_nonblocking_read,
    release_lock_file,
    reset_external_lock,
    reset_nonblocking_read,
)
from .config import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, ServerSettings
from .context import Actor
from .database import Database
from .models import LoginSession, User, WorkspaceMembership
from .quotas import StorageQuotaExceededError, ensure_storage_capacity
from .security import digest_token, secure_equal
from .service import datetime_is_expired
from .workspace_storage import (
    bind_workspace_mutation,
    commit_workspace_mutation,
    defer_unreferenced_workspace_media,
    remove_new_workspace_files,
    reset_workspace_mutation,
    restore_workspace_file_deletions,
    restore_workspace_metadata,
    snapshot_workspace_files,
    snapshot_workspace_metadata,
    stage_workspace_file_deletions,
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/health", "/ready", "/auth/login"}
PUBLIC_PREFIXES = ("/provider-media/",)
LOCK_FREE_PREFIXES = ("/auth/", "/jobs", "/tasks/", "/files/")
LOCK_FREE_PATHS = {"/config/api-keys/inspect"}
logger = logging.getLogger(__name__)


class ServerAuthMiddleware:
    """Protect the entire existing API when multi-user server mode is active."""

    def __init__(self, app: ASGIApp, *, database: Database, settings: ServerSettings) -> None:
        self.app = app
        self.database = database
        self.settings = settings
        self._workspace_locks: dict[str, asyncio.Lock] = {}
        self._lock_executor = ThreadPoolExecutor(
            max_workers=max(2, int(os.getenv("ENMOTION_WORKSPACE_LOCK_THREADS", "8"))),
            thread_name_prefix="enmotion-workspace-lock",
        )

    def _workspace_lock(self, workspace_id: str) -> asyncio.Lock:
        return self._workspace_locks.setdefault(workspace_id, asyncio.Lock())

    @staticmethod
    def _workspace_lock_path(workspace_id: str) -> Path:
        workspace_root = Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser()
        return workspace_root / workspace_id / ".workspace.lock"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        try:
            body_file, receive = await self._bounded_request_body(scope, receive)
        except ValueError as exc:
            await self._error(scope, receive, send_with_request_id, 400, str(exc))
            return
        except RequestBodyTooLarge:
            await self._error(
                scope,
                receive,
                send_with_request_id,
                413,
                "请求内容过大，请减少输入后重试。",
            )
            return

        try:
            await self._handle_http(
                scope,
                receive,
                send_with_request_id,
                path=path,
                method=method,
            )
        finally:
            body_file.close()

    async def _handle_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        path: str,
        method: str,
    ) -> None:
        public_read = method in {"GET", "HEAD"} and path.startswith(PUBLIC_PREFIXES)
        if method == "OPTIONS" or path in PUBLIC_PATHS or public_read:
            if method not in SAFE_METHODS and not self._origin_allowed(scope):
                await self._error(scope, receive, send, 403, "请求来源未获信任。")
                return
            await self.app(scope, receive, send)
            return

        auth_started = time.perf_counter()
        actor, login_session = await run_in_threadpool(self._authenticate, scope)
        auth_ms = (time.perf_counter() - auth_started) * 1000
        request_id = str(scope.get("state", {}).get("request_id", "unknown"))
        if actor is None or login_session is None:
            logger.info(
                "Authentication rejected request_id=%s status=401 auth_ms=%.3f",
                request_id,
                auth_ms,
            )
            await self._error(scope, receive, send, 401, "请先登录。")
            return
        logger.info(
            "Authentication accepted request_id=%s workspace=%s auth_ms=%.3f",
            request_id,
            actor.workspace_id,
            auth_ms,
        )

        if method not in SAFE_METHODS:
            csrf_error = self._csrf_error(scope, login_session)
            if csrf_error:
                await self._error(scope, receive, send, 403, csrf_error)
                return

        scope.setdefault("state", {})["actor"] = actor

        async def send_with_workspace(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                server_timing_values = [
                    value for name, value in headers if name.lower() == b"server-timing"
                ]
                headers = [
                    (name, value)
                    for name, value in headers
                    if name.lower() not in {b"x-enmotion-workspace-id", b"server-timing"}
                ]
                headers.append((b"x-enmotion-workspace-id", actor.workspace_id.encode("ascii")))
                timing = f"auth;dur={max(0.0, auth_ms):.3f}".encode("ascii")
                if server_timing_values:
                    timing = b", ".join([*server_timing_values, timing])
                headers.append((b"server-timing", timing))
                message["headers"] = headers
            await send(message)

        if method in SAFE_METHODS:
            # Workspace JSON stores use atomic replacement, so reads can use
            # the latest complete on-disk snapshot without taking the writer
            # lock. This keeps navigation, the asset library, and Playground
            # responsive while a worker waits minutes for an image/video
            # provider. Mutations still take the exclusive transaction lock
            # below and retain rollback/quota guarantees.
            tenant_token = bind_tenant(actor.user_id, actor.workspace_id, actor.role)
            read_token = bind_nonblocking_read(self._workspace_lock_path(actor.workspace_id))
            try:
                await self.app(scope, receive, send_with_workspace)
            finally:
                reset_nonblocking_read(read_token)
                reset_tenant(tenant_token)
            return

        if path in LOCK_FREE_PATHS or path.startswith(LOCK_FREE_PREFIXES):
            tenant_token = bind_tenant(actor.user_id, actor.workspace_id, actor.role)
            try:
                await self.app(scope, receive, send_with_workspace)
            finally:
                reset_tenant(tenant_token)
            return

        # Existing EnMotion project records are JSON documents. Keep an entire
        # authenticated request atomic relative to the worker and other writes
        # in the same workspace. Different workspaces still execute in parallel.
        async with self._workspace_lock(actor.workspace_id):
            loop = asyncio.get_running_loop()
            descriptor, lock_path = await loop.run_in_executor(
                self._lock_executor,
                acquire_lock_file,
                self._workspace_lock_path(actor.workspace_id),
            )
            external_lock_token = bind_external_lock(lock_path)
            tenant_token = bind_tenant(actor.user_id, actor.workspace_id, actor.role)
            mutation_token = bind_workspace_mutation(actor.workspace_id)
            starting_files: set[str] | None = None
            starting_metadata: dict[str, bytes | None] | None = None
            try:
                if method not in SAFE_METHODS:
                    starting_files, starting_metadata = await asyncio.gather(
                        run_in_threadpool(snapshot_workspace_files, actor.workspace_id),
                        run_in_threadpool(snapshot_workspace_metadata, actor.workspace_id),
                    )
                    buffered: list[Message] = []

                    async def buffer_send(message: Message) -> None:
                        # Copy mutable message/header containers before an
                        # upstream response object can reuse them.
                        cloned = dict(message)
                        if "headers" in cloned:
                            cloned["headers"] = list(cloned["headers"])
                        buffered.append(cloned)

                    try:
                        await self.app(scope, receive, buffer_send)
                    except Exception:
                        await run_in_threadpool(
                            self._rollback_workspace,
                            actor.workspace_id,
                            starting_files,
                            starting_metadata,
                        )
                        raise

                    status = next(
                        (
                            int(message.get("status", 500))
                            for message in buffered
                            if message["type"] == "http.response.start"
                        ),
                        500,
                    )
                    if status >= 400:
                        await run_in_threadpool(
                            self._rollback_workspace,
                            actor.workspace_id,
                            starting_files,
                            starting_metadata,
                        )
                    else:
                        try:
                            await run_in_threadpool(
                                defer_unreferenced_workspace_media,
                                actor.workspace_id,
                                starting_metadata,
                            )
                            # Move files that became unreachable out of the
                            # live output tree before measuring quota. This
                            # lets an in-place replacement succeed when the
                            # old and new files briefly coexist, while keeping
                            # the old files recoverable until quota validation
                            # and the metadata transaction both succeed.
                            await run_in_threadpool(
                                stage_workspace_file_deletions,
                                actor.workspace_id,
                            )
                            await run_in_threadpool(
                                ensure_storage_capacity,
                                self.database,
                                workspace_id=actor.workspace_id,
                            )
                        except StorageQuotaExceededError as exc:
                            removed_files, removed_bytes = await run_in_threadpool(
                                self._rollback_workspace,
                                actor.workspace_id,
                                starting_files,
                                starting_metadata,
                            )
                            response = JSONResponse(
                                {
                                    "detail": "存储空间不足，请删除部分文件后重试。",
                                    "rolled_back_files": removed_files,
                                    "rolled_back_bytes": removed_bytes,
                                },
                                status_code=507,
                                headers={"Cache-Control": "no-store"},
                            )
                            await response(scope, receive, send_with_workspace)
                            return
                        except Exception:
                            await run_in_threadpool(
                                self._rollback_workspace,
                                actor.workspace_id,
                                starting_files,
                                starting_metadata,
                            )
                            raise
                        else:
                            await run_in_threadpool(commit_workspace_mutation, actor.workspace_id)

                    for message in buffered:
                        await send_with_workspace(message)
                else:
                    await self.app(scope, receive, send_with_workspace)
            finally:
                reset_workspace_mutation(mutation_token)
                reset_external_lock(external_lock_token)
                # Unlock/close cannot block, and doing it inline guarantees it
                # is never queued behind threads waiting on long provider work.
                release_lock_file(descriptor)
                reset_tenant(tenant_token)

    async def _bounded_request_body(
        self, scope: Scope, receive: Receive
    ) -> tuple[tempfile.SpooledTemporaryFile[bytes], Receive]:
        """Read and replay one bounded body, including chunked requests."""

        declared = self._declared_content_length(scope)
        limit = self.settings.max_request_body_bytes
        if declared is not None and declared > limit:
            raise RequestBodyTooLarge

        body_file = tempfile.SpooledTemporaryFile(max_size=min(limit, 1024 * 1024), mode="w+b")
        total = 0
        disconnected = False
        try:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                    break
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    raise RequestBodyTooLarge
                body_file.write(chunk)
                if not message.get("more_body", False):
                    break
            if declared is not None and not disconnected and total != declared:
                raise ValueError("请求头中的 Content-Length 与请求内容不一致")
            body_file.seek(0)
        except Exception:
            body_file.close()
            raise

        complete = False

        async def replay() -> Message:
            nonlocal complete
            if disconnected:
                return {"type": "http.disconnect"}
            if complete:
                return {"type": "http.disconnect"}
            chunk = body_file.read(64 * 1024)
            more = body_file.tell() < total
            complete = not more
            return {"type": "http.request", "body": chunk, "more_body": more}

        return body_file, replay

    @staticmethod
    def _declared_content_length(scope: Scope) -> int | None:
        values = [
            value.decode("ascii", "strict").strip()
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if not values:
            return None
        if len(set(values)) != 1:
            raise ValueError("请求包含互相冲突的 Content-Length 头")
        try:
            length = int(values[0])
        except ValueError as exc:
            raise ValueError("Content-Length 请求头无效") from exc
        if length < 0:
            raise ValueError("Content-Length 请求头无效")
        return length

    @staticmethod
    def _rollback_workspace(
        workspace_id: str,
        starting_files: set[str],
        starting_metadata: dict[str, bytes | None],
    ) -> tuple[int, int]:
        # Tombstones must return to their original locations before newly
        # created files are reconciled against the starting snapshot.
        restore_workspace_file_deletions(workspace_id)
        restore_workspace_metadata(workspace_id, starting_metadata)
        removed = remove_new_workspace_files(workspace_id, starting_files)
        # Restored JSON must not be shadowed by a mutated in-memory cache.
        comic_api = sys.modules.get("src.apps.comic_gen.api")
        comic_registry = getattr(comic_api, "_workspace_pipelines", None)
        if comic_registry is not None:
            comic_registry.discard(workspace_id)
        playground_api = sys.modules.get("src.apps.playground.api")
        playground_registry = getattr(playground_api, "_workspace_playgrounds", None)
        if playground_registry is not None:
            playground_registry.discard(workspace_id)
        return removed

    def _authenticate(self, scope: Scope) -> tuple[Actor | None, LoginSession | None]:
        headers = Headers(scope=scope)
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            return None, None
        morsel = cookie.get(self.settings.session_cookie_name)
        if morsel is None or not morsel.value:
            return None, None

        token_hash = digest_token(morsel.value, self.settings.session_secret)
        with self.database.session() as db:
            row = db.execute(
                select(LoginSession, User, WorkspaceMembership)
                .join(User, User.id == LoginSession.user_id)
                .join(
                    WorkspaceMembership,
                    (WorkspaceMembership.user_id == LoginSession.user_id)
                    & (WorkspaceMembership.workspace_id == LoginSession.workspace_id),
                )
                .where(LoginSession.token_hash == token_hash)
            ).one_or_none()
            if row is None:
                return None, None
            login_session, user, membership = row
            if (
                login_session.revoked_at is not None
                or datetime_is_expired(login_session.expires_at)
                or not user.is_active
            ):
                return None, None
            actor = Actor(
                user_id=user.id,
                username=user.username,
                role=user.role,
                workspace_id=login_session.workspace_id,
                membership_role=membership.role,
                session_id=login_session.id,
            )
            # Objects are detached once the short request lookup session closes;
            # all fields used later are already loaded scalar columns.
            db.expunge(login_session)
            return actor, login_session

    def _csrf_error(self, scope: Scope, login_session: LoginSession) -> str | None:
        if not self._origin_allowed(scope):
            return "请求来源未获信任。"

        headers = Headers(scope=scope)
        header_token = headers.get(CSRF_HEADER_NAME)
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            return "安全校验已失效，请刷新页面后重试。"
        csrf_cookie = cookie.get(CSRF_COOKIE_NAME)
        if not header_token or csrf_cookie is None or not csrf_cookie.value:
            return "缺少安全校验信息，请刷新页面后重试。"
        if not secure_equal(header_token, csrf_cookie.value):
            return "安全校验已失效，请刷新页面后重试。"
        presented_hash = digest_token(header_token, self.settings.session_secret)
        if not secure_equal(presented_hash, login_session.csrf_hash):
            return "安全校验已失效，请刷新页面后重试。"
        return None

    def _origin_allowed(self, scope: Scope) -> bool:
        origin = Headers(scope=scope).get("origin")
        # Non-browser automation may omit Origin and still must present the
        # unguessable CSRF token for authenticated mutations.
        if not origin:
            return True
        return origin.rstrip("/") in self.settings.allowed_origins

    async def _error(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        request_id = str(scope.get("state", {}).get("request_id", uuid.uuid4().hex))
        code = {
            401: "AUTHENTICATION_REQUIRED",
            403: "PERMISSION_DENIED",
            408: "REQUEST_TIMED_OUT",
            429: "RATE_LIMITED",
            503: "SERVER_TEMPORARILY_UNAVAILABLE",
        }.get(status_code, "REQUEST_REJECTED")
        response = JSONResponse(
            {
                "detail": detail,
                "error": {
                    "code": code,
                    "message": detail,
                    "request_id": request_id,
                    "retryable": status_code in {408, 429, 500, 502, 503, 504},
                },
            },
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


class RequestBodyTooLarge(Exception):
    """Raised before request dispatch when the ASGI byte ceiling is crossed."""
