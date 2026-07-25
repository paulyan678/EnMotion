import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen import pipeline as pipeline_module
from src.apps.comic_gen.export import ExportManager
from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.system_check import get_ffmpeg_path


@pytest.fixture(scope="module")
def media_tools():
    ffmpeg = get_ffmpeg_path()
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and not ffprobe:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file():
            ffprobe = str(sibling)
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and FFprobe are required for real-media tests")
    return ffmpeg, ffprobe


def _make_video(ffmpeg: str, path: Path, *, color: str = "blue", audio: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=160x90:r=24:d=0.6",
    ]
    if audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=0.6",
                "-shortest",
            ]
        )
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio:
        command.extend(["-c:a", "aac"])
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)


def _probe(ffprobe: str, path: Path):
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def _script(merged_video_url="videos/source.mp4"):
    now = time.time()
    return Script(
        id="project-1",
        title="Export test",
        original_text="A tiny scene",
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="scene-1",
                duration=1,
                speaker="Ada",
                dialogue="Adapt this export test.",
            )
        ],
        merged_video_url=merged_video_url,
        created_at=now,
        updated_at=now,
    )


def _manager(tmp_path: Path):
    output_root = tmp_path / "output"
    return ExportManager(
        {
            "output_root": str(output_root),
            "output_dir": str(output_root / "export"),
        }
    )


