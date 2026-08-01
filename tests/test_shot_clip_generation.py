"""Regression coverage for the shot-scoped Motion clip workflow."""

import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.apps.comic_gen.models import (
    CameraMovementData,
    ImageAsset,
    ImageVariant,
    Script,
    StoryboardFrame,
)
from src.apps.comic_gen.pipeline import (
    ComicGenPipeline,
    _motion_prompt_with_frame_type,
    _normalize_frame_type,
    clip_image_id,
)


VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"


@pytest.fixture
def pipeline(tmp_path):
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        value = ComicGenPipeline()
    value.data_file = str(tmp_path / "projects.json")
    value.series_data_file = str(tmp_path / "series.json")
    value.output_root = str(tmp_path / "output")
    value.scripts = {}
    value.series_store = {}
    return value


def make_frame() -> StoryboardFrame:
    return StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        action_description="The fictional hero crosses the room.",
        camera_movement="跟拍",
        video_prompt="Follow the fictional hero at a steady pace.",
        rendered_image_asset=ImageAsset(
            selected_id="render-a",
            variants=[
                ImageVariant(id="render-a", url="storyboard/a.png"),
                ImageVariant(id="render-b", url="storyboard/b.png"),
            ],
        ),
        t2i_image_urls=["storyboard/c.png", "uploads/custom.webp"],
        t2i_selected_index=0,
        clip_start_image_id="render-a",
        clip_start_image_url="storyboard/a.png",
    )


def install_script(pipeline: ComicGenPipeline, frame: StoryboardFrame) -> Script:
    script = Script(
        id="project-1",
        title="Motion clips",
        original_text="A fictional sequence",
        frames=[frame],
        created_at=time.time(),
        updated_at=time.time(),
    )
    pipeline.scripts[script.id] = script
    return script


def test_clip_image_identity_is_stable_across_served_urls():
    assert clip_image_id("uploads/custom.webp") == clip_image_id(
        "https://studio.example/files/uploads/custom.webp?signature=temporary"
    )


@pytest.mark.parametrize(
    ("frame_id", "source_id", "image_url", "message"),
    [
        (None, "render-a", "storyboard/a.png", "Select a storyboard shot"),
        ("frame-1", None, "storyboard/a.png", "Select a clip start image"),
        ("frame-1", "missing", "storyboard/a.png", "does not belong"),
        ("frame-1", "render-a", "storyboard/b.png", "do not match"),
    ],
)
def test_server_rejects_unassociated_or_mismatched_generation(
    pipeline, frame_id, source_id, image_url, message
):
    install_script(pipeline, make_frame())
    with pytest.raises(ValueError, match=message):
        pipeline.validate_clip_generation_request(
            "project-1", frame_id, source_id, image_url, "follow"
        )


def test_server_rejects_stale_frame_type_and_stale_selected_image(pipeline):
    frame = make_frame()
    install_script(pipeline, frame)

    with pytest.raises(ValueError, match="frame type is stale"):
        pipeline.validate_clip_generation_request(
            "project-1", frame.id, "render-a", "storyboard/a.png", "static"
        )
    with pytest.raises(ValueError, match="selected clip start image"):
        pipeline.validate_clip_generation_request(
            "project-1", frame.id, "render-b", "storyboard/b.png", "follow"
        )


def test_workspace_text_to_video_is_shot_scoped_and_has_no_image_input(pipeline):
    frame = make_frame()
    script = install_script(pipeline, frame)

    validated_frame, image_url, frame_type = pipeline.validate_clip_generation_request(
        script.id,
        frame.id,
        None,
        None,
        "follow",
        "t2v",
    )

    assert validated_frame is frame
    assert image_url is None
    assert frame_type == "follow"

    with pytest.raises(ValueError, match="must not include a source image"):
        pipeline.validate_clip_generation_request(
            script.id,
            frame.id,
            "render-a",
            "storyboard/a.png",
            "follow",
            "t2v",
        )

    with patch("src.apps.comic_gen.pipeline.resolve_model_api_key", return_value="test-key"):
        _, task_id = pipeline.create_video_task(
            script_id=script.id,
            image_url=image_url,
            source_image_id=None,
            source_image_url=None,
            frame_id=frame.id,
            frame_type=frame_type,
            prompt="A slow tracking shot through fictional neon rain.",
            model=VIDEO_MODEL,
            generation_mode="t2v",
            duration=5,
            resolution="720p",
            ratio="16:9",
            workbench_tab="direct_r2v",
        )

    task = next(item for item in script.video_tasks if item.id == task_id)
    assert task.frame_id == frame.id
    assert task.image_url == ""
    assert task.source_image_id is None
    assert task.source_image_url is None
    assert task.generation_mode == "t2v"
    assert task.workbench_tab == "direct_r2v"


