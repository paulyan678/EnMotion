import asyncio
import json

from fastapi import Request
from starlette.exceptions import HTTPException

from src.apps.comic_gen.video_failures import (
    VIDEO_FAILURE_CODE,
    VIDEO_FAILURE_DIAGNOSTIC,
    VIDEO_FAILURE_MESSAGE,
    classify_video_failure,
)
from src.apps.hybrid.errors import (
    chinese_http_exception_handler,
    chinese_unhandled_exception_handler,
)
from src.apps.playground.models import PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import GENERATION_FAILED_MESSAGE, PlaygroundService
from src.models.newapi import (
    OUTPUT_VIDEO_POLICY_ERROR_CODE,
    OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE,
    NewAPIProviderError,
)


class _MemoryStorage:
    def __init__(self, generation: PlaygroundGeneration) -> None:
        self.generation = generation
        self.output_root = "output"

    def get_generation(self, generation_id: str):
        return self.generation if generation_id == self.generation.id else None

    def update_generation(self, generation: PlaygroundGeneration) -> None:
        self.generation = generation


def test_video_failure_keeps_internal_detail_out_of_public_contract() -> None:
    leaked_detail = "Bearer secret-that-must-not-reach-the-ui"
    failure = classify_video_failure(RuntimeError(leaked_detail))

    assert failure.code == VIDEO_FAILURE_CODE
    assert failure.message == VIDEO_FAILURE_MESSAGE
    assert failure.diagnostic == VIDEO_FAILURE_DIAGNOSTIC
    assert leaked_detail not in failure.message
    assert leaked_detail not in failure.diagnostic


def test_playground_background_failure_is_stable_chinese(monkeypatch) -> None:
    leaked_detail = "provider secret-that-must-not-reach-the-ui"
    generation = PlaygroundGeneration(
        id="generation-1",
        mode=PlaygroundMode.T2I,
        model_id="gpt-image-2",
        prompt="测试",
        created_at="2026-07-24T00:00:00+00:00",
    )
    storage = _MemoryStorage(generation)
    service = PlaygroundService(storage)

    def fail(_generation) -> None:
        raise RuntimeError(leaked_detail)

    monkeypatch.setattr(
        service,
        "_process_image_generation",
        fail,
    )

    service.process_generation(generation.id)

    assert storage.generation.status == "failed"
    assert storage.generation.error == GENERATION_FAILED_MESSAGE
    assert leaked_detail not in storage.generation.error


def test_playground_preserves_safe_provider_failure_metadata(monkeypatch) -> None:
    generation = PlaygroundGeneration(
        id="generation-policy",
        mode=PlaygroundMode.T2V,
        model_id="doubao-seedance-2-0-fast-260128",
        prompt="抽象光点缓慢流动",
        created_at="2026-07-24T00:00:00+00:00",
    )
    storage = _MemoryStorage(generation)
    service = PlaygroundService(storage)
    provider_error = NewAPIProviderError(
        OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE,
        error_code=OUTPUT_VIDEO_POLICY_ERROR_CODE,
        provider_code="OutputVideoSensitiveContentDetected.PolicyViolation",
        request_id="policy-request-1",
        provider_task_id="policy-task",
    )

    def fail(_generation) -> None:
        raise provider_error

    monkeypatch.setattr(service, "_process_video_generation", fail)

    service.process_generation(generation.id)

    assert storage.generation.status == "failed"
    assert storage.generation.error == OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE
    assert storage.generation.error_code == OUTPUT_VIDEO_POLICY_ERROR_CODE
    assert "服务商错误代码：" in storage.generation.error_diagnostic
    assert "policy-request-1" in storage.generation.error_diagnostic


def test_hybrid_http_boundary_hides_non_chinese_exception_detail() -> None:
    leaked_detail = "provider Bearer secret-that-must-not-reach-the-ui"
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/generate",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50000),
        }
    )

    response = asyncio.run(
        chinese_http_exception_handler(
            request,
            HTTPException(status_code=500, detail=leaked_detail),
        )
    )

    body = json.loads(response.body)
    assert body == {"detail": "服务暂时不可用，请稍后重试。"}
    assert response.headers["x-enmotion-error-code"] == "SERVER_ERROR"
    assert leaked_detail not in response.body.decode("utf-8")


def test_hybrid_unhandled_boundary_returns_safe_chinese_message() -> None:
    leaked_detail = "database password secret-that-must-not-reach-the-ui"
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/projects",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50000),
        }
    )

    response = asyncio.run(
        chinese_unhandled_exception_handler(
            request,
            RuntimeError(leaked_detail),
        )
    )

    body = json.loads(response.body)
    assert body == {"detail": "服务暂时不可用，请稍后重试。"}
    assert leaked_detail not in response.body.decode("utf-8")
