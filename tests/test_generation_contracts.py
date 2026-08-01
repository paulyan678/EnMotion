import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen import pipeline as pipeline_module
from src.apps.comic_gen.llm import FrameRefineError, PolishError, ScriptProcessor
from src.apps.comic_gen.models import (
    CameraMovementData,
    GenerationStatus,
    GlobalAssetLibrary,
    ImageVariant,
    Script,
    StoryboardFrame,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.storyboard import StoryboardGenerator
from src.apps.playground.models import PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.models.newapi import NewAPIImageModel, NewAPIVideoModel


def test_batch_task_status_deduplicates_and_reports_missing(monkeypatch):
    def status(task_id: str):
        if task_id == "missing":
            raise HTTPException(status_code=404, detail="Task not found")
        return {"task_id": task_id, "status": "processing"}

    monkeypatch.setattr(comic_api, "get_task_status", status)

    assert comic_api.get_task_status_batch(["task-1", "task-1", "missing"]) == {
        "tasks": [{"task_id": "task-1", "status": "processing"}],
        "missing": ["missing"],
    }


def test_batch_task_status_enforces_a_bounded_request(monkeypatch):
    monkeypatch.setattr(
        comic_api,
        "get_task_status",
        lambda task_id: {"task_id": task_id, "status": "processing"},
    )

    with pytest.raises(HTTPException) as exc_info:
        comic_api.get_task_status_batch([f"task-{index}" for index in range(51)])

    assert exc_info.value.status_code == 422


def _frame(frame_id="frame-1"):
    return StoryboardFrame(
        id=frame_id,
        scene_id="scene-1",
        action_description="A character opens the door.",
    )


def _script(*frames):
    now = time.time()
    return Script(
        id="project-1",
        title="Contract test",
        original_text="A tiny script",
        frames=list(frames or [_frame()]),
        created_at=now,
        updated_at=now,
    )


class _RefineLLM:
    def __init__(self, *, content=None, config_error=None, chat_error=None):
        self.content = content
        self.config_error = config_error
        self.chat_error = chat_error

    def require_configured(self):
        if self.config_error:
            raise self.config_error

    def chat(self, **_kwargs):
        if self.chat_error:
            raise self.chat_error
        return self.content


def _processor_with(llm):
    processor = ScriptProcessor.__new__(ScriptProcessor)
    processor.llm = llm
    return processor


def test_update_frame_persists_camera_movement_type_and_refreshes_structured_data():
    frame = _frame()
    frame.camera_movement = "static"
    frame.camera_movement_structured = CameraMovementData(
        primary="static",
        secondary="orbit",
        speed="slow",
        description="An obsolete static-camera description",
    )
    script = _script(frame)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    saved = []
    pipeline._save_data = lambda: saved.append(True)

    updated = pipeline.update_frame(
        script.id,
        frame.id,
        camera_movement="follow",
    )

    updated_frame = updated.frames[0]
    assert updated_frame.camera_movement == "follow"
    assert updated_frame.camera_movement_structured.primary == "follow"
    assert updated_frame.camera_movement_structured.secondary == "orbit"
    assert updated_frame.camera_movement_structured.speed == "slow"
    assert updated_frame.camera_movement_structured.description is None
    assert saved == [True]


def test_update_frame_creates_structured_camera_movement_for_legacy_frame():
    frame = _frame()
    script = _script(frame)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None

    updated = pipeline.update_frame(
        script.id,
        frame.id,
        camera_movement="static",
    )

    updated_frame = updated.frames[0]
    assert updated_frame.camera_movement == "static"
    assert updated_frame.camera_movement_structured.primary == "static"
    assert updated_frame.camera_movement_structured.speed == "normal"


def test_update_frame_request_accepts_only_supported_camera_movement_types():
    request = comic_api.UpdateFrameRequest(
        frame_id="frame-1",
        camera_movement="follow",
    )

    assert request.camera_movement.value == "follow"
    with pytest.raises(ValueError):
        comic_api.UpdateFrameRequest(
            frame_id="frame-1",
            camera_movement="teleport",
        )


@pytest.mark.parametrize(
    "llm,reason",
    [
        (_RefineLLM(config_error=RuntimeError("missing key")), "missing_config"),
        (_RefineLLM(chat_error=TimeoutError("slow")), "provider_timeout"),
        (_RefineLLM(chat_error=RuntimeError("upstream")), "provider_error"),
        (_RefineLLM(content="not json"), "malformed_response"),
        (_RefineLLM(content="[]"), "malformed_response"),
        (_RefineLLM(content="{}"), "malformed_response"),
    ],
)
def test_rich_frame_refine_never_returns_silent_none(llm, reason):
    processor = _processor_with(llm)

    with pytest.raises(FrameRefineError) as exc_info:
        processor.refine_frame_to_rich({}, [], [])

    assert exc_info.value.reason == reason


def test_rich_frame_refine_accepts_a_meaningful_provider_object():
    processor = _processor_with(
        _RefineLLM(content=json.dumps({"visual_description": "一扇门在冷光中缓缓打开。"}))
    )

    assert processor.refine_frame_to_rich({}, [], []) == {
        "visual_description": "一扇门在冷光中缓缓打开。"
    }


def test_entity_extraction_rejects_empty_json_success():
    processor = _processor_with(_RefineLLM(content="{}"))

    with pytest.raises(RuntimeError, match="剧本解析失败"):
        processor.parse_novel("Title", "A story")


def test_default_entity_prompt_is_valid_json_without_inline_comments():
    from src.apps.comic_gen.llm import DEFAULT_ENTITY_EXTRACTION_PROMPT

    assert '"visual_weight": 5  //' not in DEFAULT_ENTITY_EXTRACTION_PROMPT


def test_storyboard_extraction_retries_then_rejects_blank_frames():
    llm = _RefineLLM(content=json.dumps({"frames": [{}]}))
    processor = _processor_with(llm)

    with pytest.raises(RuntimeError, match="自动重试后仍然失败"):
        processor.analyze_to_storyboard(
            "A story",
            {"characters": [], "scenes": [], "props": []},
        )


def test_pipeline_refine_rejects_legacy_none_instead_of_returning_original_frame():
    frame = _frame()
    script = _script(frame)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline.script_processor = SimpleNamespace(refine_frame_to_rich=lambda *_args: None)
    pipeline.resolve_episode_assets = lambda _script: {
        "characters": [],
        "scenes": [],
    }
    pipeline._save_data = lambda: pytest.fail("failed refinement must not be saved")

    with pytest.raises(RuntimeError, match="no usable result"):
        pipeline.refine_frame(script.id, frame.id)

    assert frame.visual_description is None


def test_batch_refine_counts_typed_failures_instead_of_success(monkeypatch):
    frame = _frame()
    script = _script(frame)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}

    def fail_refine(*_args):
        raise FrameRefineError("provider_error", "provider failed")

    monkeypatch.setattr(pipeline, "refine_frame", fail_refine)

    events = list(pipeline.refine_batch_generator(script.id))

    assert events[1] == (
        "frame_refine_error",
        {
            "frame_id": frame.id,
            "frame_index": 0,
            "error": "provider failed",
            "reason": "provider_error",
        },
    )
    assert events[-1] == (
        "batch_complete",
        {"total": 1, "success": 0, "failed": 1},
    )


