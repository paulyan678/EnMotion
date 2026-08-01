from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Mapping

import httpx

_MAX_MODELS_RESPONSE_BYTES = 2 * 1024 * 1024
_VALIDATION_ATTEMPTS = 3
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class ProviderValidationError(RuntimeError):
    """A safe, user-facing provider preflight failure."""


async def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > _MAX_MODELS_RESPONSE_BYTES:
            raise ProviderValidationError("provider validation failed: response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _model_ids(content: bytes) -> set[str]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderValidationError(
            "provider validation failed: models endpoint is incompatible"
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ProviderValidationError("provider validation failed: models endpoint is incompatible")
    return {
        item["id"]
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }


async def _validate_credential(
    *,
    client: httpx.AsyncClient,
    url: str,
    credential: str,
    expected_models: set[str],
) -> None:
    timeout = httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0)
    for attempt in range(_VALIDATION_ATTEMPTS):
        try:
            async with client.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
                timeout=timeout,
            ) as response:
                if response.status_code in {401, 403}:
                    raise ProviderValidationError(
                        "provider validation failed: credentials rejected"
                    )
                if response.status_code == 404:
                    raise ProviderValidationError(
                        "provider validation failed: models endpoint is incompatible"
                    )
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    if attempt + 1 < _VALIDATION_ATTEMPTS:
                        await asyncio.sleep(0.25 * (2**attempt))
                        continue
                    raise ProviderValidationError(
                        "provider validation failed: service temporarily unavailable"
                    )
                if response.status_code >= 300:
                    raise ProviderValidationError(
                        "provider validation failed: service rejected the request"
                    )
                available = _model_ids(await _read_limited(response))
                if not expected_models.issubset(available):
                    raise ProviderValidationError(
                        "provider validation failed: configured model unavailable"
                    )
                return
        except ProviderValidationError:
            raise
        except httpx.TimeoutException as exc:
            if attempt + 1 == _VALIDATION_ATTEMPTS:
                raise ProviderValidationError("provider validation failed: timed out") from exc
            await asyncio.sleep(0.25 * (2**attempt))
        except httpx.RequestError as exc:
            if attempt + 1 == _VALIDATION_ATTEMPTS:
                raise ProviderValidationError(
                    "provider validation failed: endpoint or TLS unavailable"
                ) from exc
            await asyncio.sleep(0.25 * (2**attempt))


async def validate_provider_configuration(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    credentials: Mapping[str, str],
) -> None:
    """Verify endpoint, TLS, credentials, and model visibility without billable calls."""

    models_by_credential: dict[str, set[str]] = defaultdict(set)
    for model, credential in credentials.items():
        if credential:
            models_by_credential[credential].add(model)
    if not models_by_credential:
        return
    url = f"{base_url.rstrip('/')}/models"
    tasks = [
        asyncio.create_task(
            _validate_credential(
                client=client,
                url=url,
                credential=credential,
                expected_models=models,
            )
        )
        for credential, models in models_by_credential.items()
    ]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
