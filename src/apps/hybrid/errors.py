"""Chinese-only public error responses for the managed desktop API."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...utils.newapi_models import redact_newapi_secrets

logger = logging.getLogger(__name__)

_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
_STATUS_MESSAGES = {
    400: "请求内容无效，请检查后重试。",
    401: "请先登录。",
    403: "当前账号无权执行此操作。",
    404: "请求的内容不存在。",
    405: "当前请求方式不受支持。",
    409: "当前操作与现有状态冲突，请刷新后重试。",
    413: "提交内容过大，请减少输入后重试。",
    422: "提交内容不符合要求，请检查后重试。",
    429: "请求过于频繁，请稍后重试。",
}
_STATUS_CODES = {
    400: "INVALID_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "STATE_CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def _public_message(status_code: int, detail: Any) -> str:
    if isinstance(detail, str) and _CHINESE_TEXT.search(detail):
        return detail
    if status_code >= 500:
        return "服务暂时不可用，请稍后重试。"
    return _STATUS_MESSAGES.get(status_code, "请求失败，请稍后重试。")


async def chinese_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    message = _public_message(exc.status_code, exc.detail)
    if message != exc.detail:
        logger.warning(
            "桌面 API 已隐藏非中文错误详情：状态=%s 路径=%s 详情=%s",
            exc.status_code,
            request.url.path,
            redact_newapi_secrets(exc.detail)[:500],
        )
    headers = dict(exc.headers or {})
    headers.setdefault(
        "X-EnMotion-Error-Code",
        _STATUS_CODES.get(
            exc.status_code,
            "SERVER_ERROR" if exc.status_code >= 500 else "REQUEST_FAILED",
        ),
    )
    headers.setdefault("Cache-Control", "no-store")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": message},
        headers=headers,
    )


async def chinese_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    logger.warning(
        "桌面 API 请求校验失败：路径=%s 错误数=%s",
        request.url.path,
        len(exc.errors()),
    )
    return JSONResponse(
        status_code=422,
        content={"detail": _STATUS_MESSAGES[422]},
        headers={
            "Cache-Control": "no-store",
            "X-EnMotion-Error-Code": "VALIDATION_ERROR",
        },
    )


async def chinese_unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception(
        "桌面 API 出现未处理异常：路径=%s 类型=%s",
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "服务暂时不可用，请稍后重试。"},
        headers={
            "Cache-Control": "no-store",
            "X-EnMotion-Error-Code": "SERVER_ERROR",
        },
    )
