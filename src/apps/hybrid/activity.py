"""Durable, workspace-scoped activity records for managed desktop jobs.

Hybrid desktops run long provider calls in-process rather than through the
server-mode job database.  This small atomic JSON store preserves the same
user-visible lifecycle without sharing the workspace writer lock that the
provider call holds.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_ACTIVITY_ROWS = 500
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_ID = f"{os.getpid()}:{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _activity_path(workspace_id: str) -> Path:
    if not _SAFE_WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("Invalid workspace id")
    root = Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser().resolve()
    path = (root / workspace_id / "hybrid_activity.json").resolve()
    if root not in path.parents:
        raise ValueError("Activity path escapes the configured workspace root")
    return path


def _lock_for(path: Path) -> threading.RLock:
    canonical = str(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(canonical, threading.RLock())


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read managed desktop activity: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Managed desktop activity must be a JSON array")
    return [row for row in payload if isinstance(row, dict)]


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                rows[:_MAX_ACTIVITY_ROWS],
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def record_asset_activity(
    workspace_id: str,
    *,
    task_id: str,
    job_type: str,
    source: str,
    source_route: str,
    source_id: str,
    series_id: str | None,
    asset_id: str,
    asset_type: str,
    asset_name: str,
    prompt: str | None,
    model_name: str | None,
    batch_size: int,
    aspect_ratio: str | None,
) -> dict[str, Any]:
    """Insert a queued activity before the background task starts."""

    path = _activity_path(workspace_id)
    timestamp = _now()
    source_context: dict[str, Any] = {
        "type": source,
        "route": source_route,
        "asset_id": asset_id,
        "asset_type": asset_type,
    }
    if job_type == "series_asset":
        source_context["series_id"] = source_id
    elif job_type == "project_asset":
        source_context["project_id"] = source_id
        source_context["episode_id"] = source_id
        if series_id:
            source_context["series_id"] = series_id

    parameters: dict[str, str | int | bool] = {"batch_size": max(1, int(batch_size))}
    if aspect_ratio:
        parameters["aspect_ratio"] = _compact(aspect_ratio, limit=32)

    row: dict[str, Any] = {
        "id": f"hybrid:{task_id}",
        "task_id": task_id,
        "type": job_type,
        "status": "queued",
        "category": "image",
        "source": source,
        "progress": 0,
        "progress_stage": "queued",
        "progress_is_estimated": False,
        "progress_steps": [
            {
                "id": "queued",
                "state": "active",
                "started_at": timestamp,
                "finished_at": None,
            }
        ],
        "error": None,
        "detail": _compact(asset_name or asset_id, limit=240),
        "prompt": _compact(prompt, limit=4_000) or None,
        "model_name": _compact(model_name, limit=120) or None,
        "parameters": parameters,
        "source_context": source_context,
        "input_media": [],
        "outputs": [],
        "attempts": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "started_at": None,
        "finished_at": None,
        "managed_read_only": True,
        "activity_kind": "generation",
        "billing_status": None,
        "_process_id": _PROCESS_ID,
    }
    with _lock_for(path):
        rows = [candidate for candidate in _read(path) if candidate.get("task_id") != task_id]
        _write(path, [row, *rows])
    return row


def record_video_activity(
    workspace_id: str,
    *,
    task_id: str,
    job_type: str,
    source: str,
    source_route: str,
    detail: str,
    prompt: str | None,
    model_name: str | None,
    duration: int,
    batch_size: int = 1,
    generation_mode: str = "i2v",
    resolution: str | None = None,
    ratio: str | None = None,
    source_context: dict[str, Any] | None = None,
    input_media: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Insert a queued desktop video lifecycle for the API Calls dashboard."""

    path = _activity_path(workspace_id)
    timestamp = _now()
    context = {
        "type": source,
        "route": source_route,
        **dict(source_context or {}),
    }
    parameters: dict[str, str | int | bool] = {
        "batch_size": max(1, int(batch_size)),
        "duration": max(1, int(duration)),
        "generation_mode": _compact(generation_mode, limit=32) or "i2v",
    }
    if resolution:
        parameters["resolution"] = _compact(resolution, limit=32)
    if ratio:
        parameters["ratio"] = _compact(ratio, limit=32)

    row: dict[str, Any] = {
        "id": f"hybrid:{task_id}",
        "task_id": task_id,
        "type": job_type,
        "status": "queued",
        "category": "video",
        "source": source,
        "progress": 0,
        "progress_stage": "queued",
        "progress_is_estimated": False,
        "progress_steps": [
            {
                "id": "queued",
                "state": "active",
                "started_at": timestamp,
                "finished_at": None,
            }
        ],
        "error": None,
        "detail": _compact(detail, limit=240),
        "prompt": _compact(prompt, limit=4_000) or None,
        "model_name": _compact(model_name, limit=120) or None,
        "parameters": parameters,
        "source_context": context,
        "input_media": [dict(item) for item in (input_media or [])[:20]],
        "outputs": [],
        "attempts": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "started_at": None,
        "finished_at": None,
        "managed_read_only": True,
        "activity_kind": "generation",
        "billing_status": None,
        "_process_id": _PROCESS_ID,
    }
    with _lock_for(path):
        rows = [candidate for candidate in _read(path) if candidate.get("task_id") != task_id]
        _write(path, [row, *rows])
    return row


