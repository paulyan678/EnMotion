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
from src.apps.comic_gen.pipeline import (
    AssemblyMutationConflictError,
    ComicGenPipeline,
)
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


@pytest.mark.parametrize(
    ("dubbed_task_id", "expected_filename"),
    [
        ("take-a", "selected.mp4"),
        ("take-b", "dubbed.mp4"),
    ],
)
def test_merge_uses_dub_only_for_the_selected_take(
    tmp_path,
    monkeypatch,
    dubbed_task_id,
    expected_filename,
):
    monkeypatch.chdir(tmp_path)
    video_dir = tmp_path / "output/video"
    video_dir.mkdir(parents=True)
    (video_dir / "selected.mp4").write_bytes(b"selected")
    (video_dir / "dubbed.mp4").write_bytes(b"dubbed")
    script = _script(merged_video_url=None)
    script.frames[0].selected_video_id = "take-b"
    script.frames[0].dubbed_video_task_id = dubbed_task_id
    script.frames[0].dubbed_video_url = "video/dubbed.mp4"
    if dubbed_task_id == "take-a":
        # Applied A -> selected B -> previewed B, but Apply has not happened.
        # Preview provenance must not make merge pair A's applied bytes with B.
        script.frames[0].preview_video_task_id = "take-b"
        script.frames[0].preview_video_url = "video/preview-b.mp4"
        (video_dir / "preview-b.mp4").write_bytes(b"preview")
    script.video_tasks = [
        VideoTask(
            id="take-b",
            project_id=script.id,
            frame_id="frame-1",
            image_url="",
            prompt="",
            status="completed",
            video_url="video/selected.mp4",
        )
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None
    manifests = []

    def fake_run(command, **_kwargs):
        if "-version" in command:
            return SimpleNamespace(returncode=0, stdout="ffmpeg test", stderr="")
        manifest_path = Path(command[command.index("-i") + 1])
        manifests.append(manifest_path.read_text(encoding="utf-8"))
        Path(command[-1]).write_bytes(b"merged")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

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

    pipeline.merge_videos(script.id)

    assert len(manifests) == 1
    assert expected_filename in manifests[0]
    unexpected = "dubbed.mp4" if expected_filename == "selected.mp4" else "selected.mp4"
    assert unexpected not in manifests[0]
    assert "preview-b.mp4" not in manifests[0]


def test_dub_preview_cache_hit_keeps_cached_background_audio(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    video_path = output_root / "video/source.mp4"
    dialogue_path = output_root / "audio/dialogue.mp3"
    background_path = output_root / "audio/background.wav"
    video_path.parent.mkdir(parents=True)
    dialogue_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")
    dialogue_path.write_bytes(b"d" * 1_001)
    background_path.write_bytes(b"background")

    script = _script(merged_video_url=None)
    script.frames[0].audio_url = "audio/dialogue.mp3"
    script.frames[0].selected_video_id = "take-1"
    script.frames[0].dubbed_video_task_id = "take-applied"
    script.frames[0].dubbed_video_url = "video/applied-a.mp4"
    script.frames[0].bg_audio_url = "audio/background.wav"
    script.frames[0].bg_audio_source_video = "video/source.mp4"
    script.video_tasks = [
        VideoTask(
            id="take-1",
            project_id=script.id,
            frame_id="frame-1",
            image_url="",
            prompt="",
            status="completed",
            video_url="video/source.mp4",
        )
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.output_root = str(output_root)
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"generated")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

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

    pipeline.preview_dub(script.id, script.frames[0].id, "take-1")

    assert background_path.exists()
    assert script.frames[0].bg_audio_url == "audio/background.wav"
    assert script.frames[0].preview_video_task_id == "take-1"
    assert script.frames[0].dubbed_video_task_id == "take-applied"
    assert script.frames[0].dubbed_video_url == "video/applied-a.mp4"


def test_dub_preview_revalidates_live_take_and_retires_stale_output(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    video_dir = output_root / "video"
    audio_dir = output_root / "audio"
    video_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"take a")
    (video_dir / "b.mp4").write_bytes(b"take b")
    (audio_dir / "dialogue.mp3").write_bytes(b"d" * 1_001)
    (audio_dir / "background.wav").write_bytes(b"background")

    script = _script(merged_video_url=None)
    frame = script.frames[0]
    frame.audio_url = "audio/dialogue.mp3"
    frame.selected_video_id = "take-a"
    frame.bg_audio_url = "audio/background.wav"
    frame.bg_audio_source_video = "video/a.mp4"
    script.video_tasks = [
        VideoTask(
            id="take-a",
            project_id=script.id,
            frame_id=frame.id,
            image_url="",
            prompt="",
            status="completed",
            video_url="video/a.mp4",
        ),
        VideoTask(
            id="take-b",
            project_id=script.id,
            frame_id=frame.id,
            image_url="",
            prompt="",
            status="completed",
            video_url="video/b.mp4",
        ),
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.output_root = str(output_root)
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None
    switched = False

    def fake_run(command, **_kwargs):
        nonlocal switched
        if not switched:
            switched = True
            pipeline.select_video_for_frame(script.id, frame.id, "take-b")
        Path(command[-1]).write_bytes(b"generated")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

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

    with pytest.raises(AssemblyMutationConflictError, match="changed while the preview"):
        pipeline.preview_dub(script.id, frame.id, "take-a")

    assert frame.selected_video_id == "take-b"
    assert frame.preview_video_url is None
    assert frame.preview_video_task_id is None
    assert not list(video_dir.glob("preview_*.mp4"))
    assert (audio_dir / "background.wav").exists()


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


@pytest.mark.parametrize(
    ("failure_mode", "message"),
    [
        ("nonzero", "BGM FFmpeg failed with exit code 7"),
        ("timeout", "BGM FFmpeg timed out after 300 seconds"),
        ("missing-output", "mixed output was not created"),
    ],
)
def test_merge_with_configured_bgm_never_succeeds_silently(
    tmp_path,
    monkeypatch,
    failure_mode,
    message,
):
    monkeypatch.chdir(tmp_path)
    video_dir = tmp_path / "output/video"
    bgm_dir = tmp_path / "output/presets/bgm"
    video_dir.mkdir(parents=True)
    bgm_dir.mkdir(parents=True)
    (video_dir / "source.mp4").write_bytes(b"source")
    stale_output = video_dir / "previous.mp4"
    stale_output.write_bytes(b"stale")
    (bgm_dir / "selected.wav").write_bytes(b"music")

    script = _script(merged_video_url="videos/previous.mp4")
    script.bgm_url = "presets/bgm/selected.wav"
    script.frames[0].selected_video_id = "take-1"
    script.video_tasks = [
        VideoTask(
            id="take-1",
            project_id=script.id,
            frame_id="frame-1",
            image_url="",
            prompt="",
            status="completed",
            video_url="video/source.mp4",
        )
    ]
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    saved_urls = []
    pipeline._save_data = lambda: saved_urls.append(script.merged_video_url)
    monkeypatch.setattr(pipeline, "_video_has_audio_stream", lambda *_args: False)

    def fake_run(command, **_kwargs):
        if "-version" in command:
            return SimpleNamespace(returncode=0, stdout="ffmpeg test", stderr="")
        if "concat" in command:
            Path(command[-1]).write_bytes(b"silent concat output")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        mixed_output = Path(command[-1])
        if failure_mode == "nonzero":
            mixed_output.write_bytes(b"partial mix")
            raise subprocess.CalledProcessError(
                7,
                command,
                output=b"",
                stderr=b"forced BGM failure",
            )
        if failure_mode == "timeout":
            mixed_output.write_bytes(b"partial mix")
            raise subprocess.TimeoutExpired(command, 300)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pipeline_module, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(pipeline_module.subprocess, "run", fake_run)

    with pytest.raises(
        ValueError,
        match=f"Background music could not be applied: .*{message}",
    ):
        pipeline.merge_videos(script.id)

    assert script.merged_video_url == "videos/previous.mp4"
    assert saved_urls == []
    assert stale_output.read_bytes() == b"stale"
    assert not (tmp_path / "output/merge_list_project-1.txt").exists()
    assert not list(video_dir.glob("merged_project-1_*.mp4"))
    assert not list(video_dir.glob("*_mixed.mp4"))


def test_bgm_mux_returns_none_only_when_no_track_is_configured(
    tmp_path,
    monkeypatch,
):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.output_root = str(tmp_path)
    script = _script()
    script.bgm_url = None
    monkeypatch.setattr(
        pipeline_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg must not run without BGM"),
    )

    assert (
        pipeline._maybe_apply_bgm_mux(
            script,
            str(tmp_path / "video.mp4"),
            "/fake/ffmpeg",
        )
        is None
    )


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


def test_export_route_reports_project_operation_conflict(monkeypatch):
    def fake_export(*_args, **_kwargs):
        raise pipeline_module.AssemblyOperationInProgressError(
            pipeline_module.ASSEMBLY_OPERATION_BUSY_MESSAGE
        )

    monkeypatch.setattr(comic_api.pipeline, "export_project", fake_export)

    response = TestClient(comic_api.app).post(
        "/projects/project-1/export",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == pipeline_module.ASSEMBLY_OPERATION_BUSY_MESSAGE


@pytest.mark.parametrize(
    ("pipeline_method", "path", "payload"),
    [
        (
            "analyze_text_to_frames",
            "/projects/project-1/storyboard/analyze",
            {"text": "story"},
        ),
        (
            "refine_frame",
            "/projects/project-1/frames/frame-1/refine",
            None,
        ),
        (
            "preview_dub",
            "/projects/project-1/frames/frame-1/dub/preview",
            {"video_task_id": "take-1", "offset_ms": 0},
        ),
    ],
)
def test_provider_commit_conflicts_are_reported_as_409(
    monkeypatch,
    pipeline_method,
    path,
    payload,
):
    def conflict(*_args, **_kwargs):
        raise AssemblyMutationConflictError("provider inputs changed")

    monkeypatch.setattr(comic_api.pipeline, pipeline_method, conflict)
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: False)

    response = TestClient(comic_api.app).post(path, json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "provider inputs changed"
