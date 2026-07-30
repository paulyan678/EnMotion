from __future__ import annotations

import json
import time

from src.apps.comic_gen.models import Character, ImageVariant, Series
from src.apps.hybrid import activity
from src.apps.web_runtime.workspace_snapshot import publish_workspace_snapshot


def test_hybrid_asset_activity_is_durable_and_identifies_the_asset(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))

    activity.record_asset_activity(
        "workspace-alice",
        task_id="task-1",
        job_type="series_asset",
        source="workspace",
        source_route="#/series/series-1",
        source_id="series-1",
        series_id="series-1",
        asset_id="asset-1",
        asset_type="character",
        asset_name="守塔人",
        prompt="全身角色设定图",
        model_name="gpt-image-2",
        batch_size=1,
        aspect_ratio="9:16",
    )

    queued = activity.list_activity("workspace-alice")
    assert len(queued) == 1
    assert queued[0]["detail"] == "守塔人"
    assert queued[0]["prompt"] == "全身角色设定图"
    assert queued[0]["status"] == "queued"
    assert queued[0]["source_context"]["asset_id"] == "asset-1"
    assert "_process_id" not in queued[0]

    activity.update_asset_activity("workspace-alice", "task-1", status="running")
    activity.update_asset_activity(
        "workspace-alice",
        "task-1",
        status="completed",
        outputs=[
            {
                "id": "variant-1",
                "media_type": "image",
                "media_path": "assets/variant-1.png",
                "thumbnail_path": "assets/variant-1.png",
                "filename": "variant-1.png",
            }
        ],
    )
    completed = activity.list_activity("workspace-alice")[0]
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["finished_at"]
    assert completed["outputs"][0]["id"] == "variant-1"


def test_hybrid_asset_activity_marks_restart_orphans_as_failed(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setattr(activity, "_PROCESS_ID", "process-100")
    activity.record_asset_activity(
        "workspace-alice",
        task_id="task-orphan",
        job_type="project_asset",
        source="workspace",
        source_route="#/project/project-1",
        source_id="project-1",
        series_id=None,
        asset_id="asset-1",
        asset_type="character",
        asset_name="守塔人",
        prompt=None,
        model_name="gpt-image-2",
        batch_size=1,
        aspect_ratio=None,
    )

    monkeypatch.setattr(activity, "_PROCESS_ID", "process-101")
    orphan = activity.list_activity("workspace-alice")[0]
    assert orphan["status"] == "failed"
    assert "重新启动" in orphan["error"]
    assert orphan["finished_at"]


def test_hybrid_activity_backfills_named_generated_asset_from_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    workspace_id = "workspace-alice"
    output = root / workspace_id / "output"
    output.mkdir(parents=True)
    created_at = time.time() - 60
    character = Character(
        id="asset-1",
        name="守塔人",
        description="银白色长发的守塔老人",
    )
    character.reference_sheet.image_variants = [
        ImageVariant(
            id="variant-1",
            url="assets/characters/variant-1.png",
            created_at=created_at,
            prompt_used="全身角色设定图",
        )
    ]
    character.reference_sheet.selected_image_id = "variant-1"
    series = Series(
        id="series-1",
        title="霓虹信使：失落星图",
        characters=[character],
        created_at=created_at,
        updated_at=created_at,
    )
    (output / "projects.json").write_text("{}", encoding="utf-8")
    (output / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}),
        encoding="utf-8",
    )
    (output / "library_assets.json").write_text("{}", encoding="utf-8")
    publish_workspace_snapshot(workspace_id)

    assert activity.backfill_asset_activity(workspace_id) == 1
    assert activity.backfill_asset_activity(workspace_id) == 0
    row = activity.list_activity(workspace_id)[0]
    assert row["detail"] == "守塔人"
    assert row["status"] == "completed"
    assert row["source_context"]["series_id"] == "series-1"
    assert row["outputs"] == [
        {
            "id": "variant-1",
            "media_type": "image",
            "media_path": "assets/characters/variant-1.png",
            "thumbnail_path": "assets/characters/variant-1.png",
            "filename": "variant-1.png",
        }
    ]


def test_hybrid_asset_outputs_include_only_new_unique_variants() -> None:
    from src.apps.comic_gen.api import _hybrid_asset_outputs

    character = Character(
        id="asset-1",
        name="守塔人",
        description="银白色长发的守塔老人",
    )
    existing = ImageVariant(id="existing", url="assets/existing.png")
    generated = ImageVariant(id="generated", url="assets/generated.png")
    character.reference_sheet.image_variants = [existing, generated]
    character.full_body_asset.variants = [generated]

    assert _hybrid_asset_outputs(character, frozenset({"existing"})) == [
        {
            "id": "generated",
            "media_type": "image",
            "media_path": "assets/generated.png",
            "thumbnail_path": "assets/generated.png",
            "filename": "generated.png",
        }
    ]
