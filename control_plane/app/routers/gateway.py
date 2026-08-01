from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..config import MODEL_CAPABILITIES
from ..dependencies import CurrentPrincipal
from ..http_status import UNPROCESSABLE_CONTENT
from ..models import ProviderTask
from ..schemas import IdempotentReplayResponse, UsagePublic
from ..security import token_digest
from ..services.ledger import (
    IdempotencyConflict,
    InsufficientCredits,
    RateCardNotFound,
    mark_pending,
    refund_usage,
    reserve_usage,
    settle_usage,
)
from ..services.provider_config import (
    ProviderConfigSnapshot,
    ProviderConfigUnavailable,
)
from ..services.provider_response_cache import ProviderResponseCacheError

router = APIRouter(prefix="/gateway", tags=["provider gateway"])
logger = logging.getLogger("enmotion.control_plane.gateway")

_PROVIDER_RETRY_EXHAUSTED_HEADER = "X-EnMotion-Provider-Retry-Exhausted"
_PROVIDER_RETRY_EXHAUSTED_CODES = frozenset(
    {
        "provider_connect_failed",
        "provider_rate_limited",
        "provider_request_timeout",
    }
)

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


_ALLOWED_JSON_FIELDS: dict[str, set[str]] = {
    "chat.completions": {
        "model",
        "messages",
        "response_format",
        "temperature",
        "top_p",
        "max_tokens",
        "stream",
        "seed",
        "tools",
        "tool_choice",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "user",
    },
    "images.generations": {
        "model",
        "prompt",
        "n",
        "size",
        "quality",
        "style",
        "response_format",
        "negative_prompt",
        "background",
        "moderation",
        "output_compression",
        "output_format",
    },
    "video.generations": {"model", "prompt", "metadata"},
}

_ALLOWED_VIDEO_METADATA = {
    "content",
    "duration",
    "resolution",
    "ratio",
    "generate_audio",
    "watermark",
    "seed",
}

_OPERATION_CAPABILITY = {
    "chat.completions": "chat",
    "images.generations": "image",
    "images.edits": "image",
    "video.generations": "video",
}
_IMAGE_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
_IMAGE_QUALITIES = {"auto", "low", "medium", "high"}


class _ResponseTooLarge(RuntimeError):
    pass


class _ProviderLogicalRejection(RuntimeError):
    def __init__(self, provider_code: str = "") -> None:
        super().__init__("provider returned an explicit error response")
        self.provider_code = provider_code


def _provider_error_code(payload: Any) -> str:
    """Extract one bounded, non-secret provider error code from JSON."""

    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    candidates = [payload.get("code")]
    if isinstance(error, dict):
        candidates.extend((error.get("code"), error.get("type")))
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", normalized):
            return normalized
    return ""


def _provider_rejection_code(status_code: int, provider_code: str = "") -> str:
    normalized = provider_code.casefold()
    if "quota_warning_concurrency_limit" in normalized:
        return "provider_concurrency_limited"
    if "inputimagesensitivecontentdetected.privacyinformation" in normalized:
        return "input_image_privacy"
    if "outputvideosensitivecontentdetected.policyviolation" in normalized:
        return "output_video_policy"
    return {
        401: "provider_authentication_failed",
        402: "provider_quota_exhausted",
        403: "provider_access_denied",
        404: "provider_model_unavailable",
        408: "provider_request_timeout",
        413: "provider_payload_too_large",
        429: "provider_rate_limited",
    }.get(status_code, "provider_rejected")


def _retry_delay(response: httpx.Response | None, attempt: int, base_delay: float) -> float:
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 30.0))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return max(0.0, min(delay, 30.0))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(base_delay * (2**attempt), 30.0)


