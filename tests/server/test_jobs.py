from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from src.apps.comic_gen.models import Script, VideoTask
from src.apps.comic_gen.pipeline import (
    AssemblyMutationConflictError,
    ComicGenPipeline,
)
from src.apps.comic_gen.video_failures import (
    VIDEO_FAILURE_CODE,
    VIDEO_INTERRUPTED_CODE,
    VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
    VIDEO_TIMEOUT_CODE,
)
from src.apps.server.context import Actor
from src.apps.server.database import Database
from src.apps.server.job_router import cancel_job as cancel_job_endpoint
from src.apps.server.jobs import (
    ASSEMBLY_INPUTS_CHANGED_CODE,
    JOB_HANDLERS,
    QUEUE_PUBLICATION_PENDING,
    JobCancellationOutcome,
    JobDismissalOutcome,
    JobLimitExceededError,
    JobPayloadTooLargeError,
    JobQueueUnavailableError,
    JobRetryOutcome,
    JobSpec,
    TerminalStatePersistenceError,
    abandon_reserved_jobs,
    cancel_workspace_job,
    compact_terminal_jobs,
    create_job,
    delete_frame_generation_jobs,
    dismiss_workspace_job,
    execute_job_task,
    get_workspace_job,
    job_to_dict,
    list_workspace_jobs,
    process_job,
    publish_reserved_jobs,
    queued_job_positions,
    reconcile_terminal_job_outbox,
    recover_interrupted_jobs,
    recover_stale_reservations,
    republish_queued_jobs,
    republish_unconfirmed_jobs,
    reserve_jobs,
    retry_workspace_job,
    _job_failure_result,
    _public_job_failure,
)
from src.apps.server.models import GenerationJob, Workspace, utc_now
from src.apps.server.quotas import StorageQuotaExceededError
from src.apps.server.service import create_user_with_personal_workspace
from src.apps.web_runtime.pipeline_registry import WorkspacePipelineRegistry
from src.apps.web_runtime.playground_registry import WorkspacePlaygroundRegistry
from src.models.newapi import (
    INPUT_IMAGE_PRIVACY_ERROR_CODE,
    INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
    NewAPIProviderError,
)


def test_assembly_conflict_remains_actionable_in_durable_job_result() -> None:
    conflict = AssemblyMutationConflictError("project changed during provider work")

    assert _public_job_failure(conflict, "generic failure") == str(conflict)
    assert _job_failure_result(conflict) == {
        "error_code": ASSEMBLY_INPUTS_CHANGED_CODE,
        "error_diagnostic": str(conflict),
    }


@pytest.fixture()
def database():
    value = Database("sqlite://")
    value.create_schema_for_tests()
    try:
        yield value
    finally:
        value.dispose()


def _identity(database: Database, username: str):
    with database.session() as session:
        user, workspace = create_user_with_personal_workspace(
            session,
            username=username,
            password="a sufficiently long password",
        )
        session.commit()
        return user.id, workspace.id


def _queued_job(database: Database, monkeypatch, *, username: str = "artist"):
    published: list[dict] = []

    def fake_publish(*args, **kwargs):
        published.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(execute_job_task, "apply_async", fake_publish)
    user_id, workspace_id = _identity(database, username)
    job_id = str(uuid.uuid4())
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="project_asset",
        payload={"script_id": "project-1", "asset_id": "asset-1"},
        job_id=job_id,
    )
    assert published == [
        {
            "args": (),
            "kwargs": {
                "args": [job_id],
                "task_id": job_id,
                "queue": "enmotion-generation",
            },
        }
    ]
    return record, user_id, workspace_id


def _queued_video_job(database, monkeypatch, tmp_path, *, username: str):
    """Create matching durable + workspace video records for lifecycle tests."""

    from src.apps.server import jobs as jobs_module

    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    registry = WorkspacePipelineRegistry(str(workspace_root))
    monkeypatch.setattr(jobs_module, "_worker_pipelines", registry)
    monkeypatch.setattr(
        jobs_module,
        "_worker_playgrounds",
        WorkspacePlaygroundRegistry(registry),
    )
    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    user_id, workspace_id = _identity(database, username)
    task_id = str(uuid.uuid4())
    pipeline = registry.get(workspace_id)
    task = VideoTask(
        id=task_id,
        project_id="project-1",
        image_url="uploads/original.png",
        prompt="Preserve the original prompt",
        duration=12,
        resolution="1080p",
        seed=731,
        ratio="9:16",
    )
    pipeline.scripts["project-1"] = Script(
        id="project-1",
        title="Video lifecycle",
        original_text="test",
        video_tasks=[task],
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline._save_data()
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="video",
        payload={"script_id": "project-1", "task_id": task_id},
        job_id=task_id,
    )
    return record, user_id, workspace_id, registry, task


def test_jobs_are_persisted_and_tenant_scoped(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch)
    _, other_workspace_id = _identity(database, "other-artist")

    found = get_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert found is not None
    assert job_to_dict(found)["script_id"] == "project-1"
    assert get_workspace_job(database, workspace_id=other_workspace_id, job_id=record.id) is None
    assert list_workspace_jobs(database, workspace_id=other_workspace_id) == []


def test_worker_claims_and_completes_exactly_once(database, monkeypatch):
    record, _, _ = _queued_job(database, monkeypatch)
    calls: list[str] = []

    def complete(claimed):
        calls.append(claimed.id)
        return {"artifact": "files/video/result.mp4"}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", complete)
    assert process_job(record.id, database) == {"artifact": "files/video/result.mp4"}
    assert process_job(record.id, database) is None

    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "completed"
        assert stored.progress == 100
        assert stored.attempts == 1
        assert stored.result == {"artifact": "files/video/result.mp4"}
    assert calls == [record.id]


def test_provider_safety_failure_persists_safe_message_and_diagnostics(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch, username="provider-safety-test")

    def reject(_claimed):
        raise NewAPIProviderError(
            INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
            error_code=INPUT_IMAGE_PRIVACY_ERROR_CODE,
            provider_code="InputImageSensitiveContentDetected.PrivacyInformation",
            provider_message="The input image may contain a real person",
            http_status=400,
            request_id="provider-request-1",
            phase="video submission",
        )

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", reject)
    with pytest.raises(NewAPIProviderError):
        process_job(record.id, database)

    stored = get_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE
    assert stored.result == {
        "error_code": INPUT_IMAGE_PRIVACY_ERROR_CODE,
        "error_diagnostic": (
            "阶段：提交视频任务\n"
            "HTTP 状态：400\n"
            "服务商错误代码：InputImageSensitiveContentDetected.PrivacyInformation\n"
            "请求 ID：provider-request-1"
        ),
    }
    activity = job_to_dict(stored)
    assert activity["error"] == INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE
    assert activity["error_code"] == INPUT_IMAGE_PRIVACY_ERROR_CODE
    assert "服务商错误代码：" in activity["error_diagnostic"]
    assert "real person" not in activity["error_diagnostic"]