def test_export_mp4_applies_requested_resolution(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    manager = _manager(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/source.mp4")

    media_ref = manager.render_project(
        _script(), {"resolution": "360p", "format": "mp4", "subtitles": "none"}
    )

    exported = tmp_path / "output" / media_ref
    probe = _probe(ffprobe, exported)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    assert media_ref.startswith("export/")
    assert exported.suffix == ".mp4"
    assert exported.stat().st_size > 1_000
    assert (video["width"], video["height"]) == (640, 360)
    assert video["codec_name"] == "h264"


def test_export_webm_uses_real_vp9_container(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    manager = _manager(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/source.mp4")

    media_ref = manager.render_project(
        _script(), {"resolution": "source", "format": "webm", "subtitles": "none"}
    )

    exported = tmp_path / "output" / media_ref
    probe = _probe(ffprobe, exported)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    assert exported.suffix == ".webm"
    assert video["codec_name"] == "vp9"
    assert "webm" in probe["format"]["format_name"]


def test_sidecar_subtitles_are_kept_next_to_successful_export(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    manager = _manager(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/source.mp4")

    media_ref = manager.render_project(
        _script(), {"resolution": "source", "format": "mp4", "subtitles": "sidecar"}
    )

    exported = tmp_path / "output" / media_ref
    subtitle = exported.with_suffix(".srt")
    assert float(_probe(ffprobe, exported)["format"]["duration"]) > 0
    assert subtitle.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,000\n" "Ada: Adapt this export test.\n\n"
    )


def test_embedded_subtitles_create_mov_text_stream(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    manager = _manager(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/source.mp4")

    media_ref = manager.render_project(
        _script(), {"resolution": "source", "format": "mp4", "subtitles": "embedded"}
    )

    exported = tmp_path / "output" / media_ref
    streams = _probe(ffprobe, exported)["streams"]
    subtitle_stream = next(stream for stream in streams if stream["codec_type"] == "subtitle")
    assert subtitle_stream["codec_name"] == "mov_text"
    assert not exported.with_suffix(".srt").exists()


@pytest.mark.parametrize(
    "options,message",
    [
        ({"resolution": "8k"}, "Unsupported export resolution"),
        ({"format": "avi"}, "Unsupported export format"),
        ({"subtitles": "maybe"}, "Unsupported subtitle mode"),
        (
            {"format": "webm", "subtitles": "embedded"},
            "supported only for MP4",
        ),
    ],
)
def test_export_rejects_unsupported_options_before_rendering(tmp_path, options, message):
    manager = _manager(tmp_path)

    with pytest.raises(ValueError, match=message):
        manager.render_project(_script(), options)

    assert not list((tmp_path / "output/export").glob("*"))


def test_failed_export_removes_video_and_sidecar(tmp_path, media_tools, monkeypatch):
    ffmpeg, _ = media_tools
    manager = _manager(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/source.mp4")

    monkeypatch.setattr(
        "src.apps.comic_gen.export.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, args[0], stderr="forced failure")
        ),
    )

    with pytest.raises(RuntimeError, match="FFmpeg export failed"):
        manager.render_project(
            _script(),
            {"resolution": "source", "format": "mp4", "subtitles": "sidecar"},
        )

    assert not list((tmp_path / "output/export").glob("*"))


def test_burn_in_reports_missing_ffmpeg_filter_without_leaking_srt(
    tmp_path, media_tools, monkeypatch
):
    ffmpeg, _ = media_tools
    manager = _manager(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/source.mp4")
    monkeypatch.setattr(manager, "_ffmpeg_supports_filter", lambda *_: False)

    with pytest.raises(RuntimeError, match="does not include the subtitles filter"):
        manager.render_project(
            _script(),
            {"resolution": "source", "format": "mp4", "subtitles": "burn-in"},
        )

    assert not list((tmp_path / "output/export").glob("*.srt"))


def test_pipeline_export_merges_first_when_project_has_no_merge(monkeypatch):
    script = _script(merged_video_url=None)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    calls = []

    def fake_merge(script_id):
        calls.append(("merge", script_id))
        script.merged_video_url = "videos/merged.mp4"
        return script

    def fake_render(rendered_script, options):
        calls.append(("render", rendered_script.merged_video_url, options))
        return "export/final.webm"

    monkeypatch.setattr(pipeline, "merge_videos", fake_merge)
    pipeline.export_manager = SimpleNamespace(render_project=fake_render)

    result = pipeline.export_project(
        script.id,
        {"resolution": "720p", "format": "webm", "subtitles": "none"},
    )

    assert result == "export/final.webm"
    assert calls == [
        ("merge", "project-1"),
        (
            "render",
            "videos/merged.mp4",
            {"resolution": "720p", "format": "webm", "subtitles": "none"},
        ),
    ]


def test_merge_videos_concatenates_real_media(tmp_path, media_tools, monkeypatch):
    ffmpeg, ffprobe = media_tools
    monkeypatch.chdir(tmp_path)
    _make_video(ffmpeg, tmp_path / "output/video/one.mp4", color="red")
    _make_video(ffmpeg, tmp_path / "output/video/two.mp4", color="green")

    script = _script(merged_video_url=None)
    script.frames = [
        StoryboardFrame(id="frame-1", scene_id="scene-1", selected_video_id="take-1"),
        StoryboardFrame(id="frame-2", scene_id="scene-1", selected_video_id="take-2"),
    ]
    script.video_tasks = [
        VideoTask(
            id="take-1",
            project_id=script.id,
            frame_id="frame-1",
            image_url="",
            prompt="",
            status="completed",
            video_url="video/one.mp4",
        ),
        VideoTask(
            id="take-2",
            project_id=script.id,
            frame_id="frame-2",
            image_url="",
            prompt="",
            status="completed",
            video_url="video/two.mp4",
        ),
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None

    merged = pipeline.merge_videos(script.id)

    merged_path = tmp_path / "output/video" / Path(merged.merged_video_url).name
    probe = _probe(ffprobe, merged_path)
    assert merged.merged_video_url.startswith("videos/merged_project-1_")
    assert float(probe["format"]["duration"]) >= 1.0
    assert not (tmp_path / "output/merge_list_project-1.txt").exists()


def test_failed_merge_removes_manifest_and_partial_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "output/video/broken.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"not a video")
    script = _script(merged_video_url=None)
    script.frames[0].selected_video_id = "take-1"
    script.video_tasks = [
        VideoTask(
            id="take-1",
            project_id=script.id,
            frame_id="frame-1",
            image_url="",
            prompt="",
            status="completed",
            video_url="video/broken.mp4",
        )
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}

    def fake_run(command, **_kwargs):
        if "-version" in command:
            return SimpleNamespace(returncode=0, stdout="ffmpeg version test", stderr="")
        Path(command[-1]).write_bytes(b"partial output")
        raise subprocess.CalledProcessError(
            1,
            command,
            output=b"",
            stderr=b"Invalid data found when processing input",
        )

    monkeypatch.setattr(pipeline_module, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(
        pipeline_module,
        "subprocess",
        SimpleNamespace(
            run=fake_run,
            CalledProcessError=subprocess.CalledProcessError,
            TimeoutExpired=subprocess.TimeoutExpired,
            SubprocessError=subprocess.SubprocessError,
        ),
    )

    with pytest.raises(RuntimeError, match="corrupted or incomplete"):
        pipeline.merge_videos(script.id)

    assert not (tmp_path / "output/merge_list_project-1.txt").exists()
    assert not list((tmp_path / "output/video").glob("merged_*.mp4"))


@pytest.mark.parametrize("source_has_audio", [False, True])
def test_bgm_mux_handles_silent_and_audible_merged_video(
    tmp_path, media_tools, monkeypatch, source_has_audio
):
    ffmpeg, ffprobe = media_tools
    monkeypatch.chdir(tmp_path)
    video_path = tmp_path / "output/video/silent.mp4"
    bgm_path = tmp_path / "output/presets/bgm/test.wav"
    _make_video(ffmpeg, video_path, audio=source_has_audio)
    bgm_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=48000:duration=1.2",
            str(bgm_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    script = _script(merged_video_url="videos/silent.mp4")
    script.bgm_url = "presets/bgm/test.wav"
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)

    mixed_path = pipeline._maybe_apply_bgm_mux(script, str(video_path), ffmpeg)

    assert mixed_path is not None
    streams = _probe(ffprobe, Path(mixed_path))["streams"]
    assert any(stream["codec_type"] == "audio" for stream in streams)


def test_export_route_forwards_every_option_and_preserves_url_response(monkeypatch):
    captured = {}

    def fake_export(script_id, options):
        captured.update({"script_id": script_id, "options": options})
        return "export/final.webm"

    monkeypatch.setattr(comic_api.pipeline, "export_project", fake_export)

    response = TestClient(comic_api.app).post(
        "/projects/project-1/export",
        json={"resolution": "720p", "format": "webm", "subtitles": "sidecar"},
    )

    assert response.status_code == 200
    assert response.json() == {"url": "export/final.webm"}
    assert captured == {
        "script_id": "project-1",
        "options": {"resolution": "720p", "format": "webm", "subtitles": "sidecar"},
    }


def test_export_route_maps_option_errors_to_400(monkeypatch):
    def fake_export(*_args, **_kwargs):
        raise ValueError("Unsupported export format 'avi'")

    monkeypatch.setattr(comic_api.pipeline, "export_project", fake_export)

    response = TestClient(comic_api.app).post(
        "/projects/project-1/export",
        json={"format": "avi"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported export format 'avi'"


def test_export_route_preserves_missing_project_404(monkeypatch):
    def fake_export(*_args, **_kwargs):
        raise ValueError("Script not found")

    monkeypatch.setattr(comic_api.pipeline, "export_project", fake_export)

    response = TestClient(comic_api.app).post(
        "/projects/missing/export",
        json={},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Script not found"