def update_asset_activity(
    workspace_id: str,
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    error_code: str | None = None,
    error_diagnostic: str | None = None,
    outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Atomically advance one activity through running/completed/failed."""

    if status not in {"running", "completed", "failed"}:
        raise ValueError("Unsupported managed desktop activity status")
    path = _activity_path(workspace_id)
    timestamp = _now()
    with _lock_for(path):
        rows = _read(path)
        row = next((candidate for candidate in rows if candidate.get("task_id") == task_id), None)
        if row is None:
            return None

        queued_step = {
            "id": "queued",
            "state": "completed",
            "started_at": row.get("created_at"),
            "finished_at": row.get("started_at") or timestamp,
        }
        provider_step = {
            "id": "provider_processing",
            "state": (
                "active"
                if status == "running"
                else ("completed" if status == "completed" else "failed")
            ),
            "started_at": row.get("started_at") or timestamp,
            "finished_at": timestamp if status in {"completed", "failed"} else None,
            "message": _compact(error, limit=1_000) or None,
        }
        row.update(
            status=status,
            progress=100 if status == "completed" else (50 if status == "running" else 0),
            progress_stage="completed" if status == "completed" else "provider_processing",
            progress_is_estimated=status == "running",
            progress_steps=[queued_step, provider_step],
            error=_compact(error, limit=1_000) or None,
            error_code=_compact(error_code, limit=120) or None,
            error_diagnostic=_compact(error_diagnostic, limit=2_000) or None,
            updated_at=timestamp,
            started_at=row.get("started_at") or timestamp,
            finished_at=timestamp if status in {"completed", "failed"} else None,
            _process_id=_PROCESS_ID,
        )
        if outputs is not None:
            row["outputs"] = [dict(output) for output in outputs[:50]]
        _write(path, rows)
        return dict(row)


def list_activity(workspace_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return newest-first rows and fail work orphaned by an app restart."""

    path = _activity_path(workspace_id)
    now = _now()
    with _lock_for(path):
        rows = _read(path)
        changed = False
        for row in rows:
            if row.get("status") in {"queued", "running"} and row.get("_process_id") != _PROCESS_ID:
                row.update(
                    status="failed",
                    progress=0,
                    progress_stage="provider_processing",
                    progress_is_estimated=False,
                    error="EnMotion 在生成完成前重新启动，请重新生成此素材。",
                    updated_at=now,
                    finished_at=now,
                    _process_id=_PROCESS_ID,
                )
                for step in row.get("progress_steps") or []:
                    if isinstance(step, dict) and step.get("state") == "active":
                        step["state"] = "failed"
                        step["finished_at"] = now
                changed = True
        if changed:
            _write(path, rows)
        result: list[dict[str, Any]] = []
        for row in rows[: max(1, min(int(limit), 500))]:
            result.append({key: value for key, value in row.items() if not key.startswith("_")})
        return result
