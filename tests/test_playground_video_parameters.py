from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.apps.playground.models import (
    GenerateRequest,
    PlaygroundGeneration,
    PlaygroundMode,
)
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage


STANDARD_MODEL = "doubao-seedance-2-0-260128"
FAST_MODEL = "doubao-seedance-2-0-fast-260128"
MINI_MODEL = "doubao-seedance-2-0-mini-260615"


def _request(
    *,
    mode: str = "t2v",
    model_id: str = FAST_MODEL,
    parameters: dict | None = None,
) -> GenerateRequest:
    payload = {
        "mode": mode,
        "model_id": model_id,
        "prompt": "A quiet camera push-in",
    }
    if mode == "i2v":
        payload["input_media"] = ["playground/images/source.png"]
    if parameters is not None:
        payload["parameters"] = parameters
    return GenerateRequest.model_validate(payload)


def test_video_request_materializes_authoritative_defaults():
    request = _request()

    assert request.parameters == {
        "duration": 5,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
    }


def test_video_request_normalizes_supported_string_parameters():
    request = _request(
        model_id=STANDARD_MODEL,
        parameters={
            "duration": 15,
            "resolution": " 1080P ",
            "aspect_ratio": " 9:16 ",
            "watermark": True,
        },
    )

    assert request.parameters == {
        "duration": 15,
        "resolution": "1080p",
        "aspect_ratio": "9:16",
        "generate_audio": True,
        "watermark": True,
    }


@pytest.mark.parametrize("duration", [3, 16, 5.5, "5", True])
def test_video_request_rejects_invalid_duration(duration):
    with pytest.raises(ValidationError, match="视频时长必须"):
        _request(parameters={"duration": duration})


@pytest.mark.parametrize("aspect_ratio", ["4:3", "3:4", "adaptive", 1])
def test_video_request_rejects_unsupported_aspect_ratio(aspect_ratio):
    with pytest.raises(ValidationError, match="视频画面比例必须"):
        _request(parameters={"aspect_ratio": aspect_ratio})


@pytest.mark.parametrize("resolution", ["480p", "4k", "1280x720", 720])
def test_video_request_rejects_unsupported_resolution(resolution):
    with pytest.raises(ValidationError, match="视频分辨率必须"):
        _request(parameters={"resolution": resolution})


@pytest.mark.parametrize(
    ("mode", "model_id"),
    [
        ("t2v", FAST_MODEL),
        ("t2v", MINI_MODEL),
        ("i2v", STANDARD_MODEL),
        ("i2v", FAST_MODEL),
        ("i2v", MINI_MODEL),
    ],
)
def test_1080p_is_rejected_for_incompatible_video_requests(mode, model_id):
    with pytest.raises(ValidationError, match="Seedance 2.0 文生视频"):
        _request(
            mode=mode,
            model_id=model_id,
            parameters={"resolution": "1080p"},
        )


def test_1080p_is_allowed_for_standard_text_to_video():
    request = _request(
        mode="t2v",
        model_id=STANDARD_MODEL,
        parameters={"resolution": "1080p"},
    )

    assert request.parameters["resolution"] == "1080p"


@pytest.mark.parametrize("mode", ["t2v", "i2v"])
@pytest.mark.parametrize("model_id", [STANDARD_MODEL, FAST_MODEL, MINI_MODEL])
def test_720p_is_allowed_for_every_video_model_and_mode(mode, model_id):
    request = _request(
        mode=mode,
        model_id=model_id,
        parameters={"resolution": "720p"},
    )

    assert request.parameters["resolution"] == "720p"


def test_image_request_materializes_catalog_defaults_and_strips_stale_video_parameters():
    t2i = GenerateRequest.model_validate(
        {
            "mode": "t2i",
            "model_id": "gpt-image-2",
            "prompt": "A painted fox",
        }
    )
    i2i = GenerateRequest.model_validate(
        {
            "mode": "i2i",
            "model_id": "gpt-image-2",
            "prompt": "Paint the fox",
            "input_media": ["playground/uploads/fox.png"],
            "parameters": {
                "size": "1024x1024",
                "quality": "high",
                "resolution": "1080p",
                "watermark": True,
            },
        }
    )

    assert t2i.parameters == {"size": "1536x1024", "quality": "auto"}
    assert i2i.parameters == {"size": "1024x1024", "quality": "high"}


