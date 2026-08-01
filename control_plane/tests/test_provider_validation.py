from __future__ import annotations

import asyncio

import httpx
import pytest
from app.services.provider_validation import (
    ProviderValidationError,
    validate_provider_configuration,
)


def test_provider_validation_groups_models_that_share_a_credential() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "model-a"}, {"id": "model-b"}]},
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await validate_provider_configuration(
                client=client,
                base_url="https://provider.test/v1",
                credentials={"model-a": "shared-secret", "model-b": "shared-secret"},
            )

    asyncio.run(exercise())

    assert len(calls) == 1
    assert calls[0].url == "https://provider.test/v1/models"
    assert calls[0].headers["authorization"] == "Bearer shared-secret"


def test_provider_validation_rejects_missing_model() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"data": [{"id": "other-model"}]})
    )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(
                ProviderValidationError,
                match="configured model unavailable",
            ):
                await validate_provider_configuration(
                    client=client,
                    base_url="https://provider.test/v1",
                    credentials={"model-a": "secret"},
                )

    asyncio.run(exercise())


def test_provider_validation_retries_transient_service_failure() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await validate_provider_configuration(
                client=client,
                base_url="https://provider.test/v1",
                credentials={"model-a": "secret"},
            )

    asyncio.run(exercise())

    assert attempts == 3


def test_provider_validation_reports_connection_failure_after_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("certificate verify failed", request=request)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(
                ProviderValidationError,
                match="endpoint or TLS unavailable",
            ):
                await validate_provider_configuration(
                    client=client,
                    base_url="https://provider.test/v1",
                    credentials={"model-a": "secret"},
                )

    asyncio.run(exercise())

    assert attempts == 3
