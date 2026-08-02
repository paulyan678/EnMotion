"""Durable generation jobs backed by PostgreSQL and a Celery/Redis queue."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from celery import Celery
from sqlalchemy import delete, func, or_, select

from ...models.newapi import (
    INPUT_IMAGE_PRIVACY_ERROR_CODE,
    INPUT_IMAGE_PRIVACY_PROVIDER_CODE,
    INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
    NewAPIProviderError,
)
from ...utils.generation_progress import (
    bind_generation_progress,
    reset_generation_progress,
)
from ...utils.newapi_models import get_model_spec, redact_newapi_secrets
from ..comic_gen.video_failures import (
    VIDEO_FAILURE_CODE,
    VIDEO_FAILURE_MESSAGE,
    VIDEO_INTERRUPTED_CODE,
    VIDEO_INTERRUPTED_MESSAGE,
    VIDEO_QUEUE_UNAVAILABLE_CODE,
    VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
    VideoFailure,
    classify_video_failure,
)
from ..generation_contract import compile_generation_request, provider_request
from ..web_runtime.context import bind_tenant, reset_tenant
from ..web_runtime.file_lock import interprocess_lock
from ..web_runtime.pipeline_registry import WorkspacePipelineRegistry
from ..web_runtime.playground_registry import WorkspacePlaygroundRegistry
from .database import Database, get_database
from .models import GenerationJob, Workspace, utc_now
from .quotas import (
    StorageQuotaExceededError,
    workspace_output_root,
    workspace_usage_bytes,
)
from .workspace_storage import (
    bind_workspace_mutation,
    commit_workspace_mutation,
    defer_unreferenced_workspace_media,
    remove_new_workspace_files,
    reset_workspace_mutation,
    restore_workspace_file_deletions,
    restore_workspace_metadata,
    snapshot_workspace_files,
    snapshot_workspace_metadata,
    stage_workspace_file_deletions,
)

EXECUTE_TASK_NAME = "enmotion.jobs.execute"
QUEUE_NAME = os.getenv("ENMOTION_QUEUE_NAME", "enmotion-generation")
QUEUE_PUBLICATION_PENDING = "pending-publication"
QUEUE_PUBLICATION_PENDING_PREFIX = f"{QUEUE_PUBLICATION_PENDING}:"
TERMINAL_JOB_STATUSES = ("completed", "failed", "canceled")
TERMINAL_OUTBOX_VERSION = 1
SUPPORTED_JOB_TYPES = {
    "project_asset",
    "series_asset",
    "global_asset",
    "motion_reference",
    "video",
    "playground",
    "project_assets_batch",
    "refine_batch",
    "generate_storyboard",
    "generate_video",
    "storyboard_render",
    "merge",
    "export",
    "dub_preview",
}

logger = logging.getLogger(__name__)

GENERATION_JOB_FAILED_MESSAGE = "生成任务失败，请稍后重试。"
STORYBOARD_JOB_FAILED_MESSAGE = "分镜生成失败，请稍后重试。"
PLAYGROUND_JOB_FAILED_MESSAGE = "生成失败，请稍后重试。"
ASSEMBLY_INPUTS_CHANGED_CODE = "assembly_inputs_changed"


def _is_assembly_mutation_conflict(exc: BaseException) -> bool:
    # Imported lazily to avoid coupling worker module initialization to the
    # desktop pipeline's provider stack.
    from ..comic_gen.pipeline import AssemblyMutationConflictError

    return isinstance(exc, AssemblyMutationConflictError)


def _public_job_failure(exc: BaseException, fallback: str) -> str:
    """Keep provider/runtime diagnostics out of durable UI-facing records."""

    if isinstance(exc, NewAPIProviderError):
        return str(exc)
    if isinstance(exc, StorageQuotaExceededError):
        return "存储空间不足，请删除部分文件后重试。"
    if _is_assembly_mutation_conflict(exc):
        return str(exc)
    return fallback


_STAGE_PROGRESS = {
    "queued": 0,
    "validating_request": 10,
    "preparing_inputs": 20,
    "submitted_to_provider": 30,
    "accepted_by_provider": 36,
    "provider_processing": 40,
    "downloading_output": 78,
    "persisting_media": 88,
    "finalizing": 95,
    "completed": 100,
}


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


celery_app = Celery(
    "enmotion",
    broker=os.getenv(
        "ENMOTION_QUEUE_REDIS_URL", os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    ),
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue=QUEUE_NAME,
    broker_connection_retry_on_startup=True,
    task_always_eager=_as_bool(os.getenv("ENMOTION_JOBS_EAGER")),
    task_eager_propagates=True,
)


class UnsupportedJobTypeError(ValueError):
    pass


class JobQueueUnavailableError(RuntimeError):
    pass


class JobLimitExceededError(RuntimeError):
    pass


class JobPayloadTooLargeError(ValueError):
    pass


class TerminalStatePersistenceError(RuntimeError):
    """The handler finished, but its durable database state is still pending."""

    pass


class JobCancellationOutcome(str, Enum):
    CANCELED = "canceled"
    NOT_FOUND = "not_found"
    RUNNING = "running"
    FINISHED = "finished"


class JobRetryOutcome(str, Enum):
    RETRIED = "retried"
    NOT_FOUND = "not_found"
    NOT_FAILED = "not_failed"
    CAPACITY = "capacity"


class JobDismissalOutcome(str, Enum):
    DISMISSED = "dismissed"
    NOT_FOUND = "not_found"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_type: str
    payload: dict[str, Any]
    job_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    workspace_id: str
    user_id: str
    job_type: str
    payload: dict[str, Any]
    attempts: int = 1


_worker_pipelines = WorkspacePipelineRegistry()
_worker_playgrounds = WorkspacePlaygroundRegistry(_worker_pipelines)


def _validate_job_id(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Job id must be a UUID") from exc
    return str(parsed)


def _pending_delivery_marker(job_id: str, delivery_id: str) -> str:
    """Encode an unpublished delivery without changing legacy fresh-job rows."""

    return (
        QUEUE_PUBLICATION_PENDING
        if delivery_id == job_id
        else f"{QUEUE_PUBLICATION_PENDING_PREFIX}{delivery_id}"
    )


def _delivery_id_from_queue_state(job_id: str, queue_task_id: str) -> str:
    if queue_task_id == QUEUE_PUBLICATION_PENDING:
        return job_id
    if queue_task_id.startswith(QUEUE_PUBLICATION_PENDING_PREFIX):
        return queue_task_id[len(QUEUE_PUBLICATION_PENDING_PREFIX) :]
    return queue_task_id


def _is_pending_delivery(queue_task_id: str) -> bool:
    return queue_task_id == QUEUE_PUBLICATION_PENDING or queue_task_id.startswith(
        QUEUE_PUBLICATION_PENDING_PREFIX
    )


def _publish_delivery(job_id: str, delivery_id: str) -> None:
    args = [job_id] if delivery_id == job_id else [job_id, delivery_id]
    execute_job_task.apply_async(args=args, task_id=delivery_id, queue=QUEUE_NAME)


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def job_payload_limit_bytes() -> int:
    """Maximum serialized JSON stored in one durable job row."""

    return _positive_env_int("ENMOTION_MAX_JOB_PAYLOAD_BYTES", 256 * 1024)


def validate_job_payload(payload: dict[str, Any]) -> int:
    """Reject non-JSON or oversized payloads before opening a DB transaction."""

    if not isinstance(payload, dict):
        raise ValueError("Generation job payload must be a JSON object")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Generation job payload must be JSON serializable") from exc
    limit = job_payload_limit_bytes()
    if len(encoded) > limit:
        raise JobPayloadTooLargeError(f"Generation job payload exceeds the {limit}-byte limit")
    return len(encoded)


def job_storage_reservation_bytes(job_type: str, payload: dict[str, Any]) -> int:
    """Return a conservative, bounded reservation for one provider job."""

    base = _positive_env_int("ENMOTION_JOB_STORAGE_RESERVATION_BYTES", 512 * 1024 * 1024)
    if job_type == "project_assets_batch":
        return _positive_env_int(
            "ENMOTION_BATCH_JOB_STORAGE_RESERVATION_BYTES", 4 * 1024 * 1024 * 1024
        )
    if job_type in {"generate_storyboard", "generate_video", "merge", "export"}:
        return _positive_env_int(
            "ENMOTION_LONG_MEDIA_JOB_STORAGE_RESERVATION_BYTES",
            4 * 1024 * 1024 * 1024,
        )
    if job_type in {"video", "motion_reference", "dub_preview"}:
        base = max(
            base,
            _positive_env_int("ENMOTION_VIDEO_JOB_STORAGE_RESERVATION_BYTES", 1024 * 1024 * 1024),
        )
    try:
        batch_size = max(1, min(4, int(payload.get("batch_size", 1))))
    except (TypeError, ValueError):
        batch_size = 1
    return min(
        base * batch_size,
        _positive_env_int("ENMOTION_MAX_JOB_STORAGE_RESERVATION_BYTES", 4 * 1024 * 1024 * 1024),
    )


def _durable_activity_payload(job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist normalized source-navigation metadata with every new job."""

    normalized = dict(payload)
    if "activity_source" not in normalized:
        normalized["activity_source"] = (
            "playground"
            if job_type == "playground"
            else "library" if job_type in {"series_asset", "global_asset"} else "workspace"
        )
    if "source_route" not in normalized:
        source = normalized["activity_source"]
        series_id = normalized.get("series_id")
        script_id = normalized.get("script_id")
        if source == "playground":
            normalized["source_route"] = "#/playground"
        elif source == "library":
            normalized["source_route"] = "#/library"
        elif series_id and script_id:
            normalized["source_route"] = f"#/series/{series_id}/episode/{script_id}"
        elif series_id:
            normalized["source_route"] = f"#/series/{series_id}"
        elif script_id:
            normalized["source_route"] = f"#/project/{script_id}"
        else:
            normalized["source_route"] = "#/"
    if not isinstance(normalized.get("compiled_request"), dict):
        prompt = normalized.get("prompt")
        model = next(
            (
                normalized.get(key)
                for key in ("model", "model_name", "model_id")
                if isinstance(normalized.get(key), str) and normalized.get(key).strip()
            ),
            None,
        )
        if isinstance(prompt, str) and prompt.strip() and isinstance(model, str):
            category = _job_activity_category(job_type, normalized)
            if category in {"image", "video"}:
                parameters = _public_parameters(normalized)
                normalized["compiled_request"] = compile_generation_request(
                    category=category,
                    mode=str(normalized.get("mode") or normalized.get("generation_mode") or job_type),
                    source=str(normalized["activity_source"]),
                    user_prompt=prompt,
                    requests=[
                        provider_request(
                            phase=category,
                            model=model,
                            prompt=prompt,
                            negative_prompt=normalized.get("negative_prompt"),
                            parameters=parameters,
                            input_media=[
                                value
                                for key in ("image_url", "source_image_url", "reference_image_url")
                                if isinstance((value := normalized.get(key)), str)
                            ]
                            + list(normalized.get("input_media") or []),
                        )
                    ],
                    target={"job_type": job_type},
                    prompt_parts=[
                        {
                            "kind": "user",
                            "label": "User prompt",
                            "text": prompt.strip(),
                            "editable": True,
                        }
                    ],
                )
    return normalized