def test_text_modes_drop_stale_media_and_i2v_requires_exactly_one_first_frame():
    t2v = GenerateRequest.model_validate(
        {
            "mode": "t2v",
            "model_id": FAST_MODEL,
            "prompt": "A quiet camera push-in",
            "input_media": ["playground/uploads/stale.png"],
        }
    )
    assert t2v.input_media is None

    with pytest.raises(ValidationError, match="只接受一张"):
        GenerateRequest.model_validate(
            {
                "mode": "i2v",
                "model_id": FAST_MODEL,
                "prompt": "A quiet camera push-in",
                "input_media": ["one.png", "two.png"],
            }
        )


def test_service_normalizes_legacy_empty_video_parameters_before_adapter_call():
    generation = PlaygroundGeneration(
        id="generation-1",
        mode=PlaygroundMode.T2V,
        model_id=FAST_MODEL,
        prompt="A quiet camera push-in",
        parameters={},
        created_at="2026-07-21T00:00:00+00:00",
    )
    captured = {}

    class VideoModel:
        def generate(self, **kwargs):
            captured.update(kwargs)

    service = PlaygroundService(object())
    service._newapi_video_model = VideoModel()

    service._generate_video_newapi(generation, "unused.mp4")

    assert generation.parameters == {
        "duration": 5,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
    }
    assert captured["duration"] == 5
    assert captured["resolution"] == "720p"
    assert captured["aspect_ratio"] == "16:9"


def test_service_rejects_legacy_incompatible_resolution_before_adapter_call():
    generation = PlaygroundGeneration(
        id="generation-1",
        mode=PlaygroundMode.T2V,
        model_id=FAST_MODEL,
        prompt="A quiet camera push-in",
        parameters={"resolution": "1080p"},
        created_at="2026-07-21T00:00:00+00:00",
    )

    service = PlaygroundService(object())

    with pytest.raises(ValueError, match="Seedance 2.0 文生视频"):
        service._generate_video_newapi(generation, "unused.mp4")


def test_service_persists_provider_ids_then_clears_them_after_success(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    generation = PlaygroundGeneration(
        id="accepted-generation",
        mode=PlaygroundMode.T2V,
        model_id=FAST_MODEL,
        prompt="A quiet camera push-in",
        parameters={},
        created_at="2026-07-21T00:00:00+00:00",
    )
    storage.add_generation(generation)
    service = PlaygroundService(storage)
    observed_provider_ids = []

    class VideoModel:
        def generate(self, *, output_path, on_provider_ids, **_kwargs):
            on_provider_ids("newapi", "provider-task-123", "provider-request-123")
            accepted = storage.get_generation(generation.id)
            observed_provider_ids.append(
                (
                    accepted.provider_name,
                    accepted.provider_task_id,
                    accepted.provider_request_id,
                )
            )
            from pathlib import Path

            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"video")

    service._newapi_video_model = VideoModel()
    monkeypatch.setattr(service, "_create_video_thumbnail", lambda _path: None)
    monkeypatch.setattr(service, "_enforce_server_file_quota", lambda _path: None)

    service.process_generation(generation.id)

    persisted = storage.get_generation(generation.id)
    assert observed_provider_ids == [
        ("newapi", "provider-task-123", "provider-request-123")
    ]
    assert persisted.status == "completed"
    assert len(persisted.outputs) == 1
    assert persisted.provider_name is None
    assert persisted.provider_task_id is None
    assert persisted.provider_request_id is None


def test_partial_image_batch_is_retryable_without_duplicate_outputs(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    generation = PlaygroundGeneration(
        id="partial-image-generation",
        mode=PlaygroundMode.T2I,
        model_id="gpt-image-2",
        prompt="Three illustrated lantern studies",
        parameters={},
        batch_size=3,
        created_at="2026-07-21T00:00:00+00:00",
    )
    storage.add_generation(generation)
    service = PlaygroundService(storage)
    attempts = 0

    class ImageModel:
        def generate(self, *, output_path, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("transient provider failure")
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")

    service._newapi_image_model = ImageModel()
    monkeypatch.setattr(service, "_assert_generated_media", lambda *_args: None)
    monkeypatch.setattr(service, "_enforce_server_file_quota", lambda _path: None)

    service.process_generation(generation.id)

    partial = storage.get_generation(generation.id)
    assert partial.status == "failed"
    assert partial.error_code == "partial_batch_failed"
    assert "1/3" in (partial.error or "")
    assert len(partial.outputs) == 1

    service.prepare_generation_retry(generation.id)
    service.process_generation(generation.id)

    completed = storage.get_generation(generation.id)
    assert completed.status == "completed"
    assert len(completed.outputs) == 3
    assert len({output.media_path for output in completed.outputs}) == 3
    assert attempts == 4
