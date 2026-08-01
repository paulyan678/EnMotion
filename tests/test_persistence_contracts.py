"""Regression tests for user-facing JSON persistence failure semantics."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.comic_gen.models import Script
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.playground import api as playground_api
from src.apps.playground import storage as storage_module
from src.apps.playground.models import (
    PlaygroundGeneration,
    PlaygroundMode,
    PlaygroundOutput,
    PlaygroundTemplate,
)
from src.apps.playground.service import (
    PlaygroundService,
    UnsupportedPlaygroundLibraryMediaError,
)
from src.apps.playground.storage import PlaygroundStorage


def _generation(generation_id: str = "generation-1") -> PlaygroundGeneration:
    return PlaygroundGeneration(
        id=generation_id,
        mode=PlaygroundMode.T2I,
        model_id="gpt-image-2",
        prompt="draw a fox",
        status="pending",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _template(template_id: str = "template-1") -> PlaygroundTemplate:
    return PlaygroundTemplate(
        id=template_id,
        name="Portrait",
        prompt="draw a portrait",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _storage(tmp_path, monkeypatch) -> PlaygroundStorage:
    monkeypatch.setattr(
        PlaygroundStorage, "HISTORY_PATH", str(tmp_path / "playground_history.json")
    )
    monkeypatch.setattr(
        PlaygroundStorage, "TEMPLATES_PATH", str(tmp_path / "playground_templates.json")
    )
    return PlaygroundStorage()


def test_disjoint_audio_mix_updates_are_atomic():
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.read_only = False
    script = Script(
        id="mix-project",
        title="Mix",
        original_text="",
        bgm_url="presets/bgm/old.mp3",
        mix_settings={"dialogue": 100, "bgm": 35, "sfx": 60},
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline.scripts = {script.id: script}
    first_save_entered = threading.Event()
    second_update_started = threading.Event()
    release_first_save = threading.Event()
    save_count = 0

    def save_data():
        nonlocal save_count
        save_count += 1
        if save_count == 1:
            first_save_entered.set()
            assert release_first_save.wait(timeout=5)

    pipeline._save_data = save_data

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            pipeline.update_audio_mix,
            script.id,
            {"dialogue_volume": 40},
        )
        assert first_save_entered.wait(timeout=5)

        def update_bgm():
            second_update_started.set()
            return pipeline.update_audio_mix(
                script.id,
                {"bgm_url": "presets/bgm/new.mp3", "bgm_volume": 20},
            )

        second = executor.submit(update_bgm)
        assert second_update_started.wait(timeout=5)
        release_first_save.set()
        first.result(timeout=5)
        with pytest.raises(RuntimeError, match="assembly operation is already running"):
            second.result(timeout=5)

    # A rejected concurrent client can safely retry after the first atomic
    # mutation completes without losing that first client's disjoint field.
    pipeline.update_audio_mix(
        script.id,
        {"bgm_url": "presets/bgm/new.mp3", "bgm_volume": 20},
    )

    assert script.bgm_url == "presets/bgm/new.mp3"
    assert script.mix_settings == {"dialogue": 40, "bgm": 20, "sfx": 60}
    assert save_count == 2


def test_audio_mix_change_invalidates_and_retires_merged_video(tmp_path):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.read_only = False
    pipeline.output_root = str(tmp_path)
    merged_path = tmp_path / "comic_gen" / "mix-project" / "merged.mp4"
    merged_path.parent.mkdir(parents=True)
    merged_path.write_bytes(b"old merged video")
    script = Script(
        id="mix-project",
        title="Mix",
        original_text="",
        bgm_url="presets/bgm/old.mp3",
        merged_video_url="comic_gen/mix-project/merged.mp4",
        mix_settings={"dialogue": 100, "bgm": 35, "sfx": 60},
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None

    updated = pipeline.update_audio_mix(script.id, {"dialogue_volume": 40})

    assert updated.merged_video_url is None
    assert updated.mix_settings == {"dialogue": 40, "bgm": 35, "sfx": 60}
    assert updated.updated_at > 1.0
    assert not merged_path.exists()


def test_audio_mix_noop_preserves_current_merged_video(tmp_path):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.read_only = False
    pipeline.output_root = str(tmp_path)
    merged_path = tmp_path / "comic_gen" / "mix-project" / "merged.mp4"
    merged_path.parent.mkdir(parents=True)
    merged_path.write_bytes(b"current merged video")
    script = Script(
        id="mix-project",
        title="Mix",
        original_text="",
        bgm_url="presets/bgm/current.mp3",
        merged_video_url="comic_gen/mix-project/merged.mp4",
        mix_settings={"dialogue": 40, "bgm": 35, "sfx": 60},
        created_at=1.0,
        updated_at=2.0,
    )
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: pytest.fail("no-op mix update must not persist")

    updated = pipeline.update_audio_mix(
        script.id,
        {
            "bgm_url": "presets/bgm/current.mp3",
            "dialogue_volume": 40,
        },
    )

    assert updated.merged_video_url == "comic_gen/mix-project/merged.mp4"
    assert updated.updated_at == 2.0
    assert merged_path.exists()


def test_merge_and_export_share_one_project_operation_lock():
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    script = Script(
        id="assembly-project",
        title="Assembly",
        original_text="",
        merged_video_url="comic_gen/assembly-project/merged.mp4",
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline.scripts = {script.id: script}
    export_started = threading.Event()
    release_export = threading.Event()

    def render_project(_script, _options):
        export_started.set()
        assert release_export.wait(timeout=5)
        return "comic_gen/assembly-project/export.mp4"

    pipeline.export_manager = SimpleNamespace(render_project=render_project)

    with ThreadPoolExecutor(max_workers=1) as executor:
        export = executor.submit(pipeline.export_project, script.id, {})
        assert export_started.wait(timeout=5)
        with pytest.raises(RuntimeError, match="assembly operation is already running"):
            pipeline.merge_videos(script.id)
        with pytest.raises(RuntimeError, match="assembly operation is already running"):
            pipeline.update_audio_mix(script.id, {"dialogue_volume": 25})
        assert script.mix_settings == {"dialogue": 100, "bgm": 35, "sfx": 60}
        release_export.set()
        assert export.result(timeout=5) == "comic_gen/assembly-project/export.mp4"


@pytest.mark.parametrize("target_name", ["playground_history.json", "playground_templates.json"])
def test_malformed_playground_state_fails_closed(tmp_path, monkeypatch, target_name):
    target = tmp_path / target_name
    target.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Failed to load playground data"):
        _storage(tmp_path, monkeypatch)

    assert target.read_text(encoding="utf-8") == "{broken"


def test_playground_atomic_write_preserves_existing_file(tmp_path, monkeypatch):
    storage = _storage(tmp_path, monkeypatch)
    storage.add_generation(_generation())
    target = tmp_path / "playground_history.json"
    original = target.read_text(encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(storage_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        storage.add_generation(_generation("generation-2"))

    assert target.read_text(encoding="utf-8") == original
    assert [item.id for item in storage.list_history()] == ["generation-1"]
    assert list(tmp_path.glob(".playground_history.json.*.tmp")) == []


def test_running_playground_generation_cannot_be_deleted(tmp_path, monkeypatch):
    storage = _storage(tmp_path, monkeypatch)
    generation = _generation()
    generation.status = "processing"
    storage.add_generation(generation)
    monkeypatch.setattr(playground_api, "_current_storage", lambda: storage)

    with pytest.raises(HTTPException) as error:
        playground_api.delete_generation(generation.id)

    assert error.value.status_code == 409
    assert storage.get_generation(generation.id) is not None


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_hybrid_restart_recovers_orphan_playground_generation(tmp_path, monkeypatch, status):
    output_root = tmp_path / "output"
    output_root.mkdir()
    generation = _generation("interrupted-generation")
    generation.status = status
    (output_root / "playground_history.json").write_text(
        json.dumps([generation.model_dump()]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")

    storage = PlaygroundStorage(
        output_root=str(output_root),
        shared_workspace=True,
    )

    recovered = storage.get_generation(generation.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error == PlaygroundStorage.ORPHAN_RECOVERY_REASON
    assert storage.active_generation_ids() == []
    persisted = json.loads((output_root / "playground_history.json").read_text(encoding="utf-8"))
    assert persisted[0]["status"] == "failed"


def test_hybrid_restart_preserves_accepted_playground_video_for_resume(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    output_root.mkdir()
    generation = _generation("accepted-video")
    generation.mode = PlaygroundMode.T2V
    generation.status = "processing"
    generation.provider_name = "newapi"
    generation.provider_task_id = "provider-task-123"
    generation.provider_request_id = "provider-request-123"
    (output_root / "playground_history.json").write_text(
        json.dumps([generation.model_dump()]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")

    storage = PlaygroundStorage(
        output_root=str(output_root),
        shared_workspace=True,
    )

    recovered = storage.get_generation(generation.id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.error is None
    assert recovered.provider_task_id == "provider-task-123"
    assert storage.resumable_generation_ids() == [generation.id]


def test_server_restart_preserves_durable_playground_generation(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    output_root.mkdir()
    generation = _generation("durable-generation")
    (output_root / "playground_history.json").write_text(
        json.dumps([generation.model_dump()]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "false")

    storage = PlaygroundStorage(
        output_root=str(output_root),
        shared_workspace=True,
    )

    preserved = storage.get_generation(generation.id)
    assert preserved is not None
    assert preserved.status == "pending"
    assert storage.active_generation_ids() == [generation.id]


def test_all_playground_mutations_roll_back_in_memory_on_save_failure(tmp_path, monkeypatch):
    storage = _storage(tmp_path, monkeypatch)
    storage.add_generation(_generation())
    storage.add_template(_template())

    generation = storage.get_generation("generation-1")
    template = storage.get_template("template-1")
    assert generation is not None and template is not None
    generation.status = "completed"
    template.name = "Changed"

    def fail_save(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "_save_file", fail_save)

    for mutation in (
        lambda: storage.add_generation(_generation("generation-2")),
        lambda: storage.update_generation(generation),
        lambda: storage.delete_generation("generation-1"),
        lambda: storage.add_template(_template("template-2")),
        lambda: storage.update_template(template),
        lambda: storage.delete_template("template-1"),
    ):
        with pytest.raises(OSError, match="disk full"):
            mutation()

    persisted_generation = storage.get_generation("generation-1")
    persisted_template = storage.get_template("template-1")
    assert persisted_generation is not None and persisted_generation.status == "pending"
    assert persisted_template is not None and persisted_template.name == "Portrait"
    assert [item.id for item in storage.list_history()] == ["generation-1"]
    assert [item.id for item in storage.list_templates()] == ["template-1"]


def test_packaged_config_malformed_file_is_not_reset_or_partially_applied(tmp_path, monkeypatch):
    from src.apps.comic_gen import api

    config_path = tmp_path / "config.json"
    original = json.dumps({"ENMOTION_PERSISTENCE_FIRST": "new", "BROKEN": {"nested": True}})
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))
    monkeypatch.setenv("ENMOTION_PERSISTENCE_FIRST", "old")

    with pytest.raises(RuntimeError, match="Failed to load user configuration"):
        api.load_user_config()
    with pytest.raises(RuntimeError, match="Failed to load user configuration"):
        api.save_user_config({"NEWAPI_BASE_URL": "http://127.0.0.1:9999/v1"})

    assert os.environ["ENMOTION_PERSISTENCE_FIRST"] == "old"
    assert config_path.read_text(encoding="utf-8") == original


def test_packaged_config_does_not_override_operator_process_environment(tmp_path, monkeypatch):
    from src.apps.comic_gen import api

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"NEWAPI_BASE_URL": "https://saved.example/v1", "SAVED_ONLY": "yes"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://operator.example/v1")
    monkeypatch.delenv("SAVED_ONLY", raising=False)

    api.load_user_config()

    assert os.environ["NEWAPI_BASE_URL"] == "https://operator.example/v1"
    assert os.environ["SAVED_ONLY"] == "yes"


def test_packaged_config_atomic_failure_preserves_file_and_process_state(tmp_path, monkeypatch):
    from src.apps.comic_gen import api

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"NEWAPI_BASE_URL": "https://before.example/v1"}',
        encoding="utf-8",
    )
    os.chmod(config_path, 0o600)
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://before.example/v1")

    def fail_replace(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(api.os, "replace", fail_replace)
    with pytest.raises(HTTPException) as exc_info:
        api.update_env_config(
            api.EnvConfig(NEWAPI_BASE_URL="https://after.example/v1"),
            SimpleNamespace(state=SimpleNamespace()),
        )

    assert exc_info.value.status_code == 500
    assert "disk unavailable" in exc_info.value.detail
    assert os.environ["NEWAPI_BASE_URL"] == "https://before.example/v1"
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "NEWAPI_BASE_URL": "https://before.example/v1"
    }
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_packaged_config_remove_is_atomic_private_and_propagates_failures(tmp_path, monkeypatch):
    from src.apps.comic_gen import api

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))
    api.save_user_config({"SECRET": "credential", "KEEP": "value"})
    api.remove_user_config_keys(["SECRET"])

    assert json.loads(config_path.read_text(encoding="utf-8")) == {"KEEP": "value"}
    assert config_path.stat().st_mode & 0o777 == 0o600

    def fail_write(*_args):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(api, "_atomic_write_private_json", fail_write)
    with pytest.raises(OSError, match="read-only filesystem"):
        api.remove_user_config_keys(["KEEP"])
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"KEEP": "value"}


def test_concurrent_packaged_config_updates_do_not_lose_keys(tmp_path, monkeypatch):
    from src.apps.comic_gen import api

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))
    real_write = api._atomic_write_private_json

    def slow_write(*args):
        # Hold the read/modify/write window open long enough for an unlocked
        # implementation to let every worker read the same stale snapshot.
        time.sleep(0.01)
        real_write(*args)

    monkeypatch.setattr(api, "_atomic_write_private_json", slow_write)
    updates = [{f"KEY_{index}": str(index)} for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(api.save_user_config, updates))

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        f"KEY_{index}": str(index) for index in range(8)
    }


def test_development_env_defaults_do_not_override_explicit_process_values(tmp_path, monkeypatch):
    from src.apps.comic_gen import api

    env_file = tmp_path / ".env"
    env_file.write_text("ENMOTION_PRECEDENCE_TEST=file-default\n", encoding="utf-8")
    monkeypatch.setenv("ENMOTION_PRECEDENCE_TEST", "explicit-process-value")

    api._load_project_environment(str(env_file))

    assert os.environ["ENMOTION_PRECEDENCE_TEST"] == "explicit-process-value"


def test_development_config_is_private_and_remove_errors_propagate(tmp_path, monkeypatch):
    import dotenv

    from src.apps.comic_gen import api

    config_path = tmp_path / ".env"
    monkeypatch.setattr(api, "get_user_config_path", lambda: str(config_path))
    api.save_user_config({"SECRET": "credential", "KEEP": "value"})
    api.remove_user_config_keys(["SECRET"])

    assert dotenv.dotenv_values(config_path) == {"KEEP": "value"}
    assert config_path.stat().st_mode & 0o777 == 0o600

    def fail_unset(*_args):
        raise OSError("development config is read-only")

    monkeypatch.setattr(dotenv, "unset_key", fail_unset)
    with pytest.raises(OSError, match="development config is read-only"):
        api.remove_user_config_keys(["KEEP"])


def test_save_to_library_persists_output_relative_url_and_propagates_failure(tmp_path, monkeypatch):
    from src.apps.comic_gen import api as comic_api

    monkeypatch.chdir(tmp_path)
    storage = _storage(tmp_path, monkeypatch)
    source = tmp_path / "output" / "playground" / "images" / "fox.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    generation = _generation()
    generation.outputs.append(
        PlaygroundOutput(
            id="output-1",
            media_path="output/playground/images/fox.png",
            media_type="image",
        )
    )
    storage.add_generation(generation)
    completed = storage.get_generation(generation.id)
    assert completed is not None
    completed.status = "completed"
    storage.update_generation(completed)
    terminal_timestamp = storage.get_generation(generation.id).finished_at
    assert terminal_timestamp
    service = PlaygroundService(storage)
    created = []

    def create_asset(asset_type, payload, *, asset_id=None):
        created.append((asset_type, payload, asset_id))
        return SimpleNamespace(id=asset_id)

    monkeypatch.setattr(comic_api.pipeline, "create_library_asset", create_asset)
    with pytest.raises(ValueError, match="必须选择"):
        service.save_to_library("generation-1", "output-1", "../../escape")
    assert created == []
    assert service.save_to_library("generation-1", "output-1", "prop") == "prop"
    assert created[0][0] == "prop"
    assert created[0][1]["image_url"].startswith("assets/prop/")
    assert created[0][1]["image_url"].endswith("_fox.png")
    assert not (tmp_path / "escape" / "fox.png").exists()
    saved_output = storage.get_generation("generation-1").outputs[0]
    assert saved_output.saved_to_library is True
    assert saved_output.library_category == "prop"
    assert saved_output.library_asset_id == created[0][2]
    assert saved_output.library_media_path == created[0][1]["image_url"]
    assert storage.get_generation(generation.id).finished_at == terminal_timestamp
    saved_files = set((tmp_path / "output" / "assets" / "prop").iterdir())
    assert service.save_to_library("generation-1", "output-1", "scene") == "prop"
    assert set((tmp_path / "output" / "assets" / "prop").iterdir()) == saved_files
    assert len(created) == 1

    second = _generation("generation-2")
    second.outputs.append(
        PlaygroundOutput(
            id="output-2",
            media_path="output/playground/images/fox.png",
            media_type="image",
        )
    )
    storage.add_generation(second)

    def fail_create(*_args, **_kwargs):
        raise OSError("library persistence failed")

    monkeypatch.setattr(comic_api.pipeline, "create_library_asset", fail_create)
    with pytest.raises(OSError, match="library persistence failed"):
        service.save_to_library("generation-2", "output-2", "prop")
    assert storage.get_generation("generation-2").outputs[0].saved_to_library is False
    assert set((tmp_path / "output" / "assets" / "prop").iterdir()) == saved_files


def test_concurrent_library_save_retries_create_one_asset_and_file(tmp_path, monkeypatch):
    from src.apps.comic_gen import api as comic_api

    monkeypatch.chdir(tmp_path)
    storage = _storage(tmp_path, monkeypatch)
    source = tmp_path / "output" / "playground" / "images" / "harbor.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    generation = _generation("concurrent-save")
    generation.outputs = [
        PlaygroundOutput(
            id="output-1",
            media_path="output/playground/images/harbor.png",
            media_type="image",
        )
    ]
    storage.add_generation(generation)
    service = PlaygroundService(storage)
    created: list[str] = []

    def create_asset(_asset_type, _payload, *, asset_id=None):
        time.sleep(0.03)
        created.append(asset_id)
        return SimpleNamespace(id=asset_id)

    monkeypatch.setattr(comic_api.pipeline, "create_library_asset", create_asset)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: service.save_to_library(
                    generation.id,
                    "output-1",
                    "scene",
                ),
                range(2),
            )
        )

    assert results == ["scene", "scene"]
    assert len(created) == 1
    assert len(list((tmp_path / "output" / "assets" / "scene").iterdir())) == 1
    persisted = storage.get_generation(generation.id).outputs[0]
    assert persisted.saved_to_library is True
    assert persisted.library_category == "scene"


def test_library_save_rolls_back_after_final_persistence_failure_and_retries(
    tmp_path,
    monkeypatch,
):
    from src.apps.comic_gen import api as comic_api

    monkeypatch.chdir(tmp_path)
    storage = _storage(tmp_path, monkeypatch)
    source = tmp_path / "output" / "playground" / "images" / "compass.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    generation = _generation("rollback-save")
    generation.outputs = [
        PlaygroundOutput(
            id="output-1",
            media_path="output/playground/images/compass.png",
            media_type="image",
        )
    ]
    storage.add_generation(generation)
    service = PlaygroundService(storage)
    active_assets: dict[str, str] = {}
    created_ids: list[str] = []
    deleted_ids: list[str] = []

    def create_asset(asset_type, _payload, *, asset_id=None):
        created_ids.append(asset_id)
        active_assets[asset_id] = asset_type
        return SimpleNamespace(id=asset_id)

    def delete_asset(asset_type, asset_id, *, force=False):
        assert force is True
        assert active_assets.pop(asset_id) == asset_type
        deleted_ids.append(asset_id)

    monkeypatch.setattr(comic_api.pipeline, "create_library_asset", create_asset)
    monkeypatch.setattr(comic_api.pipeline, "delete_library_asset", delete_asset)
    original_save_file = storage._save_file

    def fail_final_metadata(path, items):
        output = items[0].outputs[0] if items and items[0].outputs else None
        if path == storage.history_path and output and output.saved_to_library:
            raise OSError("final history persistence failed")
        return original_save_file(path, items)

    monkeypatch.setattr(storage, "_save_file", fail_final_metadata)
    with pytest.raises(OSError, match="final history persistence failed"):
        service.save_to_library(generation.id, "output-1", "prop")

    pending = storage.get_generation(generation.id).outputs[0]
    assert pending.saved_to_library is False
    assert pending.library_category == "prop"
    assert pending.library_asset_id == created_ids[0]
    assert active_assets == {}
    assert deleted_ids == [created_ids[0]]
    assert list((tmp_path / "output" / "assets" / "prop").iterdir()) == []

    monkeypatch.setattr(storage, "_save_file", original_save_file)
    assert service.save_to_library(generation.id, "output-1", "character") == "prop"
    assert created_ids == [pending.library_asset_id, pending.library_asset_id]
    assert active_assets == {pending.library_asset_id: "prop"}
    assert len(list((tmp_path / "output" / "assets" / "prop").iterdir())) == 1


def test_library_save_reconciles_a_crash_window_without_duplicates(tmp_path, monkeypatch):
    from src.apps.comic_gen import api as comic_api

    monkeypatch.chdir(tmp_path)
    storage = _storage(tmp_path, monkeypatch)
    source = tmp_path / "output" / "playground" / "images" / "lantern.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    generation = _generation("crash-save")
    generation.outputs = [
        PlaygroundOutput(
            id="output-1",
            media_path="output/playground/images/lantern.png",
            media_type="image",
        )
    ]
    storage.add_generation(generation)
    service = PlaygroundService(storage)
    asset_id, media_path = service._library_save_identifiers(
        generation.id,
        "output-1",
        "scene",
        source.name,
    )
    assert storage.prepare_output_library_save(
        generation.id,
        "output-1",
        "scene",
        asset_id,
        media_path,
    ) == ("scene", asset_id, media_path)
    copied = tmp_path / "output" / media_path
    copied.parent.mkdir(parents=True)
    copied.write_bytes(source.read_bytes())
    active_assets = {asset_id: SimpleNamespace(id=asset_id)}

    def create_asset(_asset_type, _payload, *, asset_id=None):
        return active_assets.setdefault(asset_id, SimpleNamespace(id=asset_id))

    monkeypatch.setattr(comic_api.pipeline, "create_library_asset", create_asset)

    assert service.save_to_library(generation.id, "output-1", "character") == "scene"
    assert list(active_assets) == [asset_id]
    assert list(copied.parent.iterdir()) == [copied]
    persisted = storage.get_generation(generation.id).outputs[0]
    assert persisted.saved_to_library is True
    assert persisted.library_category == "scene"


def test_save_to_library_api_returns_the_persisted_replay_category(monkeypatch):
    from src.apps.playground.models import SaveToLibraryRequest

    service = SimpleNamespace(save_to_library=lambda _generation_id, _output_id, _category: "scene")
    monkeypatch.setattr(playground_api, "_current_service", lambda: service)

    assert playground_api.save_to_library(
        "generation-1",
        "output-1",
        SaveToLibraryRequest(category="prop"),
    ) == {"ok": True, "category": "scene"}


def test_video_output_cannot_be_registered_as_an_image_library_asset(tmp_path, monkeypatch):
    from src.apps.comic_gen import api as comic_api
    from src.apps.playground.models import SaveToLibraryRequest

    monkeypatch.chdir(tmp_path)
    storage = _storage(tmp_path, monkeypatch)
    source = tmp_path / "output" / "playground" / "videos" / "harbor.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    generation = _generation("video-save")
    generation.mode = PlaygroundMode.T2V
    generation.outputs = [
        PlaygroundOutput(
            id="output-1",
            media_path="output/playground/videos/harbor.mp4",
            media_type="video",
        )
    ]
    storage.add_generation(generation)
    service = PlaygroundService(storage)
    created = []
    monkeypatch.setattr(
        comic_api.pipeline,
        "create_library_asset",
        lambda *_args, **_kwargs: created.append(True),
    )

    with pytest.raises(
        UnsupportedPlaygroundLibraryMediaError,
        match="仅支持保存图像输出",
    ):
        service.save_to_library(generation.id, "output-1", "scene")
    assert created == []
    assert not (tmp_path / "output" / "assets").exists()

    monkeypatch.setattr(playground_api, "_current_service", lambda: service)
    with pytest.raises(HTTPException) as exc_info:
        playground_api.save_to_library(
            generation.id,
            "output-1",
            SaveToLibraryRequest(category="scene"),
        )
    assert exc_info.value.status_code == 400
    assert "仅支持保存图像输出" in str(exc_info.value.detail)


def test_delete_upload_preserves_media_referenced_by_persisted_generation(tmp_path):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    uploaded = output_root / "playground" / "uploads" / "queued-reference.png"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"image")
    generation = _generation("persisted-input")
    generation.input_media = ["playground/uploads/queued-reference.png"]
    storage.add_generation(generation)

    assert storage.delete_upload("playground/uploads/queued-reference.png") is False
    assert uploaded.exists()


def test_delete_upload_fails_closed_when_references_cannot_be_scanned(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    uploaded = output_root / "playground" / "uploads" / "keep-visible.png"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"image")
    monkeypatch.setattr(storage, "_reclaim_deleted_value", lambda _value: None)

    with pytest.raises(RuntimeError, match="Could not verify workspace references"):
        storage.delete_upload("playground/uploads/keep-visible.png")
    assert uploaded.exists()


def test_delete_playground_generation_reclaims_only_its_outputs(tmp_path):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    first_path = output_root / "playground" / "images" / "first.png"
    shared_path = output_root / "playground" / "images" / "shared.png"
    first_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"first")
    shared_path.write_bytes(b"shared")

    first = _generation("first")
    first.outputs = [
        PlaygroundOutput(id="first-only", media_path=str(first_path), media_type="image"),
        PlaygroundOutput(id="shared-a", media_path=str(shared_path), media_type="image"),
    ]
    second = _generation("second")
    second.outputs = [
        PlaygroundOutput(id="shared-b", media_path=str(shared_path), media_type="image")
    ]
    storage.add_generation(first)
    storage.add_generation(second)

    assert storage.delete_generation("first") is True
    assert not first_path.exists()
    assert shared_path.exists()
    assert storage.delete_generation("second") is True
    assert not shared_path.exists()


def test_delete_playground_generation_preserves_project_references(tmp_path):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    shared_path = output_root / "playground" / "images" / "shared.png"
    shared_path.parent.mkdir(parents=True)
    shared_path.write_bytes(b"shared")
    generation = _generation("shared-generation")
    generation.outputs = [
        PlaygroundOutput(
            id="shared-output",
            media_path="playground/images/shared.png",
            media_type="image",
        )
    ]
    storage.add_generation(generation)
    (output_root / "projects.json").write_text(
        json.dumps([{"image_url": "playground/images/shared.png"}]),
        encoding="utf-8",
    )

    assert storage.delete_generation(generation.id) is True
    assert shared_path.exists()


def test_i2i_generation_resolves_relative_playground_upload(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    service = PlaygroundService(storage)
    upload = output_root / "playground" / "uploads" / "reference.png"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"image")
    generation = _generation("i2i-relative-upload")
    generation.mode = PlaygroundMode.I2I
    generation.input_media = ["playground/uploads/reference.png"]
    captured = {}

    class ImageModel:
        def generate(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    service._newapi_image_model = ImageModel()
    service._generate_image_newapi(
        generation,
        str(output_root / "playground" / "images" / "result.png"),
        0,
    )

    assert captured["ref_image_paths"] == [str(upload)]


def test_server_playground_history_never_persists_absolute_workspace_paths(tmp_path, monkeypatch):
    output_root = tmp_path / "workspaces" / "workspace-alice" / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    service = PlaygroundService(storage)
    generation = _generation("server-generation")
    storage.add_generation(generation)

    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    monkeypatch.setattr(service, "_enforce_server_file_quota", lambda _path: None)

    def write_image(_generation, path, _index):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"image")

    monkeypatch.setattr(service, "_generate_image_newapi", write_image)
    service.process_generation(generation.id)

    persisted = storage.get_generation(generation.id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.outputs[0].media_path.startswith("playground/images/")
    assert str(output_root) not in persisted.model_dump_json()
