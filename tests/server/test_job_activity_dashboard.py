from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.apps.server.context import Actor
from src.apps.server.database import Database
from src.apps.server.job_router import download_job_output
from src.apps.server.jobs import (
    ClaimedJob,
    _finalize_job_result,
    _finish_job_once,
    _update_job_progress,
    job_to_dict,
)
from src.apps.server.models import GenerationJob
from src.apps.server.quotas import workspace_output_root
from src.apps.server.service import create_user_with_personal_workspace


@pytest.fixture()
def database():
    value = Database("sqlite://")
    value.create_schema_for_tests()
    try:
        yield value
    finally:
        value.dispose()


def _identity(database: Database, username: str = "activity-user") -> tuple[str, str]:
    with database.session() as session:
        user, workspace = create_user_with_personal_workspace(
            session,
            username=username,
            password="a sufficiently long password",
        )
        session.commit()
        return user.id, workspace.id


def _actor(user_id: str, workspace_id: str) -> Actor:
    return Actor(
        user_id=user_id,
        username="activity-user",
        role="user",
        workspace_id=workspace_id,
        membership_role="owner",
        session_id="session",
    )


def test_activity_serialization_exposes_safe_details_and_persisted_outputs(database):
    user_id, workspace_id = _identity(database)
    identifier = str(uuid.uuid4())
    with database.session() as session:
        session.add(
            GenerationJob(
                id=identifier,
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="playground",
                status="running",
                payload={
                    "generation_id": "generation-1",
                    "activity_source": "playground",
                    "model_id": "gpt-image-2",
                    "mode": "t2i",
                    "prompt": "A quiet harbor at dawn",
                    "parameters": {
                        "aspect_ratio": "16:9",
                        "seed": 42,
                        "authorization": "must-not-leak",
                    },
                    "api_key": "must-not-leak",
                },
                result={
                    "url": "exports/final.mp4",
                    "provider_payload": {"authorization": "must-not-leak"},
                    "outputs": [
                        {
                            "id": "output-1",
                            "media_type": "image",
                            "media_path": "playground/images/result.png",
                            "filename": "result.png",
                            "mime_type": "image/png",
                        }
                    ],
                },
                progress=58,
                progress_stage="provider_processing",
                progress_is_estimated=True,
                provider_progress=50,
                progress_steps=[
                    {
                        "id": "provider_processing",
                        "state": "active",
                        "started_at": "2026-07-23T01:00:00+00:00",
                        "finished_at": None,
                        "message": "Provider is rendering",
                    }
                ],
            )
        )
        session.commit()
        record = session.get(GenerationJob, identifier)
        activity = job_to_dict(record)

    assert activity["model_name"] == "GPT Image 2"
    assert activity["provider_progress"] == 50
    assert activity["parameters"] == {"mode": "t2i", "aspect_ratio": "16:9", "seed": 42}
    assert activity["source_context"]["playground_generation_id"] == "generation-1"
    assert activity["source_context"]["route"] == "#/playground"
    assert activity["outputs"][0]["media_path"] == "playground/images/result.png"
    assert activity["result"] == {"url": "exports/final.mp4"}
    assert "authorization" not in activity["parameters"]
    assert "api_key" not in activity


def test_provider_progress_is_preserved_separately_and_terminal_step_is_closed(database):
    user_id, workspace_id = _identity(database)
    identifier = str(uuid.uuid4())
    with database.session() as session:
        session.add(
            GenerationJob(
                id=identifier,
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="video",
                status="running",
                payload={"script_id": "episode-1"},
                progress=36,
                progress_stage="accepted_by_provider",
                progress_is_estimated=True,
                progress_steps=[
                    {
                        "id": "accepted_by_provider",
                        "state": "active",
                        "started_at": "2026-07-23T01:00:00+00:00",
                        "finished_at": None,
                        "message": "Accepted",
                    }
                ],
            )
        )
        session.commit()

    _update_job_progress(
        database,
        workspace_id=workspace_id,
        job_id=identifier,
        stage="provider_processing",
        message="Rendering",
        percent=50,
        estimated=False,
    )
    with database.session() as session:
        running = session.get(GenerationJob, identifier)
        assert running.provider_progress == 50
        assert running.progress == 58
        assert running.progress_is_estimated is True
        assert running.progress_steps[-1]["state"] == "active"

    assert (
        _finish_job_once(
            database,
            identifier,
            workspace_id=workspace_id,
            status="failed",
            error="Provider rejected the request",
        )
        == "updated"
    )
    with database.session() as session:
        failed = session.get(GenerationJob, identifier)
        assert failed.status == "failed"
        assert failed.progress == 58
        assert failed.progress_steps[-1]["state"] == "failed"
        assert failed.progress_steps[-1]["finished_at"]


def test_output_manifest_and_authenticated_download_use_server_owned_media(
    database,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    user_id, workspace_id = _identity(database)
    media = workspace_output_root(workspace_id) / "playground" / "images" / "result.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"\x89PNG\r\n\x1a\nserver-owned-image")

    claimed = ClaimedJob(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="playground",
        payload={"generation_id": "generation-1"},
    )
    persisted = _finalize_job_result(
        claimed,
        {
            "generation_id": "generation-1",
            "_output_references": [{"id": "output-1", "path": str(media)}],
        },
    )
    output = persisted["outputs"][0]
    assert output["media_path"] == "playground/images/result.png"
    assert output["mime_type"] == "image/png"
    assert output["size_bytes"] == media.stat().st_size

    with database.session() as session:
        session.add(
            GenerationJob(
                id=claimed.id,
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="playground",
                status="completed",
                payload=claimed.payload,
                result=persisted,
                progress=100,
            )
        )
        session.commit()

    response = download_job_output(
        claimed.id,
        "output-1",
        actor=_actor(user_id, workspace_id),
        database=database,
    )
    assert response.path == media
    assert response.filename == "result.png"
    assert response.media_type == "image/png"

    with pytest.raises(HTTPException) as denied:
        download_job_output(
            claimed.id,
            "output-1",
            actor=_actor(user_id, "another-workspace"),
            database=database,
        )
    assert denied.value.status_code == 404


def test_video_output_generates_and_persists_a_server_owned_poster(
    database,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    user_id, workspace_id = _identity(database)
    video = workspace_output_root(workspace_id) / "playground" / "videos" / "result.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"server-owned-video")

    def fake_ffmpeg(command, **kwargs):
        del kwargs
        Path(command[-1]).write_bytes(b"generated-poster")
        return None

    monkeypatch.setattr(
        "src.apps.server.jobs.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr("src.apps.server.jobs.subprocess.run", fake_ffmpeg)
    claimed = ClaimedJob(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="playground",
        payload={"generation_id": "generation-video-1"},
    )

    persisted = _finalize_job_result(
        claimed,
        {
            "generation_id": "generation-video-1",
            "_output_references": [
                {
                    "id": "video-output-1",
                    "path": str(video),
                    "thumbnail_path": "playground/images/source-frame.png",
                }
            ],
        },
    )

    output = persisted["outputs"][0]
    assert output["media_type"] == "video"
    assert output["thumbnail_path"] == "playground/videos/result.poster.jpg"
    poster = workspace_output_root(workspace_id) / output["thumbnail_path"]
    assert poster.read_bytes() == b"generated-poster"
