from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from . import __version__
from .config import Settings
from .database import Database
from .models import User
from .routers import account, admin, auth, gateway, releases
from .security import ConcurrentKeyLimiter, SlidingWindowLimiter, hash_password
from .services.ledger import recover_interrupted_reservations


logger = logging.getLogger("enmotion.control_plane")


def create_app(
    settings: Settings,
    *,
    provider_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_create_schema:
            db.create_schema()
        with db.session() as session:
            recovered = recover_interrupted_reservations(session)
        if recovered:
            logger.warning(
                "Marked %d interrupted usage reservation(s) for reconciliation",
                recovered,
            )
        timeout = httpx.Timeout(
            connect=settings.provider_connect_timeout_seconds,
            read=settings.provider_read_timeout_seconds,
            write=settings.provider_read_timeout_seconds,
            pool=settings.provider_connect_timeout_seconds,
        )
        provider_limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        release_limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=provider_limits,
            transport=provider_transport,
            follow_redirects=False,
            trust_env=False,
        ) as provider_client:
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=release_limits,
                transport=provider_transport,
                follow_redirects=False,
                trust_env=False,
            ) as release_client:
                app.state.provider_client = provider_client
                app.state.release_client = release_client
                yield

    app = FastAPI(
        title="EnMotion Control Plane",
        version=__version__,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.login_account_limiter = SlidingWindowLimiter(
        settings.login_attempts_per_minute
    )
    app.state.login_ip_limiter = SlidingWindowLimiter(
        max(30, settings.login_attempts_per_minute * 3)
    )
    app.state.login_global_limiter = SlidingWindowLimiter(
        max(100, settings.login_attempts_per_minute * 10)
    )
    app.state.password_hash_slots = threading.BoundedSemaphore(4)
    app.state.release_download_limiter = ConcurrentKeyLimiter(
        global_limit=2,
        per_key_limit=1,
    )
    app.state.dummy_password_hash = hash_password("not-a-real-user-password")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")[:80] or str(uuid.uuid4())
        try:
            response = await call_next(request)
        except HTTPException:
            raise
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api/") else response.headers.get(
                "Cache-Control", "no-cache"
            )
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):
        log_path = request.url.path
        if log_path.startswith("/api/v1/releases/session/"):
            log_path = "/api/v1/releases/session/[redacted]"
        logger.exception(
            "Unhandled request error type=%s path=%s",
            type(exc).__name__,
            log_path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error"},
        )

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(account.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(gateway.router, prefix="/api/v1")
    app.include_router(releases.router, prefix="/api/v1")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/health/ready")
    def ready() -> dict[str, str | int]:
        try:
            with db.session() as session:
                session.execute(text("SELECT 1"))
                users = session.scalar(select(func.count()).select_from(User))
        except Exception as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "database schema is not ready"
            ) from exc
        return {"status": "ready", "users": int(users or 0)}

    static_root = Path(__file__).resolve().parent / "static" / "admin"
    app.mount("/admin", StaticFiles(directory=static_root, html=True), name="admin")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/admin/")

    return app
