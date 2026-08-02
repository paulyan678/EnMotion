from __future__ import annotations

import pytest

from src.apps.generation_contract import (
    assert_generation_checksum,
    compile_generation_request,
    provider_request,
)
from src.apps.playground.models import GenerateRequest, compile_playground_request


SEEDANCE_MODELS = (
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-2-0-mini-260615",
)


def test_generation_snapshot_is_deterministic_and_checksum_protected():
    request = provider_request(
        phase="image",
        model="gpt-image-2",
        prompt="  A quiet harbor at dawn  ",
        parameters={"quality": "high", "size": "1536x1024"},
    )

    first = compile_generation_request(
        category="image",
        mode="t2i",
        user_prompt="A quiet harbor at dawn",
        source="playground",
        requests=[request],
        target={"surface": "playground"},
    )
    second = compile_generation_request(
        category="image",
        mode="t2i",
        user_prompt="A quiet harbor at dawn",
        source="playground",
        requests=[request],
        target={"surface": "playground"},
    )

    assert first == second
    assert first["compiled_request_id"].startswith("genreq_")
    assert len(first["checksum"]) == 64
    assert_generation_checksum(first, first["checksum"].upper())
    with pytest.raises(ValueError, match="changed after provider-request review"):
        assert_generation_checksum(first, "0" * 64)


def test_gpt_image_snapshot_only_exposes_fields_the_provider_receives():
    request = GenerateRequest.model_validate(
        {
            "mode": "i2i",
            "model_id": "gpt-image-2",
            "prompt": "Turn the sketch into a cinematic still",
            "negative_prompt": "legacy field that NewAPI ignores",
            "input_media": ["playground/uploads/sketch.png"],
            "parameters": {"size": "1024x1024", "quality": "high"},
            "batch_size": 3,
        }
    )

    compiled = compile_playground_request(request)
    exact = compiled["provider_requests"][0]

    assert exact == {
        "phase": "image",
        "model": "gpt-image-2",
        "prompt": "Turn the sketch into a cinematic still",
        "parameters": {"quality": "high", "size": "1024x1024"},
        "input_media": ["playground/uploads/sketch.png"],
    }
    assert compiled["target"]["requested_outputs"] == 3


@pytest.mark.parametrize("model_id", SEEDANCE_MODELS)
@pytest.mark.parametrize("mode", ("t2v", "i2v"))
def test_every_seedance_model_and_mode_compiles_the_selected_request(model_id, mode):
    payload = {
        "mode": mode,
        "model_id": model_id,
        "prompt": "A smooth orbit around a paper sculpture",
        "parameters": {
            "duration": 6,
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "generate_audio": True,
            "watermark": False,
            "seed": 42,
        },
    }
    if mode == "i2v":
        payload["input_media"] = ["playground/uploads/sculpture.png"]

    compiled = compile_playground_request(GenerateRequest.model_validate(payload))
    exact = compiled["provider_requests"][0]

    assert compiled["mode"] == mode
    assert exact["model"] == model_id
    assert exact["parameters"] == payload["parameters"]
    assert exact["input_media"] == (
        ["playground/uploads/sculpture.png"] if mode == "i2v" else []
    )


def test_snapshot_normalization_deduplicates_and_bounds_reference_media():
    media = [" same.png ", "same.png", *[f"image-{index}.png" for index in range(30)]]
    exact = provider_request(
        phase="image",
        model="gpt-image-2",
        prompt="Use the references",
        input_media=media,
    )

    assert exact["input_media"][0] == "same.png"
    assert len(exact["input_media"]) == 16
