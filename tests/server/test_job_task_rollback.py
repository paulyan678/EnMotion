from __future__ import annotations

import threading

from src.apps.comic_gen.models import (
    Character,
    GenerationStatus,
    Script,
    VideoTask,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


def _script(*, character: Character | None = None, task: VideoTask | None = None) -> Script:
    return Script(
        id="project-1",
        title="Rollback test",
        original_text="test",
        characters=[character] if character else [],
        video_tasks=[task] if task else [],
        created_at=1.0,
        updated_at=1.0,
    )


def _pipeline(script: Script, tmp_path) -> ComicGenPipeline:
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.output_root = str(tmp_path)
    pipeline.scripts = {script.id: script}
    pipeline.series_store = {}
    pipeline.asset_generation_tasks = {}
    pipeline.video_generation_tasks = {}
    pipeline._save_data = lambda: None
    pipeline._save_series_data_unlocked = lambda: None
    return pipeline


def test_asset_task_rollback_restores_previous_status(tmp_path):
    character = Character(id="character-1", name="A", description="A person")
    character.status = GenerationStatus.PROCESSING
    script = _script(character=character)
    pipeline = _pipeline(script, tmp_path)
    pipeline.asset_generation_tasks["task-1"] = {
        "script_id": script.id,
        "asset_id": character.id,
        "asset_type": "character",
        "previous_asset_status": GenerationStatus.PENDING,
        "asset_is_series_level": False,
    }

    assert pipeline.rollback_asset_generation_task("task-1")
    assert character.status is GenerationStatus.PENDING
    assert pipeline.asset_generation_tasks == {}


def test_stale_asset_reservation_becomes_retryable_failure(tmp_path):
    character = Character(id="character-1", name="A", description="A person")
    character.status = GenerationStatus.PROCESSING
    script = _script(character=character)
    pipeline = _pipeline(script, tmp_path)

    assert pipeline.fail_orphaned_asset_reservation(script.id, character.id, "character")
    assert character.status is GenerationStatus.FAILED


def test_forget_asset_task_drops_bookkeeping_without_rolling_back_state(tmp_path):
    character = Character(id="character-1", name="A", description="A person")
    character.status = GenerationStatus.PROCESSING
    script = _script(character=character)
    pipeline = _pipeline(script, tmp_path)
    pipeline.asset_generation_tasks["task-1"] = {"status": "pending"}

    assert pipeline.forget_asset_generation_task("task-1")
    assert pipeline.asset_generation_tasks == {}
    assert character.status is GenerationStatus.PROCESSING


def test_video_task_rollback_removes_task_asset_link_and_snapshot(tmp_path):
    snapshot = tmp_path / "video_inputs" / "task-1.png"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"snapshot")
    task = VideoTask(
        id="task-1",
        project_id="project-1",
        image_url="video_inputs/task-1.png",
        prompt="Animate",
    )
    character = Character(
        id="character-1",
        name="A",
        description="A person",
        video_assets=[task],
    )
    script = _script(character=character, task=task)
    pipeline = _pipeline(script, tmp_path)

    assert pipeline.rollback_video_task(script.id, task.id)
    assert script.video_tasks == []
    assert character.video_assets == []
    assert not snapshot.exists()