def _validate_image_response(content: bytes) -> None:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("image response must be valid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    if not isinstance(first, dict):
        if isinstance(payload, dict):
            error = payload.get("error")
            code = payload.get("code")
            if isinstance(error, dict):
                code = error.get("code") or error.get("type") or code
            normalized_code = str(code or "").strip()
            if normalized_code.casefold() not in {"", "0", "200", "ok", "success"} or error:
                safe_code = (
                    normalized_code
                    if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", normalized_code)
                    else ""
                )
                raise _ProviderLogicalRejection(safe_code)
        raise ValueError("image response must contain data[0]")
    encoded = first.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            if not base64.b64decode(encoded, validate=True):
                raise ValueError("empty image payload")
        except (TypeError, ValueError, binascii.Error) as exc:
            raise ValueError("image response contains invalid base64") from exc
        return
    url = first.get("url")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return
    raise ValueError("image response contains neither b64_json nor a URL")


def _canonical_fingerprint(operation: str, value: Any) -> str:
    canonical = json.dumps(
        {"operation": operation, "request": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("idempotency-key", "").strip()
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise HTTPException(
            UNPROCESSABLE_CONTENT,
            "Idempotency-Key must be 8-128 safe ASCII characters",
        )
    return value


async def _bounded_body(request: Request) -> bytes:
    limit = request.app.state.settings.max_request_body_bytes
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "request too large")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid Content-Length") from exc
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "request too large")
    return body


async def _json_body(request: Request, operation: str) -> dict[str, Any]:
    raw = await _bounded_body(request)
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "request body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(UNPROCESSABLE_CONTENT, "JSON body must be an object")
    unknown = set(body) - _ALLOWED_JSON_FIELDS[operation]
    if unknown:
        raise HTTPException(
            UNPROCESSABLE_CONTENT,
            "unsupported provider fields: " + ", ".join(sorted(unknown)),
        )
    if operation == "video.generations":
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            raise HTTPException(UNPROCESSABLE_CONTENT, "video metadata must be an object")
        metadata_unknown = set(metadata) - _ALLOWED_VIDEO_METADATA
        if metadata_unknown:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "unsupported video metadata fields: " + ", ".join(sorted(metadata_unknown)),
            )
        content = metadata.get("content")
        if not isinstance(content, list) or not 1 <= len(content) <= 2:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "video metadata content must contain text and at most one image",
            )
        text_parts = [
            item
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ]
        image_parts = [
            item for item in content if isinstance(item, dict) and item.get("type") == "image_url"
        ]
        if (
            len(text_parts) != 1
            or len(image_parts) > 1
            or len(text_parts) + len(image_parts) != len(content)
        ):
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "video content must contain exactly one text item and at most one image_url",
            )
        if image_parts:
            image_url = image_parts[0].get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if not isinstance(url, str) or not (
                url.startswith("data:image/") or url.startswith("https://")
            ):
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    "video image_url must be an HTTPS URL or image data URL",
                )
        duration = metadata.get("duration")
        resolution = metadata.get("resolution")
        ratio = metadata.get("ratio")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "video duration must be an integer from 4 to 15",
            )
        if resolution not in {"720p", "1080p"}:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "video resolution must be 720p or 1080p",
            )
        if ratio not in {"16:9", "9:16", "1:1"}:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "video ratio must be 16:9, 9:16, or 1:1",
            )
        if not isinstance(metadata.get("generate_audio"), bool) or not isinstance(
            metadata.get("watermark"), bool
        ):
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "generate_audio and watermark must be booleans",
            )
        if "seed" in metadata and (
            isinstance(metadata["seed"], bool) or not isinstance(metadata["seed"], int)
        ):
            raise HTTPException(UNPROCESSABLE_CONTENT, "video seed must be an integer")
        if resolution == "1080p" and (
            image_parts or body.get("model") != "doubao-seedance-2-0-260128"
        ):
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "1080p is supported only by Seedance 2.0 text-to-video",
            )
    elif operation == "chat.completions":
        messages = body.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 256:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "messages must be a non-empty list with at most 256 entries",
            )
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "system",
                "developer",
                "user",
                "assistant",
                "tool",
            }:
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    "each message must contain an allowed role and supported content",
                )
            content = message.get("content")
            if isinstance(content, str):
                continue
            if message.get("role") != "user" or not isinstance(content, list):
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    "multimodal content is supported only for user messages",
                )
            if not 1 <= len(content) <= 5:
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    "multimodal chat content must contain 1-5 parts",
                )
            text_parts = 0
            image_parts = 0
            for part in content:
                if not isinstance(part, dict):
                    raise HTTPException(
                        UNPROCESSABLE_CONTENT,
                        "each multimodal chat part must be an object",
                    )
                if part.get("type") == "text":
                    if (
                        set(part) != {"type", "text"}
                        or not isinstance(part.get("text"), str)
                        or not part["text"].strip()
                    ):
                        raise HTTPException(
                            UNPROCESSABLE_CONTENT,
                            "chat text parts must contain non-empty text",
                        )
                    text_parts += 1
                    continue
                if part.get("type") == "image_url":
                    if set(part) != {"type", "image_url"}:
                        raise HTTPException(
                            UNPROCESSABLE_CONTENT,
                            "chat image parts contain unsupported fields",
                        )
                    image_url = part.get("image_url")
                    if not isinstance(image_url, dict) or set(image_url) - {
                        "url",
                        "detail",
                    }:
                        raise HTTPException(
                            UNPROCESSABLE_CONTENT,
                            "chat image_url must be an object with url and optional detail",
                        )
                    url = image_url.get("url")
                    if not isinstance(url, str) or not url.startswith(("data:image/", "https://")):
                        raise HTTPException(
                            UNPROCESSABLE_CONTENT,
                            "chat image_url must be an HTTPS URL or image data URL",
                        )
                    if image_url.get("detail", "auto") not in {
                        "auto",
                        "low",
                        "high",
                    }:
                        raise HTTPException(
                            UNPROCESSABLE_CONTENT,
                            "unsupported chat image detail",
                        )
                    image_parts += 1
                    continue
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    "unsupported multimodal chat part type",
                )
            if text_parts != 1 or not 1 <= image_parts <= 4:
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    "multimodal chat content requires one text and 1-4 images",
                )
    elif operation == "images.generations":
        if not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
            raise HTTPException(UNPROCESSABLE_CONTENT, "image prompt is required")
        if body.get("size", "1024x1024") not in _IMAGE_SIZES:
            raise HTTPException(UNPROCESSABLE_CONTENT, "unsupported image size")
        if body.get("quality", "high") not in _IMAGE_QUALITIES:
            raise HTTPException(UNPROCESSABLE_CONTENT, "unsupported image quality")
        body.setdefault("n", 1)
        body.setdefault("size", "1024x1024")
        body.setdefault("quality", "high")
    if operation.startswith("images.") and body.get("n", 1) != 1:
        raise HTTPException(
            UNPROCESSABLE_CONTENT,
            "EnMotion currently permits exactly one image per billable request",
        )
    return body