def test_single_frame_refine_api_exposes_typed_provider_failure(monkeypatch):
    def fail_refine(*_args):
        raise FrameRefineError("provider_timeout", "provider timed out")

    monkeypatch.setattr(comic_api.pipeline, "refine_frame", fail_refine)

    response = TestClient(comic_api.app).post("/projects/project-1/frames/frame-1/refine")

    assert response.status_code == 504
    assert response.json()["detail"] == {
        "error": "frame_refine_failed",
        "reason": "provider_timeout",
        "message": "provider timed out",
    }


def test_storyboard_prompt_refine_rejects_missing_frame_before_calling_provider():
    script = _script(_frame())
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}

    class NeverCalledProcessor:
        def polish_storyboard_prompt(self, *_args):
            pytest.fail("missing frames must be rejected before spending an LLM request")

    pipeline.script_processor = NeverCalledProcessor()

    with pytest.raises(ValueError, match="Frame missing not found"):
        pipeline.refine_frame_prompt(script.id, "missing", "draft", [])


def test_video_polish_rejects_unreadable_image_for_vision_capable_model(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "src.apps.comic_gen.llm._chat_model_accepts_images",
        lambda _model: True,
    )
    processor = _processor_with(
        SimpleNamespace(
            is_configured=True,
            require_configured=lambda *_args: "future-vision-chat-model",
            chat=lambda **_kwargs: pytest.fail("provider must not run without its image"),
        )
    )

    with pytest.raises(PolishError) as exc_info:
        processor.polish_video_prompt(
            "camera pushes in",
            image_urls=[str(tmp_path / "missing.png")],
        )

    assert exc_info.value.reason == "image_unavailable"