def test_workbench_persists_exact_variant_prompt_and_uploaded_selection(pipeline):
    frame = make_frame()
    install_script(pipeline, frame)

    updated = pipeline.update_frame_workbench(
        "project-1",
        frame.id,
        clip_start_image_id="render-b",
        clip_start_image_url="storyboard/b.png",
        video_prompt="Orbit slowly while the fictional hero pauses.",
    )
    assert updated is not None
    assert updated.clip_start_image_id == "render-b"
    assert updated.clip_start_image_url == "storyboard/b.png"
    assert updated.video_prompt == "Orbit slowly while the fictional hero pauses."

    uploaded = pipeline.upload_t2i_frame("project-1", frame.id, "uploads/new.png")
    assert uploaded is not None
    assert uploaded.t2i_image_urls[-1] == "uploads/new.png"
    assert uploaded.t2i_selected_index == len(uploaded.t2i_image_urls) - 1
    assert uploaded.clip_start_image_id == clip_image_id("uploads/new.png")
    assert uploaded.clip_start_image_url == "uploads/new.png"


def test_selected_storyboard_variant_survives_project_reload(pipeline):
    frame = make_frame()
    install_script(pipeline, frame)

    pipeline.select_asset_variant(
        "project-1", frame.id, "storyboard_frame", "render-b"
    )

    reloaded = ComicGenPipeline.__new__(ComicGenPipeline)
    reloaded.data_file = pipeline.data_file
    saved_frame = reloaded._load_data()["project-1"].frames[0]

    assert saved_frame.rendered_image_asset.selected_id == "render-b"
    assert saved_frame.rendered_image_url == "storyboard/b.png"
    assert saved_frame.image_url == "storyboard/b.png"
    assert saved_frame.clip_start_image_id == "render-b"
    assert saved_frame.clip_start_image_url == "storyboard/b.png"


def test_deleting_selected_generated_variant_selects_nearest_remaining_variant(pipeline):
    frame = make_frame()
    frame.t2i_selected_index = 1
    frame.clip_start_image_id = clip_image_id("uploads/custom.webp")
    frame.clip_start_image_url = "uploads/custom.webp"
    install_script(pipeline, frame)

    updated, removed = pipeline.delete_frame_t2i_image("project-1", frame.id, 1)

    assert removed == "uploads/custom.webp"
    assert updated.t2i_selected_index == 0
    assert updated.clip_start_image_id == clip_image_id("storyboard/c.png")
    assert updated.clip_start_image_url == "storyboard/c.png"


def test_frame_type_is_canonical_and_augments_instead_of_replacing_prompt():
    assert _normalize_frame_type("跟拍") == "follow"
    assert _normalize_frame_type("Push In") == "push_in"
    prompt = _motion_prompt_with_frame_type("Keep the hero's gesture subtle.", "follow")
    assert prompt.startswith("Keep the hero's gesture subtle.")
    assert "tracking shot" in prompt


def test_structured_frame_type_remains_authoritative_when_legacy_text_is_descriptive(pipeline):
    frame = make_frame()
    frame.camera_movement = "A gentle cinematic move"
    frame.camera_movement_structured = CameraMovementData(
        primary="push_in",
        speed="slow",
        description="Move closer to the fictional hero.",
    )
    install_script(pipeline, frame)

    _, _, frame_type = pipeline.validate_clip_generation_request(
        "project-1", frame.id, "render-a", "storyboard/a.png", "push_in"
    )

    assert frame_type == "push_in"