def _validate_model(
    request: Request,
    operation: str,
    model: Any,
) -> tuple[str, str, ProviderConfigSnapshot]:
    if not isinstance(model, str) or model not in MODEL_CAPABILITIES:
        raise HTTPException(UNPROCESSABLE_CONTENT, "unsupported provider model")
    if MODEL_CAPABILITIES[model] != _OPERATION_CAPABILITY[operation]:
        raise HTTPException(
            UNPROCESSABLE_CONTENT,
            "provider model capability does not match this operation",
        )
    try:
        provider_config = request.app.state.provider_config.current()
    except ProviderConfigUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "provider configuration is unavailable",
        ) from exc
    credential = provider_config.credentials.get(model)
    if not credential:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the selected provider model is not configured",
        )
    return model, credential, provider_config


def _rate_context(body: dict[str, Any]) -> dict[str, object]:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    content = metadata.get("content") if isinstance(metadata.get("content"), list) else []
    has_image = any(isinstance(item, dict) and item.get("type") == "image_url" for item in content)
    return {
        "size": body.get("size"),
        "quality": body.get("quality"),
        "duration": metadata.get("duration"),
        "resolution": metadata.get("resolution"),
        "ratio": metadata.get("ratio"),
        "generation_mode": "i2v" if has_image else "t2v",
        "generate_audio": metadata.get("generate_audio"),
    }


def _extract_task_id(payload: dict[str, Any], depth: int = 0) -> str | None:
    if depth > 4:
        return None
    for key in ("task_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and _TASK_ID.fullmatch(value):
            return value
    for key in ("data", "result", "output"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            task_id = _extract_task_id(nested, depth + 1)
            if task_id:
                return task_id
    return None


def _reserve(
    request: Request,
    *,
    user_id: str,
    operation: str,
    model: str,
    key: str,
    fingerprint: str,
    rate_context: dict[str, object],
):
    try:
        with request.app.state.db.session() as session:
            return reserve_usage(
                session,
                user_id=user_id,
                operation=operation,
                model=model,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                rate_context=rate_context,
            )
    except InsufficientCredits as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
    except RateCardNotFound as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


async def _replay_response(
    request: Request,
    outcome,
    *,
    allow_cached_provider_response: bool = False,
) -> Response:
    if allow_cached_provider_response and outcome.usage.status in {
        "settled",
        "pending_reconciliation",
    }:
        cached = await run_in_threadpool(
            request.app.state.provider_response_cache.load,
            outcome.usage.id,
        )
        if cached is not None:
            if outcome.usage.status == "pending_reconciliation":
                await run_in_threadpool(
                    _settle,
                    request,
                    outcome.usage.id,
                    upstream_status=cached.status_code,
                )
            headers = dict(cached.headers)
            headers["X-EnMotion-Usage-ID"] = outcome.usage.id
            headers["X-EnMotion-Idempotent-Replay"] = "true"
            return Response(
                content=cached.content,
                status_code=cached.status_code,
                headers=headers,
            )
    body = IdempotentReplayResponse(
        usage_request=UsagePublic.model_validate(outcome.usage)
    ).model_dump(mode="json")
    headers = None
    if (
        outcome.usage.status == "refunded"
        and outcome.usage.error_code in _PROVIDER_RETRY_EXHAUSTED_CODES
    ):
        headers = {_PROVIDER_RETRY_EXHAUSTED_HEADER: "true"}
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=body,
        headers=headers,
    )


def _provider_idempotency_key(request: Request, user_id: str, client_key: str) -> str:
    """Namespace client keys across users sharing one provider credential."""

    return token_digest(
        request.app.state.settings.session_hmac_secret,
        f"provider:{user_id}:{client_key}",
    )


def _upstream_headers(credential: str, key: str, content_type: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential}",
        "Content-Type": content_type,
        "Idempotency-Key": key,
        "User-Agent": "EnMotion-Control-Plane/0.1",
    }


