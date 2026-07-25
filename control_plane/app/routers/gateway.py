from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
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


router = APIRouter(prefix="/gateway", tags=["provider gateway"])

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
            status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "invalid Content-Length"
            ) from exc
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "JSON body must be an object")
    unknown = set(body) - _ALLOWED_JSON_FIELDS[operation]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unsupported provider fields: " + ", ".join(sorted(unknown)),
        )
    if operation == "video.generations":
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "video metadata must be an object"
            )
        metadata_unknown = set(metadata) - _ALLOWED_VIDEO_METADATA
        if metadata_unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "unsupported video metadata fields: " + ", ".join(sorted(metadata_unknown)),
            )
        content = metadata.get("content")
        if not isinstance(content, list) or not 1 <= len(content) <= 2:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            item
            for item in content
            if isinstance(item, dict) and item.get("type") == "image_url"
        ]
        if len(text_parts) != 1 or len(image_parts) > 1 or len(text_parts) + len(image_parts) != len(content):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "video content must contain exactly one text item and at most one image_url",
            )
        if image_parts:
            image_url = image_parts[0].get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if not isinstance(url, str) or not (
                url.startswith("data:image/") or url.startswith("https://")
            ):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "video image_url must be an HTTPS URL or image data URL",
                )
        duration = metadata.get("duration")
        resolution = metadata.get("resolution")
        ratio = metadata.get("ratio")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "video duration must be an integer from 4 to 15",
            )
        if resolution not in {"720p", "1080p"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "video resolution must be 720p or 1080p",
            )
        if ratio not in {"16:9", "9:16", "1:1"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "video ratio must be 16:9, 9:16, or 1:1",
            )
        if not isinstance(metadata.get("generate_audio"), bool) or not isinstance(
            metadata.get("watermark"), bool
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "generate_audio and watermark must be booleans",
            )
        if "seed" in metadata and (
            isinstance(metadata["seed"], bool) or not isinstance(metadata["seed"], int)
        ):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "video seed must be an integer")
        if resolution == "1080p" and (
            image_parts or body.get("model") != "doubao-seedance-2-0-260128"
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "1080p is supported only by Seedance 2.0 text-to-video",
            )
    elif operation == "chat.completions":
        messages = body.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 256:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "messages must be a non-empty list with at most 256 entries",
            )
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role")
                not in {"system", "developer", "user", "assistant", "tool"}
                or not isinstance(message.get("content"), str)
            ):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "each message must contain an allowed role and string content",
                )
    elif operation == "images.generations":
        if not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "image prompt is required")
        if body.get("size", "1024x1024") not in _IMAGE_SIZES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported image size")
        if body.get("quality", "high") not in _IMAGE_QUALITIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported image quality")
        body.setdefault("n", 1)
        body.setdefault("size", "1024x1024")
        body.setdefault("quality", "high")
    if operation.startswith("images.") and body.get("n", 1) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "EnMotion currently permits exactly one image per billable request",
        )
    return body


def _validate_model(request: Request, operation: str, model: Any) -> tuple[str, str]:
    if not isinstance(model, str) or model not in MODEL_CAPABILITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported provider model")
    if MODEL_CAPABILITIES[model] != _OPERATION_CAPABILITY[operation]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "provider model capability does not match this operation",
        )
    credential = request.app.state.settings.provider_credentials.get(model)
    if not credential:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the selected provider model is not configured",
        )
    return model, credential


def _rate_context(body: dict[str, Any]) -> dict[str, object]:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    content = metadata.get("content") if isinstance(metadata.get("content"), list) else []
    has_image = any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )
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


def _replay_response(outcome) -> JSONResponse:
    body = IdempotentReplayResponse(
        usage_request=UsagePublic.model_validate(outcome.usage)
    ).model_dump(mode="json")
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body)


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


def _provider_url(request: Request, path: str) -> str:
    return f"{request.app.state.settings.provider_base_url.rstrip('/')}/{path.lstrip('/')}"


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
            )
        )


