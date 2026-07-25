from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.apps.comic_gen.api import (
    AddCharacterRequest,
    CreateProjectRequest,
    UpdateFrameRequest,
)
from src.utils.oss_utils import authoritative_media_reference


def test_server_oss_mirror_keeps_local_media_authoritative(monkeypatch, tmp_path):
    root = tmp_path / "output"
    media = root / "storyboard" / "frame.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"frame")
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")

    assert authoritative_media_reference(
        str(media), str(root), "enmotion/workspaces/w/storyboard/frame.png"
    ) == "storyboard/frame.png"


def test_desktop_oss_mirror_preserves_existing_object_key_behavior(monkeypatch, tmp_path):
    root = tmp_path / "output"
    media = root / "storyboard" / "frame.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"frame")
    monkeypatch.delenv("ENMOTION_SERVER_MODE", raising=False)
    monkeypatch.setenv("ENMOTION_DEPLOYMENT_MODE", "desktop")

    object_key = "enmotion/storyboard/frame.png"
    assert authoritative_media_reference(str(media), str(root), object_key) == object_key


def test_authoritative_reference_rejects_path_outside_output(monkeypatch, tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")

    with pytest.raises(ValueError, match="escapes"):
        authoritative_media_reference(str(outside), str(root), "remote/key.png")


def test_core_project_metadata_inputs_are_bounded():
    with pytest.raises(ValidationError):
        CreateProjectRequest(title="x" * 201, text="ok")
    with pytest.raises(ValidationError):
        AddCharacterRequest(name="x" * 201, description="ok")
    with pytest.raises(ValidationError):
        UpdateFrameRequest(frame_id="frame", character_ids=["id"] * 101)


def test_server_video_output_name_is_collision_resistant(monkeypatch, tmp_path):
    from src.apps.comic_gen.models import StoryboardFrame
    from src.apps.comic_gen.video import VideoGenerator
    from src.apps.web_runtime.context import bind_tenant, reset_tenant

    root = tmp_path / "output"
    source = root / "storyboard" / "input.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    generator = VideoGenerator({"output_root": str(root)})

    def generate(**kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return str(output), None

    monkeypatch.setattr(generator.model, "generate", generate)
    frame = StoryboardFrame(
        id="frame-one",
        scene_id="scene-one",
        image_url="storyboard/input.png",
        action_description="move",
    )
    token = bind_tenant("user", "workspace", "worker")
    try:
        generated = generator.generate_clip(frame)
    finally:
        reset_tenant(token)

    assert generated.video_url.startswith("video/frame-one_")
    assert generated.video_url.endswith(".mp4")