def _response_headers(
    response: httpx.Response,
    *,
    raw_stream: bool = True,
) -> dict[str, str]:
    allowed = {
        "content-type",
        "content-disposition",
        "cache-control",
        "etag",
        "last-modified",
        "x-request-id",
    }
    if raw_stream:
        allowed.update({"content-length", "content-encoding"})
    return {key: value for key, value in response.headers.items() if key.lower() in allowed}


async def _stream_response(response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        if response.is_stream_consumed:
            if response.content:
                yield response.content
            return
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()


async def _read_response_limited(response: httpx.Response, limit: int) -> bytes:
    payload = bytearray()
    if response.is_stream_consumed:
        if len(response.content) > limit:
            raise _ResponseTooLarge
        return response.content
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(payload) + len(chunk) > limit:
            raise _ResponseTooLarge
        payload.extend(chunk)
    return bytes(payload)


def _provider_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _refund(
    request: Request,
    usage_id: str,
    *,
    reason: str,
    upstream_status: int | None = None,
    error_code: str | None = None,
) -> None:
    with request.app.state.db.session() as session:
        refund_usage(
            session,
            usage_id=usage_id,
            reason=reason,
            upstream_status=upstream_status,
            error_code=error_code,
        )


def _pending(
    request: Request,
    usage_id: str,
    *,
    code: str,
    upstream_status: int | None = None,
) -> None:
    with request.app.state.db.session() as session:
        mark_pending(
            session,
            usage_id=usage_id,
            reason_code=code,
            upstream_status=upstream_status,
        )


def _settle(
    request: Request,
    usage_id: str,
    *,
    upstream_status: int,
) -> None:
    with request.app.state.db.session() as session:
        settle_usage(
            session,
            usage_id=usage_id,
            upstream_status=upstream_status,
        )


def _settle_video(
    request: Request,
    *,
    usage_id: str,
    upstream_status: int,
    task_id: str,
    user_id: str,
    model: str,
    provider_config_version: int,
) -> None:
    with request.app.state.db.session() as session:
        outcome = settle_usage(
            session,
            usage_id=usage_id,
            upstream_status=upstream_status,
            upstream_task_id=task_id,
        )
        usage = outcome.usage
        session.add(
            ProviderTask(
                user_id=user_id,
                usage_request_id=usage.id,
                upstream_task_id=task_id,
                model=model,
                provider_config_version=provider_config_version,
            )
        )


def _owned_task_credential(
    request: Request,
    *,
    user_id: str,
    task_id: str,
) -> tuple[str, str]:
    with request.app.state.db.session() as session:
        task = session.scalar(
            select(ProviderTask)
            .where(ProviderTask.upstream_task_id == task_id)
            .where(ProviderTask.user_id == user_id)
        )
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider task not found")
        try:
            provider_config = request.app.state.provider_config.get_version(
                task.provider_config_version
            )
        except ProviderConfigUnavailable as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "provider configuration for this task is unavailable",
            ) from exc
        credential = provider_config.credentials.get(task.model)
        if not credential:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "provider model is not configured",
            )
        return credential, provider_config.base_url