def test_storyboard_render_rejects_generator_failed_status():
    frame = _frame()
    script = _script(frame)

    class FailedGenerator:
        def generate_frame(self, target, *_args, **_kwargs):
            target.status = GenerationStatus.FAILED
            return target

    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline.storyboard_generator = FailedGenerator()
    pipeline.resolve_episode_assets = lambda _script: {"characters": [], "scenes": []}
    save_calls = []
    pipeline._save_data = lambda: save_calls.append(True)

    with pytest.raises(RuntimeError, match="without an output image"):
        pipeline.generate_storyboard_render(
            script.id,
            frame.id,
            composition_data=None,
            prompt="cinematic frame",
        )

    assert frame.status == GenerationStatus.FAILED
    assert save_calls == [True, True]


def test_storyboard_batch_keeps_successful_variants_when_a_later_call_fails(
    monkeypatch,
    tmp_path,
):
    frame = _frame()
    generator = StoryboardGenerator.__new__(StoryboardGenerator)
    generator.output_root = str(tmp_path)
    generator.output_dir = str(tmp_path / "storyboard")
    calls = 0

    class PartiallyFailingModel:
        def generate(self, _prompt, output_path, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("provider timeout")
            with open(output_path, "wb") as handle:
                handle.write(b"generated image")

    class DisabledUploader:
        is_configured = False

    generator.model = PartiallyFailingModel()
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", DisabledUploader)

    result = generator.generate_frame(
        frame,
        characters=[],
        scene=None,
        prompt="A brass fox in the rain",
        batch_size=2,
        model_name="gpt-image-2",
    )

    assert calls == 2
    assert result.status == GenerationStatus.COMPLETED
    assert len(result.rendered_image_asset.variants) == 1
    assert result.rendered_image_asset.selected_id == result.rendered_image_asset.variants[0].id
    assert result.rendered_image_url == result.rendered_image_asset.variants[0].url
    assert (tmp_path / result.rendered_image_url).read_bytes() == b"generated image"


def test_storyboard_generation_surfaces_provider_error_when_no_image_succeeds(
    monkeypatch,
    tmp_path,
):
    frame = _frame()
    generator = StoryboardGenerator.__new__(StoryboardGenerator)
    generator.output_root = str(tmp_path)
    generator.output_dir = str(tmp_path / "storyboard")

    class FailingModel:
        @staticmethod
        def generate(_prompt, _output_path, **_kwargs):
            raise RuntimeError("provider unavailable")

    generator.model = FailingModel()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        generator.generate_frame(
            frame,
            characters=[],
            scene=None,
            prompt="A brass fox in the rain",
            batch_size=1,
            model_name="gpt-image-2",
        )

    assert frame.status == GenerationStatus.FAILED


def test_storyboard_render_merge_preserves_edits_and_deletions():
    frame = _frame()
    frame.rendered_image_asset.variants.append(
        ImageVariant(id="old-variant", url="storyboard/old.png")
    )
    frame.rendered_image_asset.selected_id = "old-variant"
    script = _script(frame)

    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline.resolve_episode_assets = lambda _script: {"characters": [], "scenes": []}
    save_calls = []
    pipeline._save_data = lambda: save_calls.append(True)
    plan = pipeline.prepare_storyboard_render(
        script.id, frame.id, composition_data=None, prompt="cinematic frame"
    )

    # Simulate work while the provider runs: edit the frame and delete an old
    # take. The detached result must not overwrite or resurrect either action.
    frame.action_description = "A newer user edit"
    frame.rendered_image_asset.variants.clear()
    frame.rendered_image_asset.selected_id = None
    generated = plan.frame
    generated.rendered_image_asset.variants.append(
        ImageVariant(id="new-variant", url="storyboard/new.png")
    )
    generated.rendered_image_asset.selected_id = "new-variant"
    generated.rendered_image_url = "storyboard/new.png"
    generated.image_url = "storyboard/new.png"
    generated.status = GenerationStatus.COMPLETED

    pipeline.commit_storyboard_render_plan(plan, generated)

    assert frame.action_description == "A newer user edit"
    assert [item.id for item in frame.rendered_image_asset.variants] == ["new-variant"]
    assert frame.rendered_image_asset.selected_id == "new-variant"
    assert frame.image_url == "storyboard/new.png"
    assert frame.t2i_image_urls == ["storyboard/new.png"]
    assert frame.t2i_selected_index == 0
    assert frame.clip_start_image_id == "new-variant"
    assert frame.clip_start_image_url == "storyboard/new.png"
    assert save_calls == [True, True]


def test_storyboard_render_selects_new_image_in_bounded_t2i_history():
    frame = _frame()
    frame.t2i_image_urls = [f"storyboard/old-{index}.png" for index in range(10)]
    frame.t2i_image_urls[4] = "storyboard/new.png"
    frame.t2i_selected_index = 4
    script = _script(frame)

    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline.resolve_episode_assets = lambda _script: {"characters": [], "scenes": []}
    pipeline._save_data = lambda: None
    plan = pipeline.prepare_storyboard_render(
        script.id, frame.id, composition_data=None, prompt="cinematic frame"
    )
    generated = plan.frame
    generated.rendered_image_asset.variants.append(
        ImageVariant(id="new-variant", url="storyboard/new.png")
    )
    generated.rendered_image_asset.selected_id = "new-variant"
    generated.rendered_image_url = "storyboard/new.png"
    generated.image_url = "storyboard/new.png"
    generated.status = GenerationStatus.COMPLETED

    pipeline.commit_storyboard_render_plan(plan, generated)

    assert len(frame.t2i_image_urls) == 10
    assert frame.t2i_image_urls[-1] == "storyboard/new.png"
    assert frame.t2i_image_urls.count("storyboard/new.png") == 1
    assert frame.t2i_selected_index == 9


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            "/projects/project-1/assets/generate",
            {"asset_id": "asset-1", "asset_type": "scene", "batch_size": 0},
        ),
        (
            "/projects/project-1/assets/generate_motion_ref",
            {"asset_id": "asset-1", "asset_type": "scene", "batch_size": -1},
        ),
        (
            "/projects/project-1/video_tasks",
            {"prompt": "move", "generation_mode": "t2v", "batch_size": 0},
        ),
        (
            "/projects/project-1/storyboard/render",
            {"frame_id": "frame-1", "prompt": "render", "batch_size": 0},
        ),
    ],
)
def test_generation_endpoints_reject_zero_output_batches(path, payload):
    response = TestClient(comic_api.app).post(path, json=payload)

    assert response.status_code == 422