@pytest.mark.parametrize(
    ("failure_factory", "expected_code"),
    [
        pytest.param(
            lambda: ConnectionError("video submission failed before a provider id was returned"),
            VIDEO_FAILURE_CODE,
            id="submission-failure",
        ),
        pytest.param(
            lambda: NewAPIProviderError(
                INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
                error_code=INPUT_IMAGE_PRIVACY_ERROR_CODE,
                provider_code="InputImageSensitiveContentDetected.PrivacyInformation",
                provider_message="The input image may contain a real person",
                http_status=400,
                phase="video submission",
            ),
            INPUT_IMAGE_PRIVACY_ERROR_CODE,
            id="provider-rejection",
        ),
        pytest.param(
            lambda: RuntimeError("provider polling failed with an unreachable response"),
            VIDEO_FAILURE_CODE,
            id="polling-failure",
        ),
        pytest.param(
            lambda: TimeoutError("provider polling did not finish within 3600 seconds"),
            VIDEO_TIMEOUT_CODE,
            id="timeout",
        ),
        pytest.param(
            lambda: OSError("video download failed before the response could be saved"),
            VIDEO_FAILURE_CODE,
            id="download-failure",
        ),
        pytest.param(
            lambda: OSError("video persistence failed while saving projects.json"),
            VIDEO_FAILURE_CODE,
            id="persistence-failure",
        ),
    ],
)
def test_video_worker_failure_survives_workspace_rollback_and_page_reload(
    database,
    monkeypatch,
    tmp_path,
    failure_factory,
    expected_code,
):
    """The durable failure writeback must happen after metadata rollback."""

    record, _, workspace_id, registry, _ = _queued_video_job(
        database,
        monkeypatch,
        tmp_path,
        username=f"video-failure-{expected_code}-{uuid.uuid4().hex[:6]}",
    )

    def fail_after_processing(claimed):
        active = registry.get(claimed.workspace_id)
        task = active.scripts["project-1"].video_tasks[0]
        task.status = "processing"
        active._save_data()
        raise failure_factory()

    monkeypatch.setitem(JOB_HANDLERS, "video", fail_after_processing)
    with pytest.raises(Exception):
        process_job(record.id, database)

    stored = get_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.result is not None
    assert stored.result["error_code"] == expected_code
    assert stored.finished_at is not None
    assert job_to_dict(stored)["status"] == "failed"

    # A fresh registry load represents refresh/reopen and proves the project
    # task did not revert to its pre-worker pending snapshot.
    registry.discard(workspace_id)
    reloaded = registry.get(workspace_id).scripts["project-1"].video_tasks[0]
    assert reloaded.status == "failed"
    assert reloaded.error_code == expected_code
    assert reloaded.error
    assert reloaded.error_diagnostic


def test_failed_video_retry_uses_original_parameters_and_can_complete(
    database, monkeypatch, tmp_path
):
    record, _, workspace_id, registry, original = _queued_video_job(
        database,
        monkeypatch,
        tmp_path,
        username="video-retry-success",
    )
    original_recipe = {
        field: getattr(original, field)
        for field in (
            "image_url",
            "prompt",
            "duration",
            "resolution",
            "seed",
            "ratio",
            "model",
            "generation_mode",
        )
    }

    monkeypatch.setitem(
        JOB_HANDLERS,
        "video",
        lambda _claimed: (_ for _ in ()).throw(TimeoutError("provider polling timed out")),
    )
    with pytest.raises(TimeoutError):
        process_job(record.id, database)

    published: list[str] = []
    monkeypatch.setattr(
        execute_job_task,
        "apply_async",
        lambda *_args, **kwargs: published.append(kwargs["task_id"]),
    )
    outcome, retried = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert outcome is JobRetryOutcome.RETRIED
    assert retried is not None
    delivery_id = retried.queue_task_id
    assert delivery_id is not None
    assert published == [delivery_id]

    registry.discard(workspace_id)
    pending = registry.get(workspace_id).scripts["project-1"].video_tasks[0]
    assert pending.status == "pending"
    assert pending.error is None
    assert {field: getattr(pending, field) for field in original_recipe} == original_recipe

    def complete_retry(claimed):
        active = registry.get(claimed.workspace_id)
        task = active.scripts["project-1"].video_tasks[0]
        task.status = "completed"
        task.video_url = "video/retried.mp4"
        active._save_data()
        return {"script_id": "project-1", "task_id": task.id}

    monkeypatch.setitem(JOB_HANDLERS, "video", complete_retry)
    assert process_job(record.id, database, delivery_id) == {
        "script_id": "project-1",
        "task_id": record.id,
    }
    registry.discard(workspace_id)
    completed = registry.get(workspace_id).scripts["project-1"].video_tasks[0]
    assert completed.status == "completed"
    assert completed.video_url == "video/retried.mp4"


def test_canceling_failed_video_retry_restores_failed_task_and_retry_action(
    database, monkeypatch, tmp_path
):
    record, _, workspace_id, registry, _ = _queued_video_job(
        database,
        monkeypatch,
        tmp_path,
        username="video-retry-cancel",
    )
    monkeypatch.setitem(
        JOB_HANDLERS,
        "video",
        lambda _claimed: (_ for _ in ()).throw(RuntimeError("provider rejected request")),
    )
    with pytest.raises(RuntimeError):
        process_job(record.id, database)

    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke",
        lambda *_args, **_kwargs: None,
    )
    outcome, _ = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert outcome is JobRetryOutcome.RETRIED
    assert (
        cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobCancellationOutcome.CANCELED
    )

    registry.discard(workspace_id)
    restored = registry.get(workspace_id).scripts["project-1"].video_tasks[0]
    assert restored.status == "failed"
    assert restored.error_code == VIDEO_FAILURE_CODE
    second_outcome, _ = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert second_outcome is JobRetryOutcome.RETRIED


def test_retried_video_refreshes_replacement_and_preserves_structured_failure(
    monkeypatch,
):
    from src.apps.server import jobs as jobs_module

    task = SimpleNamespace(
        id="video-task",
        status="failed",
        error=INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
        error_code=INPUT_IMAGE_PRIVACY_ERROR_CODE,
        error_diagnostic=(
            "Provider code: " "InputImageSensitiveContentDetected.PrivacyInformation"
        ),
    )

    class FakePipeline:
        refreshed: list[tuple[str, str]] = []

        def refresh_asset_video_task_input(self, script_id, task_id):
            self.refreshed.append((script_id, task_id))
            return True

        def process_video_task(self, script_id, task_id):
            assert (script_id, task_id) == ("project-1", "video-task")

        def get_script(self, script_id):
            assert script_id == "project-1"
            return SimpleNamespace(video_tasks=[task])

    pipeline = FakePipeline()

    @contextmanager
    def locked(_workspace_id):
        yield pipeline

    monkeypatch.setattr(jobs_module._worker_pipelines, "locked", locked)
    claimed = jobs_module.ClaimedJob(
        id="video-task",
        workspace_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        job_type="video",
        payload={"script_id": "project-1", "task_id": "video-task"},
        attempts=2,
    )

    with pytest.raises(NewAPIProviderError) as exc_info:
        jobs_module._video(claimed)

    assert pipeline.refreshed == [("project-1", "video-task")]
    assert exc_info.value.error_code == INPUT_IMAGE_PRIVACY_ERROR_CODE
    assert "InputImageSensitiveContentDetected" in exc_info.value.diagnostic