async def _send_billable(
    request: Request,
    *,
    usage_id: str,
    method: str,
    path: str,
    headers: dict[str, str],
    content: bytes | None = None,
    json_body: dict[str, Any] | None = None,
    data: list[tuple[str, str]] | None = None,
    files: list[tuple[str, tuple[str, Any, str]]] | None = None,
    capture_video_task: tuple[str, str, int] | None = None,
    cache_provider_response: bool = False,
    provider_base_url: str,
) -> Response:
    client: httpx.AsyncClient = request.app.state.provider_client
    settings = request.app.state.settings
    upstream: httpx.Response | None = None
    for attempt in range(settings.provider_submission_attempts):
        try:
            upstream_request = client.build_request(
                method,
                _provider_url(provider_base_url, path),
                headers=headers,
                content=content,
                json=json_body,
                data=data,
                files=files,
            )
            upstream = await client.send(upstream_request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            if attempt + 1 < settings.provider_submission_attempts:
                logger.warning(
                    "Provider connection failed before acceptance; retrying usage=%s attempt=%d/%d error=%s",
                    usage_id,
                    attempt + 1,
                    settings.provider_submission_attempts,
                    type(exc).__name__,
                )
                await asyncio.sleep(
                    _retry_delay(None, attempt, settings.provider_retry_backoff_seconds)
                )
                continue
            await run_in_threadpool(
                _refund,
                request,
                usage_id,
                reason="provider connection failed before acceptance",
                error_code="provider_connect_failed",
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider connection failed",
                headers={_PROVIDER_RETRY_EXHAUSTED_HEADER: "true"},
            ) from exc
        except httpx.RequestError as exc:
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="ambiguous_provider_transport_error",
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider transport outcome is ambiguous; credits remain reserved",
            ) from exc

        if upstream.status_code in {
            status.HTTP_408_REQUEST_TIMEOUT,
            status.HTTP_429_TOO_MANY_REQUESTS,
        } and (attempt + 1 < settings.provider_submission_attempts):
            delay = _retry_delay(
                upstream,
                attempt,
                settings.provider_retry_backoff_seconds,
            )
            await upstream.aclose()
            logger.warning(
                "Provider explicitly rejected a retryable submission; "
                "retrying usage=%s status=%d attempt=%d/%d delay=%.2fs",
                usage_id,
                upstream.status_code,
                attempt + 1,
                settings.provider_submission_attempts,
                delay,
            )
            await asyncio.sleep(delay)
            upstream = None
            continue
        break

    if upstream is None:
        raise RuntimeError("provider submission loop exited without a response")

    if 400 <= upstream.status_code < 500:
        upstream_status = upstream.status_code
        retry_after = upstream.headers.get("retry-after")
        provider_code = ""
        try:
            rejection_body = await _read_response_limited(upstream, 64 * 1024)
            rejection_payload = json.loads(rejection_body)
            provider_code = _provider_error_code(rejection_payload)
        except (_ResponseTooLarge, UnicodeDecodeError, json.JSONDecodeError, httpx.HTTPError):
            provider_code = ""
        finally:
            await upstream.aclose()
        error_code = _provider_rejection_code(upstream_status, provider_code)
        await run_in_threadpool(
            _refund,
            request,
            usage_id,
            reason="provider rejected request",
            upstream_status=upstream_status,
            error_code=error_code,
        )
        response_status = upstream_status
        if error_code == "provider_concurrency_limited":
            response_status = status.HTTP_429_TOO_MANY_REQUESTS
            retry_after = retry_after or "15"
        response_headers = {"Retry-After": retry_after} if retry_after else {}
        if error_code in _PROVIDER_RETRY_EXHAUSTED_CODES:
            response_headers[_PROVIDER_RETRY_EXHAUSTED_HEADER] = "true"
        return JSONResponse(
            status_code=response_status,
            content={
                "detail": "provider rejected request",
                "code": error_code,
                "provider_status": upstream_status,
            },
            headers=response_headers or None,
        )
    if upstream.status_code >= 500:
        await upstream.aclose()
        await run_in_threadpool(
            _pending,
            request,
            usage_id,
            code="ambiguous_provider_server_error",
            upstream_status=upstream.status_code,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": "provider failure has an ambiguous billing outcome; "
                "credits remain reserved",
                "code": "provider_outcome_ambiguous",
                "provider_status": upstream.status_code,
            },
        )

    if 300 <= upstream.status_code < 400:
        await upstream.aclose()
        await run_in_threadpool(
            _pending,
            request,
            usage_id,
            code="ambiguous_provider_redirect",
            upstream_status=upstream.status_code,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": "provider redirect was refused; credits remain reserved",
                "code": "provider_outcome_ambiguous",
                "provider_status": upstream.status_code,
            },
        )

    if capture_video_task:
        model, user_id, provider_config_version = capture_video_task
        try:
            payload_bytes = await _read_response_limited(
                upstream,
                2 * 1024 * 1024,
            )
        except _ResponseTooLarge:
            await upstream.aclose()
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="invalid_video_submission_response",
                upstream_status=upstream.status_code,
            )
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={"detail": "provider video response was unexpectedly large"},
            )
        except httpx.HTTPError as exc:
            await upstream.aclose()
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="ambiguous_video_submission_response_error",
                upstream_status=upstream.status_code,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider accepted the video request but its response was incomplete; "
                "credits remain reserved for reconciliation",
            ) from exc
        await upstream.aclose()
        task_id: str | None = None
        try:
            payload = json.loads(payload_bytes)
            if not isinstance(payload, dict):
                raise ValueError("video response must be a JSON object")
            task_id = _extract_task_id(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            task_id = None
        if task_id is None:
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="invalid_video_submission_response",
                upstream_status=upstream.status_code,
            )
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={
                    "detail": "provider accepted the video request without a usable task id; "
                    "credits remain reserved for reconciliation"
                },
            )
        try:
            await run_in_threadpool(
                _settle_video,
                request,
                usage_id=usage_id,
                upstream_status=upstream.status_code,
                task_id=task_id,
                user_id=user_id,
                model=model,
                provider_config_version=provider_config_version,
            )
        except Exception as exc:
            try:
                await run_in_threadpool(
                    _pending,
                    request,
                    usage_id,
                    code="settlement_persistence_failed",
                    upstream_status=upstream.status_code,
                )
            except Exception:
                pass
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider accepted the request but settlement persistence failed; "
                "credits remain reserved for reconciliation",
            ) from exc
        return Response(
            content=payload_bytes,
            status_code=upstream.status_code,
            headers=_response_headers(upstream, raw_stream=False),
        )

    if cache_provider_response:
        upstream_status = upstream.status_code
        response_headers = _response_headers(upstream, raw_stream=False)
        try:
            payload_bytes = await _read_response_limited(
                upstream,
                request.app.state.provider_response_cache.max_content_bytes,
            )
            _validate_image_response(payload_bytes)
        except _ProviderLogicalRejection as exc:
            await upstream.aclose()
            await run_in_threadpool(
                _refund,
                request,
                usage_id,
                reason="provider returned an explicit image error",
                upstream_status=upstream_status,
                error_code="provider_rejected",
            )
            logical_error: dict[str, Any] = {
                "message": "provider returned an explicit error response"
            }
            if exc.provider_code:
                logical_error["code"] = exc.provider_code
            return JSONResponse(
                status_code=UNPROCESSABLE_CONTENT,
                content={
                    "detail": "provider rejected request",
                    "code": "provider_request_rejected",
                    "provider_status": upstream_status,
                    "error": logical_error,
                },
            )
        except (_ResponseTooLarge, ValueError, httpx.HTTPError) as exc:
            await upstream.aclose()
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="invalid_image_provider_response",
                upstream_status=upstream_status,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider accepted the image request but returned an invalid response; "
                "credits remain reserved for reconciliation",
            ) from exc
        await upstream.aclose()
        try:
            await run_in_threadpool(
                request.app.state.provider_response_cache.store,
                usage_id,
                status_code=upstream_status,
                headers=response_headers,
                content=payload_bytes,
            )
        except ProviderResponseCacheError as exc:
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="provider_response_cache_failed",
                upstream_status=upstream_status,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider accepted the image request but its result could not be cached; "
                "credits remain reserved for reconciliation",
            ) from exc
        try:
            await run_in_threadpool(
                _settle,
                request,
                usage_id,
                upstream_status=upstream_status,
            )
        except Exception as exc:
            try:
                await run_in_threadpool(
                    _pending,
                    request,
                    usage_id,
                    code="settlement_persistence_failed",
                    upstream_status=upstream_status,
                )
            except Exception:
                pass
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "provider accepted the request but settlement persistence failed; "
                "credits remain reserved for reconciliation",
            ) from exc
        response_headers["X-EnMotion-Usage-ID"] = usage_id
        return Response(
            content=payload_bytes,
            status_code=upstream_status,
            headers=response_headers,
        )

    try:
        await run_in_threadpool(
            _settle,
            request,
            usage_id,
            upstream_status=upstream.status_code,
        )
    except Exception as exc:
        await upstream.aclose()
        try:
            await run_in_threadpool(
                _pending,
                request,
                usage_id,
                code="settlement_persistence_failed",
                upstream_status=upstream.status_code,
            )
        except Exception:
            pass
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "provider accepted the request but settlement persistence failed; "
            "credits remain reserved for reconciliation",
        ) from exc
    return StreamingResponse(
        _stream_response(upstream),
        status_code=upstream.status_code,
        headers=_response_headers(
            upstream,
            raw_stream=not upstream.is_stream_consumed,
        ),
        background=BackgroundTask(upstream.aclose),
    )