def test_asset_generation_endpoint_rejects_unknown_generation_type():
    response = TestClient(comic_api.app).post(
        "/projects/project-1/assets/generate",
        json={
            "asset_id": "character-1",
            "asset_type": "character",
            "generation_type": "unknown",
        },
    )

    assert response.status_code == 422


def _patch_pipeline_init_io(monkeypatch):
    monkeypatch.setattr(ComicGenPipeline, "_load_data", lambda _self: {})
    monkeypatch.setattr(ComicGenPipeline, "_load_series_data", lambda _self: {})
    monkeypatch.setattr(
        ComicGenPipeline,
        "_load_library_data",
        lambda _self: GlobalAssetLibrary(),
    )
    monkeypatch.setattr(ComicGenPipeline, "_repair_series_bindings", lambda _self: None)
    monkeypatch.setattr(ComicGenPipeline, "_recover_orphan_tasks", lambda _self: None)


@pytest.mark.parametrize("preload,expected_starts", [(None, 0), ("0", 0), ("1", 1)])
def test_pipeline_startup_only_preloads_demucs_when_opted_in(
    tmp_path, monkeypatch, preload, expected_starts
):
    monkeypatch.chdir(tmp_path)
    _patch_pipeline_init_io(monkeypatch)
    if preload is None:
        monkeypatch.delenv("ENMOTION_PRELOAD_DEMUCS", raising=False)
    else:
        monkeypatch.setenv("ENMOTION_PRELOAD_DEMUCS", preload)
    starts = []
    monkeypatch.setattr(
        ComicGenPipeline,
        "_start_demucs_warmup",
        lambda _self: starts.append(True),
    )

    ComicGenPipeline()

    assert len(starts) == expected_starts


def test_demucs_first_use_starts_one_lazy_warmup(monkeypatch):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._demucs_ready = threading.Event()
    pipeline._demucs_error = None
    pipeline._demucs_warmup_lock = threading.Lock()
    pipeline._demucs_warmup_started = False
    created_threads = []

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            created_threads.append((name, daemon))
            self.target = target

        def start(self):
            self.target()

    def fake_warmup():
        pipeline._demucs_ready.set()

    pipeline._warmup_demucs_model = fake_warmup
    monkeypatch.setattr(pipeline_module.threading, "Thread", InlineThread)

    assert pipeline._ensure_demucs_model_ready(timeout=0.1) is True
    assert pipeline._ensure_demucs_model_ready(timeout=0.1) is True
    assert created_threads == [("enmotion-demucs-warmup", True)]