def test_playground_provider_wait_does_not_hold_workspace_lock(database, monkeypatch):
    from src.apps.server import jobs as jobs_module

    record, _, _ = _queued_job(database, monkeypatch, username="playground-lock-test")
    generation_id = str(uuid.uuid4())
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.job_type = "playground"
        stored.payload = {"generation_id": generation_id}
        session.commit()

    lock_depth = 0

    @contextmanager
    def tracked_lock(_path):
        nonlocal lock_depth
        lock_depth += 1
        try:
            yield
        finally:
            lock_depth -= 1

    observed_depths: list[int] = []

    def complete(_claimed):
        observed_depths.append(lock_depth)
        return {"generation_id": generation_id}

    monkeypatch.setattr(jobs_module, "interprocess_lock", tracked_lock)
    monkeypatch.setattr(
        jobs_module,
        "_reconcile_playground_job_storage",
        lambda *_args, **_kwargs: {"storage_usage_bytes": 0, "job_output_bytes": 0},
    )
    monkeypatch.setitem(JOB_HANDLERS, "playground", complete)

    assert process_job(record.id, database) == {"generation_id": generation_id}
    assert observed_depths == [0]


def test_storyboard_provider_wait_keeps_workspace_mutations_responsive(
    database, monkeypatch, tmp_path
):
    from src.apps.server import jobs as jobs_module

    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ENMOTION_DATA_DIR", str(tmp_path / "app-data"))
    record, _, workspace_id = _queued_job(database, monkeypatch, username="storyboard-lock-test")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.job_type = "storyboard_render"
        stored.payload = {
            "script_id": "project-1",
            "frame_id": "frame-1",
            "composition_data": {"reference_image_urls": []},
            "prompt": "cinematic frame",
            "batch_size": 1,
        }
        session.commit()

    output_root = tmp_path / "workspaces" / workspace_id / "output"
    output_path = output_root / "storyboard" / "frame-1_variant-1.png"
    lock_path = output_root.parent / ".workspace.lock"
    provider_started = threading.Event()
    release_provider = threading.Event()
    commit_calls: list[str] = []

    class RenderPipeline:
        def prepare_storyboard_render(self, *_args, **_kwargs):
            return SimpleNamespace(frame=SimpleNamespace())

        def execute_storyboard_render_plan(self, plan):
            provider_started.set()
            if not release_provider.wait(5):
                raise TimeoutError("test provider was not released")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"rendered")
            return plan.frame

        def storyboard_render_output_paths(self, _plan, _generated_frame):
            return [output_path] if output_path.exists() else []

        def validate_storyboard_render_result(self, _generated_frame):
            return None

        def commit_storyboard_render_plan(self, _plan, _generated_frame):
            assert (output_root / "concurrent-user-edit.json").exists()
            commit_calls.append("committed")

        def fail_storyboard_render_plan(self, _plan):
            pytest.fail("successful render must not be marked failed")

    pipeline = RenderPipeline()

    class RenderRegistry:
        def lock_path_for(self, _workspace_id):
            return lock_path

        @contextmanager
        def locked(self, _workspace_id):
            with jobs_module.interprocess_lock(lock_path):
                yield pipeline

        def discard(self, _workspace_id):
            return None

    monkeypatch.setattr(jobs_module, "_worker_pipelines", RenderRegistry())
    result: dict[str, object] = {}

    def run_worker():
        try:
            result["value"] = process_job(record.id, database)
        except BaseException as exc:  # pragma: no cover - reported below
            result["error"] = exc

    worker = threading.Thread(target=run_worker)
    worker.start()
    assert provider_started.wait(2)

    mutation_finished = threading.Event()

    def run_concurrent_mutation():
        with jobs_module.interprocess_lock(lock_path):
            output_root.mkdir(parents=True, exist_ok=True)
            (output_root / "concurrent-user-edit.json").write_text(
                '{"preserved": true}', encoding="utf-8"
            )
        mutation_finished.set()

    mutation = threading.Thread(target=run_concurrent_mutation)
    mutation.start()
    responsive = mutation_finished.wait(1)
    release_provider.set()
    mutation.join(timeout=2)
    worker.join(timeout=5)

    assert responsive, "workspace mutation waited for the storyboard provider"
    assert not worker.is_alive()
    assert "error" not in result
    assert result["value"] == {
        "script_id": "project-1",
        "frame_id": "frame-1",
    }
    assert commit_calls == ["committed"]
    assert (output_root / "concurrent-user-edit.json").exists()


def test_worker_failure_is_visible_and_not_retried_implicitly(database, monkeypatch):
    record, _, _ = _queued_job(database, monkeypatch)

    def fail(_claimed):
        raise RuntimeError("provider rejected the request")

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", fail)
    with pytest.raises(RuntimeError, match="provider rejected"):
        process_job(record.id, database)

    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "failed"
        assert stored.attempts == 1
        assert stored.error == "生成任务失败，请稍后重试。"


@pytest.mark.parametrize(
    ("job_type", "owner_payload", "expected_call"),
    [
        (
            "project_asset",
            {"script_id": "project-1"},
            ("project-1", "asset-1", "character"),
        ),
        (
            "series_asset",
            {"series_id": "series-1"},
            ("series", "series-1", "asset-1", "character"),
        ),
        (
            "global_asset",
            {"source_id": "global"},
            ("global", "global", "asset-1", "character"),
        ),
    ],
)
def test_worker_failure_marks_rolled_back_asset_reservation_failed(
    database, monkeypatch, job_type, owner_payload, expected_call
):
    from src.apps.server import jobs as jobs_module

    record, _, _ = _queued_job(database, monkeypatch, username=f"failed-{job_type}")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.job_type = job_type
        stored.payload = {
            **owner_payload,
            "asset_id": "asset-1",
            "asset_type": "character",
        }
        session.commit()

    events: list[object] = []

    class FakePipeline:
        def fail_orphaned_asset_reservation(self, *args):
            events.append(("failed", args))
            return True

        def fail_orphaned_source_asset_reservation(self, *args):
            events.append(("failed", args))
            return True

    pipeline = FakePipeline()
    monkeypatch.setattr(jobs_module._worker_pipelines, "get", lambda _workspace_id: pipeline)
    original_rollback = jobs_module._rollback_job_workspace

    def tracked_rollback(*args, **kwargs):
        events.append("rollback")
        return original_rollback(*args, **kwargs)

    monkeypatch.setattr(jobs_module, "_rollback_job_workspace", tracked_rollback)

    def fail(_claimed):
        raise RuntimeError("provider rejected the request")

    monkeypatch.setitem(JOB_HANDLERS, job_type, fail)
    with pytest.raises(RuntimeError, match="provider rejected"):
        process_job(record.id, database)

    assert events == ["rollback", ("failed", expected_call)]
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "failed"
        assert stored.error == "生成任务失败，请稍后重试。"


