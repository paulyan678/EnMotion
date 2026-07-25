"""Stable, user-safe failure metadata for persisted video tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ...models.newapi import NewAPIProviderError
from ...utils.newapi_models import redact_newapi_secrets

logger = logging.getLogger(__name__)

VIDEO_FAILURE_CODE = "video_generation_failed"
VIDEO_FAILURE_MESSAGE = "视频生成失败，请稍后重试。"
VIDEO_TIMEOUT_CODE = "video_generation_timeout"
VIDEO_TIMEOUT_MESSAGE = "视频生成等待超时，服务商尚未返回结果。您可以稍后重试。"
VIDEO_INTERRUPTED_CODE = "video_generation_interrupted"
VIDEO_INTERRUPTED_MESSAGE = (
    "视频生成在完成前中断。为避免重复计费，系统没有自动重试；" + "请确认服务商任务状态后再重试。"
)
VIDEO_QUEUE_UNAVAILABLE_CODE = "video_queue_unavailable"
VIDEO_QUEUE_UNAVAILABLE_MESSAGE = "视频生成暂时无法进入队列，请稍后重试。"
VIDEO_CANCELED_CODE = "video_generation_canceled"
VIDEO_CANCELED_MESSAGE = "视频生成已取消。"
VIDEO_FAILURE_DIAGNOSTIC = "视频生成服务未能完成此任务。"
VIDEO_TIMEOUT_DIAGNOSTIC = "视频服务在规定时间内未返回结果。"


@dataclass(frozen=True, slots=True)
class VideoFailure:
    message: str
    code: str
    diagnostic: str


def classify_video_failure(exc: BaseException) -> VideoFailure:
    """Return a localized-key-friendly message plus redacted diagnostics."""

    if isinstance(exc, NewAPIProviderError):
        return VideoFailure(
            message=str(exc),
            code=exc.error_code,
            diagnostic=exc.diagnostic,
        )

    raw = redact_newapi_secrets(str(exc) or exc.__class__.__name__)[:4000]
    logger.warning("视频生成内部诊断：%s", raw)
    normalized = raw.casefold()
    is_timeout = isinstance(exc, TimeoutError) or any(
        marker in normalized for marker in ("timed out", "timeout", "did not finish within")
    )
    if is_timeout:
        return VideoFailure(
            VIDEO_TIMEOUT_MESSAGE,
            VIDEO_TIMEOUT_CODE,
            VIDEO_TIMEOUT_DIAGNOSTIC,
        )
    return VideoFailure(
        VIDEO_FAILURE_MESSAGE,
        VIDEO_FAILURE_CODE,
        VIDEO_FAILURE_DIAGNOSTIC,
    )