async def _send_reserved_billable(
    request: Request,
    *,
    usage_id: str,
    **kwargs,
) -> Response:
    """Never leave a nonterminal reservation hidden after an interrupted task."""

    try:
        return await _send_billable(
            request,
            usage_id=usage_id,
            **kwargs,
        )
    except BaseException:
        # Keep this short synchronous write reliable even when the coroutine is
        # being cancelled. mark_pending is idempotent and preserves an existing
        # terminal or reconciliation state.
        try:
            _pending(
                request,
                usage_id,
                code="interrupted_before_terminal_state",
            )
        except Exception:
            pass
        raise


async def _json_gateway(
    operation: str,
    provider_path: str,
    request: Request,
    principal: CurrentPrincipal,
    *,
    capture_video_task: bool = False,
) -> Response:
    body = await _json_body(request, operation)
    model, credential, provider_config = _validate_model(
        request,
        operation,
        body.get("model"),
    )
    key = _idempotency_key(request)
    outcome = await run_in_threadpool(
        _reserve,
        request,
        user_id=principal.user_id,
        operation=operation,
        model=model,
        key=key,
        fingerprint=_canonical_fingerprint(operation, body),
        rate_context=_rate_context(body),
    )
    if outcome.replay:
        return await _replay_response(
            request,
            outcome,
            allow_cached_provider_response=operation == "images.generations",
        )
    provider_key = _provider_idempotency_key(request, principal.user_id, key)
    return await _send_reserved_billable(
        request,
        usage_id=outcome.usage.id,
        method="POST",
        path=provider_path,
        headers=_upstream_headers(credential, provider_key, "application/json"),
        json_body=body,
        capture_video_task=(
            (
                model,
                principal.user_id,
                provider_config.version,
            )
            if capture_video_task
            else None
        ),
        cache_provider_response=operation == "images.generations",
        provider_base_url=provider_config.base_url,
    )