def test_terminal_commit_retries_without_repeating_handler(database, monkeypatch, tmp_path):
    from src.apps.server import jobs as jobs_module

    monkeypatch.setenv("ENMOTION_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("ENMOTION_JOB_FINALIZE_ATTEMPTS", "3")
    monkeypatch.setattr(jobs_module.time, "sleep", lambda _delay: None)
    record, _, _ = _queued_job(database, monkeypatch, username="finalize-retry")
    handler_calls: list[str] = []

    def complete(claimed):
        handler_calls.append(claimed.id)
        return {"artifact": "files/video/retried.mp4"}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", complete)
    original_finish = jobs_module._finish_job_once
    finish_calls = 0

    def fail_once(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            raise ConnectionError("one-shot PostgreSQL commit failure")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(jobs_module, "_finish_job_once", fail_once)

    assert process_job(record.id, database) == {"artifact": "files/video/retried.mp4"}
    assert handler_calls == [record.id]
    assert finish_calls == 2
    assert list((tmp_path / "app-data" / "job-terminal-outbox").glob("*.json")) == []
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "completed"
        assert stored.result == {"artifact": "files/video/retried.mp4"}


def test_terminal_outbox_reconciles_without_repeating_handler(database, monkeypatch, tmp_path):
    from src.apps.server import jobs as jobs_module

    monkeypatch.setenv("ENMOTION_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("ENMOTION_JOB_FINALIZE_ATTEMPTS", "2")
    monkeypatch.setattr(jobs_module.time, "sleep", lambda _delay: None)
    record, _, _ = _queued_job(database, monkeypatch, username="finalize-outbox")
    handler_calls: list[str] = []

    def complete(claimed):
        handler_calls.append(claimed.id)
        return {"artifact": "files/video/outbox.mp4"}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", complete)
    original_finish = jobs_module._finish_job_once
    monkeypatch.setattr(
        jobs_module,
        "_finish_job_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("PostgreSQL unavailable")),
    )

    with pytest.raises(TerminalStatePersistenceError, match="reconciliation will retry"):
        process_job(record.id, database)

    marker = tmp_path / "app-data" / "job-terminal-outbox" / f"{record.id}.json"
    assert marker.is_file()
    with database.session() as session:
        assert session.get(GenerationJob, record.id).status == "running"

    monkeypatch.setattr(jobs_module, "_finish_job_once", original_finish)
    assert reconcile_terminal_job_outbox(database) == 1
    assert not marker.exists()
    assert handler_calls == [record.id]
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "completed"
        assert stored.result == {"artifact": "files/video/outbox.mp4"}


def test_cancelled_queue_item_is_never_executed(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch)
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke", lambda *_args, **_kwargs: None
    )
    assert cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert process_job(record.id, database) is None
    with database.session() as session:
        assert session.get(GenerationJob, record.id).status == "canceled"


@pytest.mark.parametrize(
    ("job_type", "owner_payload", "expected_call"),
    [
        (
            "project_asset",
            {"script_id": "project-1"},
            ("project-1", "asset-1", "character", "completed"),
        ),
        (
            "series_asset",
            {"series_id": "series-1"},
            ("series", "series-1", "asset-1", "character", "completed"),
        ),
        (
            "global_asset",
            {"source_id": "global"},
            ("global", "global", "asset-1", "character", "completed"),
        ),
    ],
)
def test_cancelled_asset_job_restores_its_previous_status(
    database, monkeypatch, tmp_path, job_type, owner_payload, expected_call
):
    from src.apps.server import jobs as jobs_module

    record, _, workspace_id = _queued_job(database, monkeypatch, username=f"cancel-{job_type}")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.job_type = job_type
        stored.payload = {
            **owner_payload,
            "asset_id": "asset-1",
            "asset_type": "character",
            "previous_asset_status": "completed",
        }
        session.commit()

    class FakePipeline:
        restored: list[tuple] = []
        failed: list[tuple] = []

        def restore_asset_reservation(self, *args):
            self.restored.append(args)
            return True

        def restore_source_asset_reservation(self, *args):
            self.restored.append(args)
            return True

        def fail_orphaned_asset_reservation(self, *args):
            self.failed.append(args)
            return True

        def fail_orphaned_source_asset_reservation(self, *args):
            self.failed.append(args)
            return True

    pipeline = FakePipeline()
    monkeypatch.setattr(jobs_module._worker_pipelines, "get", lambda _workspace_id: pipeline)
    monkeypatch.setattr(
        jobs_module._worker_pipelines,
        "lock_path_for",
        lambda _workspace_id: tmp_path / ".workspace.lock",
    )
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke", lambda *_args, **_kwargs: None
    )

    assert (
        cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobCancellationOutcome.CANCELED
    )
    assert pipeline.restored == [expected_call]
    assert pipeline.failed == []
    with database.session() as session:
        assert session.get(GenerationJob, record.id).status == "canceled"


def test_cancelled_legacy_asset_job_becomes_retryable(database, monkeypatch, tmp_path):
    from src.apps.server import jobs as jobs_module

    record, _, workspace_id = _queued_job(database, monkeypatch, username="cancel-legacy-asset")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.payload = {
            "script_id": "project-1",
            "asset_id": "asset-1",
            "asset_type": "scene",
        }
        session.commit()

    class FakePipeline:
        failed: list[tuple] = []

        def fail_orphaned_asset_reservation(self, *args):
            self.failed.append(args)
            return True

    pipeline = FakePipeline()
    monkeypatch.setattr(jobs_module._worker_pipelines, "get", lambda _workspace_id: pipeline)
    monkeypatch.setattr(
        jobs_module._worker_pipelines,
        "lock_path_for",
        lambda _workspace_id: tmp_path / ".workspace.lock",
    )
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke", lambda *_args, **_kwargs: None
    )

    assert (
        cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobCancellationOutcome.CANCELED
    )
    assert pipeline.failed == [("project-1", "asset-1", "scene")]


def test_worker_startup_fails_interrupted_jobs_without_repeating_them(database, monkeypatch):
    record, _, _ = _queued_job(database, monkeypatch)
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.status = "running"
        session.commit()

    assert recover_interrupted_jobs(database) == 1
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "failed"
        assert "重复计费" in stored.error


def test_worker_startup_persists_interrupted_video_failure_in_project(
    database, monkeypatch, tmp_path
):
    record, _, workspace_id, registry, _ = _queued_video_job(
        database,
        monkeypatch,
        tmp_path,
        username="interrupted-video",
    )
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.status = "running"
        session.commit()
    active = registry.get(workspace_id)
    active.scripts["project-1"].video_tasks[0].status = "processing"
    active._save_data()

    assert recover_interrupted_jobs(database) == 1

    registry.discard(workspace_id)
    task = registry.get(workspace_id).scripts["project-1"].video_tasks[0]
    assert task.status == "failed"
    assert task.error_code == VIDEO_INTERRUPTED_CODE
    assert "重复计费" in (task.error or "")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "failed"
        assert stored.result["error_code"] == VIDEO_INTERRUPTED_CODE


