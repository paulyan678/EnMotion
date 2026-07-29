import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.models import Script
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def bgm_app(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.output_root = "output"
    pipeline._save_lock = threading.RLock()
    pipeline._assembly_operation_locks_guard = threading.Lock()
    pipeline._assembly_operation_locks = {}
    pipeline._save_data = Mock()
    script = Script(
        id="project-1",
        title="Mix",
        original_text="",
        created_at=1,
        updated_at=1,
    )
    pipeline.scripts = {script.id: script}
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    return TestClient(comic_api.app), pipeline, script, tmp_path


def _custom_directory(tmp_path: Path) -> Path:
    return tmp_path / "output" / "audio" / "custom_bgm" / "project-1"


def _install_old_custom_bgm(
    tmp_path: Path,
    script: Script,
) -> Path:
    old_path = _custom_directory(tmp_path) / "old.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(b"old music")
    script.bgm_url = "audio/custom_bgm/project-1/old.mp3"
    return old_path


def test_custom_bgm_upload_is_local_project_owned_and_retires_previous(
    bgm_app,
) -> None:
    client, pipeline, script, tmp_path = bgm_app
    old_path = _install_old_custom_bgm(tmp_path, script)
    merged_path = tmp_path / "output" / "video" / "merged.mp4"
    merged_path.parent.mkdir(parents=True)
    merged_path.write_bytes(b"old merge")
    script.merged_video_url = "videos/merged.mp4"

    response = client.post(
        "/projects/project-1/audio_mix/bgm",
        files={"file": ("score.mp3", b"ID3-new-music", "audio/mpeg")},
    )

    assert response.status_code == 200
    selected = response.json()["bgm_url"]
    assert selected.startswith("audio/custom_bgm/project-1/")
    assert not selected.startswith(("http://", "https://"))
    assert (tmp_path / "output" / selected).read_bytes() == b"ID3-new-music"
    assert script.bgm_url == selected
    assert script.merged_video_url is None
    assert not old_path.exists()
    assert not merged_path.exists()
    pipeline._save_data.assert_called_once()


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "status_code"),
    [
        ("empty.mp3", b"", "audio/mpeg", 400),
        ("cover.png", b"image", "image/png", 415),
    ],
)
def test_custom_bgm_rejects_empty_or_non_audio_without_leaking_file(
    bgm_app,
    filename,
    content,
    content_type,
    status_code,
) -> None:
    client, _pipeline, script, tmp_path = bgm_app

    response = client.post(
        "/projects/project-1/audio_mix/bgm",
        files={"file": (filename, content, content_type)},
    )

    assert response.status_code == status_code
    assert script.bgm_url is None
    directory = _custom_directory(tmp_path)
    assert not directory.exists() or list(directory.iterdir()) == []


def test_custom_bgm_size_limit_removes_partial_upload(bgm_app, monkeypatch) -> None:
    client, _pipeline, script, tmp_path = bgm_app
    monkeypatch.setattr(
        comic_api,
        "AUDIO_UPLOAD_POLICY",
        replace(comic_api.AUDIO_UPLOAD_POLICY, max_bytes=4),
    )

    response = client.post(
        "/projects/project-1/audio_mix/bgm",
        files={"file": ("large.mp3", b"12345", "audio/mpeg")},
    )

    assert response.status_code == 413
    assert script.bgm_url is None
    directory = _custom_directory(tmp_path)
    assert not directory.exists() or list(directory.iterdir()) == []


def test_custom_bgm_persistence_failure_keeps_old_selection_and_removes_new_file(
    bgm_app,
) -> None:
    client, pipeline, script, tmp_path = bgm_app
    old_path = _install_old_custom_bgm(tmp_path, script)
    pipeline._save_data.side_effect = OSError("disk unavailable")

    response = client.post(
        "/projects/project-1/audio_mix/bgm",
        files={"file": ("replacement.wav", b"RIFF-new-music", "audio/wav")},
    )

    assert response.status_code == 500
    assert script.bgm_url == "audio/custom_bgm/project-1/old.mp3"
    assert old_path.exists()
    assert list(_custom_directory(tmp_path).iterdir()) == [old_path]


def test_custom_bgm_busy_conflict_returns_409_and_removes_staged_file(
    bgm_app,
) -> None:
    client, pipeline, script, tmp_path = bgm_app
    active_lock = threading.Lock()
    active_lock.acquire()
    pipeline._assembly_operation_locks[script.id] = active_lock
    try:
        response = client.post(
            "/projects/project-1/audio_mix/bgm",
            files={"file": ("score.mp3", b"ID3-music", "audio/mpeg")},
        )
    finally:
        active_lock.release()

    assert response.status_code == 409
    assert script.bgm_url is None
    directory = _custom_directory(tmp_path)
    assert not directory.exists() or list(directory.iterdir()) == []


def test_clearing_custom_bgm_retires_only_project_owned_track(bgm_app) -> None:
    client, _pipeline, script, tmp_path = bgm_app
    old_path = _install_old_custom_bgm(tmp_path, script)

    response = client.put(
        "/projects/project-1/audio_mix",
        json={"bgm_url": None},
    )

    assert response.status_code == 200
    assert script.bgm_url is None
    assert not old_path.exists()


def test_clearing_custom_bgm_does_not_delete_cross_project_traversal(bgm_app) -> None:
    client, _pipeline, script, tmp_path = bgm_app
    other = tmp_path / "output" / "audio" / "custom_bgm" / "project-2" / "other.mp3"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other project")
    script.bgm_url = "audio/custom_bgm/project-1/../project-2/other.mp3"

    response = client.put(
        "/projects/project-1/audio_mix",
        json={"bgm_url": None},
    )

    assert response.status_code == 200
    assert script.bgm_url is None
    assert other.read_bytes() == b"other project"


def test_custom_bgm_path_cannot_cross_project_boundary(bgm_app) -> None:
    _client, pipeline, _script, tmp_path = bgm_app
    other = tmp_path / "output" / "audio" / "custom_bgm" / "project-2" / "other.mp3"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other project")

    with pytest.raises(ValueError, match="belong to this project"):
        pipeline.set_custom_bgm(
            "project-1",
            "audio/custom_bgm/project-2/other.mp3",
        )

    with pytest.raises(ValueError, match="belong to this project"):
        pipeline.set_custom_bgm(
            "project-1",
            "audio/custom_bgm/project-1/../project-2/other.mp3",
        )
