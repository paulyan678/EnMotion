"""Authenticated job history and cancellation endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ...utils.media_security import (
    UnsafeMediaReferenceError,
    resolve_workspace_media_path,
)
from .context import Actor, get_current_actor
from .database import Database, get_database
from .jobs import (
    JobCancellationOutcome,
    JobDismissalOutcome,
    JobQueueUnavailableError,
    JobRetryOutcome,
    cancel_workspace_job_with_record,
    dismiss_workspace_job,
    get_workspace_job,
    job_to_dict,
    list_workspace_jobs,
    queued_job_positions,
    retry_workspace_job,
)
from .quotas import workspace_output_root

router = APIRouter(prefix="/jobs", tags=["jobs"])

JOB_NOT_FOUND_MESSAGE = "未找到此任务。"
JOB_OUTPUT_NOT_FOUND_MESSAGE = "未找到此任务的输出文件。"
JOB_QUEUE_UNAVAILABLE_MESSAGE = "生成队列暂时不可用，请稍后重试。"


def _database() -> Database:
    return get_database()


@router.get("")
def list_jobs(
    limit: int = 50,
    actor: Actor = Depends(get_current_actor),
    database: Database = Depends(_database),
) -> list[dict]:
    records = list_workspace_jobs(database, workspace_id=actor.workspace_id, limit=limit)
    positions = queued_job_positions(
        database,
        job_ids=[record.id for record in records if record.status == "queued"],
    )
    return [job_to_dict(record, queue_position=positions.get(record.id)) for record in records]


@router.get("/{job_id}")
def get_job(
    job_id: str,
    actor: Actor = Depends(get_current_actor),
    database: Database = Depends(_database),
) -> dict:
    record = get_workspace_job(database, workspace_id=actor.workspace_id, job_id=job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_MESSAGE)
    return job_to_dict(record)


@router.get("/{job_id}/outputs/{output_id}/download")
def download_job_output(
    job_id: str,
    output_id: str,
    actor: Actor = Depends(get_current_actor),
    database: Database = Depends(_database),
) -> FileResponse:
    """Download one persisted output after workspace authorization."""

    record = get_workspace_job(database, workspace_id=actor.workspace_id, job_id=job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_MESSAGE)
    result = record.result if isinstance(record.result, dict) else {}
    outputs = result.get("outputs")
    output = (
        next(
            (
                item
                for item in outputs
                if isinstance(item, dict) and str(item.get("id")) == output_id
            ),
            None,
        )
        if isinstance(outputs, list)
        else None
    )
    if output is None or not isinstance(output.get("media_path"), str):
        raise HTTPException(status_code=404, detail=JOB_OUTPUT_NOT_FOUND_MESSAGE)
    try:
        path = Path(
            resolve_workspace_media_path(
                workspace_output_root(actor.workspace_id),
                output["media_path"],
                require_file=True,
            )
        )
    except UnsafeMediaReferenceError as exc:
        raise HTTPException(status_code=404, detail="任务输出文件暂时不可用。") from exc
    filename_value = output.get("filename")
    filename = Path(filename_value).name if isinstance(filename_value, str) else path.name
    if not filename:
        filename = path.name
    media_type = output.get("mime_type")
    if not isinstance(media_type, str) or "/" not in media_type:
        media_type = "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment",
    )


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    actor: Actor = Depends(get_current_actor),
    database: Database = Depends(_database),
) -> dict:
    outcome, record = cancel_workspace_job_with_record(
        database,
        workspace_id=actor.workspace_id,
        job_id=job_id,
    )
    if outcome is JobCancellationOutcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_MESSAGE)
    if outcome is JobCancellationOutcome.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="正在运行的任务无法安全取消，请等待任务完成。",
        )
    if outcome is JobCancellationOutcome.FINISHED:
        raise HTTPException(status_code=409, detail="此任务已经结束。")
    assert record is not None
    return job_to_dict(record)


@router.post("/{job_id}/retry")
def retry_job(
    job_id: str,
    actor: Actor = Depends(get_current_actor),
    database: Database = Depends(_database),
) -> dict:
    try:
        outcome, record = retry_workspace_job(
            database, workspace_id=actor.workspace_id, job_id=job_id
        )
    except JobQueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=JOB_QUEUE_UNAVAILABLE_MESSAGE) from exc
    if outcome is JobRetryOutcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_MESSAGE)
    if outcome is JobRetryOutcome.NOT_FAILED:
        raise HTTPException(status_code=409, detail="只有失败的任务可以重试。")
    if outcome is JobRetryOutcome.CAPACITY:
        raise HTTPException(
            status_code=429,
            detail="工作区生成队列已满，请等待正在运行的任务完成。",
        )
    assert record is not None
    positions = queued_job_positions(database, job_ids=[record.id])
    return job_to_dict(record, queue_position=positions.get(record.id))


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_job(
    job_id: str,
    actor: Actor = Depends(get_current_actor),
    database: Database = Depends(_database),
) -> None:
    outcome = dismiss_workspace_job(database, workspace_id=actor.workspace_id, job_id=job_id)
    if outcome is JobDismissalOutcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_MESSAGE)
    if outcome is JobDismissalOutcome.ACTIVE:
        raise HTTPException(
            status_code=409,
            detail="进行中的任务无法移除，请先取消或等待任务完成。",
        )