def test_terminal_history_compaction_preserves_active_and_recent_jobs(database):
    user_id, workspace_id = _identity(database, "history-compaction")
    now = utc_now()
    with database.session() as session:
        records = [
            GenerationJob(
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="project_asset",
                status="completed",
                payload={},
                progress=100,
                finished_at=now - timedelta(hours=1),
                created_at=now - timedelta(hours=2),
            ),
            *[
                GenerationJob(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    job_type="project_asset",
                    status=status,
                    payload={},
                    progress=100 if status == "completed" else 1,
                    finished_at=now - timedelta(days=offset),
                    created_at=now - timedelta(days=offset, hours=1),
                )
                for offset, status in (
                    (2, "completed"),
                    (3, "failed"),
                    (4, "canceled"),
                    (40, "completed"),
                )
            ],
            GenerationJob(
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="project_asset",
                status="running",
                payload={},
                progress=1,
                started_at=now - timedelta(days=40),
                created_at=now - timedelta(days=40),
            ),
        ]
        session.add_all(records)
        session.commit()
        active_id = records[-1].id
        recent_id = records[0].id

    assert (
        compact_terminal_jobs(
            database,
            retention_days=30,
            max_terminal_per_workspace=2,
            batch_size=100,
            now=now,
        )
        == 3
    )

    with database.session() as session:
        remaining = list(
            session.scalars(select(GenerationJob).where(GenerationJob.workspace_id == workspace_id))
        )
    assert {record.id for record in remaining} == {recent_id, records[1].id, active_id}
    assert next(record for record in remaining if record.id == active_id).status == "running"


def test_delete_frame_generation_jobs_removes_only_owned_rows_and_revokes_queue(
    database, monkeypatch
):
    user_id, workspace_id = _identity(database, "frame-job-cleanup")
    other_user_id, other_workspace_id = _identity(database, "other-frame-job-cleanup")
    target_task_id = str(uuid.uuid4())
    revoked: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke",
        lambda task_id, terminate=False: revoked.append((task_id, terminate)),
    )

    with database.session() as session:
        records = [
            GenerationJob(
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="storyboard_render",
                status="queued",
                queue_task_id="frame-delivery",
                payload={"script_id": "project-1", "frame_id": "frame-delete"},
                result={"image_url": "storyboard/deleted.png"},
            ),
            GenerationJob(
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="video",
                status="failed",
                payload={"script_id": "project-1", "task_id": target_task_id},
                result={"video_url": "video/deleted.mp4"},
            ),
            GenerationJob(
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="dub_preview",
                status="running",
                payload={"script_id": "project-1", "frame_id": "frame-delete"},
            ),
            GenerationJob(
                workspace_id=workspace_id,
                user_id=user_id,
                job_type="storyboard_render",
                status="completed",
                payload={"script_id": "project-1", "frame_id": "frame-keep"},
            ),
            GenerationJob(
                workspace_id=other_workspace_id,
                user_id=other_user_id,
                job_type="storyboard_render",
                status="completed",
                payload={"script_id": "project-1", "frame_id": "frame-delete"},
            ),
        ]
        session.add_all(records)
        session.commit()
        removed_ids = {records[0].id, records[1].id, records[2].id}
        retained_ids = {records[3].id, records[4].id}

    snapshots = delete_frame_generation_jobs(
        database,
        workspace_id=workspace_id,
        script_id="project-1",
        frame_id="frame-delete",
        task_ids=[target_task_id],
    )

    assert {snapshot["id"] for snapshot in snapshots} == removed_ids
    assert any(snapshot["result"].get("video_url") == "video/deleted.mp4" for snapshot in snapshots)
    assert revoked == [("frame-delivery", False)]
    with database.session() as session:
        remaining_ids = set(session.scalars(select(GenerationJob.id)))
    assert remaining_ids == retained_ids


def test_worker_startup_republishes_queued_rows(database, monkeypatch):
    record, _, _ = _queued_job(database, monkeypatch)
    published: list[str] = []

    def publish(*_args, **kwargs):
        published.append(kwargs["task_id"])

    monkeypatch.setattr(execute_job_task, "apply_async", publish)
    assert republish_queued_jobs(database) == 1
    assert published == [record.id]


def test_queue_publication_failure_becomes_terminal(database, monkeypatch):
    user_id, workspace_id = _identity(database, "queue-test")
    job_id = str(uuid.uuid4())

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("redis is unavailable")

    monkeypatch.setattr(execute_job_task, "apply_async", unavailable)
    with pytest.raises(JobQueueUnavailableError):
        create_job(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            job_type="project_asset",
            payload={},
            job_id=job_id,
        )
    with database.session() as session:
        stored = session.get(GenerationJob, job_id)
        assert stored.status == "failed"
        assert stored.error == "生成队列暂时不可用，请稍后重试。"


def test_running_job_cannot_claim_cancellation(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch)
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.status = "running"
        session.commit()

    revoked: list[str] = []
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke",
        lambda job_id, **_kwargs: revoked.append(job_id),
    )
    assert (
        cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobCancellationOutcome.RUNNING
    )
    with database.session() as session:
        assert session.get(GenerationJob, record.id).status == "running"
    assert revoked == []

    actor = Actor(
        user_id="unused",
        username="artist",
        role="user",
        workspace_id=workspace_id,
        membership_role="owner",
        session_id="session",
    )
    with pytest.raises(HTTPException) as raised:
        cancel_job_endpoint(record.id, actor=actor, database=database)
    assert raised.value.status_code == 409