def _owned_task_credential(
    request: Request,
    *,
    user_id: str,
    task_id: str,
) -> str:
    with request.app.state.db.session() as session:
        task = session.scalar(
            select(ProviderTask)
            .where(ProviderTask.upstream_task_id == task_id)
            .where(ProviderTask.user_id == user_id)
        )
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "provider task not found")
        credential = request.app.state.settings.provider_credentials.get(task.model)
        if not credential:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "provider model is not configured",
            )
        return credential


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
    capture_video_task: tuple[str, str] | None = None,
) -> Response:
    client: httpx.AsyncClient = request.app.state.provider_client
    try:
        upstream_request = client.build_request(
            method,
            _provider_url(request, path),
            headers=headers,
            content=content,
            json=json_body,
            data=data,
            files=files,
        )
        upstream = await client.send(upstream_request, stream=True)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        await run_in_threadpool(
            _refund,
            request,
            usage_id,
            reason="provider connection failed before acceptance",
            error_code="provider_connect_failed",
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "provider connection failed"
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

    if 400 <= upstream.status_code < 500:
        await upstream.aclose()
        await run_in_threadpool(
            _refund,
            request,
            usage_id,
            reason="provider rejected request",
            upstream_status=upstream.status_code,
            error_code="provider_rejected",
        )
        return JSONResponse(
            status_code=upstream.status_code,
            content={
                "detail": "provider rejected request",
                "provider_status": upstream.status_code,
            },
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
                "provider_status": upstream.status_code,
            },
        )

    if capture_video_task:
        model, user_id = capture_video_task
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
    model, credential = _validate_model(request, operation, body.get("model"))
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
        return _replay_response(outcome)
    provider_key = _provider_idempotency_key(request, principal.user_id, key)
    return await _send_reserved_billable(
        request,
        usage_id=outcome.usage.id,
        method="POST",
        path=provider_path,
        headers=_upstream_headers(credential, provider_key, "application/json"),
        json_body=body,
        capture_video_task=(model, principal.user_id) if capture_video_task else None,
    )


@router.post("/chat/completions")
async def chat_completions(request: Request, principal: CurrentPrincipal) -> Response:
    return await _json_gateway(
        "chat.completions", "chat/completions", request, principal
    )


@router.post("/images/generations")
async def image_generations(request: Request, principal: CurrentPrincipal) -> Response:
    return await _json_gateway(
        "images.generations", "images/generations", request, principal
    )


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
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "unsupported provider fields: " + ", ".join(sorted(unknown)),
            )
        scalar_fields = {"model", "prompt", "n", "size", "quality"}
        for field_name in scalar_fields:
            if len(form.getlist(field_name)) > 1:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"multipart field {field_name} must appear at most once",
                )
        if len(form.getlist("mask")) > 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "multipart field mask must appear at most once",
            )
        model, credential = _validate_model(request, "images.edits", form.get("model"))
        if not isinstance(form.get("prompt"), str) or not str(form.get("prompt")).strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "image prompt is required")
        if str(form.get("n", "1")) != "1":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "EnMotion currently permits exactly one image per billable request",
            )
        if str(form.get("size", "1024x1024")) not in _IMAGE_SIZES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported image size")
        if str(form.get("quality", "high")) not in _IMAGE_QUALITIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported image quality")
        data: list[tuple[str, str]] = []
        uploaded_fields: list[str] = []
        fingerprints: list[dict[str, str | int]] = []
        upload_ordinal = 0
        for field_name, value in form.multi_items():
            if isinstance(value, UploadFile):
                if not (value.content_type or "").lower().startswith("image/"):
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
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
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "image edits require between 1 and 16 image[] uploads",
            )
        effective_size = str(form.get("size", "1024x1024"))
        effective_quality = str(form.get("quality", "high"))
        canonical_fields = [
            pair for pair in data if pair[0] not in {"n", "size", "quality"}
        ]
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
            return _replay_response(outcome)
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
                "Content-Type": request.headers.get(
                    "content-type", "multipart/form-data"
                ),
            },
            content=raw_body,
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid task id")
    credential = await run_in_threadpool(
        _owned_task_credential,
        request,
        user_id=principal.user_id,
        task_id=task_id,
    )
    client: httpx.AsyncClient = request.app.state.provider_client
    try:
        upstream_request = client.build_request(
            "GET",
            _provider_url(request, provider_path),
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
