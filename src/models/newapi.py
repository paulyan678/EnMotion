"""Strict New API compatible image and video adapters."""

from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

from ..utils.generation_progress import report_generation_progress
from ..utils.newapi_models import (
    IMAGE,
    VIDEO,
    get_model_spec,
    get_selected_model,
    redact_newapi_secrets,
    resolve_model_api_key,
    validate_model_for_mode,
)
from .base import ImageGenModel, VideoGenModel

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
VIDEO_SUCCESS_STATUSES = frozenset(
    {"completed", "complete", "succeeded", "success", "successful", "done"}
)
VIDEO_FAILURE_STATUSES = frozenset({"failed", "failure", "fail", "error", "cancelled", "canceled"})
VIDEO_ACTIVE_STATUSES = frozenset(
    {"pending", "queued", "processing", "running", "in_progress", "in-progress"}
)
PROVIDER_SUCCESS_CODES = frozenset({"0", "200", "ok", "success", "succeeded"})
IMAGE_REPLAY_TIMEOUT_SECONDS = 60
INPUT_IMAGE_PRIVACY_ERROR_CODE = "input_image_privacy"
INPUT_IMAGE_PRIVACY_PROVIDER_CODE = "InputImageSensitiveContentDetected.PrivacyInformation"
INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE = (
    "视频服务商拒绝了所选参考图，因为图片可能包含真人形象。"
    "请选择或生成明显为虚构角色的图片后重试。"
)
OUTPUT_VIDEO_POLICY_ERROR_CODE = "output_video_policy"
OUTPUT_VIDEO_POLICY_PROVIDER_CODE = "OutputVideoSensitiveContentDetected.PolicyViolation"
OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE = (
    "视频服务商拒绝了生成结果，因为输出可能触发内容或版权政策。"
    "请调整提示词，避免受版权保护的角色、品牌或作品风格后重试。"
)
PROVIDER_CONNECTION_ERROR_CODE = "provider_connection_failed"
PROVIDER_RETRY_EXHAUSTED_HEADER = "X-EnMotion-Provider-Retry-Exhausted"
PROVIDER_CONNECTION_PUBLIC_MESSAGE = (
    "暂时无法连接到 AI 服务商。请稍后重试；如果持续失败，请联系管理员检查服务商线路。"
)
PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE = "provider_outcome_ambiguous"
PROVIDER_OUTCOME_AMBIGUOUS_PUBLIC_MESSAGE = (
    "AI 服务商未确认本次任务结果。为避免重复生成或计费，EnMotion 没有发起新的生成；"
    "请联系管理员核对接口调用记录。"
)
PROVIDER_RATE_LIMIT_ERROR_CODE = "provider_rate_limited"
PROVIDER_RATE_LIMIT_PUBLIC_MESSAGE = (
    "AI 服务商当前繁忙。EnMotion 已自动重试，但服务商仍未接受请求，请稍后再试。"
)
PROVIDER_CONCURRENCY_ERROR_CODE = "provider_concurrency_limited"
PROVIDER_CONCURRENCY_PUBLIC_MESSAGE = (
    "当前视频模型的服务商并发队列已满。EnMotion 已排队重试，"
    "但在等待时间内仍未获得生成名额，请稍后再试。"
)
PROVIDER_AUTH_ERROR_CODE = "provider_authentication_failed"
PROVIDER_AUTH_PUBLIC_MESSAGE = "AI 服务商凭证无效或已过期，请联系管理员检查模型配置。"
PROVIDER_ACCESS_ERROR_CODE = "provider_access_denied"
PROVIDER_ACCESS_PUBLIC_MESSAGE = "当前服务商账户无权使用所选模型，请联系管理员检查模型权限。"
PROVIDER_QUOTA_ERROR_CODE = "provider_quota_exhausted"
PROVIDER_QUOTA_PUBLIC_MESSAGE = (
    "EnMotion 点数或 AI 服务商额度不足，请联系管理员检查账户余额和服务商账单。"
)
PROVIDER_REQUEST_ERROR_CODE = "provider_request_rejected"
PROVIDER_REQUEST_PUBLIC_MESSAGE = "AI 服务商拒绝了请求，请检查提示词和生成参数后重试。"
PROVIDER_PAYLOAD_TOO_LARGE_ERROR_CODE = "provider_payload_too_large"
PROVIDER_PAYLOAD_TOO_LARGE_PUBLIC_MESSAGE = "参考图片或请求内容过大，请减少参考图或压缩图片后重试。"
RATE_CARD_MISSING_ERROR_CODE = "rate_card_missing"
RATE_CARD_MISSING_PUBLIC_MESSAGE = "当前模型尚未配置计费规则，请联系管理员在管理中心添加后重试。"
_PHASE_LABELS = {
    "chat completion": "生成文本",
    "image submission": "提交图像任务",
    "image request": "请求图像服务",
    "video generation": "视频生成",
    "video submission": "提交视频任务",
    "video request": "请求视频服务",
    "video processing": "处理视频任务",
}
IMAGE_SIZE_ALIASES = {
    "576x1024": "1024x1536",
    "768x1024": "1024x1536",
    "1024x1536": "1024x1536",
    "1024x576": "1536x1024",
    "1024x768": "1536x1024",
    "1536x1024": "1536x1024",
    "1024x1024": "1024x1024",
}
# Keep the desktop wait longer than the control plane's provider read timeout
# so a provider timeout is returned as a structured API result instead of the
# desktop abandoning the connection first.
DEFAULT_IMAGE_REQUEST_TIMEOUT_SECONDS = 960.0


class NewAPIProviderError(RuntimeError):
    """A provider rejection with a safe user message and bounded diagnostics."""

    def __init__(
        self,
        public_message: str,
        *,
        error_code: str,
        provider_code: str = "",
        provider_message: str = "",
        http_status: int | None = None,
        request_id: str = "",
        phase: str = "video generation",
        provider_task_id: str = "",
        diagnostic_override: str = "",
        retry_exhausted: bool = False,
    ) -> None:
        super().__init__(public_message)
        self.error_code = error_code
        self.provider_code = redact_newapi_secrets(provider_code)[:200]
        self.provider_message = redact_newapi_secrets(provider_message)[:1000]
        self.http_status = http_status
        self.request_id = redact_newapi_secrets(request_id)[:200]
        self.phase = phase
        self.provider_task_id = redact_newapi_secrets(provider_task_id)[:200]
        self.diagnostic_override = redact_newapi_secrets(diagnostic_override)[:4000]
        self.retry_exhausted = retry_exhausted

    @property
    def diagnostic(self) -> str:
        if self.diagnostic_override:
            return self.diagnostic_override
        phase = _PHASE_LABELS.get(self.phase, self.phase)
        parts = [f"阶段：{phase}"]
        if self.http_status is not None:
            parts.append(f"HTTP 状态：{self.http_status}")
        if self.provider_code:
            parts.append(f"服务商错误代码：{self.provider_code}")
        if self.provider_task_id:
            parts.append(f"服务商任务 ID：{self.provider_task_id}")
        if self.request_id:
            parts.append(f"请求 ID：{self.request_id}")
        return "\n".join(parts)

    def job_result(self) -> Dict[str, str]:
        return {
            "error_code": self.error_code,
            "error_diagnostic": self.diagnostic,
        }


