"""Deterministic, inspectable contracts for provider generation requests.

Generation UIs work with editable drafts, while workers need an immutable
snapshot of the exact values submitted to a provider.  This module is kept
free of provider clients and workspace state so previews, API handlers, queue
workers, retries, and activity history can share one canonical representation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence


GENERATION_COMPILER_VERSION = "1.0"


def _json_value(value: Any) -> Any:
    """Return a stable JSON-compatible value without leaking object reprs."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _compact_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {
        str(key): _json_value(item)
        for key, item in value.items()
        if item is not None
    }


def _compact_media(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or ():
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result[:16]


def provider_request(
    *,
    phase: str,
    model: str,
    prompt: str,
    negative_prompt: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    input_media: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build one exact, provider-safe request envelope."""

    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise ValueError("Generation prompt cannot be empty")
    normalized_model = (model or "").strip()
    if not normalized_model:
        raise ValueError("Generation model cannot be empty")
    request: dict[str, Any] = {
        "phase": (phase or "generate").strip() or "generate",
        "model": normalized_model,
        "prompt": normalized_prompt,
        "parameters": _compact_mapping(parameters),
        "input_media": _compact_media(input_media),
    }
    if isinstance(negative_prompt, str) and negative_prompt.strip():
        request["negative_prompt"] = negative_prompt.strip()
    return request


def compile_generation_request(
    *,
    category: str,
    mode: str,
    user_prompt: str,
    requests: Sequence[Mapping[str, Any]],
    source: str,
    target: Mapping[str, Any] | None = None,
    prompt_parts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze provider requests and attach deterministic provenance/checksum."""

    if not requests:
        raise ValueError("At least one provider request is required")
    normalized_requests: list[dict[str, Any]] = []
    for source_request in requests:
        item = _json_value(dict(source_request))
        if not isinstance(item, dict):
            raise ValueError("Provider request must be an object")
        normalized_requests.append(provider_request(
            phase=str(item.get("phase") or "generate"),
            model=str(item.get("model") or ""),
            prompt=str(item.get("prompt") or ""),
            negative_prompt=(
                str(item["negative_prompt"])
                if item.get("negative_prompt") is not None
                else None
            ),
            parameters=item.get("parameters") if isinstance(item.get("parameters"), dict) else {},
            input_media=item.get("input_media") if isinstance(item.get("input_media"), list) else [],
        ))

    body = {
        "compiler_version": GENERATION_COMPILER_VERSION,
        "category": (category or "other").strip().lower(),
        "mode": (mode or "generate").strip().lower(),
        "source": (source or "workspace").strip().lower(),
        "user_prompt": (user_prompt or "").strip(),
        "prompt_parts": [_json_value(dict(item)) for item in (prompt_parts or ())],
        "target": _compact_mapping(target),
        "provider_requests": normalized_requests,
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        **body,
        "compiled_request_id": f"genreq_{checksum[:24]}",
        "checksum": checksum,
    }


def assert_generation_checksum(compiled: Mapping[str, Any], expected: str | None) -> None:
    """Reject a submission if the reviewed draft changed before enqueue."""

    if expected is None:
        return
    normalized = expected.strip().lower()
    actual = str(compiled.get("checksum") or "").strip().lower()
    if not normalized or normalized != actual:
        raise ValueError(
            "Generation inputs changed after provider-request review. Review the request again."
        )


def first_provider_request(compiled: Mapping[str, Any]) -> dict[str, Any]:
    requests = compiled.get("provider_requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise ValueError("Compiled generation request has no provider request")
    return dict(requests[0])


def provider_request_for_phase(
    compiled: Mapping[str, Any] | None,
    phase: str,
) -> dict[str, Any] | None:
    if not isinstance(compiled, Mapping):
        return None
    requests = compiled.get("provider_requests")
    if not isinstance(requests, list):
        return None
    for request in requests:
        if isinstance(request, dict) and request.get("phase") == phase:
            return dict(request)
    return dict(requests[0]) if len(requests) == 1 and isinstance(requests[0], dict) else None
