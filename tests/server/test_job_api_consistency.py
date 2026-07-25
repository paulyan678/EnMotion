from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.models import Script, VideoTask


class _FakeVideoPipeline:
    def __init__(self) -> None:
        self.script = Script(
            id="project-1",
            title="Queue rollback",
            original_text="test",
            created_at=1.0,
            updated_at=1.0,
        )
        self.rolled_back: list[str] = []
        self.failed: list[tuple[str, str, str | None, str | None]] = []

    def create_video_task(self, *, task_id, **_kwargs):
        task = VideoTask(
            id=task_id,
            project_id=self.script.id,
            image_url="https://example.invalid/source.png",
            prompt="Animate",
        )
        self.script.video_tasks.append(task)
        return self.script, task_id

    def rollback_video_task(self, script_id, task_id):
        assert script_id == self.script.id
        self.rolled_back.append(task_id)
        self.script.video_tasks = [item for item in self.script.video_tasks if item.id != task_id]
        return True

    def mark_video_task_failed(
        self,
        script_id,
        task_id,
        error_message,
        *,
        error_code=None,
        error_diagnostic=None,
        **_kwargs,
    ):
        assert script_id == self.script.id
        task = next(item for item in self.script.video_tasks if item.id == task_id)
        task.status = "failed"
        task.error = error_message
        task.error_code = error_code
        task.error_diagnostic = error_diagnostic
        self.failed.append((task_id, error_message, error_code, error_diagnostic))
        return True

    def get_script(self, script_id):
        return self.script if script_id == self.script.id else None


def test_video_batch_publish_failure_marks_every_prepared_task_failed(monkeypatch):
    pipeline = _FakeVideoPipeline()
    captured_specs = []
    abandoned: list[str] = []

    def reserve(specs):
        captured_specs.extend(specs)
        return [SimpleNamespace(id=item["job_id"]) for item in specs]

    def unavailable(_records):
        raise HTTPException(status_code=503, detail="queue unavailable")

    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(comic_api, "reserve_workspace_jobs", reserve)
    monkeypatch.setattr(comic_api, "publish_workspace_job_reservations", unavailable)
    monkeypatch.setattr(
        comic_api,
        "abandon_workspace_job_reservations",
        lambda records: abandoned.extend(item.id for item in records),
    )

    request = comic_api.CreateVideoTaskRequest(
        image_url="https://example.invalid/source.png",
        prompt="Animate",
        batch_size=3,
    )
    response = comic_api.create_video_task("project-1", request, BackgroundTasks())

    reserved_ids = [item["job_id"] for item in captured_specs]
    assert len(reserved_ids) == 3
    assert len(set(reserved_ids)) == 3
    assert pipeline.rolled_back == []
    assert abandoned == []
    assert [item.id for item in pipeline.script.video_tasks] == reserved_ids
    assert [item.status for item in pipeline.script.video_tasks] == ["failed"] * 3
    body = json.loads(response.body)
    assert [item["status"] for item in body] == ["failed"] * 3
    assert all(item["error_code"] == "video_queue_unavailable" for item in body)


def test_video_batch_admission_failure_happens_before_any_task_mutation(monkeypatch):
    pipeline = _FakeVideoPipeline()

    def reject(_specs):
        raise HTTPException(status_code=429, detail="active job limit")

    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(comic_api, "reserve_workspace_jobs", reject)
    monkeypatch.setattr(comic_api, "abandon_workspace_job_reservations", lambda _records: None)
    request = comic_api.CreateVideoTaskRequest(
        image_url="https://example.invalid/source.png",
        prompt="Animate",
        batch_size=4,
    )

    with pytest.raises(HTTPException) as raised:
        comic_api.create_video_task("project-1", request, BackgroundTasks())

    assert raised.value.status_code == 429
    assert pipeline.script.video_tasks == []
    assert pipeline.rolled_back == []