class _Response:
    def __init__(self, payload=None, *, body=b"", headers=None):
        self._payload = payload
        self.content = body
        self.headers = headers or {}
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=65536):
        del chunk_size
        if self.content:
            yield self.content


def test_newapi_image_rejects_empty_success_download(tmp_path, monkeypatch):
    responses = iter(
        [
            _Response({"data": [{"url": "https://cdn.example/image.png"}]}),
            _Response(body=b""),
        ]
    )
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
    monkeypatch.setattr(
        "src.models.newapi.requests.request",
        lambda *_args, **_kwargs: next(responses),
    )
    output = tmp_path / "empty.png"

    with pytest.raises(RuntimeError, match="empty image download"):
        NewAPIImageModel({}).generate(
            "draw a fox",
            str(output),
            model_id="gpt-image-2",
        )

    assert not output.exists()


def test_newapi_video_rejects_empty_success_download(tmp_path, monkeypatch):
    responses = iter(
        [
            _Response(
                {
                    "task_id": "task-1",
                    "status": "succeeded",
                    "url": "https://cdn.example/video.mp4",
                }
            ),
            _Response(body=b""),
        ]
    )
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
    monkeypatch.setattr(
        "src.models.newapi.requests.request",
        lambda *_args, **_kwargs: next(responses),
    )
    output = tmp_path / "empty.mp4"

    with pytest.raises(RuntimeError, match="empty video download"):
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )

    assert not output.exists()


def test_playground_adapter_return_without_file_marks_generation_failed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    generation = PlaygroundGeneration(
        id="generation-1",
        mode=PlaygroundMode.T2I,
        model_id="gpt-image-2",
        prompt="draw a fox",
        batch_size=1,
        created_at="2026-01-01T00:00:00+00:00",
    )

    class Storage:
        def get_generation(self, generation_id):
            return generation if generation_id == generation.id else None

        def update_generation(self, _generation):
            pass

    service = PlaygroundService(Storage())
    monkeypatch.setattr(service, "_generate_image_newapi", lambda *_args: None)

    service.process_generation(generation.id)

    assert generation.status == "failed"
    assert generation.outputs == []
    assert generation.error == "生成失败，请稍后重试。"


def test_atomic_json_write_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "projects.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(pipeline_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="disk unavailable"):
        pipeline_module._atomic_json_dump(str(target), {"new": True})

    assert target.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob(".projects.json.*.tmp")) == []


def test_project_save_propagates_persistence_failure(monkeypatch, tmp_path):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.data_file = str(tmp_path / "projects.json")
    pipeline.scripts = {}

    def fail_write(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline_module, "_atomic_json_dump", fail_write)

    with pytest.raises(OSError, match="disk full"):
        pipeline._save_data()


def test_mutation_does_not_report_success_when_project_save_fails(monkeypatch):
    frame = _frame()
    script = _script(frame)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.scripts = {script.id: script}

    def fail_save():
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "_save_data", fail_save)

    with pytest.raises(OSError, match="disk full"):
        pipeline.update_frame_workbench(
            script.id,
            frame.id,
            workbench_tab_mode="direct_r2v",
        )


def test_cancel_state_change_propagates_persistence_failure(monkeypatch):
    script = _script()
    script.video_tasks = [
        SimpleNamespace(id="task-1", status="pending", error=None),
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline.scripts = {script.id: script}

    def fail_save():
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "_save_data", fail_save)

    with pytest.raises(OSError, match="disk full"):
        pipeline.mark_video_task_failed(script.id, "task-1", "Canceled by user")


@pytest.mark.parametrize(
    "attribute,loader,error_text",
    [
        ("data_file", "_load_data", "Failed to load project data"),
        ("series_data_file", "_load_series_data", "Failed to load series data"),
        ("library_data_file", "_load_library_data", "Failed to load library data"),
    ],
)
def test_malformed_persisted_state_fails_closed(tmp_path, attribute, loader, error_text):
    target = tmp_path / f"{attribute}.json"
    target.write_text("{broken", encoding="utf-8")
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    setattr(pipeline, attribute, str(target))

    with pytest.raises(RuntimeError, match=error_text):
        getattr(pipeline, loader)()

    assert target.read_text(encoding="utf-8") == "{broken"
