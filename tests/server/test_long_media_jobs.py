from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.apps.comic_gen import api as comic_api
from src.apps.server import jobs as jobs_module


class _SubmissionPipeline:
    def __init__(self) -> None:
        self.script = SimpleNamespace(
            id="project-1",
            frames=[SimpleNamespace(id="frame-1")],
        )

    def get_script(self, script_id: str):
        return self.script if script_id == self.script.id else None

    def __getattr__(self, name: str):
        if name in {
            "generate_storyboard",
            "generate_video",
            "generate_storyboard_render",
            "merge_videos",
            "export_project",
            "preview_dub",
            "refine_batch_generator",
        }:
            raise AssertionError(f"{name} must not run inside a server API request")
        raise AttributeError(name)


def test_long_job_submission_always_returns_a_pollable_marker(monkeypatch):
    monkeypatch.setattr(
        comic_api,
        "enqueue_workspace_job",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="already-finished", status="completed"
        ),
    )

    assert comic_api.enqueue_long_workspace_job("merge", {"script_id": "project-1"}) == {
        "task_id": "already-finished",
        "status": "queued",
    }


@pytest.mark.parametrize(
    "invoke,expected_type,expected_payload",
    [
        (
            lambda: comic_api.refine_storyboard_batch("project-1"),
            "refine_batch",
            {"script_id": "project-1"},
        ),
        (
            lambda: comic_api.generate_storyboard("project-1"),
            "generate_storyboard",
            {"script_id": "project-1"},
        ),
        (
            lambda: comic_api.generate_video("project-1"),
            "generate_video",
            {"script_id": "project-1"},
        ),
        (
            lambda: comic_api.render_frame(
                "project-1",
                comic_api.RenderFrameRequest(
                    frame_id="frame-1",
                    composition_data={"reference_image_urls": []},
                    prompt="cinematic frame",
                    batch_size=2,
                ),
            ),
            "storyboard_render",
            {
                "script_id": "project-1",
                "frame_id": "frame-1",
                "composition_data": {"reference_image_urls": []},
                "prompt": "cinematic frame",
                "batch_size": 2,
            },
        ),
        (
            lambda: comic_api.merge_videos("project-1"),
            "merge",
            {"script_id": "project-1"},
        ),
        (
            lambda: comic_api.export_project(
                "project-1",
                comic_api.ExportRequest(
                    resolution="720p", format="webm", subtitles="sidecar"
                ),
            ),
            "export",
            {
                "script_id": "project-1",
                "options": {
                    "resolution": "720p",
                    "format": "webm",
                    "subtitles": "sidecar",
                },
            },
        ),
        (
            lambda: comic_api.preview_dub(
                "project-1",
                "frame-1",
                comic_api.DubPreviewRequest(video_task_id="video-1", offset_ms=250),
            ),
            "dub_preview",
            {
                "script_id": "project-1",
                "frame_id": "frame-1",
                "video_task_id": "video-1",
                "offset_ms": 250,
            },
        ),
    ],
)
def test_server_routes_submit_long_operations_without_running_them(
    monkeypatch, invoke, expected_type, expected_payload
):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(comic_api, "pipeline", _SubmissionPipeline())
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(
        comic_api,
        "enqueue_long_workspace_job",
        lambda job_type, payload: (
            captured.append((job_type, payload))
            or {"task_id": "durable-job", "status": "queued"}
        ),
    )

    assert invoke() == {"task_id": "durable-job", "status": "queued"}
    assert captured == [(expected_type, expected_payload)]


class _WorkerPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name == "export_project":
            return "export/final.mp4"
        return SimpleNamespace(id="project-1")

    def generate_storyboard(self, *args, **kwargs):
        return self._record("generate_storyboard", *args, **kwargs)

    def refine_batch_generator(self, *args, **kwargs):
        self._record("refine_batch_generator", *args, **kwargs)
        yield "frame_refine_start", {"frame_id": "frame-1", "total": 1}
        yield "batch_complete", {"total": 1, "success": 1, "failed": 0}

    def generate_video(self, *args, **kwargs):
        return self._record("generate_video", *args, **kwargs)

    def generate_storyboard_render(self, *args, **kwargs):
        return self._record("generate_storyboard_render", *args, **kwargs)

    def merge_videos(self, *args, **kwargs):
        return self._record("merge_videos", *args, **kwargs)

    def export_project(self, *args, **kwargs):
        return self._record("export_project", *args, **kwargs)

    def preview_dub(self, *args, **kwargs):
        return self._record("preview_dub", *args, **kwargs)


