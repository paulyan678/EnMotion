import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.apps.comic_gen.pipeline import (
    AssemblyMutationConflictError,
    AssemblyOperationInProgressError,
    ComicGenPipeline,
)


def _task(task_id: str, frame_id: str, url: str, created_at: float) -> VideoTask:
    return VideoTask(
        id=task_id,
        project_id="project-1",
        frame_id=frame_id,
        image_url="",
        prompt="move",
        status="completed",
        video_url=url,
        created_at=created_at,
    )


def _script(*, two_frames: bool = False) -> Script:
    frames = [
        StoryboardFrame(
            id="frame-1",
            scene_id="scene-1",
            dialogue="Original line",
            duration=5,
            selected_video_id="take-a",
            video_url="video/a.mp4",
        )
    ]
    tasks = [
        _task("take-a", "frame-1", "video/a.mp4", 1),
        _task("take-b", "frame-1", "video/b.mp4", 2),
    ]
    if two_frames:
        frames.append(
            StoryboardFrame(
                id="frame-2",
                scene_id="scene-1",
                selected_video_id="take-c",
                video_url="video/c.mp4",
            )
        )
        tasks.append(_task("take-c", "frame-2", "video/c.mp4", 3))
    return Script(
        id="project-1",
        title="Assembly",
        original_text="text",
        frames=frames,
        video_tasks=tasks,
        merged_video_url="videos/merged.mp4",
        created_at=time.time(),
        updated_at=time.time(),
    )


@pytest.fixture
def pipeline(tmp_path: Path) -> ComicGenPipeline:
    value = ComicGenPipeline.__new__(ComicGenPipeline)
    value.output_root = str(tmp_path / "output")
    value.scripts = {}
    value.series_store = {}
    value.library_store = SimpleNamespace(characters=[], scenes=[], props=[])
    value._save_lock = threading.RLock()
    value._assembly_operation_locks_guard = threading.Lock()
    value._assembly_operation_locks = {}
    value._extraction_cache = {}
    value._consumed_extraction_revisions = {}
    value._save_data = Mock()
    value._save_series_data = Mock()
    value._effective_chat_model = Mock(return_value="deepseek-v4-flash")
    return value


def _install_with_merged_file(
    pipeline: ComicGenPipeline,
    script: Script,
) -> Path:
    merged_path = Path(pipeline.output_root) / "video" / "merged.mp4"
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    merged_path.write_bytes(b"old merged output")
    pipeline.scripts = {script.id: script}
    return merged_path


def test_manual_take_selection_invalidates_and_retires_legacy_merged_path(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)

    pipeline.select_video_for_frame(script.id, "frame-1", "take-b")

    assert script.frames[0].selected_video_id == "take-b"
    assert script.merged_video_url is None
    assert not merged_path.exists()


def test_persistence_failure_rolls_back_source_and_keeps_merged_file(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)
    pipeline._save_data.side_effect = OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        pipeline.select_video_for_frame(script.id, "frame-1", "take-b")

    assert pipeline.scripts[script.id] is script
    assert script.frames[0].selected_video_id == "take-a"
    assert script.frames[0].is_video_pinned is False
    assert script.merged_video_url == "videos/merged.mp4"
    assert merged_path.exists()


def test_active_merge_blocks_user_source_mutation_without_partial_state(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)
    active_lock = threading.Lock()
    active_lock.acquire()
    pipeline._assembly_operation_locks[script.id] = active_lock
    try:
        with pytest.raises(AssemblyOperationInProgressError):
            pipeline.select_video_for_frame(script.id, "frame-1", "take-b")
    finally:
        active_lock.release()

    assert script.frames[0].selected_video_id == "take-a"
    assert script.merged_video_url == "videos/merged.mp4"
    assert merged_path.exists()


def test_metadata_only_frame_edit_preserves_merge_but_subtitle_edit_invalidates(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)

    pipeline.update_frame(
        script.id,
        "frame-1",
        action_description="Metadata-only blocking note",
    )

    assert script.merged_video_url == "videos/merged.mp4"
    assert merged_path.exists()

    pipeline.update_frame(script.id, "frame-1", dialogue="Changed subtitle")

    assert script.merged_video_url is None
    assert not merged_path.exists()


@pytest.mark.parametrize("mutation_name", ["add", "copy", "delete", "reorder"])
def test_frame_structure_mutations_invalidate_merge(
    pipeline: ComicGenPipeline,
    mutation_name: str,
) -> None:
    script = _script(two_frames=mutation_name == "reorder")
    merged_path = _install_with_merged_file(pipeline, script)

    if mutation_name == "add":
        pipeline.add_frame(script.id, action_description="New shot")
    elif mutation_name == "copy":
        pipeline.copy_frame(script.id, "frame-1")
    elif mutation_name == "delete":
        pipeline.delete_frame(script.id, "frame-1")
    else:
        pipeline.reorder_frames(script.id, ["frame-2", "frame-1"])

    assert script.merged_video_url is None
    assert not merged_path.exists()


