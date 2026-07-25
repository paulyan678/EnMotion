import json
from copy import deepcopy

import pytest
import yaml

from src.utils.model_catalog import (
    FRONTEND_GENERATED_MODEL_CATALOG_PATH,
    GENERATED_MODEL_CATALOG_PATH,
    MODEL_CATALOG_ROOT,
    MODEL_CATALOG_SCHEMA_PATH,
    SUPPORTED_PROVIDER_BACKENDS,
    SUPPORTED_SELECTION_GROUPS,
    build_catalog_dict,
    build_catalog_validation_report,
    build_provider_family_configs,
    get_catalog_accessor,
    get_default_model_settings,
    write_frontend_generated_catalog,
    write_generated_catalog,
)


APPROVED_BY_GROUP = {
    "chat": {
        "deepseek-v4-flash",
        "qwen3.7-max",
        "deepseek-v4-pro",
    },
    "image": {"gpt-image-2"},
    "video": {
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "doubao-seedance-2-0-mini-260615",
    },
}

APPROVED_MODELS = set().union(*APPROVED_BY_GROUP.values())

EXPECTED_DEFAULTS = {
    "chat_model": "deepseek-v4-flash",
    "image_model": "gpt-image-2",
    "video_model": "doubao-seedance-2-0-fast-260128",
}

