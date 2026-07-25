from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.apps.playground.models import (
    GenerateRequest,
    PlaygroundGeneration,
    PlaygroundMode,
)
from src.apps.playground.service import PlaygroundService


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


def test_image_request_parameters_are_not_rewritten():
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
            "parameters": {"size": "1024x1024", "quality": "high"},
        }
    )

    assert t2i.parameters is None
    assert i2i.parameters == {"size": "1024x1024", "quality": "high"}


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