def test_copied_frame_nested_state_is_independent(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    original = script.frames[0]
    original.character_ids = ["character-1"]
    original.composition_data = {"layers": [{"id": "layer-1"}]}
    _install_with_merged_file(pipeline, script)

    pipeline.copy_frame(script.id, original.id)
    copied = script.frames[1]
    copied.character_ids.append("character-2")
    copied.composition_data["layers"][0]["id"] = "changed"

    assert original.character_ids == ["character-1"]
    assert original.composition_data == {"layers": [{"id": "layer-1"}]}


def test_deleting_unselected_fallback_task_invalidates_when_it_changes_fallback(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    script.frames[0].selected_video_id = None
    script.frames[0].video_url = None
    merged_path = _install_with_merged_file(pipeline, script)

    pipeline.delete_video_task(script.id, "take-a")

    assert script.merged_video_url is None
    assert not merged_path.exists()


def test_preview_only_revert_preserves_merge_but_applied_revert_invalidates(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    script.frames[0].preview_video_url = "video/preview.mp4"
    script.frames[0].preview_video_task_id = "take-a"
    merged_path = _install_with_merged_file(pipeline, script)
    preview_path = Path(pipeline.output_root) / "video" / "preview.mp4"
    preview_path.write_bytes(b"preview")

    pipeline.revert_dub(script.id, "frame-1")

    assert script.merged_video_url == "videos/merged.mp4"
    assert merged_path.exists()

    script.frames[0].dubbed_video_url = "video/dubbed.mp4"
    script.frames[0].dubbed_video_task_id = "take-a"
    dubbed_path = Path(pipeline.output_root) / "video" / "dubbed.mp4"
    dubbed_path.write_bytes(b"dubbed")
    pipeline.revert_dub(script.id, "frame-1")

    assert script.merged_video_url is None
    assert not merged_path.exists()


def test_refine_commits_detached_frame_and_invalidates_subtitle_timing(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)
    pipeline.script_processor = Mock()
    pipeline.script_processor.refine_frame_to_rich.return_value = {"duration": 8}

    refined = pipeline.refine_frame(script.id, "frame-1")

    assert refined.duration == 8
    assert script.frames[0] is refined
    assert script.merged_video_url is None
    assert not merged_path.exists()


def test_refine_busy_commit_leaves_live_frame_untouched(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)
    pipeline.script_processor = Mock()
    pipeline.script_processor.refine_frame_to_rich.return_value = {"duration": 8}
    active_lock = threading.Lock()
    active_lock.acquire()
    pipeline._assembly_operation_locks[script.id] = active_lock
    try:
        with pytest.raises(AssemblyOperationInProgressError):
            pipeline.refine_frame(script.id, "frame-1")
    finally:
        active_lock.release()

    assert pipeline.script_processor.refine_frame_to_rich.called
    assert script.frames[0].duration == 5
    assert script.merged_video_url == "videos/merged.mp4"
    assert merged_path.exists()


def test_refine_rejects_stale_provider_result_and_preserves_newer_edit(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    merged_path = _install_with_merged_file(pipeline, script)
    pipeline.script_processor = Mock()

    def edit_during_provider(*_args):
        pipeline.update_frame(
            script.id,
            "frame-1",
            action_description="Newer user edit",
        )
        return {"duration": 8}

    pipeline.script_processor.refine_frame_to_rich.side_effect = edit_during_provider

    with pytest.raises(AssemblyMutationConflictError, match="changed while AI refinement"):
        pipeline.refine_frame(script.id, "frame-1")

    assert script.frames[0].action_description == "Newer user edit"
    assert script.frames[0].duration == 5
    assert script.merged_video_url == "videos/merged.mp4"
    assert merged_path.exists()


def test_storyboard_analysis_rejects_stale_result_instead_of_overwriting_newer_frames(
    pipeline: ComicGenPipeline,
) -> None:
    script = _script()
    _install_with_merged_file(pipeline, script)
    pipeline.script_processor = Mock()

    def edit_during_provider(*_args, **_kwargs):
        pipeline.update_frame(
            script.id,
            "frame-1",
            action_description="Newer storyboard edit",
        )
        return [
            {
                "action_summary": "Stale AI replacement",
                "scene_ref_name": "",
                "duration": 5,
            }
        ]

    pipeline.script_processor.analyze_to_storyboard.side_effect = edit_during_provider

    with pytest.raises(AssemblyMutationConflictError, match="project changed"):
        pipeline.analyze_text_to_frames(script.id, "text")

    assert [item.id for item in script.frames] == ["frame-1"]
    assert script.frames[0].action_description == "Newer storyboard edit"


def test_reparse_with_no_frames_still_persists_replacement_and_retires_merge(
    pipeline: ComicGenPipeline,
) -> None:
    existing = _script()
    existing.frames = []
    existing.video_tasks = []
    merged_path = _install_with_merged_file(pipeline, existing)
    replacement = Script(
        id="temporary",
        title="Reparsed",
        original_text="new text",
        created_at=time.time(),
        updated_at=time.time(),
    )
    pipeline._extraction_cache[existing.id] = (time.time(), "revision-1", replacement)

    reparsed = pipeline.reparse_project(
        existing.id,
        "new text",
        preview_revision="revision-1",
    )

    assert reparsed.title == "Reparsed"
    assert pipeline.scripts[existing.id] is reparsed
    assert reparsed.merged_video_url is None
    assert not merged_path.exists()
    pipeline._save_data.assert_called_once()