def reserve_jobs(
    db: Database,
    *,
    workspace_id: str,
    user_id: str,
    specs: Sequence[JobSpec],
) -> list[GenerationJob]:
    """Atomically admit jobs without making them visible to queue workers.

    A reserved row is ``queued`` with a null ``queue_task_id``.  Claims and
    startup republishing both ignore that state until ``publish_reserved_jobs``
    marks the complete reservation set ready.  This makes batch admission
    all-or-nothing and lets API handlers safely build their workspace task
    records after capacity has been secured.
    """

    if not specs:
        raise ValueError("At least one job is required")
    request_limit = _positive_env_int("ENMOTION_MAX_JOBS_PER_REQUEST", 10)
    if len(specs) > request_limit:
        raise JobLimitExceededError(f"A single request may create at most {request_limit} jobs")
    _worker_pipelines.validate_workspace_id(workspace_id)

    records: list[GenerationJob] = []
    identifiers: set[str] = set()
    for spec in specs:
        if spec.job_type not in SUPPORTED_JOB_TYPES:
            raise UnsupportedJobTypeError(f"Unsupported generation job: {spec.job_type}")
        validate_job_payload(spec.payload)
        identifier = _validate_job_id(spec.job_id) if spec.job_id else str(uuid.uuid4())
        if identifier in identifiers:
            raise ValueError("Duplicate job id in one reservation")
        identifiers.add(identifier)
        payload = _durable_activity_payload(spec.job_type, spec.payload)
        records.append(
            GenerationJob(
                id=identifier,
                workspace_id=workspace_id,
                user_id=user_id,
                job_type=spec.job_type,
                payload=payload,
                status="queued",
                progress=0,
                progress_stage="queued",
                progress_is_estimated=True,
                progress_steps=[
                    {
                        "id": "queued",
                        "state": "active",
                        "started_at": utc_now().isoformat(),
                        "finished_at": None,
                        "message": "正在等待可用的处理服务",
                    }
                ],
                queue_task_id=None,
            )
        )

    with db.session() as session:
        workspace = session.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        )
        if workspace is None:
            raise ValueError("Workspace not found")
        active_records = list(
            session.scalars(
                select(GenerationJob).where(
                    GenerationJob.workspace_id == workspace_id,
                    GenerationJob.status.in_(("queued", "running")),
                )
            )
        )
        active_limit = _positive_env_int("ENMOTION_MAX_ACTIVE_JOBS_PER_WORKSPACE", 10)
        if len(active_records) + len(records) > active_limit:
            raise JobLimitExceededError(
                f"Workspace may have at most {active_limit} queued or running jobs"
            )

        daily_limit = _positive_env_int("ENMOTION_MAX_DAILY_JOBS_PER_WORKSPACE", 100)
        daily_jobs = (
            session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.workspace_id == workspace_id,
                    GenerationJob.created_at >= utc_now() - timedelta(hours=24),
                )
            )
            or 0
        )
        if daily_jobs + len(records) > daily_limit:
            raise JobLimitExceededError(
                f"Workspace reached its {daily_limit}-job rolling daily limit"
            )

        active_reservation = sum(
            job_storage_reservation_bytes(item.job_type, dict(item.payload or {}))
            for item in active_records
        )
        requested_reservation = sum(
            job_storage_reservation_bytes(item.job_type, dict(item.payload or {}))
            for item in records
        )
        usage = workspace_usage_bytes(workspace_id)
        if usage + active_reservation + requested_reservation > workspace.storage_quota_bytes:
            raise StorageQuotaExceededError(
                "Workspace does not have enough storage available for these generation jobs"
            )
        session.add_all(records)
        session.commit()
        for record in records:
            session.expunge(record)
    return records


def abandon_reserved_jobs(db: Database, *, job_ids: Sequence[str]) -> int:
    """Delete setup reservations that were never published."""

    identifiers = [_validate_job_id(value) for value in job_ids]
    removed = 0
    with db.session() as session:
        records = list(
            session.scalars(
                select(GenerationJob).where(GenerationJob.id.in_(identifiers)).with_for_update()
            )
        )
        for record in records:
            if record.status == "queued" and record.queue_task_id is None:
                session.delete(record)
                removed += 1
        session.commit()
    return removed


def publish_reserved_jobs(
    db: Database,
    *,
    job_ids: Sequence[str],
    delivery_ids: Mapping[str, str] | None = None,
) -> list[GenerationJob]:
    """Mark a complete reservation set ready, then publish every queue item."""

    identifiers = [_validate_job_id(value) for value in job_ids]
    supplied_deliveries = {
        _validate_job_id(job_id): _validate_job_id(delivery_id)
        for job_id, delivery_id in (delivery_ids or {}).items()
    }
    if set(supplied_deliveries) - set(identifiers):
        raise ValueError("Delivery id mapping contains an unknown job id")
    deliveries = {
        identifier: supplied_deliveries.get(identifier, identifier) for identifier in identifiers
    }
    pending_markers = {
        identifier: _pending_delivery_marker(identifier, deliveries[identifier])
        for identifier in identifiers
    }
    with db.session() as session:
        records = list(
            session.scalars(
                select(GenerationJob).where(GenerationJob.id.in_(identifiers)).with_for_update()
            )
        )
        by_id = {record.id: record for record in records}
        if len(by_id) != len(identifiers) or any(
            by_id[value].status != "queued" or by_id[value].queue_task_id is not None
            for value in identifiers
        ):
            raise ValueError("Job reservation is missing or is no longer publishable")
        for identifier in identifiers:
            by_id[identifier].queue_task_id = pending_markers[identifier]
            by_id[identifier].updated_at = utc_now()
        session.commit()

    try:
        for identifier in identifiers:
            _publish_delivery(identifier, deliveries[identifier])
    except Exception as exc:
        # The API holds the workspace file lock while publishing, and workers
        # acquire that lock before claiming.  We can therefore revoke the
        # already-published subset and make every still-queued row terminal
        # before any handler begins mutating workspace state.
        for identifier in identifiers:
            try:
                celery_app.control.revoke(deliveries[identifier], terminate=False)
            except Exception:
                pass
        with db.session() as session:
            failed = list(
                session.scalars(
                    select(GenerationJob).where(GenerationJob.id.in_(identifiers)).with_for_update()
                )
            )
            now = utc_now()
            for record in failed:
                if record.status == "queued":
                    record.status = "failed"
                    record.error = "生成队列暂时不可用，请稍后重试。"
                    record.retry_context = None
                    record.finished_at = now
                    record.updated_at = now
                    _mark_progress_terminal(
                        record,
                        status="failed",
                        message=record.error,
                        now=now,
                    )
            session.commit()
        raise JobQueueUnavailableError("生成队列暂时不可用") from exc

    with db.session() as session:
        published = list(
            session.scalars(
                select(GenerationJob).where(GenerationJob.id.in_(identifiers)).with_for_update()
            )
        )
        now = utc_now()
        for record in published:
            if record.status == "queued" and record.queue_task_id == pending_markers[record.id]:
                record.queue_task_id = deliveries[record.id]
                record.updated_at = now
        session.commit()

    return [
        record
        for identifier in identifiers
        if (record := get_job_by_id(db, identifier)) is not None
    ]


def get_job_by_id(db: Database, job_id: str) -> GenerationJob | None:
    with db.session() as session:
        record = session.get(GenerationJob, _validate_job_id(job_id))
        if record is not None:
            session.expunge(record)
        return record


def create_job(
    db: Database,
    *,
    workspace_id: str,
    user_id: str,
    job_type: str,
    payload: dict[str, Any],
    job_id: str | None = None,
) -> GenerationJob:
    """Persist and publish a job, returning only after both steps succeed."""

    try:
        reserved = reserve_jobs(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            specs=[JobSpec(job_type=job_type, payload=payload, job_id=job_id)],
        )
        return publish_reserved_jobs(db, job_ids=[reserved[0].id])[0]
    except Exception:
        # Validation/admission errors occur before a row is inserted.  A setup
        # failure after reservation is safely removable because it was never
        # visible to a worker.
        if "reserved" in locals():
            abandon_reserved_jobs(db, job_ids=[reserved[0].id])
        raise


def enqueue_job(
    *,
    workspace_id: str,
    user_id: str,
    job_type: str,
    payload: dict[str, Any],
    job_id: str | None = None,
) -> GenerationJob:
    return create_job(
        get_database(),
        workspace_id=workspace_id,
        user_id=user_id,
        job_type=job_type,
        payload=payload,
        job_id=job_id,
    )


def get_workspace_job(db: Database, *, workspace_id: str, job_id: str) -> GenerationJob | None:
    with db.session() as session:
        record = session.scalar(
            select(GenerationJob).where(
                GenerationJob.id == job_id,
                GenerationJob.workspace_id == workspace_id,
            )
        )
        if record is not None:
            session.expunge(record)
        return record


def list_workspace_jobs(db: Database, *, workspace_id: str, limit: int = 50) -> list[GenerationJob]:
    safe_limit = max(1, min(limit, 200))
    with db.session() as session:
        records = list(
            session.scalars(
                select(GenerationJob)
                .where(GenerationJob.workspace_id == workspace_id)
                .order_by(GenerationJob.created_at.desc())
                .limit(safe_limit)
            )
        )
        for record in records:
            session.expunge(record)
        return records


def queued_job_positions(db: Database, *, job_ids: Sequence[str]) -> dict[str, int]:
    """Return global FIFO positions for the requested published queue rows.

    Only numeric positions are exposed, never another workspace's job data.
    Unpublished reservations are intentionally omitted because they are not
    yet eligible for worker execution.
    """

    requested = {_validate_job_id(value) for value in job_ids}
    if not requested:
        return {}
    with db.session() as session:
        queued_ids = list(
            session.scalars(
                select(GenerationJob.id)
                .where(
                    GenerationJob.status == "queued",
                    GenerationJob.queue_task_id.is_not(None),
                )
                .order_by(GenerationJob.created_at.asc(), GenerationJob.id.asc())
            )
        )
    return {
        identifier: position
        for position, identifier in enumerate(queued_ids, start=1)
        if identifier in requested
    }


def _asset_reservation_payload_is_complete(job_type: str, payload: dict[str, Any]) -> bool:
    """Return whether a durable asset row identifies its reserved asset."""

    if job_type == "project_asset":
        owner_key = "script_id"
    elif job_type == "series_asset":
        owner_key = "series_id"
    elif job_type == "global_asset":
        owner_key = "source_id"
    else:
        return False
    return all(payload.get(key) for key in (owner_key, "asset_id", "asset_type"))


def _fail_asset_reservation(record: GenerationJob | ClaimedJob) -> bool:
    """Mark a still-PROCESSING durable asset reservation as failed."""

    payload = dict(record.payload or {})
    if not _asset_reservation_payload_is_complete(record.job_type, payload):
        return False
    pipeline = _worker_pipelines.get(record.workspace_id)
    if record.job_type == "project_asset":
        return pipeline.fail_orphaned_asset_reservation(
            payload["script_id"], payload["asset_id"], payload["asset_type"]
        )
    source_kind = "series" if record.job_type == "series_asset" else "global"
    source_id = payload["series_id"] if source_kind == "series" else payload["source_id"]
    return pipeline.fail_orphaned_source_asset_reservation(
        source_kind,
        source_id,
        payload["asset_id"],
        payload["asset_type"],
    )


def _restore_canceled_asset_reservation(record: GenerationJob) -> str | None:
    """Restore a queued asset's status without clobbering later mutations.

    New submissions persist ``previous_asset_status`` before changing the
    asset to PROCESSING. Legacy queue rows do not have that field, so their
    safest recovery is FAILED: the asset becomes retryable instead of being
    left behind with a permanent processing spinner.
    """

    payload = dict(record.payload or {})
    if not _asset_reservation_payload_is_complete(record.job_type, payload):
        return None
    token = bind_tenant(record.user_id, record.workspace_id, "worker")
    try:
        with interprocess_lock(_worker_pipelines.lock_path_for(record.workspace_id)):
            pipeline = _worker_pipelines.get(record.workspace_id)
            previous_status = payload.get("previous_asset_status")
            restored = False
            if previous_status is not None:
                try:
                    if record.job_type == "project_asset":
                        restored = pipeline.restore_asset_reservation(
                            payload["script_id"],
                            payload["asset_id"],
                            payload["asset_type"],
                            previous_status,
                        )
                    else:
                        source_kind = "series" if record.job_type == "series_asset" else "global"
                        source_id = (
                            payload["series_id"]
                            if source_kind == "series"
                            else payload["source_id"]
                        )
                        restored = pipeline.restore_source_asset_reservation(
                            source_kind,
                            source_id,
                            payload["asset_id"],
                            payload["asset_type"],
                            previous_status,
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not restore canceled asset reservation %s: %s",
                        record.id,
                        exc,
                    )
            if not restored:
                _fail_asset_reservation(record)
    except Exception as exc:
        return str(exc)[:1000]
    finally:
        reset_tenant(token)
    return None


