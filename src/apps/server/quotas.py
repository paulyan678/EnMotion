"""Workspace storage accounting and quota enforcement."""

from __future__ import annotations

import os
from pathlib import Path

from .database import Database
from .models import Workspace


class StorageQuotaExceededError(RuntimeError):
    pass


def workspace_output_root(workspace_id: str) -> Path:
    if not workspace_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in workspace_id):
        raise ValueError("Invalid workspace id")
    root = Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser().resolve()
    output = (root / workspace_id / "output").resolve()
    if root not in output.parents:
        raise ValueError("Workspace path escapes the configured root")
    return output


def workspace_usage_bytes(workspace_id: str) -> int:
    root = workspace_output_root(workspace_id)
    if not root.exists():
        return 0
    total = 0
    for directory, _subdirectories, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                # Atomic replacements can remove a just-enumerated temp file.
                continue
    return total


def workspace_quota_bytes(database: Database, workspace_id: str) -> int:
    with database.session() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found")
        return workspace.storage_quota_bytes


def ensure_storage_capacity(
    database: Database,
    *,
    workspace_id: str,
    reserve_bytes: int = 0,
) -> int:
    quota = workspace_quota_bytes(database, workspace_id)
    usage = workspace_usage_bytes(workspace_id)
    if usage + max(0, reserve_bytes) > quota:
        raise StorageQuotaExceededError(
            f"Workspace storage quota exceeded ({usage} of {quota} bytes used)"
        )
    return usage


def enforce_saved_file_quota(
    database: Database,
    *,
    workspace_id: str,
    created_path: str,
) -> None:
    try:
        ensure_storage_capacity(database, workspace_id=workspace_id)
    except StorageQuotaExceededError:
        # The current request owns this freshly-created file, so rollback is
        # safe and avoids leaving the workspace permanently above quota.
        try:
            Path(created_path).unlink()
        except FileNotFoundError:
            pass
        raise
