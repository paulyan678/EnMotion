"""Opt-in multi-user server mode integration."""

from __future__ import annotations

from fastapi import FastAPI

from .config import ServerSettings, server_mode_enabled
from .context import Actor, RequestContext, get_current_actor, get_request_context

__all__ = [
    "Actor",
    "RequestContext",
    "get_current_actor",
    "get_request_context",
    "include_server_mode",
    "server_mode_enabled",
]


def include_server_mode(app: FastAPI) -> bool:
    """Register auth routes and fail-closed protection when explicitly enabled.

    Call this once, immediately after constructing the FastAPI app and before
    registering CORS middleware.  It is a no-op in desktop/local mode.
    """

    if not server_mode_enabled():
        return False

    from .database import get_database
    from .job_router import router as job_router
    from .jobs import recover_stale_reservations
    from .middleware import ServerAuthMiddleware
    from .provider_media import install_provider_media_access_log_filter
    from .router import router

    settings = ServerSettings.from_env()
    database = get_database()
    app.state.enmotion_server_settings = settings
    app.state.enmotion_server_database = database
    app.include_router(router)
    app.include_router(job_router)
    app.add_middleware(ServerAuthMiddleware, database=database, settings=settings)
    install_provider_media_access_log_filter()
    app.router.add_event_handler("startup", lambda: recover_stale_reservations(database))
    return True
