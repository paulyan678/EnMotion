import pytest

from src.apps.comic_gen.models import ModelSettings
from src.utils.newapi_models import (
    CHAT,
    IMAGE,
    VIDEO,
    DEFAULT_MODELS,
    MODEL_SPECS,
    MissingNewAPIKeyError,
    get_model_spec,
    migrate_legacy_newapi_environment,
    models_for_capability,
    public_model_status,
    resolve_model_api_key,
    validate_model_for_mode,
)
from src.utils.provider_registry import (
    ProviderFamilyConfig,
    ProviderRegistry,
    SUPPORTED_PROVIDER_BACKENDS,
    get_default_provider_registry,
    get_gateway_for_model,
)


def test_registry_contains_only_the_seven_approved_models():
    assert set(MODEL_SPECS) == {
        "gpt-image-2",
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "doubao-seedance-2-0-mini-260615",
        "deepseek-v4-flash",
        "qwen3.7-max",
        "deepseek-v4-pro",
    }
    assert [m.model_id for m in models_for_capability(IMAGE)] == ["gpt-image-2"]
    assert len(models_for_capability(VIDEO)) == 3
    assert len(models_for_capability(CHAT)) == 3


@pytest.mark.parametrize("model_id", ["wan2.7-i2v", "kling-v3", "viduq3-pro", "seedance-2.0-i2v"])
def test_unsupported_models_are_rejected(model_id):
    with pytest.raises(ValueError, match="Unsupported New API model"):
        get_model_spec(model_id)


def test_modes_are_category_filtered_and_r2v_is_rejected():
    validate_model_for_mode("gpt-image-2", "i2i")
    validate_model_for_mode("doubao-seedance-2-0-fast-260128", "i2v")
    with pytest.raises(ValueError):
        validate_model_for_mode("gpt-image-2", "i2v")
    with pytest.raises(ValueError):
        validate_model_for_mode("doubao-seedance-2-0-fast-260128", "r2v")


def test_model_specific_key_resolution_never_falls_back():
    env = {
        "NEWAPI_VIDEO_API_KEY": "legacy-shared",
        "NEWAPI_SEEDANCE_2_FAST_API_KEY": "fast-only",
    }
    assert (
        resolve_model_api_key("doubao-seedance-2-0-fast-260128", VIDEO, env)
        == "fast-only"
    )
    with pytest.raises(MissingNewAPIKeyError):
        resolve_model_api_key("doubao-seedance-2-0-mini-260615", VIDEO, env)


def test_legacy_migration_copies_only_to_selected_model_without_overwrite():
    env = {
        "NEWAPI_VIDEO_MODEL": "doubao-seedance-2-0-mini-260615",
        "NEWAPI_VIDEO_API_KEY": "legacy-video",
        "NEWAPI_SEEDANCE_2_FAST_API_KEY": "keep-fast",
    }
    updates = migrate_legacy_newapi_environment(env)
    assert updates["NEWAPI_SEEDANCE_2_MINI_API_KEY"] == "legacy-video"
    assert env["NEWAPI_SEEDANCE_2_FAST_API_KEY"] == "keep-fast"
    assert "NEWAPI_SEEDANCE_2_API_KEY" not in env


def test_stale_project_settings_migrate_to_approved_defaults():
    settings = ModelSettings(
        chat_model="old-chat",
        image_model="wan2.7-image",
        i2v_model="kling-v3-i2v",
        r2v_model="viduq3-pro-r2v",
    )
    assert settings.chat_model == DEFAULT_MODELS[CHAT]
    assert settings.image_model == DEFAULT_MODELS[IMAGE]
    assert settings.video_model == DEFAULT_MODELS[VIDEO]
    assert "r2v_model" not in settings.model_dump()


def test_public_status_is_metadata_only_and_has_enabled_flag():
    rows = public_model_status({})
    assert len(rows) == 7
    assert all(row["enabled"] is True for row in rows)
    assert all(row["configured"] is False for row in rows)
    assert all("api_key" not in row for row in rows)


def test_catalog_provider_registry_exposes_newapi_only():
    registry = get_default_provider_registry()
    assert SUPPORTED_PROVIDER_BACKENDS == ("newapi",)
    assert registry.resolve_backend("gpt-image-2") == "newapi"
    assert (
        registry.resolve_backend("doubao-seedance-2-0-fast-260128")
        == "newapi"
    )


def test_catalog_provider_registry_rejects_non_newapi_metadata():
    with pytest.raises(ValueError, match="New API only"):
        ProviderRegistry(
            [
                ProviderFamilyConfig(
                    model_family="unsupported-",
                    backend_default="legacy-provider",
                )
            ]
        )

    with pytest.raises(ValueError, match="New API only"):
        ProviderRegistry(
            [
                ProviderFamilyConfig(
                    model_family="unsupported-",
                    credential_sources={"legacy-provider": ("LEGACY_KEY",)},
                )
            ]
        )


def test_gateway_lookup_rejects_provider_switching():
    assert get_gateway_for_model("gpt-image-2") == "newapi"
    with pytest.raises(ValueError, match="New API only"):
        get_gateway_for_model("gpt-image-2", backend="legacy-provider")
