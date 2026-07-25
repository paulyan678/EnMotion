from __future__ import annotations

from pathlib import Path

import pytest

from src.apps.server.workspace_storage import (
    WorkspaceFileDeletionError,
    bind_workspace_mutation,
    commit_workspace_mutation,
    defer_workspace_file_deletions,
    reset_workspace_mutation,
    stage_workspace_file_deletions,
)


def test_partial_purge_failure_is_reported_as_irreversible_and_cleans_empty_dirs(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = "purge-failure"
    output = tmp_path / "workspaces" / workspace_id / "output"
    first = output / "a" / "first.png"
    second = output / "b" / "second.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    token = bind_workspace_mutation(workspace_id)
    try:
        defer_workspace_file_deletions(workspace_id, {first, second})
        assert stage_workspace_file_deletions(workspace_id) == (2, 11)
        assert not first.exists()
        assert not second.exists()

        real_unlink = Path.unlink

        def fail_second_tombstone(path: Path, *args, **kwargs):
            if path.name == second.name and ".trash" in path.parts:
                raise PermissionError("read-only tombstone")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_second_tombstone)
        with pytest.raises(
            WorkspaceFileDeletionError, match="irreversible commit point"
        ):
            commit_workspace_mutation(workspace_id)

        trash = output.parent / ".trash"
        assert not list(trash.rglob(first.name))
        assert not list(trash.rglob("a"))
        assert len(list(trash.rglob(second.name))) == 1
    finally:
        reset_workspace_mutation(token)