def test_failed_job_retry_cancel_restores_failure_and_can_retry_again(database, monkeypatch):
    from src.apps.server import jobs as jobs_module

    record, _, workspace_id = _queued_job(database, monkeypatch, username="retry-activity")
    original_payload = {
        "script_id": "project-1",
        "asset_id": "asset-1",
        "asset_type": "character",
        "prompt": "preserve this request",
    }
    original_started_at = utc_now() - timedelta(seconds=8)
    original_finished_at = utc_now()
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.status = "failed"
        stored.payload = original_payload
        stored.progress = 17
        stored.error = "provider unavailable"
        stored.result = {"provider_request_id": "request-1"}
        stored.attempts = 1
        stored.started_at = original_started_at
        stored.finished_at = original_finished_at
        session.commit()
        session.refresh(stored)
        # SQLite test storage normalizes timezone-aware datetimes to the exact
        # naive values that the production snapshot code reads from the row.
        original_started_at = stored.started_at
        original_finished_at = stored.finished_at

    published: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        execute_job_task,
        "apply_async",
        lambda *_args, **kwargs: published.append((kwargs["args"], kwargs["task_id"])),
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke",
        lambda delivery_id, **_kwargs: revoked.append(delivery_id),
    )
    reservation_rollbacks: list[str] = []
    monkeypatch.setattr(
        jobs_module,
        "_restore_canceled_asset_reservation",
        lambda canceled: reservation_rollbacks.append(canceled.id),
    )

    outcome, retried = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)

    assert outcome is JobRetryOutcome.RETRIED
    assert retried is not None
    assert retried.id == record.id
    assert retried.status == "queued"
    assert retried.error is None
    assert retried.progress == 0
    assert retried.started_at is None
    assert retried.finished_at is None
    first_delivery_id = retried.queue_task_id
    assert first_delivery_id is not None
    assert first_delivery_id != record.id
    assert published == [([record.id, first_delivery_id], first_delivery_id)]
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.payload == original_payload
        assert {
            key: stored.retry_context[key]
            for key in (
                "error",
                "progress",
                "result",
                "started_at",
                "finished_at",
                "provider_progress",
            )
        } == {
            "error": "provider unavailable",
            "progress": 17,
            "result": {"provider_request_id": "request-1"},
            "started_at": original_started_at.isoformat(),
            "finished_at": original_finished_at.isoformat(),
            "provider_progress": None,
        }
        assert stored.retry_context["progress_stage"] == "queued"
        assert stored.retry_context["progress_is_estimated"] is True
        assert stored.retry_context["progress_steps"][0]["id"] == "queued"

    # A stale legacy delivery for the durable job id cannot claim this retry.
    assert process_job(record.id, database) is None
    assert (
        cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobCancellationOutcome.CANCELED
    )
    assert revoked == [first_delivery_id]
    assert reservation_rollbacks == []
    with database.session() as session:
        restored = session.get(GenerationJob, record.id)
        assert restored.status == "failed"
        assert restored.error == "provider unavailable"
        assert restored.progress == 17
        assert restored.result == {"provider_request_id": "request-1"}
        assert restored.payload == original_payload
        assert restored.progress_stage == "queued"
        assert restored.progress_is_estimated is True
        assert restored.provider_progress is None
        assert restored.progress_steps[0]["id"] == "queued"
        assert restored.started_at == original_started_at
        assert restored.finished_at == original_finished_at
        assert restored.retry_context is None

    second_outcome, second_retry = retry_workspace_job(
        database, workspace_id=workspace_id, job_id=record.id
    )
    assert second_outcome is JobRetryOutcome.RETRIED
    assert second_retry is not None
    second_delivery_id = second_retry.queue_task_id
    assert second_delivery_id is not None
    assert second_delivery_id not in {record.id, first_delivery_id}
    assert published[-1] == ([record.id, second_delivery_id], second_delivery_id)

    # A late delivery from the canceled retry is fenced off from the new retry.
    assert process_job(record.id, database, first_delivery_id) is None
    with database.session() as session:
        still_queued = session.get(GenerationJob, record.id)
        assert still_queued.status == "queued"
        assert still_queued.queue_task_id == second_delivery_id


def test_cancel_endpoint_returns_restored_failed_retry(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch, username="retry-endpoint")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.status = "failed"
        stored.error = "original provider failure"
        stored.progress = 23
        stored.finished_at = utc_now()
        session.commit()

    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke", lambda *_args, **_kwargs: None
    )
    outcome, _ = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert outcome is JobRetryOutcome.RETRIED
    actor = Actor(
        user_id=record.user_id,
        username="retry-endpoint",
        role="user",
        workspace_id=workspace_id,
        membership_role="owner",
        session_id="session",
    )

    response = cancel_job_endpoint(record.id, actor=actor, database=database)

    assert response["id"] == record.id
    assert response["status"] == "failed"
    assert response["error"] == "original provider failure"
    assert response["progress"] == 23


def test_retry_rejects_non_failed_and_full_workspace(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch, username="retry-guards")

    outcome, _ = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert outcome is JobRetryOutcome.NOT_FAILED

    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.status = "failed"
        stored.error = "failed"
        session.commit()
    monkeypatch.setenv("ENMOTION_MAX_ACTIVE_JOBS_PER_WORKSPACE", "1")
    create_job(
        database,
        workspace_id=workspace_id,
        user_id=record.user_id,
        job_type="project_asset",
        payload={"script_id": "active"},
    )

    outcome, _ = retry_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert outcome is JobRetryOutcome.CAPACITY


def test_job_activity_metadata_queue_position_and_dismissal(database, monkeypatch):
    record, _, workspace_id = _queued_job(database, monkeypatch, username="activity-metadata")
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.job_type = "playground"
        stored.payload = {
            "generation_id": record.id,
            "mode": "i2v",
            "prompt": "  a   concise video prompt  ",
            "activity_source": "playground",
        }
        session.commit()

    positions = queued_job_positions(database, job_ids=[record.id])
    activity = job_to_dict(
        get_workspace_job(database, workspace_id=workspace_id, job_id=record.id),
        queue_position=positions[record.id],
    )
    assert activity["category"] == "video"
    assert activity["source"] == "playground"
    assert activity["detail"] == "a concise video prompt"
    assert activity["queue_position"] == 1
    assert "payload" not in activity

    assert (
        dismiss_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobDismissalOutcome.ACTIVE
    )
    cancel_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
    assert (
        dismiss_workspace_job(database, workspace_id=workspace_id, job_id=record.id)
        is JobDismissalOutcome.DISMISSED
    )
    assert get_workspace_job(database, workspace_id=workspace_id, job_id=record.id) is None


def test_unpublished_reservation_is_not_claimed_or_republished(database, monkeypatch):
    user_id, workspace_id = _identity(database, "reserved-test")
    record = reserve_jobs(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        specs=[JobSpec("project_asset", {}, str(uuid.uuid4()))],
    )[0]
    published: list[str] = []
    monkeypatch.setattr(
        execute_job_task,
        "apply_async",
        lambda *_args, **kwargs: published.append(kwargs["task_id"]),
    )

    assert process_job(record.id, database) is None
    assert republish_queued_jobs(database) == 0
    assert published == []
    assert abandon_reserved_jobs(database, job_ids=[record.id]) == 1
    with database.session() as session:
        assert session.get(GenerationJob, record.id) is None


