"""HTTP contract coverage for exact-owner Home Asset Library editing."""

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GenerationStatus,
    ImageAsset,
    ImageVariant,
    Scene,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
    VideoVariant,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


def _pipeline(output_root) -> ComicGenPipeline:
    with (
        patch("src.apps.comic_gen.pipeline.ScriptProcessor"),
        patch("src.apps.comic_gen.pipeline.AssetGenerator"),
        patch("src.apps.comic_gen.pipeline.StoryboardGenerator"),
        patch("src.apps.comic_gen.pipeline.VideoGenerator"),
        patch("src.apps.comic_gen.pipeline.ExportManager"),
    ):
        return ComicGenPipeline(
            {
                "output_root": str(output_root),
                "recover_orphan_tasks": False,
            }
        )


def test_source_asset_get_and_patch_return_complete_direct_payload(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    asset = pipeline.create_library_asset(
        "character",
        {
            "name": "Old name",
            "description": "Old description",
            "image_url": "uploads/reference.png",
        },
    )
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    client = TestClient(comic_api.app)
    path = f"/asset-sources/global/global/assets/character/{asset.id}"

    response = client.patch(
        path,
        json={
            "attributes": {
                "name": "Edited name",
                "description": "Edited description",
                "age": "30",
            },
            "prompts": {
                "image_prompt": "Character reference prompt",
                "full_body_video_prompt": "Character motion prompt",
                "headshot_video_prompt": "Portrait motion prompt",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == asset.id
    assert body["asset_type"] == "character"
    assert body["source"] == "global"
    assert body["source_id"] == "global"
    assert body["name"] == "Edited name"
    assert body["reference_sheet"]["image_prompt"] == "Character reference prompt"
    assert body["reference_sheet"]["video_prompt"] == "Character motion prompt"
    assert body["full_body"]["video_prompt"] == "Character motion prompt"
    assert body["head_shot"]["video_prompt"] == "Portrait motion prompt"
    assert body["reference_sheet"]["image_variants"][0]["url"].endswith(
        "uploads/reference.png"
    )
    assert "asset" not in body

    fetched = client.get(path)
    assert fetched.status_code == 200
    assert fetched.json()["age"] == "30"


@pytest.mark.parametrize(
    ("asset_type", "original_name", "renamed_name"),
    [
        ("scene", "Old square", "Renamed square"),
        ("prop", "Old tool", "Renamed tool"),
    ],
)
def test_source_asset_patch_renames_scenes_and_props(
    tmp_path,
    monkeypatch,
    asset_type,
    original_name,
    renamed_name,
):
    pipeline = _pipeline(tmp_path)
    asset = pipeline.create_library_asset(
        asset_type,
        {"name": original_name, "description": "Shared asset"},
    )
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    client = TestClient(comic_api.app)
    path = f"/asset-sources/global/global/assets/{asset_type}/{asset.id}"

    response = client.patch(path, json={"attributes": {"name": renamed_name}})

    assert response.status_code == 200
    assert response.json()["name"] == renamed_name
    assert client.get(path).json()["name"] == renamed_name


def test_episode_variant_actions_return_resolved_series_and_global_assets(
    tmp_path, monkeypatch
):
    pipeline = _pipeline(tmp_path)
    series_variant = ImageVariant(
        id="series-variant",
        url="assets/series-character.png",
    )
    global_variant = ImageVariant(
        id="global-variant",
        url="assets/global-scene.png",
    )
    series_character = Character(
        id="series-character",
        name="Series hero",
        description="Shared by the series",
        reference_sheet=AssetUnit(
            selected_image_id=series_variant.id,
            image_variants=[series_variant],
        ),
        image_url=series_variant.url,
    )
    global_scene = Scene(
        id="global-scene",
        name="Global square",
        description="Shared everywhere",
        image_asset=ImageAsset(
            selected_id=global_variant.id,
            variants=[global_variant],
        ),
        image_url=global_variant.url,
    )
    now = time.time()
    series = Series(
        id="series-1",
        title="Series",
        episode_ids=["episode-1"],
        characters=[series_character],
        created_at=now,
        updated_at=now,
    )
    episode = Script(
        id="episode-1",
        title="Episode",
        original_text="text",
        series_id=series.id,
        episode_number=1,
        created_at=now,
        updated_at=now,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode
    pipeline.library_store.scenes.append(global_scene)
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    client = TestClient(comic_api.app)

    selected = client.post(
        f"/projects/{episode.id}/assets/variant/select",
        json={
            "asset_id": series_character.id,
            "asset_type": "character",
            "variant_id": series_variant.id,
            "generation_type": "reference_sheet",
        },
    )

    assert selected.status_code == 200
    selected_body = selected.json()
    assert [(item["id"], item["source"]) for item in selected_body["characters"]] == [
        (series_character.id, "series")
    ]
    assert [(item["id"], item["source"]) for item in selected_body["scenes"]] == [
        (global_scene.id, "global")
    ]

    favorited = client.post(
        f"/projects/{episode.id}/assets/variant/favorite",
        json={
            "asset_id": global_scene.id,
            "asset_type": "scene",
            "variant_id": global_variant.id,
            "is_favorited": True,
        },
    )

    assert favorited.status_code == 200
    favorite_body = favorited.json()
    assert [(item["id"], item["source"]) for item in favorite_body["characters"]] == [
        (series_character.id, "series")
    ]
    resolved_scene = favorite_body["scenes"][0]
    assert resolved_scene["id"] == global_scene.id
    assert resolved_scene["source"] == "global"
    assert resolved_scene["image_asset"]["variants"][0]["is_favorited"] is True


def test_source_asset_api_allows_unreferenced_project_type_change(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    now = time.time()
    script = Script(
        id="episode-1",
        title="Episode",
        original_text="text",
        characters=[
            Character(id="character-1", name="Hero", description="Local hero")
        ],
        created_at=now,
        updated_at=now,
    )
    pipeline.scripts[script.id] = script
    monkeypatch.setattr(comic_api, "pipeline", pipeline)

    response = TestClient(comic_api.app).patch(
        "/asset-sources/project/episode-1/assets/character/character-1",
        json={"target_asset_type": "scene"},
    )

    assert response.status_code == 200
    assert response.json()["asset_type"] == "scene"
    assert response.json()["source"] == "project"
    assert pipeline.scripts[script.id].characters == []
    assert pipeline.scripts[script.id].scenes[0].id == "character-1"


def test_source_asset_api_rejects_referenced_project_type_change(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    now = time.time()
    script = Script(
        id="episode-1",
        title="Episode",
        original_text="text",
        characters=[
            Character(id="character-1", name="Hero", description="Local hero")
        ],
        frames=[
            StoryboardFrame(
                id="frame-1", scene_id="", character_ids=["character-1"]
            )
        ],
        created_at=now,
        updated_at=now,
    )
    pipeline.scripts[script.id] = script
    monkeypatch.setattr(comic_api, "pipeline", pipeline)

    response = TestClient(comic_api.app).patch(
        "/asset-sources/project/episode-1/assets/character/character-1",
        json={"target_asset_type": "scene"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "asset_type_change_conflict"
    assert detail["source"] == "project"
    assert detail["reason"] == "referenced"
    assert detail["references"][0]["frame_id"] == "frame-1"


def test_source_asset_generate_exposes_task_and_persists_global_result(
    tmp_path, monkeypatch
):
    pipeline = _pipeline(tmp_path)
    scene = pipeline.create_library_asset(
        "scene", {"name": "Square", "description": "Open plaza"}
    )

    def generate_scene(target, **kwargs):
        target.image_prompt = kwargs["prompt"]
        target.image_asset.variants.insert(
            0,
            ImageVariant(
                id="generated",
                url="uploads/generated.png",
                prompt_used=kwargs["prompt"],
            ),
        )
        target.image_asset.selected_id = "generated"
        target.image_url = "uploads/generated.png"
        target.status = GenerationStatus.COMPLETED

    pipeline.asset_generator.generate_scene.side_effect = generate_scene
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    path = f"/asset-sources/global/global/assets/scene/{scene.id}/generate"
    with (
        patch("src.apps.comic_gen.pipeline.get_selected_model", return_value="image-model"),
        patch("src.apps.comic_gen.pipeline.get_model_spec") as get_model_spec,
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        get_model_spec.return_value.model_id = "image-model"
        response = TestClient(comic_api.app).post(
            path,
            json={
                "asset_id": scene.id,
                "asset_type": "scene",
                "prompt": "A lively square at sunrise",
                "aspect_ratio": "16:9",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == scene.id
    assert body["asset_type"] == "scene"
    assert body["source"] == "global"
    assert body["task_id"] == body["_task_id"]
    assert body["task_status"] == "queued"
    assert "asset" not in body

    reloaded = _pipeline(tmp_path)
    saved = reloaded.library_store.scenes[0]
    assert saved.status is GenerationStatus.COMPLETED
    assert saved.image_prompt == "A lively square at sunrise"
    assert saved.image_asset.selected_id == "generated"


@pytest.mark.parametrize("source_kind", ["project", "series", "global"])
def test_source_asset_durable_payload_captures_pre_reservation_status(
    tmp_path, monkeypatch, source_kind
):
    pipeline = _pipeline(tmp_path)
    scene = Scene(
        id="scene-1",
        name="Square",
        description="Open plaza",
        status=GenerationStatus.COMPLETED,
    )
    now = time.time()
    if source_kind == "project":
        source_id = "episode-1"
        pipeline.scripts[source_id] = Script(
            id=source_id,
            title="Episode",
            original_text="text",
            scenes=[scene],
            created_at=now,
            updated_at=now,
        )
    elif source_kind == "series":
        source_id = "series-1"
        pipeline.series_store[source_id] = Series(
            id=source_id,
            title="Series",
            scenes=[scene],
            created_at=now,
            updated_at=now,
        )
    else:
        source_id = "global"
        pipeline.library_store.scenes.append(scene)

    captured = []
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(
        comic_api,
        "reserve_workspace_jobs",
        lambda specs: captured.extend(specs) or [object()],
    )
    monkeypatch.setattr(comic_api, "publish_workspace_job_reservations", lambda _rows: None)
    with (
        patch("src.apps.comic_gen.pipeline.get_selected_model", return_value="image-model"),
        patch("src.apps.comic_gen.pipeline.get_model_spec") as get_model_spec,
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        get_model_spec.return_value.model_id = "image-model"
        response = TestClient(comic_api.app).post(
            f"/asset-sources/{source_kind}/{source_id}/assets/scene/{scene.id}/generate",
            json={
                "asset_id": scene.id,
                "asset_type": "scene",
                "prompt": "A lively square",
            },
        )

    assert response.status_code == 200
    assert captured[0]["payload"]["previous_asset_status"] == "completed"
    assert scene.status is GenerationStatus.PROCESSING


def test_source_asset_delete_previews_references_then_cascades_after_force(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    media_path = tmp_path / "assets" / "hero.png"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"hero")
    now = time.time()
    character = Character(
        id="character-1",
        name="Series hero",
        description="Shared character",
        image_url="assets/hero.png",
    )
    series = Series(
        id="series-1",
        title="Series",
        episode_ids=["episode-1"],
        characters=[character],
        created_at=now,
        updated_at=now,
    )
    task = VideoTask(
        id="motion-1",
        project_id="episode-1",
        asset_id=character.id,
        image_url="assets/hero.png",
        prompt="animate",
        status="failed",
    )
    episode = Script(
        id="episode-1",
        title="Episode",
        original_text="text",
        series_id=series.id,
        episode_number=1,
        frames=[StoryboardFrame(id="frame-1", scene_id="", character_ids=[character.id])],
        video_tasks=[task],
        created_at=now,
        updated_at=now,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode
    pipeline._save_data()
    pipeline._save_series_data()
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "current_output_root", lambda: str(tmp_path))
    client = TestClient(comic_api.app)
    path = f"/asset-sources/series/{series.id}/assets/character/{character.id}"

    preview = client.get(f"{path}/delete-impact")

    assert preview.status_code == 200
    assert preview.json()["has_references"] is True
    assert preview.json()["reference_count"] == 3
    assert {item["reference_type"] for item in preview.json()["references"]} == {
        "series",
        "storyboard",
        "generation_task",
    }

    refused = client.delete(path)
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "asset_in_use"
    assert pipeline.series_store[series.id].characters == [character]

    deleted = client.delete(path, params={"force": True})

    assert deleted.status_code == 200
    assert deleted.json()["reclaimed_media_files"] == 1
    assert deleted.json()["reference_count"] == 3
    assert pipeline.series_store[series.id].characters == []
    assert pipeline.scripts[episode.id].frames[0].character_ids == []
    assert pipeline.scripts[episode.id].video_tasks == []
    assert not media_path.exists()

    reloaded = _pipeline(tmp_path)
    assert reloaded.series_store[series.id].characters == []
    assert reloaded.scripts[episode.id].frames[0].character_ids == []
    assert reloaded.scripts[episode.id].video_tasks == []


def test_source_asset_force_delete_cancels_queued_durable_generation(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    asset = pipeline.create_library_asset(
        "scene", {"name": "Square", "description": "Shared square"}
    )
    queued_reference = {
        "reference_type": "generation_task",
        "owner_kind": "global",
        "owner_id": "global",
        "task_id": "job-1",
        "task_status": "queued",
        "job_type": "global_asset",
        "durable": True,
    }
    canceled = []
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(
        comic_api,
        "_durable_source_asset_job_references",
        lambda *_args: [queued_reference],
    )
    monkeypatch.setattr(
        comic_api,
        "_cancel_queued_asset_jobs",
        lambda references: canceled.extend(references),
    )
    monkeypatch.setattr(comic_api, "reclaim_deleted_media", lambda _snapshot: [])
    client = TestClient(comic_api.app)
    path = f"/asset-sources/global/global/assets/scene/{asset.id}"

    refused = client.delete(path)
    assert refused.status_code == 409
    assert refused.json()["detail"]["references"] == [queued_reference]

    deleted = client.delete(path, params={"force": True})

    assert deleted.status_code == 200
    assert canceled == [queued_reference]
    assert pipeline.library_store.scenes == []


def test_source_asset_delete_failure_keeps_record_and_skips_media_cleanup(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    asset = pipeline.create_library_asset(
        "prop", {"name": "Radio", "description": "A shared radio"}
    )
    reclaimed = []
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(
        pipeline,
        "delete_source_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    monkeypatch.setattr(
        comic_api,
        "reclaim_deleted_media",
        lambda snapshot: reclaimed.append(snapshot) or [],
    )

    response = TestClient(comic_api.app).delete(
        f"/asset-sources/global/global/assets/prop/{asset.id}"
    )

    assert response.status_code == 500
    assert pipeline.library_store.props == [asset]
    assert reclaimed == []


def test_source_asset_delete_preserves_media_still_used_by_another_asset(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    media_path = tmp_path / "assets" / "shared-place.png"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"shared")
    first = pipeline.create_library_asset(
        "scene",
        {
            "name": "First square",
            "description": "One view",
            "image_url": "assets/shared-place.png",
        },
    )
    second = pipeline.create_library_asset(
        "scene",
        {
            "name": "Second square",
            "description": "Another view",
            "image_url": "assets/shared-place.png",
        },
    )
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "current_output_root", lambda: str(tmp_path))

    response = TestClient(comic_api.app).delete(
        f"/asset-sources/global/global/assets/scene/{first.id}"
    )

    assert response.status_code == 200
    assert response.json()["reclaimed_media_files"] == 0
    assert pipeline.library_store.scenes == [second]
    assert media_path.exists()


def test_desired_state_favorite_mutates_only_the_exact_owner(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path)
    now = time.time()
    shared_id = "same-character-id"
    project_character = Character(
        id=shared_id,
        name="Project hero",
        description="Project-owned",
    )
    series_character = Character(
        id=shared_id,
        name="Series hero",
        description="Series-owned",
    )
    global_character = Character(
        id=shared_id,
        name="Global hero",
        description="Global-owned",
    )
    series = Series(
        id="series-1",
        title="Series",
        characters=[series_character],
        episode_ids=["episode-1"],
        created_at=now,
        updated_at=now,
    )
    episode = Script(
        id="episode-1",
        title="Episode",
        original_text="text",
        series_id=series.id,
        characters=[project_character],
        created_at=now,
        updated_at=now,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode
    pipeline.library_store.characters.append(global_character)
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    client = TestClient(comic_api.app)

    response = client.put(
        f"/asset-sources/series/{series.id}/assets/character/{shared_id}/favorite",
        json={"starred": True},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "series"
    assert project_character.starred is False
    assert series_character.starred is True
    assert global_character.starred is False

    retry = client.put(
        f"/asset-sources/series/{series.id}/assets/character/{shared_id}/favorite",
        json={"starred": True},
    )
    assert retry.status_code == 200
    assert series_character.starred is True


def test_exact_owner_motion_variant_actions_persist_for_global_character(
    tmp_path, monkeypatch
):
    pipeline = _pipeline(tmp_path)
    character = pipeline.create_library_asset(
        "character",
        {
            "name": "Global hero",
            "description": "A shared fictional hero",
            "image_url": "uploads/hero.png",
        },
    )
    first = VideoVariant(id="motion-1", url="videos/motion-1.mp4")
    second = VideoVariant(id="motion-2", url="videos/motion-2.mp4")
    character.full_body.video_variants = [first, second]
    character.full_body.selected_video_id = first.id
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "reclaim_deleted_media", lambda _snapshot: [])
    client = TestClient(comic_api.app)
    base = (
        f"/asset-sources/global/global/assets/character/{character.id}"
        "/motion/variants"
    )

    selected = client.post(
        f"{base}/{second.id}/select",
        json={"motion_type": "full_body"},
    )
    assert selected.status_code == 200
    assert selected.json()["full_body"]["selected_video_id"] == second.id

    favorited = client.put(
        f"{base}/{second.id}/favorite",
        json={"motion_type": "full_body", "is_favorited": True},
    )
    assert favorited.status_code == 200
    variants = favorited.json()["full_body"]["video_variants"]
    assert next(item for item in variants if item["id"] == second.id)[
        "is_favorited"
    ] is True

    deleted = client.request(
        "DELETE",
        f"{base}/{first.id}",
        json={"motion_type": "full_body"},
    )
    assert deleted.status_code == 200
    assert [item["id"] for item in deleted.json()["full_body"]["video_variants"]] == [
        second.id
    ]

    reloaded = _pipeline(tmp_path)
    persisted = reloaded.library_store.characters[0].full_body
    assert persisted.selected_video_id == second.id
    assert [item.id for item in persisted.video_variants] == [second.id]
    assert persisted.video_variants[0].is_favorited is True


def test_exact_owner_motion_generation_queues_canonical_owner_payload(
    tmp_path, monkeypatch
):
    pipeline = _pipeline(tmp_path)
    scene = pipeline.create_library_asset(
        "scene",
        {
            "name": "Global square",
            "description": "A shared square",
            "image_url": "uploads/square.png",
        },
    )
    captured = []
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(
        comic_api,
        "reserve_workspace_jobs",
        lambda specs: captured.extend(specs) or [object()],
    )
    monkeypatch.setattr(
        comic_api, "publish_workspace_job_reservations", lambda _rows: None
    )
    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec") as get_model_spec,
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
        patch("src.apps.comic_gen.api.get_model_spec") as request_model_spec,
    ):
        get_model_spec.return_value.model_id = "video-model"
        request_model_spec.return_value.model_id = "video-model"
        response = TestClient(comic_api.app).post(
            f"/asset-sources/global/global/assets/scene/{scene.id}/motion/generate",
            json={
                "motion_type": "scene",
                "prompt": "Slowly orbit the fictional town square",
                "model": "video-model",
                "audio_url": "uploads/scene-guide.wav",
                "duration": 6,
                "batch_size": 2,
            },
        )

    assert response.status_code == 200
    assert response.json()["task_status"] == "queued"
    assert len(captured) == 1
    payload = captured[0]["payload"]
    assert payload["source_kind"] == "global"
    assert payload["source_id"] == "global"
    assert payload["asset_type"] == "scene"
    assert payload["asset_id"] == scene.id
    assert payload["motion_type"] == "scene"
    assert payload["prompt"] == "Slowly orbit the fictional town square"
    assert payload["audio_url"] == "uploads/scene-guide.wav"
    assert payload["duration"] == 6
    assert payload["batch_size"] == 2
    assert "script_id" not in payload


def test_exact_owner_motion_generation_forwards_and_records_audio(
    tmp_path,
):
    pipeline = _pipeline(tmp_path)
    character = pipeline.create_library_asset(
        "character",
        {
            "name": "Audio guide",
            "description": "A fictional presenter",
            "image_url": "uploads/presenter.png",
        },
    )
    pipeline.video_generator.generate_i2v.return_value = {
        "video_url": "videos/presenter.mp4"
    }

    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec") as get_model_spec,
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        get_model_spec.return_value.model_id = "video-model"
        pipeline.generate_source_asset_motion_ref(
            "global",
            "global",
            "character",
            character.id,
            motion_type="full_body",
            prompt="Match the guide track",
            duration=5,
            batch_size=1,
            model_id="video-model",
            audio_url="uploads/voice-guide.wav",
        )

    pipeline.video_generator.generate_i2v.assert_called_once_with(
        image_url="uploads/presenter.png",
        prompt="Match the guide track",
        duration=5,
        audio_url="uploads/voice-guide.wav",
        model_id="video-model",
    )
    generated = character.full_body.video_variants[0]
    assert generated.url == "videos/presenter.mp4"
    assert generated.audio_url == "uploads/voice-guide.wav"