def _classified_provider_error(
    code: str,
    message: str,
    *,
    http_status: int | None = None,
    request_id: str = "",
    phase: str,
    provider_task_id: str = "",
) -> NewAPIProviderError | None:
    """Map known provider failures to stable, non-technical application errors."""

    normalized = f"{code} {message}".casefold().replace("_", "")
    is_provider_concurrency_limited = (
        "providerconcurrencylimited" in normalized
        or "quotawarningconcurrencylimit" in normalized
        or ("concurrency" in normalized and "limit" in normalized)
    )
    if is_provider_concurrency_limited:
        return NewAPIProviderError(
            PROVIDER_CONCURRENCY_PUBLIC_MESSAGE,
            error_code=PROVIDER_CONCURRENCY_ERROR_CODE,
            provider_code=code or PROVIDER_CONCURRENCY_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    is_provider_connection_failure = (
        "provider connection failed" in normalized or "providerconnectfailed" in normalized
    )
    if is_provider_connection_failure:
        logger.warning(
            "无法连接 AI 服务商：阶段=%s HTTP=%s 请求ID=%s",
            phase,
            http_status,
            redact_newapi_secrets(request_id)[:200],
        )
        return NewAPIProviderError(
            PROVIDER_CONNECTION_PUBLIC_MESSAGE,
            error_code=PROVIDER_CONNECTION_ERROR_CODE,
            provider_code=code or PROVIDER_CONNECTION_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    is_ambiguous_outcome = (
        "provideroutcomeambiguous" in normalized
        or "outcome is ambiguous" in normalized
        or "credits remain reserved" in normalized
        or "pendingreconciliation" in normalized
    )
    if is_ambiguous_outcome:
        return NewAPIProviderError(
            PROVIDER_OUTCOME_AMBIGUOUS_PUBLIC_MESSAGE,
            error_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
            provider_code=code or PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    is_quota_exhausted = http_status == 402 or any(
        marker in normalized
        for marker in (
            "providerquotaexhausted",
            "quotabelowblockthreshold",
            "insufficientquota",
            "insufficient available credits",
            "no balance left",
            "余额不足",
            "低于禁止阈值",
        )
    )
    if is_quota_exhausted:
        return NewAPIProviderError(
            PROVIDER_QUOTA_PUBLIC_MESSAGE,
            error_code=PROVIDER_QUOTA_ERROR_CODE,
            provider_code=code or PROVIDER_QUOTA_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    is_rate_limited = http_status == 429 or "providerratelimited" in normalized
    if is_rate_limited:
        return NewAPIProviderError(
            PROVIDER_RATE_LIMIT_PUBLIC_MESSAGE,
            error_code=PROVIDER_RATE_LIMIT_ERROR_CODE,
            provider_code=code or PROVIDER_RATE_LIMIT_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    if http_status == 401 or "providerauthenticationfailed" in normalized:
        return NewAPIProviderError(
            PROVIDER_AUTH_PUBLIC_MESSAGE,
            error_code=PROVIDER_AUTH_ERROR_CODE,
            provider_code=code or PROVIDER_AUTH_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    if http_status == 413 or "providerpayloadtoolarge" in normalized:
        return NewAPIProviderError(
            PROVIDER_PAYLOAD_TOO_LARGE_PUBLIC_MESSAGE,
            error_code=PROVIDER_PAYLOAD_TOO_LARGE_ERROR_CODE,
            provider_code=code or PROVIDER_PAYLOAD_TOO_LARGE_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    if "no active rate card" in normalized:
        logger.warning(
            "账号服务缺少计费规则：阶段=%s HTTP=%s 请求ID=%s",
            phase,
            http_status,
            redact_newapi_secrets(request_id)[:200],
        )
        return NewAPIProviderError(
            RATE_CARD_MISSING_PUBLIC_MESSAGE,
            error_code=RATE_CARD_MISSING_ERROR_CODE,
            provider_code=code,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    is_output_video_policy = OUTPUT_VIDEO_POLICY_PROVIDER_CODE.casefold() in normalized or (
        "outputvideo" in normalized
        and ("policyviolation" in normalized or "copyright" in normalized)
    )
    if is_output_video_policy:
        logger.warning(
            "AI 视频输出被服务商政策拒绝：阶段=%s HTTP=%s 错误代码=%s " "请求ID=%s 任务ID=%s",
            phase,
            http_status,
            redact_newapi_secrets(code)[:200],
            redact_newapi_secrets(request_id)[:200],
            redact_newapi_secrets(provider_task_id)[:200],
        )
        return NewAPIProviderError(
            OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE,
            error_code=OUTPUT_VIDEO_POLICY_ERROR_CODE,
            provider_code=code or OUTPUT_VIDEO_POLICY_PROVIDER_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    is_input_privacy = INPUT_IMAGE_PRIVACY_PROVIDER_CODE.casefold() in normalized or (
        "inputimage" in normalized and "privacyinformation" in normalized
    )
    if is_input_privacy:
        logger.warning(
            "AI 服务请求被拒绝：阶段=%s HTTP=%s 错误代码=%s " "请求ID=%s 任务ID=%s 服务商消息=%s",
            phase,
            http_status,
            redact_newapi_secrets(code)[:200],
            redact_newapi_secrets(request_id)[:200],
            redact_newapi_secrets(provider_task_id)[:200],
            redact_newapi_secrets(message)[:1000],
        )
        return NewAPIProviderError(
            INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
            error_code=INPUT_IMAGE_PRIVACY_ERROR_CODE,
            provider_code=code or INPUT_IMAGE_PRIVACY_PROVIDER_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    if http_status in {403, 404} or any(
        marker in normalized
        for marker in ("provideraccessdenied", "providermodelunavailable", "modelnotfound")
    ):
        return NewAPIProviderError(
            PROVIDER_ACCESS_PUBLIC_MESSAGE,
            error_code=PROVIDER_ACCESS_ERROR_CODE,
            provider_code=code or PROVIDER_ACCESS_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )

    if http_status in {400, 408, 409, 422}:
        return NewAPIProviderError(
            PROVIDER_REQUEST_PUBLIC_MESSAGE,
            error_code=PROVIDER_REQUEST_ERROR_CODE,
            provider_code=code or PROVIDER_REQUEST_ERROR_CODE,
            provider_message=message,
            http_status=http_status,
            request_id=request_id,
            phase=phase,
            provider_task_id=provider_task_id,
        )
    return None


def _request_id_from_text(value: str) -> str:
    """Extract a provider request id from a bounded human-readable message."""

    match = re.search(
        r"\brequest[\s_-]*id\s*[:=]\s*([A-Za-z0-9._:-]{6,200})",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    return redact_newapi_secrets(match.group(1))[:200] if match else ""


def _provider_retries_exhausted(response_or_error: object) -> bool:
    """Return whether the managed gateway already exhausted upstream retries."""

    response = getattr(response_or_error, "response", None)
    if response is None:
        response = response_or_error
    headers = getattr(response, "headers", None) or {}
    value = headers.get(PROVIDER_RETRY_EXHAUSTED_HEADER)
    if value is None:
        value = headers.get(PROVIDER_RETRY_EXHAUSTED_HEADER.casefold())
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def normalize_newapi_base_url(value: Optional[str]) -> str:
    """Return a New API root ending in ``/v1``.

    HTTPS is required for remote hosts so credentials cannot accidentally be
    transmitted in plaintext.  Plain HTTP remains available for loopback
    development servers.
    """

    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise RuntimeError("必须配置 NEWAPI_BASE_URL")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("NEWAPI_BASE_URL 必须是完整的 HTTP(S) 地址")
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("除本机回环地址外，NEWAPI_BASE_URL 必须使用 HTTPS")
    if not raw.endswith("/v1"):
        raw = f"{raw}/v1"
    return raw


def _base_url() -> str:
    from ..apps.hybrid.provider import (
        hybrid_mode_enabled,
        provider_gateway_base_url,
    )

    if hybrid_mode_enabled():
        return provider_gateway_base_url()
    return normalize_newapi_base_url(os.getenv("NEWAPI_BASE_URL"))


def normalize_newapi_image_size(value: Optional[str]) -> str:
    normalized = str(value or "1024x1024").strip().lower().replace("*", "x")
    try:
        return IMAGE_SIZE_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError("GPT Image 2 尺寸必须是 1024x1024、1024x1536 或 1536x1024") from exc


def newapi_image_timeout_seconds(value: Optional[str] = None) -> float:
    raw = value if value is not None else os.getenv("NEWAPI_IMAGE_TIMEOUT_SECONDS", "")
    try:
        timeout = float(raw or DEFAULT_IMAGE_REQUEST_TIMEOUT_SECONDS)
    except (TypeError, ValueError) as exc:
        raise ValueError("NEWAPI_IMAGE_TIMEOUT_SECONDS 必须是数字") from exc
    if not 60 <= timeout <= 1800:
        raise ValueError("NEWAPI_IMAGE_TIMEOUT_SECONDS 必须在 60 到 1800 秒之间")
    return timeout


def newapi_image_configured(model_id: Optional[str] = None) -> bool:
    try:
        selected = model_id or get_selected_model(IMAGE)
        from ..apps.hybrid.provider import hybrid_mode_enabled, provider_gateway_token

        if hybrid_mode_enabled():
            provider_gateway_token()
        else:
            resolve_model_api_key(selected, IMAGE)
        _base_url()
        return True
    except (RuntimeError, ValueError):
        return False


def newapi_video_configured(model_id: Optional[str] = None) -> bool:
    try:
        selected = model_id or get_selected_model(VIDEO)
        from ..apps.hybrid.provider import hybrid_mode_enabled, provider_gateway_token

        if hybrid_mode_enabled():
            provider_gateway_token()
        else:
            resolve_model_api_key(selected, VIDEO)
        _base_url()
        return True
    except (RuntimeError, ValueError):
        return False


def _auth_headers(model_id: str, capability: str) -> Dict[str, str]:
    from ..apps.hybrid.provider import hybrid_mode_enabled, provider_gateway_token

    if hybrid_mode_enabled():
        # The token belongs to the request-bound employee. Company provider
        # credentials never exist in the desktop process.
        return {"Authorization": f"Bearer {provider_gateway_token()}"}
    return {"Authorization": f"Bearer {resolve_model_api_key(model_id, capability)}"}


def _response_error(response: requests.Response) -> str:
    message = ""
    code = ""
    try:
        payload = response.json()
        code, message = _extract_provider_error(payload)
        if not message and not isinstance(payload, (dict, list)):
            message = str(payload or "")
    except Exception:
        message = getattr(response, "text", "") or ""
    message = redact_newapi_secrets(" ".join(message.split()))[:500]
    code = redact_newapi_secrets(" ".join(code.split()))[:100]
    request_id = _response_request_id(response)
    details = [f"HTTP {response.status_code}"]
    if code:
        details.append(code)
    if message:
        details.append(message)
    if request_id:
        details.append(f"request id {request_id}")
    return ": ".join(details)


def _safe_pre_submission_transport_error(exc: requests.RequestException) -> bool:
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ProxyError,
            requests.exceptions.SSLError,
        ),
    ):
        return True
    if not isinstance(exc, requests.exceptions.ConnectionError):
        return False
    normalized = str(exc).casefold()
    return any(
        marker in normalized
        for marker in (
            "failed to establish a new connection",
            "name resolution",
            "nodename nor servname provided",
            "connection refused",
            "network is unreachable",
        )
    )


def _rewind_request_files(kwargs: Dict[str, Any]) -> None:
    """Reset multipart file streams before an idempotent gateway replay."""

    for _field_name, value in kwargs.get("files") or []:
        file_value = value[1] if isinstance(value, tuple) and len(value) >= 2 else None
        seek = getattr(file_value, "seek", None)
        if callable(seek):
            seek(0)


def _managed_session_was_rejected(response: requests.Response) -> bool:
    """Distinguish an expired employee bearer from an upstream provider 401."""

    if response.status_code != 401:
        return False
    try:
        provider_code, _message = _extract_provider_error(response.json())
    except Exception:
        provider_code = ""
    # Gateway-wrapped upstream authentication failures always carry a stable
    # provider code. The control plane's own authentication dependency returns
    # only a bounded detail string.
    return not provider_code


def _request(
    method: str,
    url: str,
    *,
    max_attempts: Optional[int] = None,
    phase: str = "request",
    **kwargs,
) -> requests.Response:
    """Issue a request with bounded, idempotency-aware retries.

    Managed submissions reuse one idempotency key while retrying failures that
    happened before a connection was established. When the control plane
    explicitly reports that the provider never accepted a request and its
    reservation was refunded, the next attempt uses a fresh key. Managed image
    endpoints also support bounded same-key recovery after an ambiguous response
    failure: the control plane either reports the original request as still
    reserved or replays its encrypted cached result, so this path cannot create
    a second provider charge. GET polling and downloads retain bounded transient
    retries.
    """

    normalized_method = method.upper()
    from ..apps.hybrid.provider import hybrid_mode_enabled

    hybrid = hybrid_mode_enabled()
    safe_method = normalized_method in {"GET", "HEAD", "OPTIONS"}
    hybrid_submission = hybrid and not safe_method
    recoverable_image_submission = hybrid_submission and phase == "image submission"
    if hybrid:
        # The account gateway must stream results itself. Never carry an
        # employee bearer, request body, or idempotency key across a redirect.
        kwargs["allow_redirects"] = False
    if hybrid_submission:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
        if recoverable_image_submission:
            # Base64 image JSON is already compressed poorly and can be several
            # megabytes. Identity encoding keeps a deterministic Content-Length
            # across Caddy/VPN paths instead of relying on a long-lived chunked
            # gzip response.
            headers.setdefault("Accept-Encoding", "identity")
        kwargs["headers"] = headers
    attempts = (
        max_attempts
        if max_attempts is not None
        else (4 if hybrid_submission else (3 if safe_method else 1))
    )
    if attempts < 1:
        raise ValueError("max_attempts 必须至少为 1")
    last_response: Optional[requests.Response] = None
    last_exception: Optional[requests.RequestException] = None
    recovering_image_response = False
    for attempt in range(attempts):
        request_kwargs = kwargs
        if recovering_image_response:
            request_kwargs = dict(kwargs)
            configured_timeout = kwargs.get("timeout")
            request_kwargs["timeout"] = (
                min(float(configured_timeout), IMAGE_REPLAY_TIMEOUT_SECONDS)
                if isinstance(configured_timeout, (int, float))
                else IMAGE_REPLAY_TIMEOUT_SECONDS
            )
            _rewind_request_files(request_kwargs)
        try:
            response = requests.request(normalized_method, url, **request_kwargs)
        except requests.RequestException as exc:
            last_exception = exc
            safe_pre_submission_failure = (
                hybrid_submission and _safe_pre_submission_transport_error(exc)
            )
            retryable_transport = safe_method or safe_pre_submission_failure
            ambiguous_image_failure = (
                recoverable_image_submission and not safe_pre_submission_failure
            )
            if ambiguous_image_failure and attempt < attempts - 1:
                recovering_image_response = True
                logger.warning(
                    "Managed image response was interrupted; recovering the exact "
                    "result with the same idempotency key attempt=%d/%d error=%s",
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                )
                time.sleep(min(2**attempt, 5))
                continue
            if ambiguous_image_failure:
                raise NewAPIProviderError(
                    PROVIDER_OUTCOME_AMBIGUOUS_PUBLIC_MESSAGE,
                    error_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                    provider_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                    provider_message=type(exc).__name__,
                    phase=phase,
                ) from exc
            if not retryable_transport or attempt == attempts - 1:
                if hybrid_submission and _safe_pre_submission_transport_error(exc):
                    raise NewAPIProviderError(
                        PROVIDER_CONNECTION_PUBLIC_MESSAGE,
                        error_code=PROVIDER_CONNECTION_ERROR_CODE,
                        provider_code=PROVIDER_CONNECTION_ERROR_CODE,
                        provider_message=type(exc).__name__,
                        phase=phase,
                    ) from exc
                message = redact_newapi_secrets(str(exc))[:500]
                raise RuntimeError(f"New API request failed: {message}") from exc
            time.sleep(min(2**attempt, 5))
            continue
        last_response = response
        if 200 <= response.status_code < 300:
            if hybrid_submission and response.status_code == 202:
                try:
                    replay = response.json()
                except Exception:
                    replay = None
                usage = replay.get("usage_request") if isinstance(replay, dict) else None
                if isinstance(usage, dict) and replay.get("idempotent_replay") is True:
                    replay_status = str(usage.get("status") or "")
                    replay_error = str(usage.get("error_code") or "")
                    if (
                        recoverable_image_submission
                        and replay_status == "reserved"
                        and attempt < attempts - 1
                    ):
                        recovering_image_response = True
                        time.sleep(min(2**attempt, 5))
                        continue
                    if (
                        replay_status == "refunded"
                        and replay_error == "provider_connect_failed"
                        and not _provider_retries_exhausted(response)
                        and attempt < attempts - 1
                    ):
                        kwargs["headers"]["Idempotency-Key"] = uuid.uuid4().hex
                        recovering_image_response = False
                        time.sleep(min(2**attempt, 5))
                        continue
                    classified = _classified_provider_error(
                        replay_error or PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                        "idempotent replay did not include a recoverable provider result",
                        http_status=response.status_code,
                        request_id=_response_request_id(response),
                        phase=phase,
                    )
                    if classified is not None:
                        classified.retry_exhausted = _provider_retries_exhausted(response)
                        raise classified
            return response
        if hybrid and _managed_session_was_rejected(response) and attempt < attempts - 1:
            from ..apps.hybrid.provider import refresh_provider_gateway_token

            close = getattr(response, "close", None)
            if callable(close):
                close()
            kwargs["headers"]["Authorization"] = f"Bearer {refresh_provider_gateway_token()}"
            _rewind_request_files(kwargs)
            logger.warning(
                "Managed gateway session expired during a request; refreshed "
                "the employee session and retained the same idempotency key"
            )
            time.sleep(min(2**attempt, 5))
            continue
        if hybrid_submission and attempt < attempts - 1:
            try:
                provider_payload = response.json()
                provider_code, provider_message = _extract_provider_error(provider_payload)
            except Exception:
                provider_code, provider_message = "", getattr(response, "text", "") or ""
            classified = _classified_provider_error(
                provider_code,
                provider_message,
                http_status=response.status_code,
                request_id=(
                    _response_request_id(response) or _request_id_from_text(provider_message)
                ),
                phase=phase,
            )
            if classified is not None:
                classified.retry_exhausted = _provider_retries_exhausted(response)
            if (
                classified is not None
                and classified.error_code == PROVIDER_CONNECTION_ERROR_CODE
                and not classified.retry_exhausted
            ):
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                kwargs["headers"]["Idempotency-Key"] = uuid.uuid4().hex
                recovering_image_response = False
                _rewind_request_files(kwargs)
                logger.warning(
                    "Managed provider submission was explicitly rejected before "
                    "acceptance and refunded; retrying with a fresh idempotency key "
                    "attempt=%d/%d phase=%s",
                    attempt + 1,
                    attempts,
                    phase,
                )
                time.sleep(min(2**attempt, 5))
                continue
        retryable_response = safe_method and response.status_code in RETRYABLE_STATUS_CODES
        if not retryable_response or attempt == attempts - 1:
            try:
                provider_payload = response.json()
                provider_code, provider_message = _extract_provider_error(provider_payload)
            except Exception:
                provider_code, provider_message = "", getattr(response, "text", "") or ""
            classified = _classified_provider_error(
                provider_code,
                provider_message,
                http_status=response.status_code,
                request_id=(
                    _response_request_id(response) or _request_id_from_text(provider_message)
                ),
                phase=phase,
            )
            if classified is not None:
                classified.retry_exhausted = _provider_retries_exhausted(response)
                raise classified
            raise RuntimeError(f"New API request failed: {_response_error(response)}")
        retry_after = response.headers.get("Retry-After")
        try:
            delay = min(float(retry_after), 10.0) if retry_after else min(2**attempt, 5)
        except (TypeError, ValueError):
            delay = min(2**attempt, 5)
        time.sleep(delay)
    raise RuntimeError(
        f"New API request failed: {_response_error(last_response)}"
        if last_response is not None
        else (
            f"New API request failed: {redact_newapi_secrets(str(last_exception))[:500]}"
            if last_exception is not None
            else "New API request failed"
        )
    )


def _response_request_id(response: requests.Response) -> str:
    headers = getattr(response, "headers", {}) or {}
    for name in ("x-request-id", "x-requestid", "request-id", "trace-id"):
        value = headers.get(name) or headers.get(name.title())
        if value:
            return redact_newapi_secrets(str(value))[:200]
    return ""


def _parse_json_object(response: requests.Response, operation: str) -> Dict[str, Any]:
    """Parse a provider response with actionable, bounded diagnostics."""

    try:
        payload = response.json()
    except Exception as exc:
        headers = getattr(response, "headers", {}) or {}
        content_type = str(headers.get("Content-Type") or headers.get("content-type") or "unknown")
        request_id = _response_request_id(response)
        excerpt = redact_newapi_secrets(" ".join(str(getattr(response, "text", "") or "").split()))[
            :200
        ]
        context = [f"HTTP {response.status_code}", f"content type {content_type}"]
        if request_id:
            context.append(f"request id {request_id}")
        if excerpt:
            context.append(f"body {excerpt}")
        raise RuntimeError(
            f"New API {operation} returned invalid JSON ({'; '.join(context)})"
        ) from exc
    if not isinstance(payload, dict):
        request_id = _response_request_id(response)
        suffix = f", request id {request_id}" if request_id else ""
        raise RuntimeError(
            f"New API {operation} returned a non-object JSON response "
            f"(HTTP {response.status_code}{suffix})"
        )
    return payload


def _ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)


def _remove_partial(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _server_mode_enabled() -> bool:
    from ..apps.server.config import server_mode_enabled

    return server_mode_enabled()


def _safe_output_path(path: str) -> str:
    if not _server_mode_enabled():
        return path
    from ..utils.media_security import (
        current_workspace_output_root,
        resolve_workspace_media_path,
    )

    return resolve_workspace_media_path(current_workspace_output_root(), path, require_file=False)


def _server_reference(reference: str) -> str:
    """Normalize a user-controlled media reference in server mode."""

    from ..utils.media_security import (
        current_workspace_output_root,
        decode_image_data_url,
        resolve_workspace_media_path,
        validate_remote_media_url,
    )
    from ..utils.oss_utils import OSSImageUploader, is_object_key

    normalized = str(reference or "").strip()
    if not normalized:
        raise ValueError("Media reference is empty")
    if is_object_key(normalized):
        normalized = OSSImageUploader().sign_url_for_api(normalized)
        if not normalized:
            raise ValueError("Object reference is unavailable for this workspace")
    if normalized.startswith("data:"):
        decode_image_data_url(normalized)
        return normalized
    if normalized.startswith(("http://", "https://")):
        return validate_remote_media_url(normalized)
    return resolve_workspace_media_path(current_workspace_output_root(), normalized)


def _save_streaming_result(
    response: requests.Response,
    output_path: str,
    media_label: str,
) -> None:
    """Persist a streamed provider result and reject empty 2xx bodies."""
    from ..utils.media_security import image_limit_bytes, media_limit_bytes

    output_path = _safe_output_path(output_path)
    max_bytes = image_limit_bytes() if media_label == "image" else media_limit_bytes()
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
    content_type = content_type.strip().lower()
    allowed_content_types = (
        ("image/",) if media_label == "image" else ("video/", "application/octet-stream")
    )
    if content_type and not any(
        content_type.startswith(prefix) for prefix in allowed_content_types
    ):
        raise RuntimeError(f"New API returned an unexpected {media_label} content type")
    advertised = response.headers.get("Content-Length")
    if advertised:
        try:
            advertised_size = int(advertised)
        except ValueError as exc:
            raise RuntimeError(f"New API returned an invalid {media_label} content length") from exc
        if advertised_size < 0 or advertised_size > max_bytes:
            raise RuntimeError(f"New API {media_label} download exceeds the configured limit")
    _ensure_parent(output_path)
    bytes_written = 0
    try:
        with open(output_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        raise RuntimeError(
                            f"New API {media_label} download exceeds the configured limit"
                        )
                    handle.write(chunk)
    except Exception:
        _remove_partial(output_path)
        raise
    if bytes_written == 0:
        _remove_partial(output_path)
        raise RuntimeError(f"New API returned an empty {media_label} download")


def _save_image_result(result: Dict[str, Any], output_path: str) -> None:
    from ..utils.media_security import image_limit_bytes

    output_path = _safe_output_path(output_path)
    data = result.get("data") if isinstance(result, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    if not isinstance(first, dict):
        raise RuntimeError("New API image response did not contain data[0]")

    _ensure_parent(output_path)
    encoded = first.get("b64_json")
    if encoded:
        try:
            limit = image_limit_bytes()
            if len(encoded) > ((limit + 2) // 3) * 4:
                raise ValueError("image payload exceeds the configured limit")
            payload = base64.b64decode(encoded, validate=True)
            if not payload:
                raise ValueError("empty image payload")
            if len(payload) > limit:
                raise ValueError("image payload exceeds the configured limit")
            with open(output_path, "wb") as handle:
                handle.write(payload)
        except (ValueError, TypeError) as exc:
            _remove_partial(output_path)
            raise RuntimeError("New API returned invalid base64 image data") from exc
        return

    url = first.get("url")
    if not url:
        raise RuntimeError("New API image response contained neither url nor b64_json")
    if _server_mode_enabled():
        from ..utils.media_security import download_remote_media, image_limit_bytes

        download_remote_media(
            str(url),
            output_path,
            max_bytes=image_limit_bytes(),
            allowed_content_prefixes=("image/",),
        )
        return
    response = _request("GET", str(url), timeout=120, stream=True)
    _save_streaming_result(response, output_path, "image")


def _validated_image_result(response: requests.Response) -> Dict[str, Any]:
    payload = _parse_json_object(response, "image submission")
    data = payload.get("data")
    first = data[0] if isinstance(data, list) and data else None
    if isinstance(first, dict) and (first.get("b64_json") or first.get("url")):
        return payload

    code, message = _extract_provider_error(payload)
    normalized_code = str(code).strip().casefold()
    if message or (normalized_code and normalized_code not in PROVIDER_SUCCESS_CODES):
        classified = _classified_provider_error(
            code,
            message,
            http_status=response.status_code,
            request_id=(_response_request_id(response) or _request_id_from_text(message)),
            phase="image submission",
        )
        if classified is not None:
            raise classified
        raise NewAPIProviderError(
            PROVIDER_REQUEST_PUBLIC_MESSAGE,
            error_code=PROVIDER_REQUEST_ERROR_CODE,
            provider_code=code or PROVIDER_REQUEST_ERROR_CODE,
            provider_message=message,
            http_status=response.status_code,
            request_id=_response_request_id(response),
            phase="image submission",
        )
    return payload


def _open_image_ref(reference: str, stack: ExitStack) -> Tuple[str, Any, str]:
    """Open a local, remote, or data-URL image for a multipart request."""

    if _server_mode_enabled():
        reference = _server_reference(reference)

    if reference.startswith("data:"):
        if _server_mode_enabled():
            from ..utils.media_security import decode_image_data_url

            mime, payload = decode_image_data_url(reference)
        else:
            try:
                header, encoded = reference.split(",", 1)
                mime = header[5:].split(";", 1)[0] or "image/png"
                payload = base64.b64decode(encoded)
            except Exception as exc:
                raise ValueError("Invalid image data URL") from exc
        file_obj = stack.enter_context(io.BytesIO(payload))
        extension = mimetypes.guess_extension(mime) or ".png"
        return f"reference{extension}", file_obj, mime

    if reference.startswith(("http://", "https://")):
        if _server_mode_enabled():
            from ..utils.media_security import download_remote_media, image_limit_bytes

            descriptor, temporary_path = tempfile.mkstemp(prefix="enmotion-ref-")
            os.close(descriptor)
            stack.callback(_remove_partial, temporary_path)
            download_remote_media(
                reference,
                temporary_path,
                max_bytes=image_limit_bytes(),
                allowed_content_prefixes=("image/",),
            )
            file_obj = stack.enter_context(open(temporary_path, "rb"))
            name = os.path.basename(urlparse(reference).path) or "reference.png"
            mime = mimetypes.guess_type(name)[0] or "image/png"
            return name, file_obj, mime
        response = _request("GET", reference, timeout=120)
        payload = response.content
        file_obj = stack.enter_context(io.BytesIO(payload))
        name = os.path.basename(urlparse(reference).path) or "reference.png"
        mime = response.headers.get("Content-Type", "").split(";", 1)[0]
        return name, file_obj, mime or mimetypes.guess_type(name)[0] or "image/png"

    if not os.path.isfile(reference):
        raise ValueError(f"Reference image not found: {reference}")
    file_obj = stack.enter_context(open(reference, "rb"))
    name = os.path.basename(reference)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return name, file_obj, mime


def _media_input(reference: Optional[str]) -> Optional[str]:
    if not reference:
        return None
    if _server_mode_enabled():
        from ..utils.media_security import decode_image_data_url, image_limit_bytes

        reference = _server_reference(reference)
        if reference.startswith("data:"):
            decode_image_data_url(reference)
            return reference
        if reference.startswith(("http://", "https://")):
            return reference
        if os.path.getsize(reference) > image_limit_bytes():
            raise ValueError("Reference image exceeds the configured limit")
        # The authenticated /files route is intentionally unreadable by an
        # external provider.  Send a short-lived URL for only this image
        # instead of embedding a multi-megabyte base64 value in request JSON.
        from ..apps.server.config import ServerSettings
        from ..apps.server.provider_media import create_provider_media_url

        settings = ServerSettings.from_env()
        if settings.public_base_url:
            return create_provider_media_url(reference, settings=settings)
        # A loopback-only development server has no provider-reachable public
        # origin. Retain the bounded data URL there; any externally reachable
        # server-mode environment must set ENMOTION_PUBLIC_BASE_URL explicitly.
    if reference.startswith(("http://", "https://", "data:")):
        return reference
    if not os.path.isfile(reference):
        return reference
    mime = mimetypes.guess_type(reference)[0] or "application/octet-stream"
    with open(reference, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _video_dimensions(resolution: str, aspect_ratio: str) -> Tuple[int, int]:
    long_edge = 1920 if str(resolution).lower() in {"1080p", "1080"} else 1280
    short_edge = 1080 if long_edge == 1920 else 720
    if aspect_ratio in {"9:16", "3:4"}:
        return short_edge, long_edge
    if aspect_ratio == "1:1":
        return short_edge, short_edge
    return long_edge, short_edge


def _walk_provider_payload(value: Any):
    """Yield every mapping in an arbitrarily nested provider response."""
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_provider_payload(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_provider_payload(nested)
    elif isinstance(value, str):
        # Some relays serialize the upstream JSON envelope inside their own
        # ``message`` field (for example ``fail_to_fetch_task``). Parse that
        # bounded wrapper so callers receive the real upstream code/message.
        candidate = value.strip()
        if len(candidate) <= 20_000 and candidate.startswith(("{", "[")):
            try:
                decoded = json.loads(candidate)
            except (TypeError, ValueError):
                return
            if isinstance(decoded, (dict, list)):
                yield from _walk_provider_payload(decoded)


def _extract_provider_error(payload: Any) -> Tuple[str, str]:
    """Extract the deepest useful error code/message from relay wrappers."""

    fallback_code = ""
    fallback_message = ""
    for candidate in _walk_provider_payload(payload):
        code_value = candidate.get("code")
        if not code_value and any(
            candidate.get(key) for key in ("message", "detail", "fail_reason")
        ):
            # A bare nested `type` is commonly a content discriminator such as
            # "text" or "image_url", not an error code.
            code_value = candidate.get("type")
        normalized_code = str(code_value or "").strip()
        if normalized_code.lower() in PROVIDER_SUCCESS_CODES:
            normalized_code = ""
        message_value = (
            candidate.get("message") or candidate.get("detail") or candidate.get("fail_reason")
        )
        if normalized_code and not fallback_code:
            fallback_code = normalized_code
        if message_value and not fallback_message:
            fallback_message = str(message_value)
        error = candidate.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or "").strip()
            if code.lower() in PROVIDER_SUCCESS_CODES:
                code = ""
            code = code or fallback_code
            message = str(error.get("message") or error.get("detail") or fallback_message or "")
            if code or message:
                return code, message
        elif error:
            return fallback_code, str(error)
    return fallback_code, fallback_message


def _usable_video_url(value: Any, excluded_urls: frozenset[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized.startswith(("https://", "http://", "data:video/")):
        return None
    if normalized in excluded_urls:
        return None
    return normalized


def _extract_video_url(
    payload: Dict[str, Any],
    *,
    excluded_urls: Optional[set[str]] = None,
) -> Optional[str]:
    """Extract only an output video URL, never an echoed input-image URL.

    Relay responses may echo ``metadata.content[].image_url.url``. Treating
    every nested key named ``url`` as an output can therefore make an I2V job
    download its source PNG as the completed video. Prefer video-specific
    fields everywhere and accept a generic URL only in a known terminal-result
    container.
    """

    excluded = frozenset(str(value).strip() for value in (excluded_urls or set()))
    candidates = list(_walk_provider_payload(payload))

    for candidate in candidates:
        for key in ("video_url", "output_url", "result_url", "download_url"):
            result = _usable_video_url(candidate.get(key), excluded)
            if result:
                return result

    for candidate in candidates:
        status = str(candidate.get("status") or "").strip().lower()
        if status not in VIDEO_SUCCESS_STATUSES:
            continue
        result = _usable_video_url(candidate.get("url"), excluded)
        if result:
            return result
        for container_name in ("metadata", "output", "result", "content"):
            container = candidate.get(container_name)
            if not isinstance(container, dict):
                continue
            for key in (
                "video_url",
                "output_url",
                "result_url",
                "download_url",
                "url",
            ):
                result = _usable_video_url(container.get(key), excluded)
                if result:
                    return result

        # Moyu's legacy wrapper can place the completed URL in the
        # unfortunately named fail_reason field. It is media only when the
        # same object explicitly reports a successful task state.
        result = _usable_video_url(candidate.get("fail_reason"), excluded)
        if result:
            return result
    return None


def _extract_video_status(payload: Dict[str, Any]) -> str:
    statuses: List[str] = []
    for candidate in _walk_provider_payload(payload):
        value = candidate.get("status")
        if value is not None and str(value).strip():
            statuses.append(str(value).strip().lower())
    # Some relays use an outer SUCCESS merely to mean that the poll request
    # itself succeeded while a nested task is still processing or has failed.
    # A terminal failure must win, then an active task state, then success.
    for known_states in (
        VIDEO_FAILURE_STATUSES,
        VIDEO_ACTIVE_STATUSES,
        VIDEO_SUCCESS_STATUSES,
    ):
        for status in reversed(statuses):
            if status in known_states:
                return status
    return statuses[-1] if statuses else ""


def _extract_video_task_id(payload: Dict[str, Any]) -> Optional[str]:
    for candidate in _walk_provider_payload(payload):
        value = candidate.get("task_id")
        if value is not None and str(value).strip():
            return redact_newapi_secrets(str(value).strip())[:200]
    for candidate in _walk_provider_payload(payload):
        value = candidate.get("id")
        if value is not None and str(value).strip():
            return redact_newapi_secrets(str(value).strip())[:200]
    return None


def _extract_provider_progress(payload: Dict[str, Any]) -> Optional[int]:
    """Return a provider-reported percentage without synthesizing one."""

    for candidate in reversed(list(_walk_provider_payload(payload))):
        for key in ("progress_percent", "percentage", "percent", "progress"):
            value = candidate.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numeric = float(value)
            elif isinstance(value, str):
                cleaned = value.strip().removesuffix("%")
                try:
                    numeric = float(cleaned)
                except ValueError:
                    continue
            else:
                continue
            if 0 <= numeric <= 1:
                numeric *= 100
            if 0 <= numeric <= 100:
                return round(numeric)
    return None


def _save_video_data_url(reference: str, output_path: str) -> None:
    """Decode one bounded base64 video returned by a legacy relay."""

    from ..utils.media_security import media_limit_bytes

    try:
        header, encoded = reference.split(",", 1)
        normalized_header = header.lower()
        mime = normalized_header.removeprefix("data:").removesuffix(";base64")
        if not normalized_header.endswith(";base64") or not mime.startswith("video/"):
            raise ValueError("unsupported video data URL")
        limit = media_limit_bytes()
        if len(encoded) > ((limit + 2) // 3) * 4:
            raise ValueError("video data URL exceeds the configured limit")
        payload = base64.b64decode(encoded, validate=True)
        if not payload or len(payload) > limit:
            raise ValueError("video data URL is empty or too large")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("New API returned an invalid video data URL") from exc

    output_path = _safe_output_path(output_path)
    _ensure_parent(output_path)
    try:
        with open(output_path, "wb") as handle:
            handle.write(payload)
    except Exception:
        _remove_partial(output_path)
        raise


def _download_video_result(
    *,
    base_url: str,
    task_id: str,
    model: str,
    output_path: str,
    direct_url: Optional[str],
) -> None:
    """Download a completed task through New API, with a safe legacy fallback.

    Current New API releases expose an authenticated content endpoint that
    resolves their stored provider URL and applies gateway-side SSRF checks.
    Older relays may not provide it, so a narrowly allowlisted direct result
    URL remains a compatibility fallback.
    """

    content_url = f"{base_url}/videos/{quote(str(task_id), safe='')}/content"
    proxy_error: Optional[Exception] = None
    try:
        response = _request(
            "GET",
            content_url,
            headers=_auth_headers(model, VIDEO),
            timeout=180,
            stream=True,
        )
        _save_streaming_result(response, output_path, "video")
        return
    except Exception as exc:
        proxy_error = exc
        _remove_partial(output_path)
        logger.warning(
            "New API content proxy failed for video task %s: %s",
            task_id,
            redact_newapi_secrets(str(exc))[:500],
        )

    if not direct_url:
        detail = redact_newapi_secrets(str(proxy_error or "unknown error"))[:500]
        raise RuntimeError(
            f"New API completed video task {task_id}, but its content download failed: {detail}"
        ) from proxy_error

    try:
        if direct_url.startswith("data:video/"):
            _save_video_data_url(direct_url, output_path)
            return
        if _server_mode_enabled():
            from ..utils.media_security import download_remote_media, media_limit_bytes

            download_remote_media(
                direct_url,
                output_path,
                max_bytes=media_limit_bytes(),
                allowed_content_prefixes=("video/", "application/octet-stream"),
            )
            return
        response = _request("GET", direct_url, timeout=180, stream=True)
        _save_streaming_result(response, output_path, "video")
    except Exception as exc:
        _remove_partial(output_path)
        proxy_detail = redact_newapi_secrets(str(proxy_error or "unknown error"))[:250]
        direct_detail = redact_newapi_secrets(str(exc))[:250]
        raise RuntimeError(
            "New API video download failed through both the authenticated "
            f"content endpoint ({proxy_detail}) and direct fallback ({direct_detail})"
        ) from exc


class NewAPIImageModel(ImageGenModel):
    """GPT Image generation/editing through a New API deployment."""

    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        start = time.time()
        report_generation_progress(
            "validating_request",
            "正在检查图像模型和请求参数",
            10,
        )
        output_path = _safe_output_path(output_path)
        base_url = _base_url()
        model = (
            kwargs.pop("model_id", None)
            or kwargs.pop("model_name", None)
            or get_selected_model(IMAGE)
        )
        # Resolve the exact model/key pair before opening files or making a
        # network request. This is the final fail-closed routing boundary.
        headers = _auth_headers(model, IMAGE)
        size = normalize_newapi_image_size(kwargs.get("size"))
        quality = kwargs.get("quality", "high")
        request_timeout = newapi_image_timeout_seconds()
        refs: List[str] = []
        if kwargs.get("ref_image_path"):
            refs.append(kwargs["ref_image_path"])
        refs.extend(kwargs.get("ref_image_paths") or [])
        report_generation_progress(
            "preparing_inputs",
            "正在准备图像生成素材",
            20,
        )

        if refs:
            with ExitStack() as stack:
                files = []
                for reference in refs[:16]:
                    name, file_obj, mime = _open_image_ref(reference, stack)
                    files.append(("image[]", (name, file_obj, mime)))
                response = _request(
                    "POST",
                    f"{base_url}/images/edits",
                    phase="image submission",
                    headers=headers,
                    files=files,
                    data={
                        "model": model,
                        "prompt": prompt,
                        "n": "1",
                        "size": size,
                        "quality": quality,
                    },
                    timeout=request_timeout,
                )
        else:
            response = _request(
                "POST",
                f"{base_url}/images/generations",
                phase="image submission",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": model,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "quality": quality,
                },
                timeout=request_timeout,
            )

        report_generation_progress(
            "submitted_to_provider",
            "图像请求已由服务商接收",
            30,
        )
        report_generation_progress(
            "downloading_output",
            "图像已生成，正在获取结果",
            75,
        )
        _save_image_result(_validated_image_result(response), output_path)
        report_generation_progress(
            "persisting_media",
            "正在将生成的图像保存到工作区",
            88,
        )
        return output_path, time.time() - start


_VIDEO_PROVIDER_LOCK = threading.Lock()


def _serialize_video_model_calls(function):
    """Prevent EnMotion surfaces and model tiers from oversubscribing video."""

    @wraps(function)
    def wrapped(self, prompt, output_path, img_url=None, img_path=None, **kwargs):
        if _VIDEO_PROVIDER_LOCK.locked():
            report_generation_progress(
                "waiting_for_video_slot",
                "视频服务已有任务在处理，当前任务正在本地排队",
                24,
                estimated=True,
            )
        with _VIDEO_PROVIDER_LOCK:
            return function(
                self,
                prompt,
                output_path,
                img_url=img_url,
                img_path=img_path,
                **kwargs,
            )

    return wrapped


def _video_concurrency_wait_seconds() -> float:
    raw = os.getenv("NEWAPI_VIDEO_CONCURRENCY_WAIT_SECONDS", "3600")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("NEWAPI_VIDEO_CONCURRENCY_WAIT_SECONDS 必须是数字") from exc
    if not 0 <= value <= 3600:
        raise ValueError("NEWAPI_VIDEO_CONCURRENCY_WAIT_SECONDS 必须在 0 到 3600 秒之间")
    return value


def _submit_video_request(
    url: str,
    *,
    headers: Dict[str, str],
    body: Dict[str, Any],
) -> requests.Response:
    """Wait out an explicit provider concurrency rejection without duplicating work."""

    wait_seconds = _video_concurrency_wait_seconds()
    deadline: float | None = None
    while True:
        try:
            return _request(
                "POST",
                url,
                phase="video submission",
                headers=headers,
                json=body,
                timeout=120,
            )
        except NewAPIProviderError as exc:
            if exc.error_code != PROVIDER_CONCURRENCY_ERROR_CODE:
                raise
            now = time.monotonic()
            if deadline is None:
                deadline = now + wait_seconds
            remaining = deadline - now
            if remaining <= 0:
                raise
            delay = min(15.0, remaining)
            logger.info(
                "Video provider concurrency slot unavailable; retrying model=%s in %.1fs",
                body.get("model"),
                delay,
            )
            report_generation_progress(
                "waiting_for_provider_slot",
                "视频服务商并发名额已满，EnMotion 正在排队等待",
                26,
                estimated=True,
            )
            time.sleep(delay)


class NewAPIVideoModel(VideoGenModel):
    """Seedance-compatible async video generation through New API."""

    @_serialize_video_model_calls
    def generate(
        self,
        prompt: str,
        output_path: str,
        img_url: Optional[str] = None,
        img_path: Optional[str] = None,
        **kwargs,
    ) -> Tuple[str, float]:
        start = time.time()
        report_generation_progress(
            "validating_request",
            "正在检查视频模型和请求参数",
            10,
        )
        output_path = _safe_output_path(output_path)
        model = (
            kwargs.pop("model_id", None)
            or kwargs.pop("model", None)
            or kwargs.pop("model_name", None)
            or get_selected_model(VIDEO)
        )
        get_model_spec(model, VIDEO)
        duration_value = kwargs.get("duration", 5)
        if isinstance(duration_value, bool) or not isinstance(duration_value, int):
            raise ValueError("Seedance 视频时长必须是整数")
        duration = duration_value
        resolution = str(kwargs.get("resolution", "720p")).strip().lower()
        aspect_ratio = str(kwargs.get("aspect_ratio", "16:9")).strip()
        seed = kwargs.get("seed")
        generate_audio = kwargs.get("generate_audio", True)
        watermark = kwargs.get("watermark", False)
        ref_images = list(kwargs.get("ref_image_urls") or [])
        primary_reference = img_path or img_url or (ref_images[0] if ref_images else None)
        generation_mode = kwargs.get("generation_mode") or ("i2v" if primary_reference else "t2v")
        validate_model_for_mode(model, generation_mode)
        if not 4 <= duration <= 15:
            raise ValueError("Seedance 视频时长必须为 4 至 15 秒")
        if resolution not in {"720p", "1080p"}:
            raise ValueError("Seedance 视频分辨率必须是 720p 或 1080p")
        if aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise ValueError("Seedance 视频画面比例必须是 16:9、9:16 或 1:1")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("Seedance 视频随机种子必须是整数")
        if not isinstance(generate_audio, bool):
            raise ValueError("Seedance 视频音频设置必须是布尔值")
        if not isinstance(watermark, bool):
            raise ValueError("Seedance 视频水印设置必须是布尔值")
        if resolution == "1080p" and (
            generation_mode == "i2v" or model != "doubao-seedance-2-0-260128"
        ):
            raise ValueError("仅 Seedance 2.0 文生视频支持 1080p")
        if len(ref_images) > 1:
            raise ValueError("当前不支持使用多张参考图生成视频")
        if ref_images and (img_path or img_url):
            raise ValueError("视频生成只能使用一张输入图片")

        if generation_mode == "i2v" and not primary_reference:
            raise ValueError("图生视频需要一张输入图片")
        if generation_mode == "t2v" and primary_reference:
            raise ValueError("文生视频不能包含输入图片")

        base_url = _base_url()
        headers = {**_auth_headers(model, VIDEO), "Content-Type": "application/json"}

        report_generation_progress(
            "preparing_inputs",
            "正在准备所选片段的起始帧",
            20,
        )
        primary_image = _media_input(primary_reference)
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        if primary_image:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": primary_image},
                    "role": "first_frame",
                }
            )
        metadata: Dict[str, Any] = {
            "content": content,
            "duration": duration,
            "resolution": resolution,
            "ratio": aspect_ratio,
            "generate_audio": generate_audio,
            "watermark": watermark,
        }
        if seed is not None:
            metadata["seed"] = seed
        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "metadata": metadata,
        }
        resumed_task_id = str(kwargs.get("provider_task_id") or "").strip()
        if resumed_task_id:
            task_id = resumed_task_id
            payload: Dict[str, Any] = {"id": task_id, "status": "processing"}
            logger.info("Resuming accepted New API video task %s", task_id)
        else:
            response = _submit_video_request(
                f"{base_url}/video/generations",
                headers=headers,
                body=body,
            )
            report_generation_progress(
                "submitted_to_provider",
                "视频请求已提交给服务商",
                30,
            )
            payload = _parse_json_object(response, "video submission")
            task_id = _extract_video_task_id(payload)
            if not task_id:
                code, message = _extract_provider_error(payload)
                normalized_code = str(code).strip().lower()
                if message or (
                    normalized_code and normalized_code not in {"0", "200", "ok", "success"}
                ):
                    classified = _classified_provider_error(
                        code,
                        message,
                        http_status=response.status_code,
                        request_id=(
                            _response_request_id(response) or _request_id_from_text(message)
                        ),
                        phase="video submission",
                    )
                    if classified is not None:
                        raise classified
                    detail = ": ".join(value for value in (code, message) if value)
                    raise RuntimeError(
                        "New API video submission failed: " f"{redact_newapi_secrets(detail)[:500]}"
                    )
                raise RuntimeError("New API video response did not contain a task_id")

            callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = kwargs.get(
                "on_provider_ids"
            )
            if callback:
                request_id = _response_request_id(response)
                try:
                    callback("newapi", str(task_id), request_id)
                except Exception as exc:
                    raise NewAPIProviderError(
                        PROVIDER_OUTCOME_AMBIGUOUS_PUBLIC_MESSAGE,
                        error_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                        provider_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                        provider_message="accepted task identity could not be persisted",
                        phase="video submission",
                        provider_task_id=str(task_id),
                    ) from exc
            logger.info("New API video task %s submitted", task_id)
        report_generation_progress(
            "accepted_by_provider",
            "服务商已接收视频任务",
            36,
        )
        report_generation_progress(
            "provider_processing",
            "服务商正在渲染视频",
            _extract_provider_progress(payload),
            estimated=_extract_provider_progress(payload) is None,
        )

        poll_interval = max(float(os.getenv("NEWAPI_VIDEO_POLL_INTERVAL", "15")), 0.1)
        max_wait = max(float(os.getenv("NEWAPI_VIDEO_MAX_WAIT", "3600")), 1.0)
        deadline = time.monotonic() + max_wait
        result = payload
        last_poll_error: RuntimeError | None = None
        excluded_urls = {primary_image} if primary_image else set()
        while True:
            status = _extract_video_status(result)
            url = _extract_video_url(result, excluded_urls=excluded_urls)
            provider_progress = _extract_provider_progress(result)
            if provider_progress is not None:
                report_generation_progress(
                    "provider_processing",
                    "服务商正在渲染视频",
                    provider_progress,
                    estimated=False,
                )
            if status in VIDEO_FAILURE_STATUSES:
                code, message = _extract_provider_error(result)
                classified = _classified_provider_error(
                    code,
                    message,
                    request_id=_request_id_from_text(message),
                    phase="video processing",
                    provider_task_id=str(task_id),
                )
                if classified is not None:
                    raise classified
                error = ": ".join(value for value in (code, message) if value)
                error = error or "unknown provider error"
                raise RuntimeError(
                    f"New API video task failed: {redact_newapi_secrets(error)[:500]}"
                )
            if status in VIDEO_SUCCESS_STATUSES or (not status and url):
                break
            if time.monotonic() >= deadline:
                last_status = status or "unknown"
                detail = (
                    str(last_poll_error)
                    if last_poll_error is not None
                    else f"provider task remained {last_status} for {max_wait:g} seconds"
                )
                raise NewAPIProviderError(
                    PROVIDER_OUTCOME_AMBIGUOUS_PUBLIC_MESSAGE,
                    error_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                    provider_code=PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
                    provider_message=detail,
                    phase="video processing",
                    provider_task_id=str(task_id),
                )
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
            for parse_attempt in range(3):
                try:
                    poll = _request(
                        "GET",
                        f"{base_url}/video/generations/{task_id}",
                        phase="video processing",
                        headers=_auth_headers(model, VIDEO),
                        timeout=30,
                        max_attempts=8,
                    )
                    result = _parse_json_object(poll, "video status poll")
                    last_poll_error = None
                    break
                except NewAPIProviderError:
                    raise
                except RuntimeError as exc:
                    last_poll_error = exc
                    if parse_attempt < 2:
                        time.sleep(min(2**parse_attempt, 5))
                        continue
                    # Polling is idempotent, so a transient malformed 200 or
                    # a provider/gateway outage can be retried until the
                    # accepted task's overall deadline without resubmitting it.
                    logger.warning(
                        "New API video status temporarily unavailable task=%s error=%s",
                        task_id,
                        redact_newapi_secrets(str(exc))[:500],
                    )
                    report_generation_progress(
                        "provider_processing",
                        "服务商状态暂时不可用，EnMotion 正在继续查询已接收的任务",
                        50,
                        estimated=True,
                    )
                    result = {"id": str(task_id), "status": "processing"}
                    break

        video_url = _extract_video_url(result, excluded_urls=excluded_urls)
        report_generation_progress(
            "downloading_output",
            "正在下载已生成的视频",
            78,
        )
        _download_video_result(
            base_url=base_url,
            task_id=str(task_id),
            model=model,
            output_path=output_path,
            direct_url=video_url,
        )
        report_generation_progress(
            "persisting_media",
            "正在将生成的视频保存到工作区",
            88,
        )
        return output_path, time.time() - start