MODEL_CREDENTIALS = {
    "gpt-image-2": "NEWAPI_GPT_IMAGE_2_API_KEY",
    "doubao-seedance-2-0-260128": "NEWAPI_SEEDANCE_2_API_KEY",
    "doubao-seedance-2-0-fast-260128": "NEWAPI_SEEDANCE_2_FAST_API_KEY",
    "doubao-seedance-2-0-mini-260615": "NEWAPI_SEEDANCE_2_MINI_API_KEY",
    "deepseek-v4-flash": "NEWAPI_DEEPSEEK_V4_FLASH_API_KEY",
    "qwen3.7-max": "NEWAPI_QWEN_37_MAX_API_KEY",
    "deepseek-v4-pro": "NEWAPI_DEEPSEEK_V4_PRO_API_KEY",
}


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_yaml(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_minimal_catalog(
    root,
    *,
    provider="newapi",
    backend="newapi",
    video_capabilities=None,
    video_selection_group="video",
    video_credentials=None,
    defaults=None,
):
    model_defaults = defaults or {
        "chat_model": "test-chat",
        "image_model": "test-image",
        "video_model": "test-video",
    }
    _write_yaml(
        root / "catalog.meta.yaml",
        {"version": 1, "defaults": {"model_settings": model_defaults}},
    )
    _write_yaml(
        root / "families" / "test.yaml",
        {
            "family": "test",
            "provider": provider,
            "routing_prefixes": ["test-"],
            "supported_backends": [backend],
            "default_backend": backend,
            "credential_sources": {
                backend: ["TEST_CHAT_KEY", "TEST_IMAGE_KEY", "TEST_VIDEO_KEY"]
            },
            "supported_modalities": ["chat", "t2i", "i2i", "t2v", "i2v"],
            "docs": {"official_snapshot_ids": ["newapi/test"]},
            "transport": {
                "image_input_mode": {backend: "test"},
                "audio_input_mode": {backend: "unsupported"},
                "reference_video_input_mode": {backend: "unsupported"},
            },
            "models": [
                {
                    "id": "test-chat",
                    "display_name": "Test Chat",
                    "description": "Test chat model",
                    "status": "active",
                    "release_stage": "stable",
                    "capabilities": ["chat"],
                    "credential_sources": {backend: ["TEST_CHAT_KEY"]},
                    "docs": {"context_hub_doc_ids": ["newapi/test-chat"]},
                    "runtime": {backend: {"gateway": "newapi"}},
                    "ui": {
                        "selection_group": "chat",
                        "visible_in": [
                            "project_settings",
                            "series_settings",
                            "global_settings",
                        ],
                    },
                },
                {
                    "id": "test-image",
                    "display_name": "Test Image",
                    "description": "Test image model",
                    "status": "active",
                    "release_stage": "stable",
                    "capabilities": ["t2i", "i2i"],
                    "credential_sources": {backend: ["TEST_IMAGE_KEY"]},
                    "docs": {"context_hub_doc_ids": ["newapi/test-image"]},
                    "runtime": {backend: {"gateway": "newapi"}},
                    "ui": {
                        "selection_group": "image",
                        "visible_in": [
                            "project_settings",
                            "series_settings",
                            "global_settings",
                        ],
                    },
                },
                {
                    "id": "test-video",
                    "display_name": "Test Video",
                    "description": "Test video model",
                    "status": "active",
                    "release_stage": "stable",
                    "capabilities": video_capabilities or ["t2v", "i2v"],
                    "credential_sources": {
                        backend: video_credentials or ["TEST_VIDEO_KEY"]
                    },
                    "docs": {"context_hub_doc_ids": ["newapi/test-video"]},
                    "runtime": {backend: {"gateway": "newapi"}},
                    "ui": {
                        "selection_group": video_selection_group,
                        "visible_in": [
                            "project_settings",
                            "series_settings",
                            "video_sidebar",
                            "global_settings",
                        ],
                    },
                },
            ],
        },
    )


def test_repo_catalog_contains_only_approved_models():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

    assert set(catalog["models"]) == APPROVED_MODELS
    assert len(catalog["models"]) == 7
    assert set(catalog["families"]) == {"newapi-chat", "gpt-image", "seedance"}


def test_models_are_filtered_into_exact_capability_groups():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    actual = {group: set() for group in SUPPORTED_SELECTION_GROUPS}

    for model_id, model in catalog["models"].items():
        actual[model["ui"]["selection_group"]].add(model_id)

    assert actual == APPROVED_BY_GROUP


def test_newapi_is_the_only_provider_and_backend():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

    assert SUPPORTED_PROVIDER_BACKENDS == ("newapi",)
    assert {family["provider"] for family in catalog["families"].values()} == {"newapi"}
    assert {
        backend
        for family in catalog["families"].values()
        for backend in family["supported_backends"]
    } == {"newapi"}
    assert {model["default_backend"] for model in catalog["models"].values()} == {
        "newapi"
    }


def test_each_model_has_exactly_its_own_key_and_gateway():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

    for model_id, expected_key in MODEL_CREDENTIALS.items():
        model = catalog["models"][model_id]
        assert model["credential_sources"] == {"newapi": [expected_key]}
        canonical_id = catalog["compat"]["legacy_model_ids"][model_id]
        mode = catalog["modes"][canonical_id]
        assert mode["credential_sources"] == {"newapi": [expected_key]}
        assert mode["runtime"] == {"newapi": {"gateway": "newapi"}}


def test_defaults_are_independent_and_exact():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    defaults = get_default_model_settings(MODEL_CATALOG_ROOT)

    assert catalog["defaults"]["model_settings"] == EXPECTED_DEFAULTS
    assert defaults.chat_model == EXPECTED_DEFAULTS["chat_model"]
    assert defaults.image_model == EXPECTED_DEFAULTS["image_model"]
    assert defaults.video_model == EXPECTED_DEFAULTS["video_model"]


def test_no_reference_to_video_capability_is_advertised():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

    assert all("r2v" not in model["capabilities"] for model in catalog["models"].values())
    for model_id in APPROVED_BY_GROUP["video"]:
        model = catalog["models"][model_id]
        assert set(model["capabilities"]) == {"t2v", "i2v"}
        assert model["inputs"]["reference_images"]["max"] == 1


def test_removed_provider_and_model_names_are_absent_from_generated_catalog():
    payload = json.dumps(build_catalog_dict(MODEL_CATALOG_ROOT)).lower()

    for removed in (
        "dashscope",
        "mulerouter",
        "mulerun",
        "happyhorse",
        "kling",
        "pixverse",
        "vidu",
        "wan2.",
        "qwen-image",
    ):
        assert removed not in payload


def test_generated_artifacts_match_sources_and_each_other():
    source = build_catalog_dict(MODEL_CATALOG_ROOT)

    assert _load_json(GENERATED_MODEL_CATALOG_PATH) == source
    assert _load_json(FRONTEND_GENERATED_MODEL_CATALOG_PATH) == source


def test_generated_catalog_is_deterministic(tmp_path):
    backend_a = tmp_path / "backend-a.json"
    backend_b = tmp_path / "backend-b.json"
    frontend = tmp_path / "frontend.json"

    write_generated_catalog(backend_a, catalog_root=MODEL_CATALOG_ROOT)
    write_generated_catalog(backend_b, catalog_root=MODEL_CATALOG_ROOT)
    write_frontend_generated_catalog(frontend, catalog_root=MODEL_CATALOG_ROOT)

    assert backend_a.read_bytes() == backend_b.read_bytes() == frontend.read_bytes()


def test_generated_schema_requires_only_three_active_model_fields():
    schema = _load_json(MODEL_CATALOG_SCHEMA_PATH)
    model_settings = schema["properties"]["defaults"]["properties"]["model_settings"]

    assert model_settings["additionalProperties"] is False
    assert set(model_settings["required"]) == {
        "chat_model",
        "image_model",
        "video_model",
    }
    assert set(model_settings["properties"]) == set(model_settings["required"])


def test_provider_family_configs_are_newapi_only():
    configs = build_provider_family_configs(build_catalog_dict(MODEL_CATALOG_ROOT))

    assert configs
    assert {config.backend_default for config in configs} == {"newapi"}
    assert all(config.backend_env_key is None for config in configs)
    assert all(set(config.credential_sources) == {"newapi"} for config in configs)
    assert all("r2v" not in config.supported_modalities for config in configs)


def test_canonical_accessor_resolves_every_approved_model():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    accessor = get_catalog_accessor(catalog)

    assert set(accessor.all_legacy_model_ids()) == APPROVED_MODELS
    assert len(accessor.all_canonical_mode_ids()) == 7
    for model_id in APPROVED_MODELS:
        canonical_id = accessor.resolve_legacy_to_canonical(model_id)
        assert canonical_id is not None
        assert accessor.resolve_canonical_to_legacy(canonical_id) == model_id
        assert accessor.resolve_to_flat(canonical_id) == model_id
        assert accessor.get_gateway(canonical_id) == "newapi"


def test_validation_report_passes_and_summarizes_exact_groups():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    report = build_catalog_validation_report(catalog, deepcopy(catalog))

    assert report.ok is True
    assert report.errors == ()
    assert report.stats["models"] == 7
    assert report.stats["defaults"] == EXPECTED_DEFAULTS
    assert set(report.stats["surface_summary"]["global_settings"]["chat"]) == (
        APPROVED_BY_GROUP["chat"]
    )
    assert set(report.stats["surface_summary"]["global_settings"]["image"]) == (
        APPROVED_BY_GROUP["image"]
    )
    assert set(report.stats["surface_summary"]["global_settings"]["video"]) == (
        APPROVED_BY_GROUP["video"]
    )


def test_validation_report_detects_generated_artifact_drift():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    frontend = deepcopy(catalog)
    frontend["models"].pop("deepseek-v4-pro")

    report = build_catalog_validation_report(catalog, frontend)

    assert report.ok is False
    assert any("does not match" in error for error in report.errors)


def test_default_validation_rebuilds_source_before_checking_artifacts(monkeypatch):
    source = build_catalog_dict(MODEL_CATALOG_ROOT)
    stale_backend = deepcopy(source)
    stale_backend["models"].pop("deepseek-v4-pro")

    monkeypatch.setattr(
        "src.utils.model_catalog.load_generated_model_catalog",
        lambda: stale_backend,
    )
    monkeypatch.setattr(
        "src.utils.model_catalog.load_frontend_generated_model_catalog",
        lambda: source,
    )

    report = build_catalog_validation_report()

    assert report.ok is False
    assert any("source YAML" in error for error in report.errors)


def test_validation_report_detects_default_visibility_regression():
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    broken = deepcopy(catalog)
    video_default = broken["defaults"]["model_settings"]["video_model"]
    broken["models"][video_default]["ui"]["visible_in"].remove("video_sidebar")

    report = build_catalog_validation_report(broken, deepcopy(broken))

    assert report.ok is False
    assert any("video_sidebar" in error for error in report.errors)


def test_unsupported_provider_is_rejected(tmp_path):
    _write_minimal_catalog(tmp_path, provider="legacy-provider")

    with pytest.raises(ValueError, match="Unsupported provider"):
        build_catalog_dict(tmp_path)


def test_noncanonical_family_filename_is_rejected(tmp_path):
    _write_minimal_catalog(tmp_path)
    family_path = tmp_path / "families" / "test.yaml"
    family_path.rename(tmp_path / "families" / "test 2.yaml")

    with pytest.raises(ValueError, match="lowercase kebab-case"):
        build_catalog_dict(tmp_path)


def test_unsupported_backend_is_rejected(tmp_path):
    _write_minimal_catalog(tmp_path, backend="legacy-backend")

    with pytest.raises(ValueError, match="Unsupported backend"):
        build_catalog_dict(tmp_path)


def test_reference_to_video_capability_is_rejected(tmp_path):
    _write_minimal_catalog(tmp_path, video_capabilities=["t2v", "r2v"])

    with pytest.raises(ValueError, match="incompatible with selection_group"):
        build_catalog_dict(tmp_path)


def test_capability_cannot_be_placed_in_wrong_selector(tmp_path):
    _write_minimal_catalog(
        tmp_path,
        video_capabilities=["t2v"],
        video_selection_group="chat",
    )

    with pytest.raises(ValueError, match="incompatible with selection_group"):
        build_catalog_dict(tmp_path)


def test_model_cannot_declare_multiple_fallback_credentials(tmp_path):
    _write_minimal_catalog(
        tmp_path,
        video_credentials=["TEST_VIDEO_KEY", "FALLBACK_KEY"],
    )

    with pytest.raises(ValueError, match="exactly one credential"):
        build_catalog_dict(tmp_path)


def test_default_cannot_point_to_another_capability_group(tmp_path):
    _write_minimal_catalog(
        tmp_path,
        defaults={
            "chat_model": "test-video",
            "image_model": "test-image",
            "video_model": "test-video",
        },
    )

    with pytest.raises(ValueError, match="must select a 'chat' model"):
        build_catalog_dict(tmp_path)