@pytest.mark.parametrize(
    "job_type,payload,expected_call,expected_result",
    [
        (
            "refine_batch",
            {"script_id": "project-1"},
            ("refine_batch_generator", ("project-1",), {}),
            {"script_id": "project-1"},
        ),
        (
            "generate_storyboard",
            {"script_id": "project-1"},
            ("generate_storyboard", ("project-1",), {}),
            {"script_id": "project-1"},
        ),
        (
            "generate_video",
            {"script_id": "project-1"},
            ("generate_video", ("project-1",), {}),
            {"script_id": "project-1"},
        ),
        (
            "storyboard_render",
            {
                "script_id": "project-1",
                "frame_id": "frame-1",
                "composition_data": {"reference_image_urls": []},
                "prompt": "cinematic frame",
                "batch_size": 2,
            },
            (
                "generate_storyboard_render",
                (
                    "project-1",
                    "frame-1",
                    {"reference_image_urls": []},
                    "cinematic frame",
                    2,
                ),
                {},
            ),
            {"script_id": "project-1", "frame_id": "frame-1"},
        ),
        (
            "merge",
            {"script_id": "project-1"},
            ("merge_videos", ("project-1",), {}),
            {"script_id": "project-1"},
        ),
        (
            "export",
            {"script_id": "project-1", "options": {"format": "mp4"}},
            ("export_project", ("project-1", {"format": "mp4"}), {}),
            {"url": "export/final.mp4"},
        ),
        (
            "dub_preview",
            {
                "script_id": "project-1",
                "frame_id": "frame-1",
                "video_task_id": "video-1",
                "offset_ms": 250,
            },
            (
                "preview_dub",
                ("project-1", "frame-1"),
                {"video_task_id": "video-1", "offset_ms": 250},
            ),
            {"script_id": "project-1", "frame_id": "frame-1"},
        ),
    ],
)
def test_long_job_handlers_run_in_the_workspace_worker(
    monkeypatch, job_type, payload, expected_call, expected_result
):
    pipeline = _WorkerPipeline()

    @contextmanager
    def locked(_workspace_id):
        yield pipeline

    monkeypatch.setattr(jobs_module._worker_pipelines, "locked", locked)
    job = jobs_module.ClaimedJob(
        id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        job_type=job_type,
        payload=payload,
    )

    assert jobs_module.JOB_HANDLERS[job_type](job) == expected_result
    assert pipeline.calls == [expected_call]


def test_desktop_refine_batch_preserves_sse_stream(monkeypatch):
    class DesktopPipeline:
        script = SimpleNamespace(id="project-1")

        def get_script(self, script_id):
            return self.script if script_id == self.script.id else None

        def refine_batch_generator(self, _script_id):
            yield "frame_refine_start", {"frame_id": "frame-1", "total": 1}
            yield "batch_complete", {"total": 1, "success": 1, "failed": 0}

    monkeypatch.setattr(comic_api, "pipeline", DesktopPipeline())
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: False)

    response = comic_api.refine_storyboard_batch("project-1")

    async def read_body() -> str:
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
        return b"".join(chunks).decode()

    body = asyncio.run(read_body())
    assert response.media_type == "text/event-stream"
    assert "event: frame_refine_start" in body
    assert 'data: {"frame_id": "frame-1", "total": 1}' in body
    assert "event: batch_complete" in body


def test_long_media_jobs_receive_conservative_storage_reservations(monkeypatch):
    monkeypatch.setenv("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", "100")
    monkeypatch.setenv("ENMOTION_VIDEO_JOB_STORAGE_RESERVATION_BYTES", "1000")
    monkeypatch.setenv("ENMOTION_LONG_MEDIA_JOB_STORAGE_RESERVATION_BYTES", "4000")

    assert jobs_module.job_storage_reservation_bytes("storyboard_render", {"batch_size": 2}) == 200
    assert jobs_module.job_storage_reservation_bytes("dub_preview", {}) == 1000
    for job_type in ("generate_storyboard", "generate_video", "merge", "export"):
        assert jobs_module.job_storage_reservation_bytes(job_type, {}) == 4000
