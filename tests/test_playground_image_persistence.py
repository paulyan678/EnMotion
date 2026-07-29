import json

import pytest

from src.apps.playground.models import PlaygroundOutput
from src.apps.playground.storage import PlaygroundStorage
from src.models.newapi import NewAPIImageModel


@pytest.mark.parametrize(
    ("legacy", "expected_path", "expected_type"),
    [
        ("output/playground/images/string.png", "output/playground/images/string.png", "image"),
        (
            {"image_url": "https://provider.example/image"},
            "https://provider.example/image",
            "image",
        ),
        ({"video_url": "playground/videos/clip.mp4"}, "playground/videos/clip.mp4", "video"),
        (
            {"data": [{"url": "/files/playground/images/nested.webp"}]},
            "/files/playground/images/nested.webp",
            "image",
        ),
    ],
)
def test_legacy_playground_outputs_serialize_to_one_media_contract(
    legacy, expected_path, expected_type
):
    output = PlaygroundOutput.model_validate(legacy)

    assert output.id
    assert output.media_path == expected_path
    assert output.media_type == expected_type
    assert output.model_dump() == {
        "id": output.id,
        "media_path": expected_path,
        "media_type": expected_type,
        "thumbnail_path": None,
        "saved_to_library": False,
        "library_category": None,
        "library_asset_id": None,
        "library_media_path": None,
    }


def test_restored_legacy_history_is_normalized_after_server_restart(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    history_path = output_root / "playground_history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-generation",
                    "mode": "t2i",
                    "model_id": "gpt-image-2",
                    "prompt": "restored image",
                    "results": [
                        {
                            "image_url": "playground/images/restored.png",
                            "saved": True,
                        }
                    ],
                    "status": "completed",
                    "created_at": "2026-07-22T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    restored = PlaygroundStorage(output_root=str(output_root)).list_history()[0]
    assert restored.outputs[0].media_path == "playground/images/restored.png"
    assert restored.outputs[0].saved_to_library is True
    assert restored.outputs[0].library_category == "prop"
    assert restored.finished_at == restored.updated_at

    # A normal later mutation rewrites the legacy envelope in canonical form,
    # and another process/restart can still restore it.
    storage = PlaygroundStorage(output_root=str(output_root))
    storage.update_generation(restored)
    reloaded = PlaygroundStorage(output_root=str(output_root)).list_history()[0]
    assert reloaded.outputs == restored.outputs
    assert "results" not in history_path.read_text(encoding="utf-8")


def test_temporary_provider_url_is_downloaded_to_a_persistent_file(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Type": "image/png"}
        text = ""

        def __init__(self, payload=None, body=b""):
            self.payload = payload
            self.content = body

        def json(self):
            return self.payload

        def iter_content(self, chunk_size=65536):
            del chunk_size
            if self.content:
                yield self.content

    provider_url = "https://provider.example/temporary/result.png?signature=short-lived"
    responses = iter(
        [
            Response({"data": [{"url": provider_url}]}),
            Response(body=b"persisted-image-bytes"),
        ]
    )

    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
    monkeypatch.setattr(
        "src.models.newapi.requests.request",
        lambda *_args, **_kwargs: next(responses),
    )

    output_path = tmp_path / "output" / "playground" / "images" / "result.png"
    returned_path, _ = NewAPIImageModel({}).generate(
        "draw a persistent image",
        str(output_path),
        model_id="gpt-image-2",
    )

    assert returned_path == str(output_path)
    assert output_path.read_bytes() == b"persisted-image-bytes"
    assert provider_url not in output_path.read_text(encoding="latin-1")
