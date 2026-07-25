"""Managed desktop integration for EnMotion."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import (
    HybridSettings,
    hybrid_mode_enabled,
    workspace_isolation_enabled,
)


def include_hybrid_mode(app: FastAPI) -> bool:
    if not hybrid_mode_enabled():
        return False

    from ..server.config import server_mode_enabled

    if server_mode_enabled():
        raise RuntimeError("EnMotion 服务端模式与混合模式不能同时启用")

    from .errors import (
        chinese_http_exception_handler,
        chinese_unhandled_exception_handler,
        chinese_validation_exception_handler,
    )
    from .middleware import HybridAuthMiddleware
    from .router import router

    settings = HybridSettings.from_env()
    app.state.enmotion_hybrid_settings = settings
    app.include_router(router, tags=["managed desktop"])
    app.add_exception_handler(
        StarletteHTTPException,
        chinese_http_exception_handler,
    )
    app.add_exception_handler(
        RequestValidationError,
        chinese_validation_exception_handler,
    )
    app.add_exception_handler(
        Exception,
        chinese_unhandled_exception_handler,
    )
    app.add_middleware(HybridAuthMiddleware, settings=settings)
    return True


__all__ = [
    "HybridSettings",
    "hybrid_mode_enabled",
    "include_hybrid_mode",
    "workspace_isolation_enabled",
]