def test_batch_reservation_admission_is_atomic(database, monkeypatch):
    user_id, workspace_id = _identity(database, "batch-limit-test")
    monkeypatch.setenv("ENMOTION_MAX_ACTIVE_JOBS_PER_WORKSPACE", "1")
    with pytest.raises(JobLimitExceededError):
        reserve_jobs(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            specs=[
                JobSpec("video", {"task_id": str(uuid.uuid4())}),
                JobSpec("video", {"task_id": str(uuid.uuid4())}),
            ],
        )
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_oversized_job_payload_is_rejected_before_database_insert(database, monkeypatch):
    user_id, workspace_id = _identity(database, "payload-limit-test")
    monkeypatch.setenv("ENMOTION_MAX_JOB_PAYLOAD_BYTES", "128")

    with pytest.raises(JobPayloadTooLargeError, match="128-byte limit"):
        reserve_jobs(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            specs=[JobSpec("storyboard_render", {"composition_data": "x" * 256})],
        )

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_partial_batch_publish_failure_makes_every_job_terminal(database, monkeypatch):
    user_id, workspace_id = _identity(database, "partial-publish-test")
    records = reserve_jobs(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        specs=[
            JobSpec("video", {"task_id": str(uuid.uuid4())}),
            JobSpec("video", {"task_id": str(uuid.uuid4())}),
        ],
    )
    calls = 0

    def flaky_publish(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("redis failed mid-batch")

    revoked: list[str] = []
    monkeypatch.setattr(execute_job_task, "apply_async", flaky_publish)
    monkeypatch.setattr(
        "src.apps.server.jobs.celery_app.control.revoke",
        lambda job_id, **_kwargs: revoked.append(job_id),
    )
    with pytest.raises(JobQueueUnavailableError):
        publish_reserved_jobs(database, job_ids=[item.id for item in records])

    with database.session() as session:
        stored = list(
            session.scalars(select(GenerationJob).order_by(GenerationJob.created_at.asc()))
        )
        assert [item.status for item in stored] == ["failed", "failed"]
    assert set(revoked) == {item.id for item in records}


def test_worker_reconciles_actual_output_against_quota(database, monkeypatch, tmp_path):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", "1")
    monkeypatch.setenv("ENMOTION_MAX_JOB_OUTPUT_BYTES", "10")
    user_id, workspace_id = _identity(database, "output-limit-test")
    with database.session() as session:
        session.get(Workspace, workspace_id).storage_quota_bytes = 1000
        session.commit()
    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="project_asset",
        payload={},
    )

    def oversized_output(_claimed):
        output = Path(tmp_path, "workspaces", workspace_id, "output", "image.bin")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"x" * 20)
        return {"artifact": "image.bin"}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", oversized_output)
    with pytest.raises(StorageQuotaExceededError, match="per-job limit"):
        process_job(record.id, database)
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "failed"
        assert stored.error == "存储空间不足，请删除部分文件后重试。"
    assert not Path(tmp_path, "workspaces", workspace_id, "output", "image.bin").exists()


def test_worker_fails_job_when_actual_workspace_usage_crosses_quota(
    database, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", "1")
    monkeypatch.setenv("ENMOTION_MAX_JOB_OUTPUT_BYTES", "1000")
    user_id, workspace_id = _identity(database, "workspace-quota-test")
    with database.session() as session:
        session.get(Workspace, workspace_id).storage_quota_bytes = 15
        session.commit()
    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="project_asset",
        payload={},
    )

    def over_quota(_claimed):
        output = Path(tmp_path, "workspaces", workspace_id, "output", "image.bin")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"x" * 20)
        return {"artifact": "image.bin"}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", over_quota)
    with pytest.raises(StorageQuotaExceededError, match="workspace storage quota"):
        process_job(record.id, database)
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        assert stored.status == "failed"
        assert stored.error == "存储空间不足，请删除部分文件后重试。"
    assert not Path(tmp_path, "workspaces", workspace_id, "output", "image.bin").exists()


def test_worker_commits_replaced_media_deletion_only_after_reconcile(
    database, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", "1")
    monkeypatch.setenv("ENMOTION_MAX_JOB_OUTPUT_BYTES", "1000")
    user_id, workspace_id = _identity(database, "replace-media-test")
    output = tmp_path / "workspaces" / workspace_id / "output"
    old_media = output / "video" / "old.mp4"
    old_media.parent.mkdir(parents=True)
    old_media.write_bytes(b"old")
    (output / "projects.json").write_text(
        json.dumps([{"video_url": "video/old.mp4"}]), encoding="utf-8"
    )
    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="project_asset",
        payload={},
    )

    def replace(_claimed):
        new_media = output / "video" / "new.mp4"
        new_media.write_bytes(b"new")
        (output / "projects.json").write_text(
            json.dumps([{"video_url": "video/new.mp4"}]), encoding="utf-8"
        )
        assert old_media.exists()
        return {"ok": True}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", replace)
    process_job(record.id, database)

    assert not old_media.exists()
    assert (output / "video" / "new.mp4").exists()


def test_worker_stages_replaced_media_before_quota_reconcile(database, monkeypatch, tmp_path):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", "1")
    monkeypatch.setenv("ENMOTION_MAX_JOB_OUTPUT_BYTES", "1000")
    user_id, workspace_id = _identity(database, "replace-tight-quota-test")
    output = tmp_path / "workspaces" / workspace_id / "output"
    old_media = output / "video" / "old.mp4"
    new_media = output / "video" / "new.mp4"
    old_media.parent.mkdir(parents=True)
    old_media.write_bytes(b"o" * 128)
    projects_path = output / "projects.json"
    projects_path.write_text(json.dumps([{"video_url": "video/old.mp4"}]), encoding="utf-8")
    starting_usage = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    with database.session() as session:
        session.get(Workspace, workspace_id).storage_quota_bytes = starting_usage + 16
        session.commit()
    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="project_asset",
        payload={},
    )

    def replace(_claimed):
        new_media.write_bytes(b"n" * 128)
        projects_path.write_text(json.dumps([{"video_url": "video/new.mp4"}]), encoding="utf-8")
        return {"ok": True}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", replace)

    assert process_job(record.id, database) == {"ok": True}
    assert not old_media.exists()
    assert new_media.read_bytes() == b"n" * 128
    assert not (output.parent / ".trash").exists()


