from pathlib import Path
from types import SimpleNamespace

from src.apps.playground.models import PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage


def _video_generation(generation_id: str = "video-generation") -> PlaygroundGeneration:
    return PlaygroundGeneration(
        id=generation_id,
        mode=PlaygroundMode.I2V,
        model_id="doubao-seedance-2-0-fast-260128",
        prompt="A camera circles the subject",
        input_media=["playground/uploads/source.png"],
        parameters={"duration": 5, "resolution": "720p", "aspect_ratio": "16:9"},
        batch_size=1,
        created_at="2026-07-21T08:00:00+00:00",
    )


def test_video_generation_persists_thumbnail_and_delete_reclaims_both(
    tmp_path, monkeypatch
):
    output_root = tmp_path / "output"
    storage = PlaygroundStorage(output_root=str(output_root))
    generation = _video_generation()
    storage.add_generation(generation)
    service = PlaygroundService(storage)

    def write_video(_generation, output_path):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")

    def write_thumbnail(video_path):
        thumbnail = output_root / "playground" / "thumbnails" / "poster.jpg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(b"thumbnail")
        assert Path(video_path).read_bytes() == b"video"
        return str(thumbnail)

    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    monkeypatch.setattr(service, "_generate_video_newapi", write_video)
    monkeypatch.setattr(service, "_create_video_thumbnail", write_thumbnail)
    monkeypatch.setattr(service, "_enforce_server_file_quota", lambda _path: None)

    service.process_generation(generation.id)

    persisted = storage.get_generation(generation.id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.outputs[0].media_path.startswith("playground/videos/")
    assert persisted.outputs[0].thumbnail_path == "playground/thumbnails/poster.jpg"

    video_path = output_root / persisted.outputs[0].media_path
    thumbnail_path = output_root / persisted.outputs[0].thumbnail_path
    assert video_path.is_file()
    assert thumbnail_path.is_file()

    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    assert storage.delete_generation(generation.id) is True
    assert not video_path.exists()
    assert not thumbnail_path.exists()


def test_create_video_thumbnail_extracts_compact_poster(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    service = PlaygroundService(PlaygroundStorage(output_root=str(output_root)))
    video_path = output_root / "playground" / "videos" / "result.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")
    quota_checked = []

    def run_ffmpeg(command, **kwargs):
        assert command[0] == "/test/ffmpeg"
        assert command[command.index("-ss") + 1] == "0.5"
        assert command[command.index("-vf") + 1] == "scale=-2:min(540\\,ih)"
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 30,
            "check": False,
        }
        Path(command[-1]).write_bytes(b"jpeg")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("src.apps.playground.service.get_ffmpeg_path", lambda: "/test/ffmpeg")
    monkeypatch.setattr("src.apps.playground.service.subprocess.run", run_ffmpeg)
    monkeypatch.setattr(service, "_enforce_server_file_quota", quota_checked.append)

    thumbnail = service._create_video_thumbnail(str(video_path))

    assert thumbnail is not None
    assert Path(thumbnail).name == "result_thumbnail.jpg"
    assert Path(thumbnail).read_bytes() == b"jpeg"
    assert quota_checked == [thumbnail]
    assert list(Path(thumbnail).parent.glob("*.tmp.jpg")) == []


def test_thumbnail_failure_does_not_discard_playable_video(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    service = PlaygroundService(PlaygroundStorage(output_root=str(output_root)))
    video_path = output_root / "playground" / "videos" / "result.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")

    monkeypatch.setattr("src.apps.playground.service.get_ffmpeg_path", lambda: "/test/ffmpeg")
    monkeypatch.setattr(
        "src.apps.playground.service.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="bad frame"),
    )

    assert service._create_video_thumbnail(str(video_path)) is None
    assert video_path.read_bytes() == b"video"
    assert not (output_root / "playground" / "thumbnails" / "result_thumbnail.jpg").exists()
