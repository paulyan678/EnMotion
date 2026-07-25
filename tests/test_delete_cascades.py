"""Regression tests for durable delete cascades in project metadata."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GlobalAssetLibrary,
    ImageAsset,
    ImageVariant,
    Prop,
    Scene,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
    VideoVariant,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def pipeline(tmp_path: Path) -> ComicGenPipeline:
    """Use real JSON persistence while replacing heavyweight generators."""

    with (
        patch("src.apps.comic_gen.pipeline.ScriptProcessor"),
        patch("src.apps.comic_gen.pipeline.AssetGenerator"),
        patch("src.apps.comic_gen.pipeline.StoryboardGenerator"),
        patch("src.apps.comic_gen.pipeline.VideoGenerator"),
        patch("src.apps.comic_gen.pipeline.ExportManager"),
    ):
        instance = ComicGenPipeline(
            {
                "output_root": str(tmp_path / "output"),
                "recover_orphan_tasks": False,
            }
        )
    instance.data_file = str(tmp_path / "projects.json")
    instance.series_data_file = str(tmp_path / "series.json")
    instance.scripts = {}
    instance.series_store = {}
    return instance


def _task(
    task_id: str,
    *,
    frame_id: str | None = None,
    asset_id: str | None = None,
) -> VideoTask:
    return VideoTask(
        id=task_id,
        project_id="project-1",
        frame_id=frame_id,
        asset_id=asset_id,
        image_url="storyboard/source.png",
        prompt="animate",
        status="completed",
        video_url=f"videos/{task_id}.mp4",
    )


def _frame_with_task_pointers(
    frame_id: str,
    task: VideoTask,
    **overrides,
) -> StoryboardFrame:
    values = {
        "id": frame_id,
        "scene_id": "scene-1",
        "selected_video_id": task.id,
        "is_video_pinned": True,
        "final_take_id": task.id,
        "dubbed_video_task_id": task.id,
        "video_url": task.video_url,
        "dubbed_video_url": "videos/dubbed.mp4",
        "preview_video_url": "videos/preview.mp4",
        "bg_audio_source_video": task.video_url,
        "bg_audio_url": "audio/background.wav",
    }
    values.update(overrides)
    return StoryboardFrame(**values)


def _script(**overrides) -> Script:
    now = time.time()
    values = {
        "id": "project-1",
        "title": "Project",
        "original_text": "text",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Script(**values)


def _persisted_project(pipeline: ComicGenPipeline) -> dict:
    payload = json.loads(Path(pipeline.data_file).read_text(encoding="utf-8"))
    return payload["project-1"]


def _series(series_id: str, **overrides) -> Series:
    now = time.time()
    values = {
        "id": series_id,
        "title": "Series",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Series(**values)


def _assert_frame_task_pointers_cleared(frame: StoryboardFrame) -> None:
    assert frame.selected_video_id is None
    assert frame.is_video_pinned is False
    assert frame.final_take_id is None
    assert frame.dubbed_video_task_id is None
    assert frame.video_url is None
    assert frame.dubbed_video_url is None
    assert frame.preview_video_url is None
    assert frame.bg_audio_source_video is None
    assert frame.bg_audio_url is None


def test_delete_frame_cascades_task_records_and_persists_no_dangling_references(
    pipeline: ComicGenPipeline,
) -> None:
    task = _task("task-for-deleted-frame", frame_id="frame-delete")
    owner = Character(
        id="character-1",
        name="Hero",
        description="Hero",
        video_assets=[task],
        reference_sheet=AssetUnit(
            selected_video_id=task.id,
            video_variants=[VideoVariant(id=task.id, url=task.video_url or "")],
        ),
    )
    deleted_frame = StoryboardFrame(id="frame-delete", scene_id="scene-1")
    retained_frame = _frame_with_task_pointers("frame-keep", task)
    script = _script(
        characters=[owner],
        frames=[deleted_frame, retained_frame],
        video_tasks=[task],
    )
    pipeline.scripts = {script.id: script}

    updated = pipeline.delete_frame(script.id, deleted_frame.id)

    assert [frame.id for frame in updated.frames] == [retained_frame.id]
    assert updated.video_tasks == []
    assert owner.video_assets == []
    assert owner.reference_sheet is not None
    assert owner.reference_sheet.video_variants == []
    assert owner.reference_sheet.selected_video_id is None
    _assert_frame_task_pointers_cleared(retained_frame)

    persisted = _persisted_project(pipeline)
    assert task.id not in json.dumps(persisted)
    assert [frame["id"] for frame in persisted["frames"]] == [retained_frame.id]


def test_delete_frame_api_removes_durable_jobs_and_reclaims_owned_media(
    pipeline: ComicGenPipeline,
    monkeypatch,
) -> None:
    from src.apps.comic_gen import api as comic_api
    from src.apps.server import database as server_database
    from src.apps.server import jobs as server_jobs

    task = _task("task-owned-by-frame", frame_id="frame-delete")
    deleted_frame = StoryboardFrame(
        id="frame-delete",
        scene_id="scene-1",
        image_url="storyboard/frame.png",
        t2i_image_urls=["storyboard/candidate.png"],
        video_url=task.video_url,
    )
    retained_frame = StoryboardFrame(id="frame-keep", scene_id="scene-1")
    script = _script(frames=[deleted_frame, retained_frame], video_tasks=[task])
    pipeline.scripts = {script.id: script}
    fake_database = object()
    cleanup_calls: list[dict] = []
    reclaimed_snapshots: list[dict] = []

    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(
        comic_api,
        "get_tenant",
        lambda *, required: SimpleNamespace(workspace_id="workspace-1"),
    )
    monkeypatch.setattr(server_database, "get_database", lambda: fake_database)

    def delete_jobs(db, **kwargs):
        cleanup_calls.append({"db": db, **kwargs})
        return [{"id": "job-1", "result": {"image_url": "storyboard/frame.png"}}]

    monkeypatch.setattr(server_jobs, "delete_frame_generation_jobs", delete_jobs)
    monkeypatch.setattr(
        comic_api,
        "reclaim_deleted_media",
        lambda snapshot: reclaimed_snapshots.append(snapshot) or [],
    )

    response = comic_api.delete_frame(script.id, deleted_frame.id)

    assert response.status_code == 200
    assert cleanup_calls == [
        {
            "db": fake_database,
            "workspace_id": "workspace-1",
            "script_id": script.id,
            "frame_id": deleted_frame.id,
            "task_ids": [task.id],
        }
    ]
    assert reclaimed_snapshots[0]["frame"]["id"] == deleted_frame.id
    assert reclaimed_snapshots[0]["video_tasks"][0]["id"] == task.id
    assert reclaimed_snapshots[0]["generation_jobs"][0]["id"] == "job-1"
    assert [frame.id for frame in pipeline.scripts[script.id].frames] == [retained_frame.id]
    assert pipeline.scripts[script.id].video_tasks == []


@pytest.mark.parametrize("asset_type", ["character", "scene", "prop"])
def test_delete_entity_cascades_relations_tasks_and_frame_pointers(
    pipeline: ComicGenPipeline,
    asset_type: str,
) -> None:
    asset_id = f"{asset_type}-delete"
    task = _task(f"task-for-{asset_type}", asset_id=asset_id)
    keeper = Character(
        id="character-keep",
        name="Keeper",
        description="Keeper",
        video_assets=[task],
    )
    frame_overrides = {
        "character": {"character_ids": [asset_id]},
        "scene": {"scene_id": asset_id},
        "prop": {"prop_ids": [asset_id]},
    }[asset_type]
    frame = _frame_with_task_pointers("frame-1", task, **frame_overrides)
    script = _script(
        characters=[keeper],
        frames=[frame],
        video_tasks=[task],
    )
    if asset_type == "character":
        script.characters.append(
            Character(id=asset_id, name="Delete", description="Delete")
        )
    elif asset_type == "scene":
        script.scenes.append(Scene(id=asset_id, name="Delete", description="Delete"))
    else:
        script.props.append(Prop(id=asset_id, name="Delete", description="Delete"))
    pipeline.scripts = {script.id: script}

    updated = getattr(pipeline, f"delete_{asset_type}")(script.id, asset_id)

    assert updated.video_tasks == []
    assert keeper.video_assets == []
    _assert_frame_task_pointers_cleared(frame)
    if asset_type == "character":
        assert asset_id not in frame.character_ids
        assert all(item.id != asset_id for item in updated.characters)
    elif asset_type == "scene":
        assert frame.scene_id == ""
        assert all(item.id != asset_id for item in updated.scenes)
    else:
        assert asset_id not in frame.prop_ids
        assert all(item.id != asset_id for item in updated.props)

    persisted_text = json.dumps(_persisted_project(pipeline))
    assert task.id not in persisted_text
    assert asset_id not in persisted_text


def test_delete_video_task_clears_all_embedded_and_frame_pointers(
    pipeline: ComicGenPipeline,
) -> None:
    task = _task("task-delete", frame_id="frame-1", asset_id="character-1")
    character = Character(
        id="character-1",
        name="Hero",
        description="Hero",
        video_assets=[task],
        reference_sheet=AssetUnit(
            selected_video_id=task.id,
            video_variants=[VideoVariant(id=task.id, url=task.video_url or "")],
        ),
    )
    frame = _frame_with_task_pointers("frame-1", task)
    script = _script(characters=[character], frames=[frame], video_tasks=[task])
    pipeline.scripts = {script.id: script}

    updated = pipeline.delete_video_task(script.id, task.id)

    assert updated.video_tasks == []
    assert character.video_assets == []
    assert character.reference_sheet is not None
    assert character.reference_sheet.video_variants == []
    assert character.reference_sheet.selected_video_id is None
    _assert_frame_task_pointers_cleared(frame)
    assert task.id not in json.dumps(_persisted_project(pipeline))

    with pytest.raises(ValueError, match="Video task not found"):
        pipeline.delete_video_task(script.id, task.id)


def test_delete_canonical_asset_unit_variant_selects_fallback_and_persists(
    pipeline: ComicGenPipeline,
) -> None:
    keep = ImageVariant(id="variant-keep", url="characters/keep.png")
    remove = ImageVariant(id="variant-delete", url="characters/delete.png")
    character = Character(
        id="character-1",
        name="Hero",
        description="Hero",
        image_url=remove.url,
        reference_sheet=AssetUnit(
            selected_image_id=remove.id,
            image_variants=[keep, remove],
        ),
    )
    script = _script(characters=[character])
    pipeline.scripts = {script.id: script}

    updated = pipeline.delete_asset_variant(
        script.id, character.id, "character", remove.id
    )

    saved_character = updated.characters[0]
    assert saved_character.reference_sheet is not None
    assert [item.id for item in saved_character.reference_sheet.image_variants] == [
        keep.id
    ]
    assert saved_character.reference_sheet.selected_image_id == keep.id
    assert saved_character.image_url == keep.url

    persisted_character = _persisted_project(pipeline)["characters"][0]
    assert [
        item["id"] for item in persisted_character["reference_sheet"]["image_variants"]
    ] == [keep.id]
    assert persisted_character["reference_sheet"]["selected_image_id"] == keep.id
    assert remove.id not in json.dumps(persisted_character)


@pytest.mark.parametrize(
    ("asset_id", "variant_id", "message"),
    [
        ("missing-character", "variant-keep", "Asset not found"),
        ("character-1", "missing-variant", "Variant not found"),
    ],
)
def test_delete_asset_variant_raises_for_missing_target(
    pipeline: ComicGenPipeline,
    asset_id: str,
    variant_id: str,
    message: str,
) -> None:
    character = Character(
        id="character-1",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            selected_image_id="variant-keep",
            image_variants=[
                ImageVariant(id="variant-keep", url="characters/keep.png")
            ],
        ),
    )
    script = _script(characters=[character])
    pipeline.scripts = {script.id: script}

    with pytest.raises(ValueError, match=message):
        pipeline.delete_asset_variant(
            script.id, asset_id, "character", variant_id
        )


def test_delete_character_variant_removes_canonical_and_legacy_mirrors(
    pipeline: ComicGenPipeline,
) -> None:
    canonical_keep = ImageVariant(
        id="canonical-keep", url="characters/canonical-keep.png"
    )
    legacy_keep = ImageVariant(id="legacy-keep", url="characters/legacy-keep.png")
    mirrored = ImageVariant(id="variant-delete", url="characters/delete.png")
    character = Character(
        id="character-1",
        name="Hero",
        description="Hero",
        image_url=mirrored.url,
        full_body_image_url=mirrored.url,
        full_body=AssetUnit(
            selected_image_id=mirrored.id,
            image_variants=[canonical_keep, mirrored],
        ),
        full_body_asset=ImageAsset(
            selected_id=mirrored.id,
            variants=[legacy_keep, mirrored],
        ),
    )
    script = _script(characters=[character])
    pipeline.scripts = {script.id: script}

    updated = pipeline.delete_asset_variant(
        script.id, character.id, "character", mirrored.id
    )

    saved = updated.characters[0]
    assert saved.full_body is not None
    assert saved.full_body_asset is not None
    assert [item.id for item in saved.full_body.image_variants] == [canonical_keep.id]
    assert [item.id for item in saved.full_body_asset.variants] == [legacy_keep.id]
    assert saved.full_body.selected_image_id == canonical_keep.id
    assert saved.full_body_asset.selected_id == legacy_keep.id
    assert saved.full_body_image_url == canonical_keep.url
    assert saved.image_url == canonical_keep.url
    assert mirrored.id not in json.dumps(_persisted_project(pipeline))


def test_delete_series_asset_cascades_to_every_episode_without_fallback(
    pipeline: ComicGenPipeline,
) -> None:
    series_id = "series-1"
    asset_id = "shared-character"
    shared_task = _task("shared-embedded-task", asset_id=asset_id)
    shared = Character(
        id=asset_id,
        name="Shared Hero",
        description="Shared Hero",
        video_assets=[shared_task],
    )
    first_task = _task("first-episode-task", asset_id=asset_id)
    second_task = _task("second-episode-task", asset_id=asset_id)
    first_frame = StoryboardFrame(
        id="frame-1", scene_id="", character_ids=[asset_id]
    )
    second_frame = StoryboardFrame(
        id="frame-2", scene_id="", character_ids=[asset_id]
    )
    first = _script(
        id="episode-1",
        series_id=series_id,
        frames=[first_frame],
        video_tasks=[shared_task, first_task],
    )
    second = _script(
        id="episode-2",
        series_id=series_id,
        frames=[second_frame],
        video_tasks=[shared_task.model_copy(deep=True), second_task],
    )
    pipeline.scripts = {first.id: first, second.id: second}
    pipeline.series_store = {
        series_id: _series(
            series_id,
            characters=[shared],
            episode_ids=[first.id, second.id],
        )
    }

    returned = pipeline.delete_character(first.id, asset_id)

    assert returned is first
    assert pipeline.series_store[series_id].characters == []
    assert first_frame.character_ids == []
    assert second_frame.character_ids == []
    assert first.video_tasks == []
    assert second.video_tasks == []
    persisted_projects = json.loads(
        Path(pipeline.data_file).read_text(encoding="utf-8")
    )
    assert asset_id not in persisted_projects["episode-1"]["frames"][0][
        "character_ids"
    ]
    assert asset_id not in persisted_projects["episode-2"]["frames"][0][
        "character_ids"
    ]
    persisted_series = json.loads(
        Path(pipeline.series_data_file).read_text(encoding="utf-8")
    )
    assert persisted_series[series_id]["characters"] == []


def test_delete_local_override_keeps_refs_and_underlying_series_task(
    pipeline: ComicGenPipeline,
) -> None:
    series_id = "series-1"
    asset_id = "shared-character"
    local_task = _task("local-task", asset_id=asset_id)
    fallback_task = _task("series-task", asset_id=asset_id)
    local = Character(
        id=asset_id,
        name="Local Hero",
        description="Local override",
        video_assets=[local_task],
    )
    inherited = Character(
        id=asset_id,
        name="Series Hero",
        description="Series fallback",
        video_assets=[fallback_task],
    )
    frame = StoryboardFrame(id="frame-1", scene_id="", character_ids=[asset_id])
    script = _script(
        series_id=series_id,
        characters=[local],
        frames=[frame],
        video_tasks=[local_task, fallback_task],
    )
    pipeline.scripts = {script.id: script}
    pipeline.series_store = {
        series_id: _series(
            series_id,
            characters=[inherited],
            episode_ids=[script.id],
        )
    }

    pipeline.delete_character(script.id, asset_id)

    assert script.characters == []
    assert frame.character_ids == [asset_id]
    assert [task.id for task in script.video_tasks] == [fallback_task.id]
    resolved = pipeline.resolve_episode_assets(script)
    assert resolved["characters"] == [inherited]
    assert pipeline.series_store[series_id].characters == [inherited]


def test_delete_series_asset_keeps_cross_episode_refs_when_global_fallback_exists(
    pipeline: ComicGenPipeline,
) -> None:
    series_id = "series-1"
    asset_id = "layered-prop"
    shared = Prop(id=asset_id, name="Series Prop", description="Series layer")
    global_fallback = Prop(
        id=asset_id, name="Global Prop", description="Global fallback"
    )
    first_frame = StoryboardFrame(id="frame-1", scene_id="", prop_ids=[asset_id])
    second_frame = StoryboardFrame(id="frame-2", scene_id="", prop_ids=[asset_id])
    first = _script(id="episode-1", series_id=series_id, frames=[first_frame])
    second = _script(id="episode-2", series_id=series_id, frames=[second_frame])
    pipeline.scripts = {first.id: first, second.id: second}
    pipeline.series_store = {
        series_id: _series(
            series_id,
            props=[shared],
            episode_ids=[first.id, second.id],
        )
    }
    pipeline.library_store = GlobalAssetLibrary(props=[global_fallback])

    pipeline.delete_prop(first.id, asset_id)

    assert pipeline.series_store[series_id].props == []
    assert first_frame.prop_ids == [asset_id]
    assert second_frame.prop_ids == [asset_id]
    assert pipeline.resolve_episode_assets(first)["props"] == [global_fallback]
    assert pipeline.resolve_episode_assets(second)["props"] == [global_fallback]


def test_delete_inherited_series_variant_updates_shared_owner(
    pipeline: ComicGenPipeline,
) -> None:
    series_id = "series-1"
    keep = ImageVariant(id="keep", url="scenes/keep.png")
    remove = ImageVariant(id="remove", url="scenes/remove.png")
    inherited = Scene(
        id="shared-scene",
        name="Shared Scene",
        description="Shared",
        image_url=remove.url,
        image_asset=ImageAsset(selected_id=remove.id, variants=[keep, remove]),
    )
    first = _script(id="episode-1", series_id=series_id)
    second = _script(id="episode-2", series_id=series_id)
    pipeline.scripts = {first.id: first, second.id: second}
    pipeline.series_store = {
        series_id: _series(
            series_id,
            scenes=[inherited],
            episode_ids=[first.id, second.id],
        )
    }

    pipeline.delete_asset_variant(first.id, inherited.id, "scene", remove.id)

    assert inherited.image_asset is not None
    assert [item.id for item in inherited.image_asset.variants] == [keep.id]
    assert inherited.image_asset.selected_id == keep.id
    assert inherited.image_url == keep.url
    assert pipeline.resolve_episode_assets(first)["scenes"] == [inherited]
    assert pipeline.resolve_episode_assets(second)["scenes"] == [inherited]
    persisted_series = json.loads(
        Path(pipeline.series_data_file).read_text(encoding="utf-8")
    )
    assert remove.id not in json.dumps(persisted_series[series_id])


def test_delete_asset_video_rejects_task_owned_by_different_asset(
    pipeline: ComicGenPipeline,
) -> None:
    wrong_owner_task = _task("video-1", asset_id="character-other")
    requested = Character(
        id="character-requested", name="Requested", description="Requested"
    )
    other = Character(id="character-other", name="Other", description="Other")
    script = _script(
        characters=[requested, other],
        video_tasks=[wrong_owner_task],
    )
    pipeline.scripts = {script.id: script}

    with pytest.raises(ValueError, match="does not belong"):
        pipeline.delete_asset_video(
            script.id, requested.id, "character", wrong_owner_task.id
        )

    assert script.video_tasks == [wrong_owner_task]


def test_delete_asset_video_rejects_conflicting_canonical_owner_even_with_embed(
    pipeline: ComicGenPipeline,
) -> None:
    embedded = _task("video-1", asset_id="character-requested")
    conflicting = _task("video-1", asset_id="character-other")
    requested = Character(
        id="character-requested",
        name="Requested",
        description="Requested",
        video_assets=[embedded],
    )
    script = _script(characters=[requested], video_tasks=[conflicting])
    pipeline.scripts = {script.id: script}

    with pytest.raises(ValueError, match="does not belong"):
        pipeline.delete_asset_video(
            script.id, requested.id, "character", embedded.id
        )

    assert script.video_tasks == [conflicting]
    assert requested.video_assets == [embedded]


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_delete_asset_video_rejects_active_task(
    pipeline: ComicGenPipeline,
    status: str,
) -> None:
    active = _task("active-video", asset_id="character-1")
    active.status = status
    character = Character(
        id="character-1",
        name="Hero",
        description="Hero",
        video_assets=[active.model_copy(deep=True)],
    )
    script = _script(characters=[character], video_tasks=[active])
    pipeline.scripts = {script.id: script}

    with pytest.raises(ValueError, match="Running video tasks"):
        pipeline.delete_asset_video(script.id, character.id, "character", active.id)

    assert script.video_tasks == [active]
    assert [task.id for task in character.video_assets] == [active.id]


def test_delete_asset_video_rejects_active_canonical_copy_in_another_episode(
    pipeline: ComicGenPipeline,
) -> None:
    series_id = "series-1"
    embedded = _task("shared-video", asset_id="shared-character")
    canonical = embedded.model_copy(deep=True)
    canonical.project_id = "episode-2"
    canonical.status = "processing"
    shared = Character(
        id="shared-character",
        name="Shared Hero",
        description="Shared",
        video_assets=[embedded],
    )
    first = _script(id="episode-1", series_id=series_id)
    second = _script(
        id="episode-2", series_id=series_id, video_tasks=[canonical]
    )
    pipeline.scripts = {first.id: first, second.id: second}
    pipeline.series_store = {
        series_id: _series(
            series_id,
            characters=[shared],
            episode_ids=[first.id, second.id],
        )
    }

    with pytest.raises(ValueError, match="Running video tasks"):
        pipeline.delete_asset_video(
            first.id, shared.id, "character", embedded.id
        )

    assert second.video_tasks == [canonical]
    assert shared.video_assets == [embedded]


def test_delete_local_asset_video_does_not_touch_unrelated_project(
    pipeline: ComicGenPipeline,
) -> None:
    local_task = _task("duplicate-id", asset_id="character-1")
    unrelated_task = local_task.model_copy(deep=True)
    unrelated_task.project_id = "project-2"
    local = Character(
        id="character-1",
        name="Local Hero",
        description="Local",
        video_assets=[local_task],
    )
    unrelated = Character(
        id="character-1",
        name="Other Hero",
        description="Unrelated",
        video_assets=[unrelated_task],
    )
    first = _script(characters=[local], video_tasks=[local_task])
    second = _script(
        id="project-2", characters=[unrelated], video_tasks=[unrelated_task]
    )
    pipeline.scripts = {first.id: first, second.id: second}

    pipeline.delete_asset_video(
        first.id, local.id, "character", local_task.id
    )

    assert first.video_tasks == []
    assert local.video_assets == []
    assert second.video_tasks == [unrelated_task]
    assert unrelated.video_assets == [unrelated_task]