def test_worker_quota_rollback_preserves_overwritten_reference_target(
    database, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", "1")
    monkeypatch.setenv("ENMOTION_MAX_JOB_OUTPUT_BYTES", "1000")
    user_id, workspace_id = _identity(database, "replace-rollback-test")
    output = tmp_path / "workspaces" / workspace_id / "output"
    old_media = output / "video" / "old.mp4"
    old_media.parent.mkdir(parents=True)
    old_media.write_bytes(b"old")
    original_metadata = json.dumps([{"video_url": "video/old.mp4"}])
    (output / "projects.json").write_text(original_metadata, encoding="utf-8")
    starting_usage = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    with database.session() as session:
        session.get(Workspace, workspace_id).storage_quota_bytes = starting_usage + 1
        session.commit()
    monkeypatch.setattr(execute_job_task, "apply_async", lambda *_args, **_kwargs: None)
    record = create_job(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type="project_asset",
        payload={},
    )

    def replace(_claimed):
        (output / "video" / "new.mp4").write_bytes(b"new file over quota")
        (output / "projects.json").write_text(
            json.dumps([{"video_url": "video/new.mp4"}]), encoding="utf-8"
        )
        return {"ok": True}

    monkeypatch.setitem(JOB_HANDLERS, "project_asset", replace)
    with pytest.raises(StorageQuotaExceededError):
        process_job(record.id, database)

    assert old_media.exists()
    assert not (output / "video" / "new.mp4").exists()
    assert (output / "projects.json").read_text(encoding="utf-8") == original_metadata


def test_unconfirmed_publication_is_republished_once(database, monkeypatch):
    user_id, workspace_id = _identity(database, "unconfirmed-test")
    record = reserve_jobs(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        specs=[JobSpec("project_asset", {}, str(uuid.uuid4()))],
    )[0]
    with database.session() as session:
        session.get(GenerationJob, record.id).queue_task_id = QUEUE_PUBLICATION_PENDING
        session.commit()
    published: list[str] = []
    monkeypatch.setattr(
        execute_job_task,
        "apply_async",
        lambda *_args, **kwargs: published.append(kwargs["task_id"]),
    )

    assert republish_unconfirmed_jobs(database) == 1
    assert republish_unconfirmed_jobs(database) == 0
    assert published == [record.id]
    with database.session() as session:
        assert session.get(GenerationJob, record.id).queue_task_id == record.id


def test_retry_delivery_token_is_preserved_by_republish_and_early_claim(database, monkeypatch):
    record, _, _ = _queued_job(database, monkeypatch, username="retry-republish")
    delivery_id = str(uuid.uuid4())
    pending_marker = f"{QUEUE_PUBLICATION_PENDING}:{delivery_id}"
    with database.session() as session:
        stored = session.get(GenerationJob, record.id)
        stored.queue_task_id = pending_marker
        session.commit()

    published: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        execute_job_task,
        "apply_async",
        lambda *_args, **kwargs: published.append((kwargs["args"], kwargs["task_id"])),
    )

    assert republish_unconfirmed_jobs(database) == 1
    assert published == [([record.id, delivery_id], delivery_id)]
    with database.session() as session:
        assert session.get(GenerationJob, record.id).queue_task_id == delivery_id

    assert republish_queued_jobs(database) == 1
    assert published[-1] == ([record.id, delivery_id], delivery_id)

    # The same delivery is also allowed to claim directly from the encoded
    # marker if it reaches a worker before API-side publication confirmation.
    second, _, _ = _queued_job(database, monkeypatch, username="retry-early-claim")
    second_delivery_id = str(uuid.uuid4())
    with database.session() as session:
        stored = session.get(GenerationJob, second.id)
        stored.queue_task_id = f"{QUEUE_PUBLICATION_PENDING}:{second_delivery_id}"
        session.commit()
    monkeypatch.setitem(JOB_HANDLERS, "project_asset", lambda _claimed: {"ok": True})

    assert process_job(second.id, database, second_delivery_id) == {"ok": True}
    with database.session() as session:
        claimed = session.get(GenerationJob, second.id)
        assert claimed.status == "completed"
        assert claimed.queue_task_id == second_delivery_id


def test_stale_video_reservation_cleans_pending_workspace_task(database, monkeypatch, tmp_path):
    from src.apps.server import jobs as jobs_module

    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))
    registry = WorkspacePipelineRegistry(str(workspace_root))
    monkeypatch.setattr(jobs_module, "_worker_pipelines", registry)
    monkeypatch.setattr(
        jobs_module,
        "_worker_playgrounds",
        WorkspacePlaygroundRegistry(registry),
    )
    user_id, workspace_id = _identity(database, "stale-video-test")
    task_id = str(uuid.uuid4())
    output_root = registry.output_root_for(workspace_id)
    pipeline = ComicGenPipeline({"output_root": str(output_root), "recover_orphan_tasks": False})
    pipeline.scripts["project-1"] = Script(
        id="project-1",
        title="Stale reservation",
        original_text="test",
        video_tasks=[
            VideoTask(
                id=task_id,
                project_id="project-1",
                image_url="https://example.invalid/image.png",
                prompt="Animate",
            )
        ],
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline._save_data()
    reserve_jobs(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        specs=[
            JobSpec(
                "video",
                {"script_id": "project-1", "task_id": task_id},
                task_id,
            )
        ],
    )

    assert recover_stale_reservations(database, max_age_seconds=0) == 1
    registry.discard(workspace_id)
    recovered_pipeline = registry.get(workspace_id)
    recovered_tasks = recovered_pipeline.scripts["project-1"].video_tasks
    assert len(recovered_tasks) == 1
    assert recovered_tasks[0].status == "failed"
    assert recovered_tasks[0].error_code == "video_queue_unavailable"
    assert "进入生成队列前中断" in recovered_tasks[0].error_diagnostic
    with database.session() as session:
        stored = session.get(GenerationJob, task_id)
        assert stored.status == "failed"
        assert stored.error == VIDEO_QUEUE_UNAVAILABLE_MESSAGE
        assert stored.result["error_code"] == "video_queue_unavailable"
        assert "进入生成队列前中断" in stored.result["error_diagnostic"]


def test_series_worker_forgets_its_transient_task(monkeypatch):
    from src.apps.server import jobs as jobs_module

    class FakePipeline:
        forgotten: list[str] = []

        def generate_series_asset(self, *_args, **_kwargs):
            return object(), "transient-task"

        def process_asset_generation_task(self, task_id):
            assert task_id == "transient-task"

        def get_asset_generation_task_status(self, task_id):
            assert task_id == "transient-task"
            return {"status": "completed"}

        def forget_asset_generation_task(self, task_id):
            self.forgotten.append(task_id)

    pipeline = FakePipeline()

    @contextmanager
    def locked(_workspace_id):
        yield pipeline

    monkeypatch.setattr(jobs_module._worker_pipelines, "locked", locked)
    result = jobs_module._series_asset(
        jobs_module.ClaimedJob(
            id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            job_type="series_asset",
            payload={
                "series_id": "series-1",
                "asset_id": "asset-1",
                "asset_type": "character",
            },
        )
    )

    assert result == {"series_id": "series-1", "asset_id": "asset-1"}
    assert pipeline.forgotten == ["transient-task"]


def test_global_asset_worker_processes_and_forgets_its_transient_task(monkeypatch):
    from src.apps.server import jobs as jobs_module

    class FakePipeline:
        forgotten: list[str] = []
        generated: list[tuple] = []

        def generate_global_asset(self, *args, **kwargs):
            self.generated.append((args, kwargs))
            return object(), "global-transient-task"

        def process_asset_generation_task(self, task_id):
            assert task_id == "global-transient-task"

        def get_asset_generation_task_status(self, task_id):
            assert task_id == "global-transient-task"
            return {"status": "completed"}

        def forget_asset_generation_task(self, task_id):
            self.forgotten.append(task_id)

    pipeline = FakePipeline()

    @contextmanager
    def locked(_workspace_id):
        yield pipeline

    monkeypatch.setattr(jobs_module._worker_pipelines, "locked", locked)
    result = jobs_module._global_asset(
        jobs_module.ClaimedJob(
            id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            job_type="global_asset",
            payload={
                "source_id": "global",
                "asset_id": "asset-1",
                "asset_type": "scene",
                "prompt": "A quiet plaza",
                "aspect_ratio": "16:9",
            },
        )
    )

    assert result == {"source_id": "global", "asset_id": "asset-1"}
    assert pipeline.forgotten == ["global-transient-task"]
    args, _ = pipeline.generated[0]
    assert args[0:2] == ("asset-1", "scene")
    assert args[6] == "A quiet plaza"
    assert args[11] == "16:9"
    assert JOB_HANDLERS["global_asset"] is jobs_module._global_asset