@router.post("/chat/completions")
async def chat_completions(request: Request, principal: CurrentPrincipal) -> Response:
    return await _json_gateway("chat.completions", "chat/completions", request, principal)


@router.post("/images/generations")
async def image_generations(request: Request, principal: CurrentPrincipal) -> Response:
    return await _json_gateway("images.generations", "images/generations", request, principal)


@router.post("/video/generations")
async def video_generations(request: Request, principal: CurrentPrincipal) -> Response:
    return await _json_gateway(
        "video.generations",
        "video/generations",
        request,
        principal,
        capture_video_task=True,
    )


@router.post("/images/edits")
async def image_edits(request: Request, principal: CurrentPrincipal) -> Response:
    raw_body = await _bounded_body(request)
    form = await request.form(
        max_files=17,
        max_fields=10,
        max_part_size=request.app.state.settings.max_request_body_bytes,
    )
    try:
        allowed_fields = {"model", "prompt", "n", "size", "quality", "image[]", "mask"}
        unknown = {key for key in form.keys() if key not in allowed_fields}
        if unknown:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "unsupported provider fields: " + ", ".join(sorted(unknown)),
            )
        scalar_fields = {"model", "prompt", "n", "size", "quality"}
        for field_name in scalar_fields:
            if len(form.getlist(field_name)) > 1:
                raise HTTPException(
                    UNPROCESSABLE_CONTENT,
                    f"multipart field {field_name} must appear at most once",
                )
        if len(form.getlist("mask")) > 1:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "multipart field mask must appear at most once",
            )
        model, credential, provider_config = _validate_model(
            request,
            "images.edits",
            form.get("model"),
        )
        if not isinstance(form.get("prompt"), str) or not str(form.get("prompt")).strip():
            raise HTTPException(UNPROCESSABLE_CONTENT, "image prompt is required")
        if str(form.get("n", "1")) != "1":
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "EnMotion currently permits exactly one image per billable request",
            )
        if str(form.get("size", "1024x1024")) not in _IMAGE_SIZES:
            raise HTTPException(UNPROCESSABLE_CONTENT, "unsupported image size")
        if str(form.get("quality", "high")) not in _IMAGE_QUALITIES:
            raise HTTPException(UNPROCESSABLE_CONTENT, "unsupported image quality")
        data: list[tuple[str, str]] = []
        uploaded_fields: list[str] = []
        fingerprints: list[dict[str, str | int]] = []
        upload_ordinal = 0
        for field_name, value in form.multi_items():
            if isinstance(value, UploadFile):
                if not (value.content_type or "").lower().startswith("image/"):
                    raise HTTPException(
                        UNPROCESSABLE_CONTENT,
                        "image edit uploads must use an image content type",
                    )
                digest = hashlib.sha256()
                while chunk := await value.read(1024 * 1024):
                    digest.update(chunk)
                await value.seek(0)
                uploaded_fields.append(field_name)
                fingerprints.append(
                    {
                        "ordinal": upload_ordinal,
                        "field": field_name,
                        "filename": value.filename or "",
                        "content_type": value.content_type or "",
                        "sha256": digest.hexdigest(),
                    }
                )
                upload_ordinal += 1
            else:
                data.append((field_name, str(value)))
        image_count = uploaded_fields.count("image[]")
        if not 1 <= image_count <= 16:
            raise HTTPException(
                UNPROCESSABLE_CONTENT,
                "image edits require between 1 and 16 image[] uploads",
            )
        effective_size = str(form.get("size", "1024x1024"))
        effective_quality = str(form.get("quality", "high"))
        canonical_fields = [pair for pair in data if pair[0] not in {"n", "size", "quality"}]
        canonical_fields.extend(
            [
                ("n", "1"),
                ("size", effective_size),
                ("quality", effective_quality),
            ]
        )
        canonical = {"fields": sorted(canonical_fields), "files": fingerprints}
        key = _idempotency_key(request)
        outcome = await run_in_threadpool(
            _reserve,
            request,
            user_id=principal.user_id,
            operation="images.edits",
            model=model,
            key=key,
            fingerprint=_canonical_fingerprint("images.edits", canonical),
            rate_context={
                "size": effective_size,
                "quality": effective_quality,
            },
        )
        if outcome.replay:
            return await _replay_response(
                request,
                outcome,
                allow_cached_provider_response=True,
            )
        provider_key = _provider_idempotency_key(request, principal.user_id, key)
        return await _send_reserved_billable(
            request,
            usage_id=outcome.usage.id,
            method="POST",
            path="images/edits",
            headers={
                "Authorization": f"Bearer {credential}",
                "Idempotency-Key": provider_key,
                "User-Agent": "EnMotion-Control-Plane/0.1",
                "Content-Type": request.headers.get("content-type", "multipart/form-data"),
            },
            content=raw_body,
            cache_provider_response=True,
            provider_base_url=provider_config.base_url,
        )
    finally:
        await form.close()