def _failed_retry_snapshot(record: GenerationJob) -> dict[str, Any]:
    """Capture the failed state that cancellation of a queued retry restores."""

    return {
        "error": record.error,
        "progress": record.progress,
        "result": record.result,
        "progress_stage": record.progress_stage,
        "progress_is_estimated": record.progress_is_estimated,
        "provider_progress": record.provider_progress,
        "progress_steps": record.progress_steps,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


def _snapshot_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Retry timestamp must be an ISO-8601 string")
    return datetime.fromisoformat(value)


def _restore_failed_retry_snapshot(record: GenerationJob, snapshot: dict[str, Any]) -> None:
    progress = snapshot.get("progress", 0)
    if isinstance(progress, bool) or not isinstance(progress, int):
        raise ValueError("Retry progress must be an integer")
    result = snapshot.get("result")
    if result is not None and not isinstance(result, dict):
        raise ValueError("Retry result must be an object")
    error = snapshot.get("error")
    if error is not None and not isinstance(error, str):
        raise ValueError("Retry error must be text")

    record.status = "failed"
    record.progress = max(0, min(100, progress))
    record.result = result
    record.error = error
    record.progress_stage = snapshot.get("progress_stage")
    record.progress_is_estimated = bool(snapshot.get("progress_is_estimated", True))
    provider_progress = snapshot.get("provider_progress")
    record.provider_progress = (
        max(0, min(100, provider_progress))
        if isinstance(provider_progress, int) and not isinstance(provider_progress, bool)
        else None
    )
    steps = snapshot.get("progress_steps")
    record.progress_steps = steps if isinstance(steps, list) else None
    record.started_at = _snapshot_datetime(snapshot.get("started_at"))
    record.finished_at = _snapshot_datetime(snapshot.get("finished_at"))
    record.retry_context = None
    record.updated_at = utc_now()


def cancel_workspace_job_with_record(
    db: Database,
    *,
    workspace_id: str,
    job_id: str,
) -> tuple[JobCancellationOutcome, GenerationJob | None]:
    """Cancel queued work and return the exact state committed by this action."""

    restored_failed_retry = False
    with db.session() as session:
        record = session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None:
            return JobCancellationOutcome.NOT_FOUND, None
        if record.status == "running":
            # Provider calls are synchronous and cannot be safely interrupted.
            # Claiming cancellation here would make the UI lie while the
            # handler continued writing and potentially charging the account.
            return JobCancellationOutcome.RUNNING, None
        if record.status in {"completed", "failed", "canceled"}:
            return JobCancellationOutcome.FINISHED, None
        retry_context = dict(record.retry_context or {})
        if retry_context:
            _restore_failed_retry_snapshot(record, retry_context)
            restored_failed_retry = True
        else:
            record.status = "canceled"
            record.error = "已由用户取消"
            record.finished_at = utc_now()
            _mark_progress_terminal(
                record,
                status="canceled",
                message=record.error,
                now=record.finished_at,
            )
        session.commit()
        session.expunge(record)
    if record.job_type == "video":
        if restored_failed_retry:
            result = dict(record.result or {})
            _sync_video_task_failure(
                workspace_id=record.workspace_id,
                user_id=record.user_id,
                payload=dict(record.payload or {}),
                failure=VideoFailure(
                    record.error or VIDEO_FAILURE_MESSAGE,
                    str(result.get("error_code") or VIDEO_FAILURE_CODE),
                    str(result.get("error_diagnostic") or record.error or ""),
                ),
            )
        else:
            _sync_video_task_canceled(
                workspace_id=record.workspace_id,
                user_id=record.user_id,
                payload=dict(record.payload or {}),
            )
    if not restored_failed_retry:
        cleanup_error = _restore_canceled_asset_reservation(record)
        if cleanup_error:
            logger.error(
                "Canceled job %s but could not restore its asset reservation: %s",
                record.id,
                cleanup_error,
            )
    # This prevents a queued task from starting. A provider call already in
    # progress is deliberately not force-killed because that can corrupt media.
    if record.queue_task_id:
        try:
            celery_app.control.revoke(
                _delivery_id_from_queue_state(record.id, record.queue_task_id),
                terminate=False,
            )
        except Exception:
            # PostgreSQL is authoritative; a later delivery sees the canceled
            # row and `_claim_job` refuses it even when Redis is unavailable.
            pass
    return JobCancellationOutcome.CANCELED, record


def cancel_workspace_job(db: Database, *, workspace_id: str, job_id: str) -> JobCancellationOutcome:
    outcome, _record = cancel_workspace_job_with_record(
        db,
        workspace_id=workspace_id,
        job_id=job_id,
    )
    return outcome


def retry_workspace_job(
    db: Database, *, workspace_id: str, job_id: str
) -> tuple[JobRetryOutcome, GenerationJob | None]:
    """Requeue one failed durable operation using its original safe payload.

    Reusing the durable id is important for Playground and video jobs because
    their workspace-side task record uses the same id. The worker handlers are
    retry-aware and reset those task records back to processing when claimed.
    """

    identifier = _validate_job_id(job_id)
    with db.session() as session:
        record = session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.id == identifier,
                GenerationJob.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None:
            return JobRetryOutcome.NOT_FOUND, None
        if record.status != "failed":
            session.expunge(record)
            return JobRetryOutcome.NOT_FAILED, record

        active_count = (
            session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.workspace_id == workspace_id,
                    GenerationJob.status.in_(("queued", "running")),
                )
            )
            or 0
        )
        active_limit = _positive_env_int("ENMOTION_MAX_ACTIVE_JOBS_PER_WORKSPACE", 10)
        if active_count >= active_limit:
            session.expunge(record)
            return JobRetryOutcome.CAPACITY, record

        record.retry_context = _failed_retry_snapshot(record)
        record.status = "queued"
        record.progress = 0
        record.progress_stage = "queued"
        record.progress_is_estimated = True
        record.provider_progress = None
        record.progress_steps = [
            {
                "id": "queued",
                "state": "active",
                "started_at": utc_now().isoformat(),
                "finished_at": None,
                "message": "重试任务正在等待可用的处理服务",
            }
        ]
        record.error = None
        record.result = None
        record.queue_task_id = None
        record.started_at = None
        record.finished_at = None
        record.updated_at = utc_now()
        session.commit()

        retry_workspace_id = record.workspace_id
        retry_user_id = record.user_id
        retry_job_type = record.job_type
        retry_payload = dict(record.payload or {})

    if retry_job_type == "video" and not _sync_video_task_retry(
        workspace_id=retry_workspace_id,
        user_id=retry_user_id,
        payload=retry_payload,
    ):
        with db.session() as session:
            failed_retry = session.scalar(
                select(GenerationJob).where(GenerationJob.id == identifier).with_for_update()
            )
            if failed_retry is not None and failed_retry.retry_context:
                _restore_failed_retry_snapshot(failed_retry, dict(failed_retry.retry_context))
                session.commit()
        raise RuntimeError("The persisted video task could not be prepared for retry")

    delivery_id = str(uuid.uuid4())
    try:
        published = publish_reserved_jobs(
            db,
            job_ids=[identifier],
            delivery_ids={identifier: delivery_id},
        )
    except JobQueueUnavailableError:
        if retry_job_type == "video":
            _sync_video_task_failure(
                workspace_id=retry_workspace_id,
                user_id=retry_user_id,
                payload=retry_payload,
                failure=VideoFailure(
                    VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
                    VIDEO_QUEUE_UNAVAILABLE_CODE,
                    "The generation queue could not publish the retried task.",
                ),
            )
        raise
    return JobRetryOutcome.RETRIED, published[0]


def dismiss_workspace_job(db: Database, *, workspace_id: str, job_id: str) -> JobDismissalOutcome:
    """Remove a terminal history row without touching generated workspace data."""

    identifier = _validate_job_id(job_id)
    with db.session() as session:
        record = session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.id == identifier,
                GenerationJob.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None:
            return JobDismissalOutcome.NOT_FOUND
        if record.status not in TERMINAL_JOB_STATUSES:
            return JobDismissalOutcome.ACTIVE
        session.delete(record)
        session.commit()
    return JobDismissalOutcome.DISMISSED