def test_task_recipe_and_provider_request_preserve_shot_image_type_and_parameters(
    pipeline, tmp_path
):
    frame = make_frame()
    script = install_script(pipeline, frame)
    _, image_url, frame_type = pipeline.validate_clip_generation_request(
        script.id, frame.id, "render-a", "storyboard/a.png", "follow"
    )

    with patch("src.apps.comic_gen.pipeline.resolve_model_api_key", return_value="test-key"):
        _, task_id = pipeline.create_video_task(
            script_id=script.id,
            image_url=image_url,
            source_image_id="render-a",
            source_image_url=image_url,
            frame_id=frame.id,
            frame_type=frame_type,
            prompt="Keep the custom gesture.",
            model=VIDEO_MODEL,
            duration=9,
            seed=17,
            resolution="720p",
            generate_audio=True,
            ratio="9:16",
            watermark=True,
            workbench_tab="t2i_i2v",
        )

    task = next(item for item in script.video_tasks if item.id == task_id)
    assert task.frame_id == frame.id
    assert task.source_image_id == "render-a"
    assert task.source_image_url == "storyboard/a.png"
    assert task.frame_type == "follow"
    assert task.duration == 9
    assert task.seed == 17
    assert task.ratio == "9:16"
    assert task.generate_audio is True
    assert task.watermark is True

    generated_path = Path(tmp_path) / "generated.mp4"
    generated_path.write_bytes(b"video")
    provider = Mock()
    provider.generate.return_value = (str(generated_path), {})
    pipeline._newapi_video_model = provider
    pipeline._download_temp_image = Mock(return_value=str(tmp_path / "input.png"))
    pipeline.process_video_task(script.id, task.id)

    request = provider.generate.call_args.kwargs
    assert request["img_url"] == task.image_url
    assert request["model_id"] == VIDEO_MODEL
    assert request["duration"] == 9
    assert request["aspect_ratio"] == "9:16"
    assert request["generate_audio"] is True
    assert request["watermark"] is True
    assert request["prompt"].startswith("Keep the custom gesture.")
    assert "tracking shot" in request["prompt"]
    assert task.status == "completed"
    assert frame.selected_video_id == task.id
    assert frame.video_url == task.video_url

    preserved = {
        "frame_id": task.frame_id,
        "source_image_id": task.source_image_id,
        "source_image_url": task.source_image_url,
        "frame_type": task.frame_type,
        "prompt": task.prompt,
        "model": task.model,
        "duration": task.duration,
        "seed": task.seed,
        "resolution": task.resolution,
        "ratio": task.ratio,
        "generate_audio": task.generate_audio,
        "watermark": task.watermark,
    }
    task.status = "failed"
    task.error = "provider rejected request"
    assert pipeline.prepare_video_task_retry(script.id, task.id)
    assert task.status == "pending"
    assert {name: getattr(task, name) for name in preserved} == preserved


@pytest.mark.parametrize(
    ("duration", "resolution", "ratio", "message"),
    [
        (3, "720p", "16:9", "between 4 and 15"),
        (5, "1080p", "16:9", "only 720p"),
        (5, "720p", "4:3", "must be 16:9, 9:16, or 1:1"),
    ],
)
def test_invalid_model_parameter_combinations_are_rejected_before_persistence(
    pipeline, duration, resolution, ratio, message
):
    frame = make_frame()
    script = install_script(pipeline, frame)
    with patch("src.apps.comic_gen.pipeline.resolve_model_api_key", return_value="test-key"):
        with pytest.raises(ValueError, match=message):
            pipeline.create_video_task(
                script_id=script.id,
                image_url="storyboard/a.png",
                prompt="Move gently.",
                duration=duration,
                resolution=resolution,
                ratio=ratio,
                model=VIDEO_MODEL,
                frame_id=frame.id,
                source_image_id="render-a",
                frame_type="follow",
            )
    assert script.video_tasks == []
