import time

import pytest

from src.apps.comic_gen.models import GenerationStatus, Script, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.video import VideoGenerator

VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"


def _frame(frame_id: str, *, image_url: str = "https://example.com/frame.png"):
    return StoryboardFrame(id=frame_id, scene_id="scene-1", image_url=image_url)


def _script(*frames: StoryboardFrame) -> Script:
    now = time.time()
    return Script(
        id="project-1",
        title="Project",
        original_text="text",
        frames=list(frames),
        model_settings={"video_model": VIDEO_MODEL},
        created_at=now,
        updated_at=now,
    )


def _generator_without_provider() -> VideoGenerator:
    return VideoGenerator.__new__(VideoGenerator)


def test_generate_video_processes_every_unfinished_frame_with_project_model(monkeypatch):
    first = _frame("frame-1")
    storyboard_complete = _frame("frame-2")
    storyboard_complete.status = GenerationStatus.COMPLETED
    already_generated = _frame("frame-3")
    already_generated.status = GenerationStatus.COMPLETED
    already_generated.video_url = "video/frame-3.mp4"
    script = _script(first, storyboard_complete, already_generated)
    generator = _generator_without_provider()
    calls = []

    def fake_generate_clip(frame, model_id=None):
        calls.append((frame.id, model_id))
        return frame.model_copy(
            update={
                "status": GenerationStatus.COMPLETED,
                "video_url": f"video/{frame.id}.mp4",
            }
        )

    monkeypatch.setattr(generator, "generate_clip", fake_generate_clip)

    result = generator.generate_video(script)

    assert result is script
    assert calls == [("frame-1", VIDEO_MODEL), ("frame-2", VIDEO_MODEL)]
    assert [frame.video_url for frame in script.frames] == [
        "video/frame-1.mp4",
        "video/frame-2.mp4",
        "video/frame-3.mp4",
    ]


def test_generate_video_marks_missing_image_failed_and_continues(monkeypatch, tmp_path):
    missing_image = _frame("frame-missing", image_url=None)
    valid = _frame("frame-valid")
    script = _script(missing_image, valid)
    generator = _generator_without_provider()
    generator.output_dir = str(tmp_path / "video")

    class SuccessfulModel:
        def generate(self, **kwargs):
            return kwargs["output_path"], None

    class DisabledUploader:
        is_configured = False

    generator.model = SuccessfulModel()
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", DisabledUploader)

    result = generator.generate_video(script)

    assert result.frames[0].status == GenerationStatus.FAILED
    assert result.frames[0].video_url is None
    assert result.frames[1].status == GenerationStatus.COMPLETED
    assert result.frames[1].video_url.endswith("frame-valid.mp4")


def test_pipeline_generate_video_rejects_missing_script():
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {}

    with pytest.raises(ValueError, match="Script not found"):
        pipeline.generate_video("missing-project")


def test_pipeline_persists_provider_failure_as_failed_frame(tmp_path):
    frame = _frame("frame-1")
    script = _script(frame)
    generator = _generator_without_provider()
    generator.output_dir = str(tmp_path / "video")

    class FailingModel:
        def generate(self, **kwargs):
            raise RuntimeError("provider unavailable")

    generator.model = FailingModel()

    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline.video_generator = generator
    save_calls = []
    pipeline._save_data = lambda: save_calls.append(True)

    result = pipeline.generate_video(script.id)

    assert result.frames[0].status == GenerationStatus.FAILED
    assert result.frames[0].video_url is None
    assert save_calls == [True]


def test_storyboard_render_provider_failure_never_returns_mock_success():
    frame = _frame("frame-1", image_url=None)
    script = _script(frame)

    class FailingStoryboardGenerator:
        def generate_frame(self, *args, **kwargs):
            raise RuntimeError("image provider unavailable")

    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline.storyboard_generator = FailingStoryboardGenerator()
    pipeline.resolve_episode_assets = lambda _script: {"characters": [], "scenes": []}
    save_calls = []
    pipeline._save_data = lambda: save_calls.append(True)

    with pytest.raises(RuntimeError, match="image provider unavailable"):
        pipeline.generate_storyboard_render(
            script.id,
            frame.id,
            composition_data=None,
            prompt="cinematic frame",
        )

    assert frame.status == GenerationStatus.FAILED
    assert frame.image_url is None
    assert frame.rendered_image_url is None
    assert save_calls == [True, True]