def delete_frame_generation_jobs(
    db: Database,
    *,
    workspace_id: str,
    script_id: str,
    frame_id: str,
    task_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Remove durable jobs owned exclusively by a deleted storyboard frame.

    Frame image/dub jobs carry ``frame_id`` directly. Video generation jobs
    historically carried only the project-side ``task_id``, so callers pass
    the task ids detached from the project in the same deletion transaction.
    The returned snapshots let workspace media GC reclaim result files while
    retaining any file still referenced by another authoritative record.

    A running provider call cannot be interrupted safely. Removing its row
    prevents it from resurfacing in API Calls, and its worker will treat the
    missing terminal row as acknowledged. Project commit code still rejects a
    result for the now-missing frame and cleans up newly-created output files.
    """

    if not workspace_id or not script_id or not frame_id:
        raise ValueError("workspace_id, script_id and frame_id are required")

    related_task_ids = {str(value) for value in task_ids if value}
    queued_deliveries: list[tuple[str, str]] = []
    snapshots: list[dict[str, Any]] = []
    with db.session() as session:
        records = list(
            session.scalars(
                select(GenerationJob)
                .where(GenerationJob.workspace_id == workspace_id)
                .with_for_update()
            )
        )
        for record in records:
            payload = dict(record.payload or {})
            if str(payload.get("script_id") or "") != script_id:
                continue
            owns_frame = str(payload.get("frame_id") or "") == frame_id
            owns_task = bool(
                related_task_ids and str(payload.get("task_id") or "") in related_task_ids
            )
            if not owns_frame and not owns_task:
                continue
            snapshots.append(
                {
                    "id": record.id,
                    "job_type": record.job_type,
                    "payload": payload,
                    "result": dict(record.result or {}),
                    "retry_context": dict(record.retry_context or {}),
                }
            )
            if record.status == "queued" and record.queue_task_id:
                queued_deliveries.append((record.id, record.queue_task_id))
            session.delete(record)
        session.commit()

    # Revocation is best-effort. PostgreSQL is authoritative: even if Redis is
    # unavailable, a later delivery cannot claim a row that no longer exists.
    for job_id, queue_task_id in queued_deliveries:
        try:
            celery_app.control.revoke(
                _delivery_id_from_queue_state(job_id, queue_task_id),
                terminate=False,
            )
        except Exception:
            pass
    return snapshots


def _job_activity_category(job_type: str, payload: dict[str, Any]) -> str:
    if job_type == "playground":
        mode = str(payload.get("mode") or "").lower()
        return "video" if mode in {"t2v", "i2v"} else "image"
    if job_type == "refine_batch":
        return "text"
    if job_type in {
        "project_asset",
        "series_asset",
        "global_asset",
        "project_assets_batch",
        "generate_storyboard",
        "storyboard_render",
    }:
        return "image"
    if job_type in {
        "motion_reference",
        "video",
        "generate_video",
        "merge",
        "export",
        "dub_preview",
    }:
        return "video"
    return "other"


def _job_activity_source(job_type: str, payload: dict[str, Any]) -> str:
    source = payload.get("activity_source")
    if source in {"playground", "workspace", "library"}:
        return str(source)
    return "playground" if job_type == "playground" else "workspace"


def _job_activity_detail(payload: dict[str, Any]) -> str | None:
    compiled = payload.get("compiled_request")
    if isinstance(compiled, dict):
        requests = compiled.get("provider_requests")
        if isinstance(requests, list) and requests and isinstance(requests[0], dict):
            value = requests[0].get("prompt")
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:240]
    for key in ("prompt", "style_prompt"):
        value = payload.get(key)
        if isinstance(value, str):
            compact = " ".join(value.split())
            if compact:
                return compact[:240]
    return None


def _display_model_name(payload: dict[str, Any]) -> str | None:
    compiled = payload.get("compiled_request")
    compiled_requests = compiled.get("provider_requests") if isinstance(compiled, dict) else None
    compiled_model = (
        compiled_requests[0].get("model")
        if isinstance(compiled_requests, list)
        and compiled_requests
        and isinstance(compiled_requests[0], dict)
        else None
    )
    model_id = next(
        (
            value
            for value in (
                compiled_model,
                payload.get("model_name"),
                payload.get("model_id"),
                payload.get("model"),
            )
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    if not model_id:
        return None
    try:
        return get_model_spec(model_id).display_name
    except Exception:
        # Some non-NewAPI adapters expose a stable product name directly.
        return str(model_id)[:120]


def _source_context(job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    source = _job_activity_source(job_type, payload)
    series_id = payload.get("series_id")
    script_id = payload.get("script_id")
    if source == "playground":
        route = "#/playground"
    elif source == "library":
        route = "#/library"
    elif series_id and script_id:
        route = f"#/series/{series_id}/episode/{script_id}"
    elif series_id:
        route = f"#/series/{series_id}"
    elif script_id:
        route = f"#/project/{script_id}"
    else:
        route = "#/"
    return {
        "type": source,
        "route": payload.get("source_route") or route,
        "series_id": series_id,
        "episode_id": script_id,
        "project_id": script_id,
        "frame_id": payload.get("frame_id"),
        "asset_id": payload.get("asset_id"),
        "asset_type": payload.get("asset_type"),
        "playground_generation_id": payload.get("generation_id"),
        "video_task_id": payload.get("task_id"),
    }


def _public_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "mode",
        "duration",
        "resolution",
        "aspect_ratio",
        "ratio",
        "batch_size",
        "generation_type",
        "frame_type",
        "generate_audio",
        "watermark",
        "seed",
    )
    parameters: dict[str, Any] = {
        key: payload[key]
        for key in allowed
        if key in payload and isinstance(payload[key], (str, int, float, bool))
    }
    nested = payload.get("parameters")
    if isinstance(nested, dict):
        for key in allowed:
            if key in nested and isinstance(nested[key], (str, int, float, bool)):
                parameters[key] = nested[key]
    compiled = payload.get("compiled_request")
    compiled_requests = compiled.get("provider_requests") if isinstance(compiled, dict) else None
    if isinstance(compiled_requests, list) and compiled_requests and isinstance(compiled_requests[0], dict):
        exact = compiled_requests[0].get("parameters")
        if isinstance(exact, dict):
            parameters = {
                str(key): value
                for key, value in exact.items()
                if isinstance(value, (str, int, float, bool))
            }
    return parameters


def _public_input_media(payload: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[str] = []
    for key in ("image_url", "source_image_url", "reference_image_url"):
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(value)
    values = payload.get("input_media")
    if isinstance(values, list):
        candidates.extend(value for value in values if isinstance(value, str))
    compiled = payload.get("compiled_request")
    compiled_requests = compiled.get("provider_requests") if isinstance(compiled, dict) else None
    if isinstance(compiled_requests, list):
        for exact in compiled_requests:
            if isinstance(exact, dict) and isinstance(exact.get("input_media"), list):
                candidates.extend(
                    value for value in exact["input_media"] if isinstance(value, str)
                )
    result: list[dict[str, str]] = []
    for index, value in enumerate(candidates[:16]):
        if value.startswith(("data:", "blob:")):
            continue
        result.append({"id": f"input-{index + 1}", "media_path": value, "media_type": "image"})
    return result


def _public_job_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep durable-job client compatibility without exposing provider envelopes."""

    allowed = (
        "url",
        "script_id",
        "series_id",
        "source_id",
        "generation_id",
        "task_id",
        "frame_id",
        "asset_id",
    )
    return {
        key: result[key]
        for key in allowed
        if key in result and isinstance(result[key], (str, int, float, bool))
    }


def job_to_dict(record: GenerationJob, *, queue_position: int | None = None) -> dict[str, Any]:
    payload = record.payload or {}
    result = record.result or {}
    error = record.error
    error_code = result.get("error_code") if isinstance(result, dict) else None
    error_diagnostic = result.get("error_diagnostic") if isinstance(result, dict) else None
    outputs = result.get("outputs") if isinstance(result, dict) else None
    compiled_request = payload.get("compiled_request")
    if not isinstance(compiled_request, dict):
        compiled_request = None
    # Older rows stored the complete provider envelope in ``error``. Present
    # those safely as soon as this release is deployed without a data migration.
    if error and INPUT_IMAGE_PRIVACY_PROVIDER_CODE.casefold() in error.casefold():
        error_code = INPUT_IMAGE_PRIVACY_ERROR_CODE
        error_diagnostic = error_diagnostic or redact_newapi_secrets(error)[:4000]
        error = INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE
    return {
        "task_id": record.id,
        "id": record.id,
        "type": record.job_type,
        "status": record.status,
        "progress": record.progress,
        "progress_stage": record.progress_stage,
        "progress_is_estimated": record.progress_is_estimated,
        "provider_progress": record.provider_progress,
        "progress_steps": record.progress_steps or [],
        "error": error,
        "error_code": error_code,
        "error_diagnostic": error_diagnostic,
        "category": _job_activity_category(record.job_type, payload),
        "source": _job_activity_source(record.job_type, payload),
        "detail": _job_activity_detail(payload),
        "prompt": _job_activity_detail(payload),
        "user_prompt": (
            compiled_request.get("user_prompt")
            if compiled_request is not None
            else payload.get("prompt")
        ),
        "compiled_request": compiled_request,
        "model_name": _display_model_name(payload),
        "parameters": _public_parameters(payload),
        "source_context": _source_context(record.job_type, payload),
        "input_media": _public_input_media(payload),
        "outputs": outputs if isinstance(outputs, list) else [],
        "result": _public_job_result(result) if isinstance(result, dict) else {},
        "queue_position": queue_position,
        "attempts": record.attempts,
        "script_id": payload.get("script_id") or payload.get("series_id"),
        "asset_id": payload.get("asset_id"),
        "asset_type": payload.get("asset_type"),
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "started_at": _iso(record.started_at),
        "finished_at": _iso(record.finished_at),
    }


def _job_failure_result(exc: Exception) -> dict[str, str] | None:
    if isinstance(exc, NewAPIProviderError):
        return exc.job_result()
    if _is_assembly_mutation_conflict(exc):
        return {
            "error_code": ASSEMBLY_INPUTS_CHANGED_CODE,
            "error_diagnostic": str(exc),
        }
    return None


def _sync_video_task_failure(
    *,
    workspace_id: str,
    user_id: str,
    payload: dict[str, Any],
    failure: VideoFailure,
    allow_completed: bool = False,
) -> bool:
    """Write a durable-job failure back to the project task after rollback."""

    script_id = payload.get("script_id")
    task_id = payload.get("task_id")
    if not script_id or not task_id:
        return False
    token = bind_tenant(user_id, workspace_id, "worker")
    try:
        with interprocess_lock(_worker_pipelines.lock_path_for(workspace_id)):
            pipeline = _worker_pipelines.get(workspace_id)
            return pipeline.mark_video_task_failed(
                str(script_id),
                str(task_id),
                failure.message,
                error_code=failure.code,
                error_diagnostic=failure.diagnostic,
                overwrite=True,
                allow_completed=allow_completed,
            )
    finally:
        reset_tenant(token)


def _sync_video_task_canceled(*, workspace_id: str, user_id: str, payload: dict[str, Any]) -> bool:
    script_id = payload.get("script_id")
    task_id = payload.get("task_id")
    if not script_id or not task_id:
        return False
    token = bind_tenant(user_id, workspace_id, "worker")
    try:
        with interprocess_lock(_worker_pipelines.lock_path_for(workspace_id)):
            return _worker_pipelines.get(workspace_id).mark_video_task_canceled(
                str(script_id), str(task_id)
            )
    finally:
        reset_tenant(token)


def _sync_video_task_retry(*, workspace_id: str, user_id: str, payload: dict[str, Any]) -> bool:
    script_id = payload.get("script_id")
    task_id = payload.get("task_id")
    if not script_id or not task_id:
        return False
    token = bind_tenant(user_id, workspace_id, "worker")
    try:
        with interprocess_lock(_worker_pipelines.lock_path_for(workspace_id)):
            return _worker_pipelines.get(workspace_id).prepare_video_task_retry(
                str(script_id), str(task_id)
            )
    finally:
        reset_tenant(token)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _update_job_progress(
    database: Database,
    *,
    workspace_id: str,
    job_id: str,
    stage: str,
    message: str | None = None,
    percent: int | None = None,
    estimated: bool = True,
) -> None:
    """Persist one real worker/provider stage transition."""

    now = utc_now()
    with database.session() as session:
        record = session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None or record.status != "running":
            return
        steps = [dict(item) for item in (record.progress_steps or []) if isinstance(item, dict)]
        current = record.progress_stage
        if current != stage:
            for item in steps:
                if item.get("state") == "active":
                    item["state"] = "completed"
                    item["finished_at"] = now.isoformat()
            steps.append(
                {
                    "id": stage,
                    "state": "active",
                    "started_at": now.isoformat(),
                    "finished_at": None,
                    "message": message,
                }
            )
        else:
            for item in reversed(steps):
                if item.get("id") == stage:
                    if message:
                        item["message"] = message
                    break
        next_progress = percent if percent is not None else _STAGE_PROGRESS.get(stage)
        if stage == "provider_processing" and percent is not None and not estimated:
            # Preserve the provider's real percentage independently while the
            # primary progress value continues to represent the complete
            # application workflow (including download and persistence).
            record.provider_progress = max(0, min(100, int(percent)))
            next_progress = 40 + round(record.provider_progress * 0.35)
            estimated = True
        if next_progress is not None:
            record.progress = max(record.progress, min(99, max(0, int(next_progress))))
        record.progress_stage = stage
        record.progress_is_estimated = estimated
        record.progress_steps = steps
        record.updated_at = now
        session.commit()


def _progress_reporter(database: Database, job: ClaimedJob):
    def report(
        stage: str,
        message: str | None,
        percent: int | None,
        estimated: bool,
    ) -> None:
        _update_job_progress(
            database,
            workspace_id=job.workspace_id,
            job_id=job.id,
            stage=stage,
            message=message,
            percent=percent,
            estimated=estimated,
        )

    return report


def _mark_progress_terminal(
    record: GenerationJob,
    *,
    status: str,
    message: str | None,
    now: datetime,
) -> None:
    """Close the active timeline step whenever a job becomes terminal."""

    steps = [dict(item) for item in (record.progress_steps or []) if isinstance(item, dict)]
    for item in steps:
        if item.get("state") == "active":
            item["state"] = "completed" if status in {"completed", "canceled"} else "failed"
            item["finished_at"] = now.isoformat()
            if message:
                item["message"] = message[:500]
    if status == "completed":
        steps.append(
            {
                "id": "completed",
                "state": "completed",
                "started_at": now.isoformat(),
                "finished_at": now.isoformat(),
                "message": "生成已完成",
            }
        )
        record.progress_stage = "completed"
        record.progress_is_estimated = False
    record.progress_steps = steps


def _claim_job(
    database: Database,
    job_id: str,
    delivery_id: str | None = None,
) -> ClaimedJob | None:
    with database.session() as session:
        record = session.scalar(
            select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
        )
        if record is None or record.status != "queued" or record.queue_task_id is None:
            return None
        effective_delivery_id = delivery_id or job_id
        expected_delivery_id = _delivery_id_from_queue_state(record.id, record.queue_task_id)
        if expected_delivery_id != effective_delivery_id:
            return None
        # A fast worker may consume a retry while the API row still contains
        # its encoded pending marker. Normalize it as part of the same claim.
        record.queue_task_id = effective_delivery_id
        record.status = "running"
        record.progress = 0
        record.provider_progress = None
        record.attempts += 1
        record.started_at = utc_now()
        record.retry_context = None
        record.updated_at = utc_now()
        claimed = ClaimedJob(
            id=record.id,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            job_type=record.job_type,
            payload=dict(record.payload or {}),
            attempts=record.attempts,
        )
        session.commit()
        return claimed


def _finish_job_once(
    database: Database,
    job_id: str,
    *,
    workspace_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    if status not in {"completed", "failed"}:
        raise ValueError("Worker terminal status must be completed or failed")
    with database.session() as session:
        record = session.scalar(
            select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
        )
        if record is None:
            return "missing"
        if record.workspace_id != workspace_id:
            return "workspace_mismatch"
        if record.status in TERMINAL_JOB_STATUSES:
            return "already_terminal"
        if record.status != "running":
            return "not_running"
        record.status = status
        record.progress = 100 if status == "completed" else record.progress
        now = utc_now()
        _mark_progress_terminal(
            record,
            status=status,
            message=error if status == "failed" else None,
            now=now,
        )
        record.result = result
        record.error = error
        record.finished_at = now
        record.updated_at = now
        session.commit()
        return "updated"


def _terminal_outbox_directory() -> Path:
    return (
        Path(os.getenv("ENMOTION_DATA_DIR", "data")).expanduser().resolve() / "job-terminal-outbox"
    )


def _terminal_intent_path(job_id: str) -> Path:
    return _terminal_outbox_directory() / f"{_validate_job_id(job_id)}.json"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_terminal_intent(
    job: ClaimedJob,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> Path:
    """Atomically record DB finalization intent before the final commit.

    The outbox lives on the persistent app-data volume.  A worker restart can
    therefore finish a job without repeating its provider request.
    """

    if status not in {"completed", "failed"}:
        raise ValueError("Worker terminal status must be completed or failed")
    payload = {
        "version": TERMINAL_OUTBOX_VERSION,
        "job_id": _validate_job_id(job.id),
        "workspace_id": job.workspace_id,
        "status": status,
        "result": result,
        "error": error,
        "finished_at": utc_now().isoformat(),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TerminalStatePersistenceError("Job terminal result is not JSON serializable") from exc
    limit = _positive_env_int("ENMOTION_MAX_JOB_TERMINAL_RECORD_BYTES", 1024 * 1024)
    if len(encoded) > limit:
        raise TerminalStatePersistenceError(f"Job terminal record exceeds the {limit}-byte limit")

    directory = _terminal_outbox_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        # A mounted volume may enforce its own mode; file mode remains private.
        pass
    destination = _terminal_intent_path(job.id)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=f".{job.id}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        _fsync_directory(directory)
        return destination
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _remove_terminal_intent(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _finish_job_with_retry(
    database: Database,
    job_id: str,
    *,
    workspace_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    attempts: int | None = None,
) -> str:
    retry_count = attempts or _positive_env_int("ENMOTION_JOB_FINALIZE_ATTEMPTS", 5)
    delay_ms = _positive_env_int("ENMOTION_JOB_FINALIZE_RETRY_DELAY_MS", 200)
    last_error: Exception | None = None
    for attempt in range(retry_count):
        try:
            outcome = _finish_job_once(
                database,
                job_id,
                workspace_id=workspace_id,
                status=status,
                result=result,
                error=error,
            )
            if outcome in {"updated", "already_terminal", "missing"}:
                return outcome
            raise RuntimeError(f"Job cannot be finalized from state {outcome}")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retry_count:
                time.sleep(min((delay_ms / 1000) * (2**attempt), 2.0))
    raise TerminalStatePersistenceError(
        f"Could not persist terminal state for job {job_id}; reconciliation will retry"
    ) from last_error


def _finish_job_durably(
    database: Database,
    job: ClaimedJob,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    marker: Path | None = None
    marker_error: Exception | None = None
    try:
        marker = _write_terminal_intent(
            job,
            status=status,
            result=result,
            error=error,
        )
    except Exception as exc:
        # A full app-data volume must not prevent a healthy PostgreSQL commit.
        # If the DB retries also fail, surface both failures because no durable
        # reconciliation record could be retained.
        marker_error = exc
        logger.exception("Could not write terminal outbox marker for job %s", job.id)

    try:
        _finish_job_with_retry(
            database,
            job.id,
            workspace_id=job.workspace_id,
            status=status,
            result=result,
            error=error,
        )
    except TerminalStatePersistenceError as exc:
        if marker_error is not None:
            raise TerminalStatePersistenceError(
                f"Could not persist terminal state or reconciliation marker for job {job.id}"
            ) from exc
        raise

    if marker is not None:
        try:
            _remove_terminal_intent(marker)
        except OSError:
            # PostgreSQL is already authoritative. The reconciler treats a marker
            # for an existing terminal row as acknowledged and removes it later.
            logger.exception("Could not remove acknowledged job outbox marker %s", marker)


def _read_terminal_intent(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Terminal outbox entry must be a regular file")
    limit = _positive_env_int("ENMOTION_MAX_JOB_TERMINAL_RECORD_BYTES", 1024 * 1024)
    if path.stat().st_size > limit:
        raise ValueError("Terminal outbox entry is too large")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != TERMINAL_OUTBOX_VERSION:
        raise ValueError("Unsupported terminal outbox record")
    job_id = _validate_job_id(raw.get("job_id"))
    if path.stem != job_id:
        raise ValueError("Terminal outbox filename does not match its job id")
    workspace_id = raw.get("workspace_id")
    status = raw.get("status")
    result = raw.get("result")
    error = raw.get("error")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("Terminal outbox workspace id is invalid")
    if status not in {"completed", "failed"}:
        raise ValueError("Terminal outbox status is invalid")
    if result is not None and not isinstance(result, dict):
        raise ValueError("Terminal outbox result is invalid")
    if error is not None and not isinstance(error, str):
        raise ValueError("Terminal outbox error is invalid")
    return {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "status": status,
        "result": result,
        "error": error,
    }


def reconcile_terminal_job_outbox(database: Database | None = None) -> int:
    """Apply pending terminal DB commits without rerunning provider work."""

    db = database or get_database()
    directory = _terminal_outbox_directory()
    if not directory.is_dir():
        return 0
    limit = _positive_env_int("ENMOTION_JOB_TERMINAL_RECONCILE_BATCH", 100)
    reconciled = 0
    for path in sorted(directory.glob("*.json"))[:limit]:
        try:
            intent = _read_terminal_intent(path)
        except Exception:
            logger.exception("Quarantining invalid job terminal outbox entry %s", path)
            try:
                path.replace(path.with_suffix(f".{uuid.uuid4().hex}.invalid"))
                _fsync_directory(directory)
            except OSError:
                logger.exception("Could not quarantine invalid outbox entry %s", path)
            continue
        try:
            _finish_job_with_retry(
                db,
                intent["job_id"],
                workspace_id=intent["workspace_id"],
                status=intent["status"],
                result=intent["result"],
                error=intent["error"],
                attempts=1,
            )
            _remove_terminal_intent(path)
            reconciled += 1
        except Exception:
            logger.exception("Job terminal reconciliation will retry %s", path)
    return reconciled


def recover_interrupted_jobs(database: Database | None = None) -> int:
    """Fail jobs left running by a terminated worker without re-charging AI APIs."""

    db = database or get_database()
    interrupted: list[tuple[str, str, dict[str, Any]]] = []
    with db.session() as session:
        records = list(
            session.scalars(
                select(GenerationJob).where(GenerationJob.status == "running").with_for_update()
            )
        )
        now = utc_now()
        for record in records:
            record.status = "failed"
            record.error = VIDEO_INTERRUPTED_MESSAGE
            if record.job_type == "video":
                record.result = {
                    "error_code": VIDEO_INTERRUPTED_CODE,
                    "error_diagnostic": (
                        "The worker stopped while this task was running. The provider request "
                        "was not automatically repeated to avoid duplicate charges."
                    ),
                }
                interrupted.append(
                    (record.workspace_id, record.user_id, dict(record.payload or {}))
                )
            record.finished_at = now
            record.updated_at = now
            _mark_progress_terminal(
                record,
                status="failed",
                message=record.error,
                now=now,
            )
        session.commit()
    for workspace_id, user_id, payload in interrupted:
        try:
            _sync_video_task_failure(
                workspace_id=workspace_id,
                user_id=user_id,
                payload=payload,
                failure=VideoFailure(
                    VIDEO_INTERRUPTED_MESSAGE,
                    VIDEO_INTERRUPTED_CODE,
                    "The worker stopped while this task was running. The provider request "
                    "was not automatically repeated to avoid duplicate charges.",
                ),
            )
        except Exception:
            logger.exception("Could not synchronize interrupted video task %s", payload)
    return len(records)


def compact_terminal_jobs(
    database: Database | None = None,
    *,
    retention_days: int | None = None,
    max_terminal_per_workspace: int | None = None,
    batch_size: int | None = None,
    now: datetime | None = None,
) -> int:
    """Bound durable history without ever deleting queued/running jobs.

    Age-based retention uses ``finished_at`` so a long-running job remains
    visible for the full retention period after it actually finishes.  Count
    compaction also preserves every terminal row from the last 24 hours; this
    prevents pruning from weakening the rolling daily admission limit.
    """

    db = database or get_database()
    keep_days = max(
        1,
        (
            retention_days
            if retention_days is not None
            else _positive_env_int("ENMOTION_JOB_HISTORY_RETENTION_DAYS", 30)
        ),
    )
    keep_count = max(
        1,
        (
            max_terminal_per_workspace
            if max_terminal_per_workspace is not None
            else _positive_env_int("ENMOTION_MAX_TERMINAL_JOBS_PER_WORKSPACE", 500)
        ),
    )
    delete_limit = max(
        1,
        (
            batch_size
            if batch_size is not None
            else _positive_env_int("ENMOTION_JOB_COMPACTION_BATCH_SIZE", 1000)
        ),
    )
    reference = now or utc_now()
    expiry_cutoff = reference - timedelta(days=keep_days)
    daily_cutoff = reference - timedelta(hours=24)

    with db.session() as session:
        candidates = list(
            session.scalars(
                select(GenerationJob.id)
                .where(
                    GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
                    GenerationJob.finished_at.is_not(None),
                    GenerationJob.finished_at < expiry_cutoff,
                )
                .order_by(GenerationJob.finished_at.asc(), GenerationJob.id.asc())
                .limit(delete_limit)
            )
        )
        candidate_set = set(candidates)

        remaining = delete_limit - len(candidates)
        if remaining > 0:
            workspace_ids = list(
                session.scalars(
                    select(GenerationJob.workspace_id)
                    .where(GenerationJob.status.in_(TERMINAL_JOB_STATUSES))
                    .distinct()
                    .order_by(GenerationJob.workspace_id.asc())
                )
            )
            for workspace_id in workspace_ids:
                if remaining <= 0:
                    break
                ranked_overflow = (
                    select(GenerationJob.id.label("id"))
                    .where(
                        GenerationJob.workspace_id == workspace_id,
                        GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
                        GenerationJob.finished_at.is_not(None),
                    )
                    .order_by(
                        GenerationJob.finished_at.desc(),
                        GenerationJob.id.desc(),
                    )
                    .offset(keep_count)
                    .subquery()
                )
                overflow = list(
                    session.scalars(
                        select(GenerationJob.id)
                        .where(
                            GenerationJob.id.in_(select(ranked_overflow.c.id)),
                            GenerationJob.finished_at < daily_cutoff,
                            *([GenerationJob.id.not_in(candidate_set)] if candidate_set else []),
                        )
                        .order_by(GenerationJob.finished_at.asc(), GenerationJob.id.asc())
                        .limit(remaining)
                    )
                )
                for identifier in overflow:
                    if identifier not in candidate_set:
                        candidates.append(identifier)
                        candidate_set.add(identifier)
                        remaining -= 1
                        if remaining <= 0:
                            break

        if not candidates:
            return 0
        result = session.execute(
            delete(GenerationJob).where(
                GenerationJob.id.in_(candidates),
                GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
            )
        )
        session.commit()
        return int(result.rowcount or 0)


def republish_queued_jobs(database: Database | None = None) -> int:
    """Rebuild Redis queue state from authoritative PostgreSQL rows.

    Duplicate deliveries are harmless because ``_claim_job`` transitions a row
    from ``queued`` exactly once under a database lock.
    """

    return _republish_jobs(database or get_database(), unconfirmed_only=False)


def republish_unconfirmed_jobs(database: Database | None = None) -> int:
    """Heal only the API-crash window around Redis publication.

    Unlike full startup recovery, this is safe to run periodically without
    duplicating every legitimately queued task behind a long provider call.
    """

    return _republish_jobs(database or get_database(), unconfirmed_only=True)


def _republish_jobs(database: Database, *, unconfirmed_only: bool) -> int:
    db = database
    conditions = [
        GenerationJob.status == "queued",
        GenerationJob.queue_task_id.is_not(None),
    ]
    if unconfirmed_only:
        conditions.append(
            or_(
                GenerationJob.queue_task_id == QUEUE_PUBLICATION_PENDING,
                GenerationJob.queue_task_id.like(f"{QUEUE_PUBLICATION_PENDING_PREFIX}%"),
            )
        )
    with db.session() as session:
        queued = list(
            session.execute(
                select(GenerationJob.id, GenerationJob.queue_task_id)
                .where(*conditions)
                .order_by(GenerationJob.created_at.asc())
            ).all()
        )
    published = 0
    for identifier, queue_task_id in queued:
        assert queue_task_id is not None
        delivery_id = _delivery_id_from_queue_state(identifier, queue_task_id)
        _publish_delivery(identifier, delivery_id)
        if unconfirmed_only:
            with db.session() as session:
                record = session.get(GenerationJob, identifier)
                if (
                    record is not None
                    and record.status == "queued"
                    and record.queue_task_id == queue_task_id
                    and _is_pending_delivery(queue_task_id)
                ):
                    record.queue_task_id = delivery_id
                    record.updated_at = utc_now()
                    session.commit()
        published += 1
    return published


def recover_stale_reservations(
    database: Database | None = None, *, max_age_seconds: int | None = None
) -> int:
    """Fail and clean API submissions abandoned before queue publication."""

    db = database or get_database()
    age = (
        max(0, max_age_seconds)
        if max_age_seconds is not None
        else _positive_env_int("ENMOTION_JOB_RESERVATION_TTL_SECONDS", 300)
    )
    cutoff = utc_now() - timedelta(seconds=age)
    with db.session() as session:
        identifiers = list(
            session.scalars(
                select(GenerationJob.id)
                .where(
                    GenerationJob.status == "queued",
                    GenerationJob.queue_task_id.is_(None),
                    GenerationJob.created_at <= cutoff,
                )
                .order_by(GenerationJob.created_at.asc())
            )
        )

    recovered = 0
    for identifier in identifiers:
        with db.session() as session:
            workspace_id = session.scalar(
                select(GenerationJob.workspace_id).where(GenerationJob.id == identifier)
            )
        if workspace_id is None:
            continue
        with interprocess_lock(_worker_pipelines.lock_path_for(workspace_id)):
            with db.session() as session:
                record = session.scalar(
                    select(GenerationJob).where(GenerationJob.id == identifier).with_for_update()
                )
                if record is None or record.status != "queued" or record.queue_task_id is not None:
                    continue
                cleanup_error = _cleanup_stale_workspace_task(record)
                record.status = "failed"
                interrupted_message = "任务在进入生成队列前中断，请重试此操作。"
                if record.job_type == "video":
                    record.error = VIDEO_QUEUE_UNAVAILABLE_MESSAGE
                    record.result = {
                        "error_code": VIDEO_QUEUE_UNAVAILABLE_CODE,
                        "error_diagnostic": (interrupted_message)[:4000],
                    }
                else:
                    record.error = interrupted_message
                if cleanup_error:
                    logger.warning(
                        "清理中断任务 %s 时出现内部错误：%s",
                        record.id,
                        cleanup_error,
                    )
                record.finished_at = utc_now()
                record.updated_at = utc_now()
                _mark_progress_terminal(
                    record,
                    status="failed",
                    message=record.error,
                    now=record.finished_at,
                )
                session.commit()
                recovered += 1
    return recovered


def _cleanup_stale_workspace_task(record: GenerationJob) -> str | None:
    token = bind_tenant(record.user_id, record.workspace_id, "worker")
    try:
        payload = dict(record.payload or {})
        if record.job_type in {"project_asset", "series_asset", "global_asset"}:
            _fail_asset_reservation(record)
        elif record.job_type == "video":
            pipeline = _worker_pipelines.get(record.workspace_id)
            pipeline.mark_video_task_failed(
                payload["script_id"],
                payload.get("task_id", record.id),
                VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
                error_code=VIDEO_QUEUE_UNAVAILABLE_CODE,
                error_diagnostic=("任务在进入生成队列前中断，请重试此操作。"),
                overwrite=True,
            )
        elif record.job_type == "playground":
            playground = _worker_playgrounds.get(record.workspace_id)
            playground.storage.delete_generation(payload["generation_id"])
    except Exception as exc:
        return str(exc)[:1000]
    finally:
        reset_tenant(token)
    return None


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}


def _video_poster_path(video_path: Path) -> Path | None:
    """Create a durable poster beside a completed video when FFmpeg is available."""

    poster = video_path.with_name(f"{video_path.stem}.poster.jpg")
    if poster.is_file():
        return poster
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    temporary = poster.with_name(f".{poster.name}.{uuid.uuid4().hex}.tmp.jpg")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.1",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-y",
                str(temporary),
            ],
            check=True,
            timeout=30,
            capture_output=True,
        )
        os.replace(temporary, poster)
        return poster
    except Exception:
        logger.info("Could not create video poster for %s", video_path.name)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        return None


def _output_manifest_item(
    workspace_id: str,
    reference: str,
    *,
    output_id: str | None = None,
    thumbnail_reference: str | None = None,
) -> dict[str, Any] | None:
    from ...utils.media_security import resolve_workspace_media_path

    root = workspace_output_root(workspace_id).resolve()
    try:
        path = Path(resolve_workspace_media_path(root, reference, require_file=True))
        relative = path.relative_to(root).as_posix()
    except Exception:
        return None
    extension = path.suffix.lower()
    if extension in _VIDEO_EXTENSIONS:
        media_type = "video"
    elif extension in _IMAGE_EXTENSIONS:
        media_type = "image"
    else:
        return None
    thumbnail_path: str | None = None
    if media_type == "video":
        generated = _video_poster_path(path)
        if generated:
            thumbnail_path = generated.relative_to(root).as_posix()
    if not thumbnail_path and thumbnail_reference:
        try:
            thumbnail = Path(
                resolve_workspace_media_path(root, thumbnail_reference, require_file=True)
            )
            thumbnail_path = thumbnail.relative_to(root).as_posix()
        except Exception:
            thumbnail_path = None
    mime_type = mimetypes.guess_type(path.name)[0] or (
        "video/mp4" if media_type == "video" else "image/png"
    )
    return {
        "id": output_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace_id}:{relative}")),
        "media_type": media_type,
        "media_path": relative,
        "thumbnail_path": thumbnail_path,
        "filename": path.name,
        "mime_type": mime_type,
        "size_bytes": path.stat().st_size,
    }


def _finalize_job_result(
    job: ClaimedJob,
    result: dict[str, Any],
    *,
    starting_files: set[str] | None = None,
) -> dict[str, Any]:
    """Attach a server-owned output manifest to the public durable result."""

    finalized = dict(result or {})
    had_explicit_manifest = "_output_references" in finalized or "outputs" in finalized
    references = finalized.pop("_output_references", [])
    outputs: list[dict[str, Any]] = []
    if isinstance(references, list):
        for index, reference in enumerate(references):
            if isinstance(reference, str):
                item = _output_manifest_item(job.workspace_id, reference)
            elif isinstance(reference, dict) and isinstance(reference.get("path"), str):
                item = _output_manifest_item(
                    job.workspace_id,
                    reference["path"],
                    output_id=str(reference.get("id") or "") or None,
                    thumbnail_reference=(
                        reference.get("thumbnail")
                        if isinstance(reference.get("thumbnail"), str)
                        else None
                    ),
                )
            else:
                item = None
            if item and all(existing["media_path"] != item["media_path"] for existing in outputs):
                outputs.append(item)

    # Export jobs historically return only ``{"url": ...}``. Resolve that
    # durable workspace URL into the output manifest without changing the
    # handler response shape used by existing clients.
    legacy_url = finalized.get("url")
    if isinstance(legacy_url, str):
        item = _output_manifest_item(job.workspace_id, legacy_url)
        if item and all(existing["media_path"] != item["media_path"] for existing in outputs):
            outputs.append(item)

    # Legacy handlers do not return media references. Associate files created
    # by this locked job, while excluding Playground's independently managed
    # directory so concurrent requests cannot cross-link outputs.
    if starting_files is not None:
        root = workspace_output_root(job.workspace_id)
        for relative in sorted(snapshot_workspace_files(job.workspace_id) - starting_files):
            if relative.startswith("playground/") or ".poster." in relative:
                continue
            item = _output_manifest_item(job.workspace_id, str(root / relative))
            if item and all(existing["media_path"] != item["media_path"] for existing in outputs):
                outputs.append(item)
    # Preserve the historical result shape for non-media jobs. Media handlers
    # explicitly opt in via ``_output_references`` and legacy handlers gain a
    # manifest only when a durable workspace file was actually discovered.
    if had_explicit_manifest or outputs:
        finalized["outputs"] = outputs
    return finalized


def process_job(
    job_id: str,
    database: Database | None = None,
    delivery_id: str | None = None,
) -> dict[str, Any] | None:
    # The admin UI writes provider settings to a volume shared by API and
    # worker. Reload before every job so changes take effect without restart.
    from .runtime_config import load_server_runtime_config

    load_server_runtime_config()
    from ...utils.oss_utils import OSSImageUploader

    OSSImageUploader.reset_instance()
    db = database or get_database()
    route = _job_route_for_job(db, job_id)
    if route is None:
        return None
    workspace_id, job_type = route

    # Playground provider calls can run for many minutes. Their service already
    # serializes each short history mutation and enforces saved-file quotas, so
    # holding the workspace lock for the remote provider wait would only freeze
    # unrelated reads such as /playground/history and /library/assets.
    if job_type == "playground":
        return _process_playground_job(db, job_id, workspace_id, delivery_id)
    if job_type == "storyboard_render":
        return _process_storyboard_render_job(db, job_id, workspace_id, delivery_id)

    # API mutation requests and worker execution use the same file lock.  A
    # just-published task therefore cannot claim and start while its API
    # request is still finalizing (or rolling back) workspace JSON state.
    with interprocess_lock(_worker_pipelines.lock_path_for(workspace_id)):
        claimed = _claim_job(db, job_id, delivery_id)
        if claimed is None:
            return None

        starting_usage = workspace_usage_bytes(claimed.workspace_id)
        starting_files = snapshot_workspace_files(claimed.workspace_id)
        starting_metadata = snapshot_workspace_metadata(claimed.workspace_id)
        token = bind_tenant(claimed.user_id, claimed.workspace_id, "worker")
        mutation_token = bind_workspace_mutation(claimed.workspace_id)
        progress_token = bind_generation_progress(_progress_reporter(db, claimed))
        try:
            try:
                _update_job_progress(
                    db,
                    workspace_id=claimed.workspace_id,
                    job_id=claimed.id,
                    stage="validating_request",
                    message="正在检查生成请求",
                )
                result = _execute_claimed_job(claimed)
                response_result = result
                persisted_result = _finalize_job_result(
                    claimed,
                    result,
                    starting_files=starting_files,
                )
                _update_job_progress(
                    db,
                    workspace_id=claimed.workspace_id,
                    job_id=claimed.id,
                    stage="finalizing",
                    message="正在整理任务信息和工作区文件",
                )
                _defer_replaced_job_media(claimed.workspace_id, starting_metadata)
                stage_workspace_file_deletions(claimed.workspace_id)
                _reconcile_job_storage(
                    db, claimed, starting_usage, starting_files, starting_metadata
                )
            except Exception as exc:
                if claimed.job_type == "video":
                    video_failure = classify_video_failure(exc)
                    error = video_failure.message
                    failure_result = {
                        "error_code": video_failure.code,
                        "error_diagnostic": video_failure.diagnostic,
                    }
                else:
                    video_failure = None
                    error = _public_job_failure(exc, GENERATION_JOB_FAILED_MESSAGE)
                    failure_result = _job_failure_result(exc)
                try:
                    _rollback_job_workspace(claimed.workspace_id, starting_files, starting_metadata)
                except Exception as rollback_exc:
                    logger.exception(
                        "任务 %s 的工作区回滚失败：%s",
                        claimed.id,
                        rollback_exc,
                    )
                try:
                    _fail_asset_reservation(claimed)
                except Exception as cleanup_exc:
                    logger.exception(
                        "任务 %s 的素材预留清理失败：%s",
                        claimed.id,
                        cleanup_exc,
                    )
                if video_failure is not None:
                    try:
                        _sync_video_task_failure(
                            workspace_id=claimed.workspace_id,
                            user_id=claimed.user_id,
                            payload=claimed.payload,
                            failure=video_failure,
                        )
                    except Exception as sync_exc:
                        logger.exception(
                            "视频任务 %s 的失败状态同步失败：%s",
                            claimed.id,
                            sync_exc,
                        )
                _finish_job_durably(
                    db,
                    claimed,
                    status="failed",
                    result=failure_result,
                    error=error[:4000],
                )
                raise
            try:
                # Purging is the irreversible commit point: after the first
                # tombstone is unlinked, a multi-file deletion cannot honestly
                # be presented as rollbackable. Keep this outside the rollback
                # block and surface any failure explicitly.
                commit_workspace_mutation(claimed.workspace_id)
            except Exception as exc:
                error = "工作区文件整理失败，请联系管理员。"
                logger.exception(
                    "任务 %s 提交工作区文件变更失败：%s",
                    claimed.id,
                    exc,
                )
                if claimed.job_type == "video":
                    failure = classify_video_failure(exc)
                    try:
                        _sync_video_task_failure(
                            workspace_id=claimed.workspace_id,
                            user_id=claimed.user_id,
                            payload=claimed.payload,
                            failure=failure,
                            allow_completed=True,
                        )
                    except Exception:
                        logger.exception(
                            "Could not synchronize video task after commit failure %s",
                            claimed.id,
                        )
                _finish_job_durably(
                    db,
                    claimed,
                    status="failed",
                    result=(
                        {
                            "error_code": failure.code,
                            "error_diagnostic": failure.diagnostic,
                        }
                        if claimed.job_type == "video"
                        else None
                    ),
                    error=error,
                )
                raise
        finally:
            reset_generation_progress(progress_token)
            reset_workspace_mutation(mutation_token)
            reset_tenant(token)

        _finish_job_durably(
            db,
            claimed,
            status="completed",
            result=persisted_result,
        )
        return response_result


def _process_playground_job(
    database: Database,
    job_id: str,
    workspace_id: str,
    delivery_id: str | None = None,
) -> dict[str, Any] | None:
    """Run a Playground provider call without monopolizing its workspace.

    Claiming still happens under the workspace lock so a just-published task
    cannot start before the API transaction that created it is committed. The
    Playground storage layer reacquires the same lock for every status/history
    write; only the slow external provider request runs unlocked.
    """

    lock_path = _worker_pipelines.lock_path_for(workspace_id)
    with interprocess_lock(lock_path):
        claimed = _claim_job(database, job_id, delivery_id)
    if claimed is None:
        return None

    token = bind_tenant(claimed.user_id, claimed.workspace_id, "worker")
    progress_token = bind_generation_progress(_progress_reporter(database, claimed))
    try:
        _update_job_progress(
            database,
            workspace_id=claimed.workspace_id,
            job_id=claimed.id,
            stage="validating_request",
            message="正在检查 Playground 生成请求",
        )
        result = _execute_claimed_job(claimed)
        response_result = result
        persisted_result = _finalize_job_result(claimed, result)
        # Reconcile under a short lock so quota accounting cannot race an API
        # mutation. Rollback is scoped to this generation only.
        with interprocess_lock(lock_path):
            _reconcile_playground_job_storage(database, claimed)
        _update_job_progress(
            database,
            workspace_id=claimed.workspace_id,
            job_id=claimed.id,
            stage="finalizing",
            message="正在整理 Playground 生成结果",
        )
    except Exception as exc:
        _finish_job_durably(
            database,
            claimed,
            status="failed",
            result=_job_failure_result(exc),
            error=_public_job_failure(exc, PLAYGROUND_JOB_FAILED_MESSAGE),
        )
        raise
    finally:
        reset_generation_progress(progress_token)
        reset_tenant(token)

    _finish_job_durably(
        database,
        claimed,
        status="completed",
        result=persisted_result,
    )
    return response_result


def _process_storyboard_render_job(
    database: Database,
    job_id: str,
    workspace_id: str,
    delivery_id: str | None = None,
) -> dict[str, Any] | None:
    """Render a frame without locking unrelated workspace actions."""
    lock_path = _worker_pipelines.lock_path_for(workspace_id)
    with interprocess_lock(lock_path):
        claimed = _claim_job(database, job_id, delivery_id)
    if claimed is None:
        return None

    token = bind_tenant(claimed.user_id, claimed.workspace_id, "worker")
    progress_token = bind_generation_progress(_progress_reporter(database, claimed))
    plan: Any | None = None
    render_pipeline: Any | None = None
    generated_frame: Any | None = None
    output_paths: list[Path] = []
    try:
        payload = claimed.payload
        _update_job_progress(
            database,
            workspace_id=claimed.workspace_id,
            job_id=claimed.id,
            stage="preparing_inputs",
            message="正在准备分镜画面素材",
        )
        with _worker_pipelines.locked(claimed.workspace_id) as pipeline:
            render_pipeline = pipeline
            plan = pipeline.prepare_storyboard_render(
                payload["script_id"],
                payload["frame_id"],
                payload.get("composition_data"),
                payload["prompt"],
                payload.get("batch_size", 1),
                payload.get("model_name"),
                payload.get("aspect_ratio"),
                payload.get("compiled_request"),
            )
            # Future requests must load the processing snapshot, not reuse the
            # detached object that is entering the provider call.
            _worker_pipelines.discard(claimed.workspace_id)

        # This slow phase changes only the detached frame and unique files.
        generated_frame = render_pipeline.execute_storyboard_render_plan(plan)
        output_paths = render_pipeline.storyboard_render_output_paths(plan, generated_frame)
        render_pipeline.validate_storyboard_render_result(generated_frame)

        # Account and merge under one short lock so quota cannot race commit.
        with _worker_pipelines.locked(claimed.workspace_id) as current:
            _reconcile_storyboard_render_job_storage(database, claimed, output_paths)
            current.commit_storyboard_render_plan(plan, generated_frame)
        _update_job_progress(
            database,
            workspace_id=claimed.workspace_id,
            job_id=claimed.id,
            stage="finalizing",
            message="正在保存所选分镜结果",
        )

        response_result = {
            "script_id": payload["script_id"],
            "frame_id": payload["frame_id"],
        }
        result = {
            **response_result,
            "_output_references": [
                {"path": str(path), "id": f"{payload['frame_id']}-{index + 1}"}
                for index, path in enumerate(output_paths)
            ],
        }
        result = _finalize_job_result(claimed, result)
    except Exception as exc:
        error = _public_job_failure(exc, STORYBOARD_JOB_FAILED_MESSAGE)
        failure_result = _job_failure_result(exc)
        if render_pipeline is not None:
            _worker_pipelines.discard(claimed.workspace_id)
        if plan is not None and render_pipeline is not None:
            try:
                generated_frame = generated_frame or plan.frame
                output_paths = render_pipeline.storyboard_render_output_paths(plan, generated_frame)
                with _worker_pipelines.locked(claimed.workspace_id) as current:
                    try:
                        _remove_storyboard_render_outputs(claimed.workspace_id, output_paths)
                    finally:
                        current.fail_storyboard_render_plan(plan)
            except Exception as cleanup_exc:
                logger.exception(
                    "分镜任务 %s 清理失败：%s",
                    claimed.id,
                    cleanup_exc,
                )
        _finish_job_durably(
            database,
            claimed,
            status="failed",
            result=failure_result,
            error=error[:4000],
        )
        raise
    finally:
        reset_generation_progress(progress_token)
        reset_tenant(token)

    _finish_job_durably(database, claimed, status="completed", result=result)
    return response_result


def _defer_replaced_job_media(
    workspace_id: str, starting_metadata: dict[str, bytes | None]
) -> list[str]:
    """Queue old local media made unreachable by a successful handler."""
    return defer_unreferenced_workspace_media(workspace_id, starting_metadata)


def _job_route_for_job(database: Database, job_id: str) -> tuple[str, str] | None:
    with database.session() as session:
        row = session.execute(
            select(GenerationJob.workspace_id, GenerationJob.job_type).where(
                GenerationJob.id == _validate_job_id(job_id)
            )
        ).one_or_none()
        return (row.workspace_id, row.job_type) if row is not None else None


def _reconcile_playground_job_storage(
    database: Database,
    job: ClaimedJob,
) -> dict[str, int]:
    """Validate only the files owned by one unlocked Playground job."""

    from ...utils.media_security import resolve_workspace_media_path

    runtime = _worker_playgrounds.get(job.workspace_id)
    generation_id = str(job.payload["generation_id"])
    generation = runtime.storage.get_generation(generation_id)
    if generation is None:
        raise RuntimeError("Playground generation disappeared while it was running")

    output_bytes = 0
    for output in generation.outputs:
        path = Path(resolve_workspace_media_path(runtime.storage.output_root, output.media_path))
        output_bytes += path.stat().st_size

    usage = workspace_usage_bytes(job.workspace_id)
    with database.session() as session:
        quota = session.scalar(
            select(Workspace.storage_quota_bytes).where(Workspace.id == job.workspace_id)
        )
    if quota is None:
        raise ValueError("Workspace not found")

    hard_output_limit = max(
        job_storage_reservation_bytes(job.job_type, job.payload),
        _positive_env_int("ENMOTION_MAX_JOB_OUTPUT_BYTES", 4 * 1024 * 1024 * 1024),
    )
    violation: str | None = None
    if output_bytes > hard_output_limit:
        violation = (
            "Generation output exceeded the per-job limit "
            f"({output_bytes} of {hard_output_limit} bytes)"
        )
    elif usage > quota:
        violation = (
            "Generation exceeded the workspace storage quota " f"({usage} of {quota} bytes used)"
        )

    if violation:
        runtime.storage.delete_generation(generation_id)
        remaining_usage = workspace_usage_bytes(job.workspace_id)
        raise StorageQuotaExceededError(
            f"{violation}. Removed this Playground generation; workspace now uses "
            f"{remaining_usage} bytes"
        )

    return {
        "storage_usage_bytes": usage,
        "job_output_bytes": output_bytes,
    }


def _validated_storyboard_render_paths(
    workspace_id: str,
    output_paths: Sequence[Path],
    *,
    require_files: bool,
) -> list[Path]:
    """Validate detached outputs before accounting for or deleting them."""
    output_root = workspace_output_root(workspace_id).resolve()
    validated: list[Path] = []
    for value in output_paths:
        raw_path = Path(value).expanduser()
        resolved = raw_path.resolve()
        if output_root != resolved and output_root not in resolved.parents:
            raise ValueError("Storyboard output path escapes the workspace")
        if raw_path.is_symlink() or (resolved.exists() and not resolved.is_file()):
            raise ValueError("Storyboard output must be a regular file")
        if require_files and not resolved.is_file():
            raise RuntimeError("Generated storyboard output is missing")
        validated.append(resolved)
    return validated


def _remove_storyboard_render_outputs(workspace_id: str, output_paths: Sequence[Path]) -> None:
    for path in _validated_storyboard_render_paths(workspace_id, output_paths, require_files=False):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _reconcile_storyboard_render_job_storage(
    database: Database,
    job: ClaimedJob,
    output_paths: Sequence[Path],
) -> dict[str, int]:
    """Enforce quotas against only files created by one detached render."""
    validated = _validated_storyboard_render_paths(
        job.workspace_id, output_paths, require_files=True
    )
    if not validated:
        raise RuntimeError("Storyboard generation produced no local output file")
    output_bytes = sum(path.stat().st_size for path in validated)
    usage = workspace_usage_bytes(job.workspace_id)
    with database.session() as session:
        quota = session.scalar(
            select(Workspace.storage_quota_bytes).where(Workspace.id == job.workspace_id)
        )
    if quota is None:
        raise ValueError("Workspace not found")

    hard_output_limit = max(
        job_storage_reservation_bytes(job.job_type, job.payload),
        _positive_env_int("ENMOTION_MAX_JOB_OUTPUT_BYTES", 4 * 1024 * 1024 * 1024),
    )
    violation: str | None = None
    if output_bytes > hard_output_limit:
        violation = (
            "Generation output exceeded the per-job limit "
            f"({output_bytes} of {hard_output_limit} bytes)"
        )
    elif usage > quota:
        violation = (
            "Generation exceeded the workspace storage quota " f"({usage} of {quota} bytes used)"
        )

    if violation:
        _remove_storyboard_render_outputs(job.workspace_id, validated)
        remaining_usage = workspace_usage_bytes(job.workspace_id)
        raise StorageQuotaExceededError(
            f"{violation}. Removed this storyboard render; workspace now uses "
            f"{remaining_usage} bytes"
        )
    return {
        "storage_usage_bytes": usage,
        "job_output_bytes": output_bytes,
    }


def _reconcile_job_storage(
    database: Database,
    job: ClaimedJob,
    starting_usage: int,
    starting_files: set[str],
    starting_metadata: dict[str, bytes | None] | None = None,
) -> dict[str, int]:
    """Reconcile estimates against actual bytes after every handler run."""

    usage = workspace_usage_bytes(job.workspace_id)
    with database.session() as session:
        quota = session.scalar(
            select(Workspace.storage_quota_bytes).where(Workspace.id == job.workspace_id)
        )
    if quota is None:
        raise ValueError("Workspace not found")
    # A replacement may stage a large old file before reconciliation, so the
    # net workspace delta can be zero (or negative) even though the provider
    # created a large new output. Count files created by this job separately
    # for the per-job ceiling; quota itself still uses post-delete live usage.
    root = workspace_output_root(job.workspace_id)
    current_files = snapshot_workspace_files(job.workspace_id)
    new_file_bytes = 0
    for relative in current_files - starting_files:
        path = root / relative
        try:
            if not path.is_symlink():
                new_file_bytes += path.stat().st_size
        except FileNotFoundError:
            continue
    output_bytes = max(new_file_bytes, max(0, usage - starting_usage))
    hard_output_limit = max(
        job_storage_reservation_bytes(job.job_type, job.payload),
        _positive_env_int("ENMOTION_MAX_JOB_OUTPUT_BYTES", 4 * 1024 * 1024 * 1024),
    )
    violation: str | None = None
    if output_bytes > hard_output_limit:
        violation = (
            f"Generation output exceeded the per-job limit "
            f"({output_bytes} of {hard_output_limit} bytes)"
        )
    elif usage > quota:
        violation = (
            f"Generation exceeded the workspace storage quota " f"({usage} of {quota} bytes used)"
        )
    if violation:
        removed_files, removed_bytes = _rollback_job_workspace(
            job.workspace_id,
            starting_files,
            starting_metadata,
        )
        remaining_usage = workspace_usage_bytes(job.workspace_id)
        raise StorageQuotaExceededError(
            f"{violation}. Removed {removed_files} newly-created output files "
            f"({removed_bytes} bytes); workspace now uses {remaining_usage} bytes"
        )
    return {
        "storage_usage_bytes": usage,
        "job_output_bytes": output_bytes,
    }


def _rollback_job_workspace(
    workspace_id: str,
    starting_files: set[str],
    starting_metadata: dict[str, bytes | None] | None,
) -> tuple[int, int]:
    """Restore one worker mutation, including media staged for deletion."""

    restore_workspace_file_deletions(workspace_id)
    if starting_metadata is not None:
        restore_workspace_metadata(workspace_id, starting_metadata)
    removed = remove_new_workspace_files(workspace_id, starting_files)
    _worker_pipelines.discard(workspace_id)
    _worker_playgrounds.discard(workspace_id)
    return removed


def _execute_claimed_job(job: ClaimedJob) -> dict[str, Any]:
    handler = JOB_HANDLERS.get(job.job_type)
    if handler is None:
        raise UnsupportedJobTypeError(f"Unsupported generation job: {job.job_type}")
    return handler(job)


def _project_asset(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.generate_asset(
            payload["script_id"],
            payload["asset_id"],
            payload["asset_type"],
            payload.get("style_preset"),
            payload.get("reference_image_url"),
            payload.get("style_prompt"),
            payload.get("generation_type", "all"),
            payload.get("prompt"),
            payload.get("apply_style", True),
            payload.get("negative_prompt"),
            payload.get("batch_size", 1),
            payload.get("model_name"),
            payload.get("aspect_ratio"),
            payload.get("compiled_request"),
        )
    return {"script_id": payload["script_id"], "asset_id": payload["asset_id"]}


def _series_asset(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        _, transient_id = pipeline.generate_series_asset(
            payload["series_id"],
            payload["asset_id"],
            payload["asset_type"],
            payload.get("style_preset"),
            payload.get("reference_image_url"),
            payload.get("style_prompt"),
            payload.get("generation_type", "all"),
            payload.get("prompt"),
            payload.get("apply_style", True),
            payload.get("negative_prompt"),
            payload.get("batch_size", 1),
            payload.get("model_name"),
            aspect_ratio=payload.get("aspect_ratio"),
            compiled_request=payload.get("compiled_request"),
        )
        try:
            pipeline.process_asset_generation_task(transient_id)
            status = pipeline.get_asset_generation_task_status(transient_id)
            if not status or status["status"] != "completed":
                raise RuntimeError((status or {}).get("error") or "Series asset generation failed")
        finally:
            pipeline.forget_asset_generation_task(transient_id)
    return {"series_id": payload["series_id"], "asset_id": payload["asset_id"]}


def _global_asset(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        _, transient_id = pipeline.generate_global_asset(
            payload["asset_id"],
            payload["asset_type"],
            payload.get("style_preset"),
            payload.get("reference_image_url"),
            payload.get("style_prompt"),
            payload.get("generation_type", "all"),
            payload.get("prompt"),
            payload.get("apply_style", True),
            payload.get("negative_prompt"),
            payload.get("batch_size", 1),
            payload.get("model_name"),
            payload.get("aspect_ratio"),
            compiled_request=payload.get("compiled_request"),
        )
        try:
            pipeline.process_asset_generation_task(transient_id)
            status = pipeline.get_asset_generation_task_status(transient_id)
            if not status or status["status"] != "completed":
                raise RuntimeError((status or {}).get("error") or "Global asset generation failed")
        finally:
            pipeline.forget_asset_generation_task(transient_id)
    return {"source_id": "global", "asset_id": payload["asset_id"]}


def _motion_reference(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        if payload.get("source_kind"):
            pipeline.generate_source_asset_motion_ref(
                payload["source_kind"],
                payload["source_id"],
                payload["asset_type"],
                payload["asset_id"],
                motion_type=payload.get("motion_type"),
                prompt=payload.get("prompt"),
                duration=payload.get("duration", 5),
                batch_size=payload.get("batch_size", 1),
                model_id=payload.get("model"),
                audio_url=payload.get("audio_url"),
                compiled_request=payload.get("compiled_request"),
            )
            return {
                "source_kind": payload["source_kind"],
                "source_id": payload["source_id"],
                "asset_id": payload["asset_id"],
            }
        pipeline.generate_motion_ref(
            script_id=payload["script_id"],
            asset_id=payload["asset_id"],
            asset_type=payload["asset_type"],
            prompt=payload.get("prompt"),
            audio_url=payload.get("audio_url"),
            duration=payload.get("duration", 5),
            batch_size=payload.get("batch_size", 1),
            model_id=payload.get("model"),
        )
    return {"script_id": payload["script_id"], "asset_id": payload["asset_id"]}


def _video(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        if job.attempts > 1:
            retry_script = pipeline.get_script(payload["script_id"])
            retry_task = next(
                (
                    item
                    for item in (retry_script.video_tasks if retry_script else [])
                    if item.id == payload["task_id"]
                ),
                None,
            )
            # An accepted provider task must resume with the exact input that
            # was originally submitted. Refreshing the source is only valid
            # when a terminal pre-acceptance rejection is being retried with a
            # fresh request.
            if retry_task is None or not getattr(retry_task, "provider_task_id", None):
                pipeline.refresh_asset_video_task_input(
                    payload["script_id"], payload["task_id"]
                )
        pipeline.process_video_task(payload["script_id"], payload["task_id"])
        script = pipeline.get_script(payload["script_id"])
        task = next(
            (
                item
                for item in (script.video_tasks if script else [])
                if item.id == payload["task_id"]
            ),
            None,
        )
        if task is None or task.status != "completed":
            if task is not None and getattr(task, "error_code", None):
                raise NewAPIProviderError(
                    getattr(task, "error", None) or "Video generation failed",
                    error_code=task.error_code,
                    diagnostic_override=(getattr(task, "error_diagnostic", None) or ""),
                )
            raise RuntimeError(getattr(task, "error", None) or "Video generation failed")
    references: list[dict[str, str]] = []
    if task and task.video_url:
        references.append(
            {
                "id": str(task.id),
                "path": task.video_url,
                "thumbnail": task.image_url,
            }
        )
    return {
        "script_id": payload["script_id"],
        "task_id": payload["task_id"],
        "_output_references": references,
    }


def _playground(job: ClaimedJob) -> dict[str, Any]:
    generation_id = job.payload["generation_id"]
    service = _worker_playgrounds.get(job.workspace_id).service
    service.process_generation(generation_id)
    generation = service.storage.get_generation(generation_id)
    if generation is None or generation.status != "completed":
        raise RuntimeError(getattr(generation, "error", None) or "Playground generation failed")
    return {
        "generation_id": generation_id,
        "_output_references": [
            {
                "id": output.id,
                "path": output.media_path,
                "thumbnail": output.thumbnail_path,
            }
            for output in generation.outputs
        ],
    }


def _project_assets_batch(job: ClaimedJob) -> dict[str, Any]:
    script_id = job.payload["script_id"]
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.generate_assets(script_id)
    return {"script_id": script_id}


def _generate_storyboard(job: ClaimedJob) -> dict[str, Any]:
    script_id = job.payload["script_id"]
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.generate_storyboard(script_id)
    return {"script_id": script_id}


def _refine_batch(job: ClaimedJob) -> dict[str, Any]:
    script_id = job.payload["script_id"]
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        # Drain the desktop-oriented generator so every frame is processed, but
        # do not persist its progress stream in durable job history.
        for _event_type, _data in pipeline.refine_batch_generator(script_id):
            pass
    return {"script_id": script_id}


def _generate_video(job: ClaimedJob) -> dict[str, Any]:
    script_id = job.payload["script_id"]
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.generate_video(script_id)
    return {"script_id": script_id}


def _storyboard_render(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    exact_options: dict[str, Any] = {}
    if payload.get("model_name") is not None:
        exact_options["model_name"] = payload["model_name"]
    if payload.get("aspect_ratio") is not None:
        exact_options["aspect_ratio"] = payload["aspect_ratio"]
    if payload.get("compiled_request") is not None:
        exact_options["compiled_request"] = payload["compiled_request"]
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.generate_storyboard_render(
            payload["script_id"],
            payload["frame_id"],
            payload.get("composition_data"),
            payload["prompt"],
            payload.get("batch_size", 1),
            **exact_options,
        )
    return {
        "script_id": payload["script_id"],
        "frame_id": payload["frame_id"],
    }


def _merge(job: ClaimedJob) -> dict[str, Any]:
    script_id = job.payload["script_id"]
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.merge_videos(script_id)
    return {"script_id": script_id}


def _export(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        export_url = pipeline.export_project(payload["script_id"], payload["options"])
    return {"url": export_url}


def _dub_preview(job: ClaimedJob) -> dict[str, Any]:
    payload = job.payload
    with _worker_pipelines.locked(job.workspace_id) as pipeline:
        pipeline.preview_dub(
            payload["script_id"],
            payload["frame_id"],
            video_task_id=payload["video_task_id"],
            offset_ms=payload.get("offset_ms", 0),
        )
    return {
        "script_id": payload["script_id"],
        "frame_id": payload["frame_id"],
    }


JOB_HANDLERS: dict[str, Callable[[ClaimedJob], dict[str, Any]]] = {
    "project_asset": _project_asset,
    "series_asset": _series_asset,
    "global_asset": _global_asset,
    "motion_reference": _motion_reference,
    "video": _video,
    "playground": _playground,
    "project_assets_batch": _project_assets_batch,
    "refine_batch": _refine_batch,
    "generate_storyboard": _generate_storyboard,
    "generate_video": _generate_video,
    "storyboard_render": _storyboard_render,
    "merge": _merge,
    "export": _export,
    "dub_preview": _dub_preview,
}


@celery_app.task(name=EXECUTE_TASK_NAME, bind=True, acks_late=True)
def execute_job_task(
    _task,
    job_id: str,
    delivery_id: str | None = None,
) -> dict[str, Any] | None:
    return process_job(job_id, delivery_id=delivery_id)
