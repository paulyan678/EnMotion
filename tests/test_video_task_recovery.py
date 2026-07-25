"""Studio video-task resilience tests.

Covers the three regressions surfaced by the "stuck on 排队中..." user
report:

  1. Backend restart eats in-memory FastAPI BackgroundTasks. The persisted
     task on disk stays at status="pending" forever and the UI shows an
     eternal spinner. Fix: pipeline.__init__ runs _recover_orphan_tasks()
     which marks pending/processing video tasks as failed with a clear
     reason, so the existing Retry button becomes usable.

  2. BG task wrapper in api.py used to silently log + drop exceptions
     that escaped pipeline.process_video_task's own try/except (e.g.
     get_script raising). The user saw nothing. Fix: a new
     pipeline.mark_video_task_failed helper writes status + error so the
     UI gets a definite failure.

  3. A stale client can submit a removed model or unsupported R2V mode.
     create_video_task rejects both before persisting a task, so the
     frontend gets a clean 400 instead of a permanently-failed task.
"""

import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from src.apps.comic_gen.models import (
    Character,
    ImageVariant,
    Script,
    StoryboardFrame,
    VideoTask,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.video_failures import (
    VIDEO_CANCELED_CODE,
    VIDEO_INTERRUPTED_CODE,
    VIDEO_TIMEOUT_CODE,
)
from src.models.newapi import (
    INPUT_IMAGE_PRIVACY_ERROR_CODE,
    INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
    NewAPIProviderError,
)


VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"
VIDEO_KEY_FIELD = "NEWAPI_SEEDANCE_2_FAST_API_KEY"


@pytest.fixture
def pipeline(tmp_path):
    """Pipeline with temp data files, real IO bypassed."""
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.scripts = {}
    p.series_store = {}
    return p


def _video_task(status="pending", task_id=None) -> VideoTask:
    return VideoTask(
        id=task_id or str(uuid.uuid4()),
        project_id="p1",
        image_url="uploads/img.png",
        prompt="prompt",
        status=status,
        model=VIDEO_MODEL,
    )


def _script_with_tasks(*tasks) -> Script:
    return Script(
        id="p1",
        title="Project",
        original_text="text",
        created_at=time.time(),
        updated_at=time.time(),
        video_tasks=list(tasks),
    )


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------


def test_orphan_recovery_marks_pending_and_processing_as_failed(pipeline):
    """Pending/processing tasks left over from a prior process die in
    mid-air when uvicorn restarts. _recover_orphan_tasks stamps them
    failed so the UI's Retry path is reachable."""
    pending = _video_task(status="pending", task_id="t-pending")
    processing = _video_task(status="processing", task_id="t-processing")
    completed = _video_task(status="completed", task_id="t-completed")
    failed = _video_task(status="failed", task_id="t-failed")
    pipeline.scripts = {
        "p1": _script_with_tasks(pending, processing, completed, failed),
    }

    pipeline._recover_orphan_tasks()

    by_id = {t.id: t for t in pipeline.scripts["p1"].video_tasks}
    assert by_id["t-pending"].status == "failed"
    assert "完成前中断" in (by_id["t-pending"].error or "")
    assert by_id["t-pending"].error_code == VIDEO_INTERRUPTED_CODE
    assert "重新启动" in (by_id["t-pending"].error_diagnostic or "")
    assert by_id["t-processing"].status == "failed"
    # Completed + failed are untouched.
    assert by_id["t-completed"].status == "completed"
    assert by_id["t-failed"].status == "failed"


def test_orphan_recovery_preserves_existing_error_message(pipeline):
    """If a stuck task already has an error message attached, the
    recovery sweep doesn't overwrite it (preserves diagnostic value)."""
    task = _video_task(status="pending", task_id="t1")
    task.error = "New API provider timed out"
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    pipeline._recover_orphan_tasks()

    recovered = pipeline.scripts["p1"].video_tasks[0]
    assert recovered.status == "failed"
    assert recovered.error == "New API provider timed out"


def test_orphan_recovery_is_noop_when_nothing_stuck(pipeline):
    pipeline.scripts = {
        "p1": _script_with_tasks(_video_task(status="completed")),
    }

    pipeline._recover_orphan_tasks()  # Should not raise

    # No save side-effect needed (recovered count was zero).
    assert pipeline.scripts["p1"].video_tasks[0].status == "completed"


# ---------------------------------------------------------------------------
# mark_video_task_failed (belt-and-suspenders writeback)
# ---------------------------------------------------------------------------


def test_mark_video_task_failed_writes_status_and_error(pipeline):
    task = _video_task(status="processing", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    ok = pipeline.mark_video_task_failed("p1", "t1", "Background error: boom")

    assert ok is True
    after = pipeline.scripts["p1"].video_tasks[0]
    assert after.status == "failed"
    assert after.error == "Background error: boom"


def test_mark_video_task_failed_does_not_downgrade_completed(pipeline):
    """A spurious wrapper exception or a late cancel must not flip a
    successful task back to failed."""
    task = _video_task(status="completed", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    ok = pipeline.mark_video_task_failed("p1", "t1", "spurious")

    assert ok is False
    assert pipeline.scripts["p1"].video_tasks[0].status == "completed"


def test_mark_video_task_failed_returns_false_for_unknown(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks(_video_task(task_id="t1"))}

    assert pipeline.mark_video_task_failed("p1", "nope", "x") is False
    assert pipeline.mark_video_task_failed("nope", "t1", "x") is False


def test_cancel_and_retry_keep_terminal_states_distinct_and_preserve_recipe(pipeline):
    task = _video_task(status="pending", task_id="t1")
    task.duration = 12
    task.resolution = "1080p"
    task.seed = 417
    task.ratio = "9:16"
    original_recipe = {
        "image_url": task.image_url,
        "prompt": task.prompt,
        "duration": task.duration,
        "resolution": task.resolution,
        "seed": task.seed,
        "ratio": task.ratio,
        "model": task.model,
    }
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    assert pipeline.mark_video_task_canceled("p1", "t1") is True
    assert task.status == "canceled"
    assert task.error_code == VIDEO_CANCELED_CODE
    assert pipeline.prepare_video_task_retry("p1", "t1") is True
    assert task.status == "pending"
    assert task.error is None
    assert task.error_code is None
    assert task.error_diagnostic is None
    assert {name: getattr(task, name) for name in original_recipe} == original_recipe


def test_terminal_state_and_retry_sync_asset_linked_task_copy(pipeline):
    task = _video_task(status="processing", task_id="asset-task")
    task.asset_id = "character-1"
    asset_copy = task.model_copy(deep=True)
    character = Character(
        id="character-1",
        name="Fictional Hero",
        description="An illustrated character",
        video_assets=[asset_copy],
    )
    script = _script_with_tasks(task)
    script.characters = [character]
    pipeline.scripts = {"p1": script}

    assert pipeline.mark_video_task_failed(
        "p1",
        task.id,
        "Provider rejected the request",
        error_code="provider_rejected",
        error_diagnostic="HTTP 400",
        overwrite=True,
    )
    linked_task = character.video_assets[0]
    assert linked_task.status == "failed"
    assert linked_task.error_code == "provider_rejected"
    assert linked_task.error_diagnostic == "HTTP 400"

    assert pipeline.prepare_video_task_retry("p1", task.id)
    linked_task = character.video_assets[0]
    assert linked_task.status == "pending"
    assert linked_task.error is None
    assert linked_task.error_code is None

    assert pipeline.mark_video_task_canceled("p1", task.id)
    linked_task = character.video_assets[0]
    assert linked_task.status == "canceled"
    assert linked_task.error_code == VIDEO_CANCELED_CODE


def test_failed_task_metadata_survives_a_fresh_pipeline_reload(tmp_path):
    output_root = tmp_path / "output"
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        first = ComicGenPipeline(
            {"output_root": str(output_root), "recover_orphan_tasks": False}
        )
        first.scripts = {
            "p1": _script_with_tasks(_video_task(status="processing", task_id="t1"))
        }
        assert first.mark_video_task_failed(
            "p1",
            "t1",
            "Video generation timed out.",
            error_code=VIDEO_TIMEOUT_CODE,
            error_diagnostic="provider polling exceeded 60 minutes",
            overwrite=True,
        )
        second = ComicGenPipeline(
            {"output_root": str(output_root), "recover_orphan_tasks": False}
        )

    reloaded = second.scripts["p1"].video_tasks[0]
    assert reloaded.status == "failed"
    assert reloaded.error_code == VIDEO_TIMEOUT_CODE
    assert reloaded.error_diagnostic == "provider polling exceeded 60 minutes"


# ---------------------------------------------------------------------------
# create_video_task: model ⇄ ref consistency guard (Bug C)
# ---------------------------------------------------------------------------


def test_create_video_task_rejects_removed_model_before_persisting(pipeline):
    """A stale selection cannot route to a removed provider model."""
    pipeline.scripts = {"p1": _script_with_tasks()}

    with pytest.raises(ValueError, match="Unsupported New API model ID"):
        pipeline.create_video_task(
            script_id="p1",
            image_url="uploads/img.png",
            prompt="A scene",
            model="wan2.7-r2v",
            generation_mode="i2v",
        )

    # Task was never persisted.
    assert pipeline.scripts["p1"].video_tasks == []


def test_create_video_task_rejects_unsupported_r2v_mode(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks()}

    with pytest.raises(ValueError, match="does not support generation mode 'r2v'"):
        pipeline.create_video_task(
            script_id="p1",
            image_url="",
            prompt="A scene",
            model=VIDEO_MODEL,
            generation_mode="r2v",
        )


def test_annotate_video_task_sets_star_and_label(pipeline):
    """User starts a take + attaches a free-text note via the new
    annotate endpoint. Both fields optional so the call can set either
    independently or together."""
    task = _video_task(status="completed", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    # Star + label together.
    after = pipeline.annotate_video_task("p1", "t1", is_starred=True, label="best lighting")
    assert after is not None
    assert after.is_starred is True
    assert after.label == "best lighting"

    # Star only — label preserved.
    after = pipeline.annotate_video_task("p1", "t1", is_starred=False)
    assert after.is_starred is False
    assert after.label == "best lighting"

    # Label only — star preserved.
    after = pipeline.annotate_video_task("p1", "t1", label="action怪")
    assert after.is_starred is False
    assert after.label == "action怪"

    # clear_label removes label, ignores label payload value.
    after = pipeline.annotate_video_task("p1", "t1", clear_label=True)
    assert after.label is None


def test_annotate_video_task_truncates_label_to_max(pipeline):
    """Label is bounded server-side at 20 chars so a runaway client
    can't store a 10 KB note in a single field."""
    task = _video_task(status="completed", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    long = "a" * 200
    after = pipeline.annotate_video_task("p1", "t1", label=long)
    assert after is not None
    assert len(after.label) == 20
    assert after.label == "a" * 20

    # Whitespace-only label clears.
    after = pipeline.annotate_video_task("p1", "t1", label="    ")
    assert after.label is None


def test_annotate_video_task_returns_none_for_unknown(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks(_video_task(task_id="t1"))}
    assert pipeline.annotate_video_task("p1", "ghost", is_starred=True) is None
    assert pipeline.annotate_video_task("ghost", "t1", is_starred=True) is None


def test_model_settings_migrates_stale_models_and_persists_approved_switch(pipeline):
    """Stale saved selections migrate, then approved switches persist."""
    from src.apps.comic_gen.models import Script
    script = Script(
        id="p1", title="P", original_text="t",
        created_at=time.time(), updated_at=time.time(),
        model_settings={
            "chat_model": "legacy-chat",
            "t2i_model": "wan2.7-image-pro",
            "i2v_model": "kling-v3-i2v",
            "r2v_model": "vidu-q3-r2v",
        },
    )
    pipeline.scripts = {"p1": script}

    assert script.model_settings.chat_model == "deepseek-v4-flash"
    assert script.model_settings.image_model == "gpt-image-2"
    assert script.model_settings.video_model == VIDEO_MODEL
    assert not hasattr(script.model_settings, "r2v_model")

    with patch.object(pipeline, "_save_data"):
        updated = pipeline.update_model_settings(
            "p1",
            chat_model="qwen3.7-max",
            video_model="doubao-seedance-2-0-mini-260615",
        )
    assert updated.model_settings.chat_model == "qwen3.7-max"
    assert updated.model_settings.video_model == "doubao-seedance-2-0-mini-260615"
    assert updated.model_settings.i2v_model == updated.model_settings.video_model


# ---------------------------------------------------------------------------
# Storyboard R2V workbench persistence (P.1 + P.2 + P.3)
# ---------------------------------------------------------------------------


def _script_with_frame(frame: StoryboardFrame, *tasks: VideoTask) -> Script:
    return Script(
        id="p1",
        title="P",
        original_text="t",
        created_at=time.time(),
        updated_at=time.time(),
        frames=[frame],
        video_tasks=list(tasks),
    )


def test_storyboard_frame_workbench_fields_default_empty():
    """Old frames without workbench_* fields parse cleanly with defaults."""
    legacy_payload = {"id": "f1", "scene_id": "s1"}
    frame = StoryboardFrame.model_validate(legacy_payload)
    assert frame.workbench_tab_mode is None
    assert frame.t2i_image_urls == []
    assert frame.t2i_selected_index == 0
    assert frame.workbench_generate_count == 1


def test_storyboard_frame_workbench_fields_round_trip():
    """New workbench state survives Pydantic round-trip."""
    frame = StoryboardFrame(
        id="f1",
        scene_id="s1",
        workbench_tab_mode="t2i_i2v",
        t2i_image_urls=["http://a", "http://b", "http://c"],
        t2i_selected_index=2,
        workbench_generate_count=4,
    )
    revived = StoryboardFrame.model_validate(frame.model_dump())
    assert revived.workbench_tab_mode == "t2i_i2v"
    assert revived.t2i_image_urls == ["http://a", "http://b", "http://c"]
    assert revived.t2i_selected_index == 2
    assert revived.workbench_generate_count == 4


def test_video_task_workbench_tab_default_none():
    """Existing VideoTask records without workbench_tab parse fine."""
    legacy_payload = {"id": "v1", "project_id": "p1", "image_url": "x", "prompt": "p"}
    task = VideoTask.model_validate(legacy_payload)
    assert task.workbench_tab is None


def test_update_frame_workbench_partial_writes(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with patch.object(pipeline, "_save_data"):
        updated = pipeline.update_frame_workbench(
            "p1", "f1", workbench_tab_mode="direct_r2v",
        )
    assert updated is not None
    assert updated.workbench_tab_mode == "direct_r2v"
    # Other fields untouched.
    assert updated.t2i_image_urls == []
    assert updated.t2i_selected_index == 0
    assert updated.workbench_generate_count == 1


def test_update_frame_workbench_rejects_unknown_tab_mode(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with pytest.raises(ValueError, match="workbench_tab_mode"):
        pipeline.update_frame_workbench("p1", "f1", workbench_tab_mode="bogus_tab")


def test_update_frame_workbench_caps_t2i_history_at_10(pipeline):
    """Server-side defense in depth — the client also caps but the
    server must not accept unbounded list growth."""
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    long_list = [f"http://img-{i}" for i in range(15)]
    with patch.object(pipeline, "_save_data"):
        updated = pipeline.update_frame_workbench(
            "p1", "f1", t2i_image_urls=long_list,
        )
    assert updated is not None
    # FIFO: oldest dropped, newest retained.
    assert updated.t2i_image_urls == [f"http://img-{i}" for i in range(5, 15)]
    assert len(updated.t2i_image_urls) == 10


def test_update_frame_workbench_clamps_selected_index_against_new_list(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with patch.object(pipeline, "_save_data"):
        updated = pipeline.update_frame_workbench(
            "p1", "f1",
            t2i_image_urls=["http://a", "http://b"],
            t2i_selected_index=99,  # out of range
        )
    assert updated is not None
    assert updated.t2i_selected_index == 1  # clamped to len-1


def test_update_frame_workbench_clamps_selected_index_to_zero_when_empty(pipeline):
    frame = StoryboardFrame(
        id="f1", scene_id="s1",
        t2i_image_urls=["http://a"],
        t2i_selected_index=0,
    )
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with patch.object(pipeline, "_save_data"):
        updated = pipeline.update_frame_workbench(
            "p1", "f1", t2i_image_urls=[], t2i_selected_index=5,
        )
    assert updated is not None
    assert updated.t2i_image_urls == []
    assert updated.t2i_selected_index == 0


def test_update_frame_workbench_clamps_generate_count_to_range(pipeline):
    # Both bounds tested against the same frame; the helper mutates in
    # place, so the second call overwrites the first — we capture each
    # clamped value at the moment of the call instead of relying on the
    # returned reference.
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with patch.object(pipeline, "_save_data"):
        too_high = pipeline.update_frame_workbench(
            "p1", "f1", workbench_generate_count=99,
        )
        assert too_high is not None
        assert too_high.workbench_generate_count == 6  # clamped to upper bound
        too_low = pipeline.update_frame_workbench(
            "p1", "f1", workbench_generate_count=0,
        )
        assert too_low is not None
        assert too_low.workbench_generate_count == 1  # clamped to lower bound


def test_update_frame_workbench_filters_blank_t2i_urls(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with patch.object(pipeline, "_save_data"):
        updated = pipeline.update_frame_workbench(
            "p1", "f1",
            t2i_image_urls=["http://a", "", "  ", "http://b", None],  # type: ignore[list-item]
        )
    assert updated is not None
    assert updated.t2i_image_urls == ["http://a", "http://b"]


def test_update_frame_workbench_returns_none_for_unknown(pipeline):
    pipeline.scripts = {"p1": _script_with_frame(StoryboardFrame(id="f1", scene_id="s1"))}
    assert pipeline.update_frame_workbench("p1", "ghost", workbench_tab_mode="t2i_i2v") is None
    assert pipeline.update_frame_workbench("ghost", "f1", workbench_tab_mode="t2i_i2v") is None


def test_create_video_task_persists_workbench_tab(pipeline, monkeypatch):
    monkeypatch.setenv(VIDEO_KEY_FIELD, "unit-test-video-key")
    pipeline.scripts = {"p1": _script_with_tasks()}
    with patch.object(pipeline, "_save_data"):
        script, task_id = pipeline.create_video_task(
            script_id="p1",
            image_url="http://example.com/img.png",
            prompt="A scene",
            model=VIDEO_MODEL,
            generation_mode="i2v",
            workbench_tab="t2i_i2v",
        )
    task = next(t for t in script.video_tasks if t.id == task_id)
    assert task.workbench_tab == "t2i_i2v"


def test_create_video_task_workbench_tab_defaults_to_none(pipeline, monkeypatch):
    """Pre-Phase-2 callers don't supply workbench_tab — must not break."""
    monkeypatch.setenv(VIDEO_KEY_FIELD, "unit-test-video-key")
    pipeline.scripts = {"p1": _script_with_tasks()}
    with patch.object(pipeline, "_save_data"):
        script, task_id = pipeline.create_video_task(
            script_id="p1",
            image_url="http://example.com/img.png",
            prompt="A scene",
            model=VIDEO_MODEL,
            generation_mode="i2v",
        )
    task = next(t for t in script.video_tasks if t.id == task_id)
    assert task.workbench_tab is None


# ---------------------------------------------------------------------------
# Remote temporary-file cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("consumer_fails", [False, True])
def test_extract_last_frame_removes_remote_temp_file(
    pipeline, tmp_path, consumer_fails
):
    task = _video_task(status="completed", task_id="video-1")
    task.video_url = "https://media.example.test/video.mp4"
    script = _script_with_tasks(task)
    script.frames = [StoryboardFrame(id="frame-1", scene_id="scene-1")]
    pipeline.scripts = {script.id: script}
    temporary = tmp_path / "downloaded-video.mp4"
    temporary.write_bytes(b"temporary video")

    def consume(*_args):
        if consumer_fails:
            raise RuntimeError("frame extraction failed")
        return script

    with patch.object(pipeline, "_download_temp_image", return_value=str(temporary)), \
         patch.object(pipeline, "_extract_last_frame_from_path", side_effect=consume):
        if consumer_fails:
            with pytest.raises(RuntimeError, match="frame extraction failed"):
                pipeline.extract_last_frame(script.id, "frame-1", task.id)
        else:
            assert pipeline.extract_last_frame(script.id, "frame-1", task.id) is script

    assert not temporary.exists()


@pytest.mark.parametrize("provider_fails", [False, True])
def test_process_video_task_removes_remote_temp_file(
    pipeline, tmp_path, provider_fails
):
    pipeline.output_root = str(tmp_path / "output")
    task = _video_task(status="pending", task_id="video-1")
    task.image_url = "https://media.example.test/source.png"
    script = _script_with_tasks(task)
    pipeline.scripts = {script.id: script}
    temporary = tmp_path / "downloaded-image.png"
    temporary.write_bytes(b"temporary image")

    class VideoModelStub:
        def generate(self, **kwargs):
            if provider_fails:
                raise RuntimeError("provider failed")
            return kwargs["output_path"], None

    pipeline._newapi_video_model = VideoModelStub()
    with patch.object(pipeline, "_download_temp_image", return_value=str(temporary)), \
         patch.object(pipeline, "_save_data"):
        pipeline.process_video_task(script.id, task.id)

    assert not temporary.exists()
    assert task.status == ("failed" if provider_fails else "completed")


def test_process_video_task_preserves_provider_safety_metadata(
    pipeline, tmp_path
):
    pipeline.output_root = str(tmp_path / "output")
    task = _video_task(status="pending", task_id="privacy-video")
    task.image_url = "https://media.example.test/source.png"
    script = _script_with_tasks(task)
    pipeline.scripts = {script.id: script}
    temporary = tmp_path / "downloaded-image.png"
    temporary.write_bytes(b"temporary image")

    class RejectingVideoModel:
        def generate(self, **_kwargs):
            raise NewAPIProviderError(
                INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
                error_code=INPUT_IMAGE_PRIVACY_ERROR_CODE,
                provider_code=(
                    "InputImageSensitiveContentDetected.PrivacyInformation"
                ),
                provider_message="The input image may contain a real person",
                http_status=400,
                phase="video request",
            )

    pipeline._newapi_video_model = RejectingVideoModel()
    with patch.object(
        pipeline, "_download_temp_image", return_value=str(temporary)
    ), patch.object(pipeline, "_save_data"):
        pipeline.process_video_task(script.id, task.id)

    assert task.status == "failed"
    assert task.error == INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE
    assert task.error_code == INPUT_IMAGE_PRIVACY_ERROR_CODE
    assert "服务商错误代码：" in (task.error_diagnostic or "")
    assert not temporary.exists()


def test_process_video_task_turns_provider_poll_timeout_into_terminal_failure(
    pipeline, tmp_path
):
    pipeline.output_root = str(tmp_path / "output")
    task = _video_task(status="pending", task_id="timeout-video")
    task.image_url = "https://media.example.test/source.png"
    script = _script_with_tasks(task)
    pipeline.scripts = {script.id: script}
    temporary = tmp_path / "downloaded-image.png"
    temporary.write_bytes(b"temporary image")

    class TimedOutVideoModel:
        def generate(self, **_kwargs):
            raise TimeoutError("Provider polling did not finish within 3600 seconds")

    pipeline._newapi_video_model = TimedOutVideoModel()
    with patch.object(
        pipeline, "_download_temp_image", return_value=str(temporary)
    ), patch.object(pipeline, "_save_data"):
        pipeline.process_video_task(script.id, task.id)

    assert task.status == "failed"
    assert task.error_code == VIDEO_TIMEOUT_CODE
    assert "等待超时" in (task.error or "")
    assert task.error_diagnostic == "视频服务在规定时间内未返回结果。"
    assert not temporary.exists()


def test_retried_asset_video_uses_current_selected_replacement(
    pipeline, tmp_path
):
    pipeline.output_root = str(tmp_path / "output")
    uploads = Path(pipeline.output_root) / "uploads"
    snapshots = Path(pipeline.output_root) / "video_inputs"
    uploads.mkdir(parents=True)
    snapshots.mkdir(parents=True)
    (uploads / "replacement.png").write_bytes(b"new-fictional-image")
    (snapshots / "asset-video.png").write_bytes(b"old-rejected-image")

    character = Character(
        id="character-1",
        name="Fictional Hero",
        description="An illustrated fantasy character",
    )
    replacement = ImageVariant(
        id="replacement",
        url="uploads/replacement.png",
    )
    character.reference_sheet.image_variants = [replacement]
    character.reference_sheet.selected_image_id = replacement.id

    task = _video_task(status="failed", task_id="asset-video")
    task.asset_id = character.id
    task.image_url = "video_inputs/asset-video.png"
    task.error = INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE
    task.error_code = INPUT_IMAGE_PRIVACY_ERROR_CODE
    task.error_diagnostic = "old diagnostic"
    script = _script_with_tasks(task)
    script.characters = [character]
    pipeline.scripts = {script.id: script}

    with patch.object(pipeline, "_save_data"):
        assert pipeline.refresh_asset_video_task_input(script.id, task.id) is True

    assert task.image_url == "video_inputs/asset-video.png"
    assert (snapshots / "asset-video.png").read_bytes() == b"new-fictional-image"
    assert task.error is None
    assert task.error_code is None
    assert task.error_diagnostic is None

    class SuccessfulRetryModel:
        def generate(self, **kwargs):
            assert kwargs["img_path"] == str(snapshots / "asset-video.png")
            return kwargs["output_path"], None

    pipeline._newapi_video_model = SuccessfulRetryModel()
    with patch.object(
        pipeline,
        "_download_temp_image",
        return_value=str(snapshots / "asset-video.png"),
    ), patch.object(pipeline, "_save_data"):
        pipeline.process_video_task(script.id, task.id)

    assert task.status == "completed"
    assert task.video_url == "video/video_asset-video.mp4"


def test_asset_video_creation_uses_selected_canonical_image(
    pipeline, tmp_path, monkeypatch
):
    pipeline.output_root = str(tmp_path / "output")
    uploads = Path(pipeline.output_root) / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "stale.png").write_bytes(b"stale-image")
    (uploads / "selected.png").write_bytes(b"selected-fictional-image")

    character = Character(
        id="character-1",
        name="Fictional Hero",
        description="An illustrated fantasy character",
        image_url="uploads/stale.png",
    )
    selected = ImageVariant(id="selected", url="uploads/selected.png")
    character.reference_sheet.image_variants = [selected]
    character.reference_sheet.selected_image_id = selected.id
    script = _script_with_tasks()
    script.characters = [character]
    pipeline.scripts = {script.id: script}
    monkeypatch.setenv(VIDEO_KEY_FIELD, "video-test-token")
    task_id = str(uuid.uuid4())

    with patch.object(pipeline, "_save_data"):
        _, created_id = pipeline.create_asset_video_task(
            script.id,
            character.id,
            "character",
            None,
            task_id=task_id,
        )

    task = next(item for item in script.video_tasks if item.id == created_id)
    assert task.image_url == f"video_inputs/{task_id}.png"
    assert (Path(pipeline.output_root) / task.image_url).read_bytes() == (
        b"selected-fictional-image"
    )
    assert "fictional character" in task.prompt


def test_failed_server_download_removes_partial_temp_file(pipeline, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    created_paths = []

    def fail_after_partial_write(_url, destination, **_kwargs):
        created_paths.append(destination)
        Path(destination).write_bytes(b"partial")
        raise RuntimeError("download interrupted")

    monkeypatch.setattr(
        "src.utils.media_security.download_remote_media",
        fail_after_partial_write,
    )

    with pytest.raises(RuntimeError, match="download interrupted"):
        pipeline._download_temp_image("https://media.example.test/image.png")

    assert len(created_paths) == 1
    assert not Path(created_paths[0]).exists()