async def _task_proxy(
    request: Request,
    principal: CurrentPrincipal,
    task_id: str,
    provider_path: str,
) -> Response:
    if not _TASK_ID.fullmatch(task_id):
        raise HTTPException(UNPROCESSABLE_CONTENT, "invalid task id")
    credential, provider_base_url = await run_in_threadpool(
        _owned_task_credential,
        request,
        user_id=principal.user_id,
        task_id=task_id,
    )
    client: httpx.AsyncClient = request.app.state.provider_client
    try:
        upstream_request = client.build_request(
            "GET",
            _provider_url(provider_base_url, provider_path),
            headers={
                "Authorization": f"Bearer {credential}",
                "User-Agent": "EnMotion-Control-Plane/0.1",
            },
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "provider request failed") from exc
    if upstream.status_code >= 400:
        await upstream.aclose()
        return JSONResponse(
            status_code=upstream.status_code,
            content={
                "detail": "provider task request failed",
                "provider_status": upstream.status_code,
            },
        )
    return StreamingResponse(
        _stream_response(upstream),
        status_code=upstream.status_code,
        headers=_response_headers(
            upstream,
            raw_stream=not upstream.is_stream_consumed,
        ),
        background=BackgroundTask(upstream.aclose),
    )


@router.get("/video/generations/{task_id}")
async def video_status(
    task_id: str,
    request: Request,
    principal: CurrentPrincipal,
) -> Response:
    return await _task_proxy(
        request,
        principal,
        task_id,
        f"video/generations/{task_id}",
    )


@router.get("/videos/{task_id}/content")
async def video_content(
    task_id: str,
    request: Request,
    principal: CurrentPrincipal,
) -> Response:
    return await _task_proxy(
        request,
        principal,
        task_id,
        f"videos/{task_id}/content",
    )
