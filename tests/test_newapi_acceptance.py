"""End-to-end contract tests for the New API-only model configuration.

These tests stay below the network boundary: they exercise selection,
credential routing, validation, persistence, and public configuration metadata
without making provider calls or exposing any real credentials.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.apps.comic_gen.llm_adapter import LLMAdapter
from src.apps.comic_gen.models import ModelSettings, Script
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.playground.models import GenerateRequest
from src.utils.newapi_models import (
    ACTIVE_MODEL_ENV,
    CHAT,
    DEFAULT_MODELS,
    IMAGE,
    MODEL_API_KEY_FIELDS,
    MODEL_SPECS,
    VIDEO,
    MissingNewAPIKeyError,
)


def _script() -> Script:
    return Script(
        id="project-1",
        title="Project",
        original_text="text",
        created_at=1.0,
        updated_at=1.0,
    )


def _minimal_pipeline() -> ComicGenPipeline:
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {"project-1": _script()}
    pipeline._save_data = lambda: None
    return pipeline


def test_chat_switch_rebuilds_client_with_the_matching_model_key(monkeypatch):
    """Changing chat models takes effect immediately and changes credentials."""

    created_clients = []
    requests = []

    class FakeCompletions:
        def __init__(self, client_index):
            self.client_index = client_index

        def create(self, **kwargs):
            requests.append((self.client_index, kwargs["model"]))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
            )

    class FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            created_clients.append((api_key, base_url))
            self.chat = SimpleNamespace(
                completions=FakeCompletions(len(created_clients) - 1)
            )

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("NEWAPI_DEEPSEEK_V4_FLASH_API_KEY", "credential-flash")
    monkeypatch.setenv("NEWAPI_QWEN_37_MAX_API_KEY", "credential-qwen")

    adapter = LLMAdapter()
    assert adapter.chat([{"role": "user", "content": "one"}], model="deepseek-v4-flash") == "ok"
    assert adapter.chat([{"role": "user", "content": "two"}], model="qwen3.7-max") == "ok"

    assert created_clients == [
        ("credential-flash", "http://127.0.0.1:9999/v1"),
        ("credential-qwen", "http://127.0.0.1:9999/v1"),
    ]
    assert requests == [(0, "deepseek-v4-flash"), (1, "qwen3.7-max")]


def test_missing_selected_chat_key_never_uses_legacy_or_another_model_key(monkeypatch):
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.delenv("NEWAPI_QWEN_37_MAX_API_KEY", raising=False)
    monkeypatch.setenv("NEWAPI_DEEPSEEK_V4_FLASH_API_KEY", "wrong-model-credential")
    monkeypatch.setenv("NEWAPI_CHAT_API_KEY", "legacy-capability-credential")
    monkeypatch.setenv("NEWAPI_API_KEY", "legacy-shared-credential")

    with pytest.raises(MissingNewAPIKeyError, match="NEWAPI_QWEN_37_MAX_API_KEY"):
        LLMAdapter().require_configured("qwen3.7-max")


def test_video_submission_validates_selected_model_key_before_persisting(monkeypatch):
    pipeline = _minimal_pipeline()
    monkeypatch.delenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", raising=False)
    monkeypatch.setenv("NEWAPI_SEEDANCE_2_API_KEY", "wrong-video-model-credential")
    monkeypatch.setenv("NEWAPI_VIDEO_API_KEY", "legacy-video-credential")

    with pytest.raises(MissingNewAPIKeyError, match="NEWAPI_SEEDANCE_2_FAST_API_KEY"):
        pipeline.create_video_task(
            script_id="project-1",
            image_url="https://example.invalid/frame.png",
            prompt="Animate the frame",
            model="doubao-seedance-2-0-fast-260128",
            generation_mode="i2v",
        )

    assert pipeline.scripts["project-1"].video_tasks == []


def test_video_model_switch_is_applied_without_pipeline_restart(monkeypatch):
    pipeline = _minimal_pipeline()
    monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "credential-fast")
    monkeypatch.setenv("NEWAPI_SEEDANCE_2_MINI_API_KEY", "credential-mini")

    with patch.object(pipeline, "_save_data"):
        script, first_id = pipeline.create_video_task(
            script_id="project-1",
            image_url="https://example.invalid/first.png",
            prompt="First take",
            model="doubao-seedance-2-0-fast-260128",
            generation_mode="i2v",
        )
        script, second_id = pipeline.create_video_task(
            script_id="project-1",
            image_url="https://example.invalid/second.png",
            prompt="Second take",
            model="doubao-seedance-2-0-mini-260615",
            generation_mode="i2v",
        )

    tasks = {task.id: task for task in script.video_tasks}
    assert tasks[first_id].model == "doubao-seedance-2-0-fast-260128"
    assert tasks[second_id].model == "doubao-seedance-2-0-mini-260615"


def test_stale_saved_model_settings_migrate_to_each_capability_default():
    settings = ModelSettings.model_validate(
        {
            "chat_model": "legacy-chat-model",
            "image_model": "wan2.7-image-pro",
            "video_model": "vidu-q3-pro",
            "r2v_model": "kling-v3-r2v",
        }
    )

    assert settings.chat_model == DEFAULT_MODELS[CHAT]
    assert settings.image_model == DEFAULT_MODELS[IMAGE]
    assert settings.video_model == DEFAULT_MODELS[VIDEO]
    assert settings.t2i_model == settings.image_model
    assert settings.i2i_model == settings.image_model
    assert settings.i2v_model == settings.video_model
    assert not hasattr(settings, "r2v_model")


@pytest.mark.parametrize(
    ("payload", "error_fragment"),
    [
        (
            {"mode": "i2v", "model_id": "kling-v3-i2v", "prompt": "x", "input_media": ["x.png"]},
            "Unsupported New API model ID",
        ),
        (
            {"mode": "i2v", "model_id": "gpt-image-2", "prompt": "x", "input_media": ["x.png"]},
            "not a video model",
        ),
        (
            {"mode": "r2v", "model_id": "doubao-seedance-2-0-fast-260128", "prompt": "x"},
            "Input should be 't2i', 'i2i', 't2v' or 'i2v'",
        ),
    ],
)
def test_playground_rejects_unsupported_models_and_modes(payload, error_fragment):
    with pytest.raises(ValidationError, match=error_fragment):
        GenerateRequest.model_validate(payload)


def test_public_config_masks_secrets_and_returns_metadata_only(monkeypatch):
    from src.apps.comic_gen import api

    for field in api.SECRET_FIELDS:
        monkeypatch.delenv(field, raising=False)
    for capability, env_field in ACTIVE_MODEL_ENV.items():
        monkeypatch.setenv(env_field, DEFAULT_MODELS[capability])
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "configured-image-credential-9876")

    response = api.get_env_config()

    masked = response["NEWAPI_GPT_IMAGE_2_API_KEY"]
    assert masked.endswith("9876")
    assert masked != "configured-image-credential-9876"
    assert "configured-image-credential-9876" not in repr(response)
    assert response["secrets_configured"]["NEWAPI_GPT_IMAGE_2_API_KEY"] is True
    assert "NEWAPI_MODELS" not in response
    assert {
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "OSS_BUCKET_NAME",
        "OSS_ENDPOINT",
        "OSS_BASE_PATH",
        "OSS_ENABLE",
    }.isdisjoint(response)


def test_admin_api_key_inspection_is_masked_by_default_and_never_cached(monkeypatch):
    from src.apps.comic_gen import api

    for field in api.SECRET_FIELDS:
        monkeypatch.delenv(field, raising=False)
    for capability, env_field in ACTIVE_MODEL_ENV.items():
        monkeypatch.setenv(env_field, DEFAULT_MODELS[capability])
    secret = "configured-image-credential-9876"
    monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", secret)
    monkeypatch.setattr(api, "server_mode_enabled", lambda: True)
    request = SimpleNamespace(
        state=SimpleNamespace(actor=SimpleNamespace(role="admin"))
    )

    response = api.inspect_api_keys(api.APIKeyInspectionRequest(), request)
    payload = json.loads(response.body)
    image_key = next(
        item
        for item in payload["items"]
        if item["api_key_field"] == "NEWAPI_GPT_IMAGE_2_API_KEY"
    )

    assert payload["revealed"] is False
    assert image_key["value"].endswith("9876")
    assert image_key["value"] != secret
    assert image_key["configured"] is True
    assert image_key["active"] is True
    assert image_key["in_use"] is True
    assert all("model_id" not in item for item in payload["items"])
    assert secret not in response.body.decode()
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"


def test_admin_must_explicitly_request_full_api_key_values(monkeypatch):
    from src.apps.comic_gen import api

    for field in api.SECRET_FIELDS:
        monkeypatch.delenv(field, raising=False)
    for capability, env_field in ACTIVE_MODEL_ENV.items():
        monkeypatch.setenv(env_field, DEFAULT_MODELS[capability])
    secret = "admin-explicit-reveal-secret"
    monkeypatch.setenv("NEWAPI_DEEPSEEK_V4_FLASH_API_KEY", secret)
    monkeypatch.setattr(api, "server_mode_enabled", lambda: True)
    request = SimpleNamespace(
        state=SimpleNamespace(actor=SimpleNamespace(role="admin"))
    )

    response = api.inspect_api_keys(
        api.APIKeyInspectionRequest(reveal=True),
        request,
    )
    payload = json.loads(response.body)
    chat_key = next(
        item
        for item in payload["items"]
        if item["api_key_field"] == "NEWAPI_DEEPSEEK_V4_FLASH_API_KEY"
    )

    assert payload["revealed"] is True
    assert chat_key["value"] == secret
    assert chat_key["in_use"] is True


@pytest.mark.parametrize("role", [None, "user"])
def test_api_key_inspection_rejects_non_admin_accounts(monkeypatch, role):
    from src.apps.comic_gen import api

    monkeypatch.setattr(api, "server_mode_enabled", lambda: True)
    actor = None if role is None else SimpleNamespace(role=role)
    request = SimpleNamespace(state=SimpleNamespace(actor=actor))

    with pytest.raises(api.HTTPException) as exc_info:
        api.inspect_api_keys(api.APIKeyInspectionRequest(reveal=True), request)

    assert exc_info.value.status_code == 404


def test_api_key_inspection_is_not_exposed_without_server_auth(monkeypatch):
    from src.apps.comic_gen import api

    monkeypatch.setattr(api, "server_mode_enabled", lambda: False)
    request = SimpleNamespace(
        state=SimpleNamespace(actor=SimpleNamespace(role="admin"))
    )

    with pytest.raises(api.HTTPException) as exc_info:
        api.inspect_api_keys(api.APIKeyInspectionRequest(), request)

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ALIBABA_CLOUD_ACCESS_KEY_ID", "removed"),
        ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "removed"),
        ("OSS_BUCKET_NAME", "removed"),
        ("OSS_ENDPOINT", "removed"),
        ("OSS_BASE_PATH", "removed"),
        ("OSS_ENABLE", True),
    ],
)
def test_settings_config_rejects_removed_storage_fields(field, value):
    from src.apps.comic_gen import api

    with pytest.raises(ValidationError):
        api.EnvConfig.model_validate({field: value})


def test_masked_secret_round_trip_does_not_overwrite_saved_value(monkeypatch):
    from src.apps.comic_gen import api

    monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "existing-credential")
    saved = []
    monkeypatch.setattr(api, "save_user_config", lambda values: saved.append(values))

    result = api.update_env_config(
        api.EnvConfig(NEWAPI_GPT_IMAGE_2_API_KEY="••••••••tial")
    )

    assert result["status"] == "success"
    assert saved == [{}]
    assert os.environ["NEWAPI_GPT_IMAGE_2_API_KEY"] == "existing-credential"


def test_global_model_selections_persist_with_private_permissions(monkeypatch, tmp_path):
    from src.apps.comic_gen import api

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))

    selections = {
        "NEWAPI_CHAT_MODEL": "deepseek-v4-pro",
        "NEWAPI_IMAGE_MODEL": "gpt-image-2",
        "NEWAPI_VIDEO_MODEL": "doubao-seedance-2-0-mini-260615",
    }
    api.save_user_config(selections)
    for field in selections:
        monkeypatch.delenv(field, raising=False)
    api.load_user_config()

    assert {field: os.environ[field] for field in selections} == selections
    assert json.loads(config_path.read_text()) == selections
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_configuration_schema_rejects_removed_provider_fields():
    from src.apps.comic_gen.api import EnvConfig

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EnvConfig.model_validate({"DASHSCOPE_API_KEY": "removed-provider-credential"})


def test_configuration_schema_rejects_cross_capability_active_model():
    from src.apps.comic_gen.api import EnvConfig

    with pytest.raises(ValidationError, match="not a chat model"):
        EnvConfig.model_validate({"NEWAPI_CHAT_MODEL": "gpt-image-2"})


def test_every_approved_model_declares_one_unique_key_field():
    fields = [spec.api_key_env for spec in MODEL_SPECS.values()]
    assert tuple(fields) == MODEL_API_KEY_FIELDS
    assert len(fields) == len(set(fields)) == 7
