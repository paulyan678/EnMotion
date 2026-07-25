"""Atomic snapshots used to enforce workspace storage transactions."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path

from .quotas import workspace_output_root


logger = logging.getLogger(__name__)


WORKSPACE_METADATA_FILES = frozenset(
    {
        "projects.json",
        "series.json",
        "library_assets.json",
        "playground_history.json",
        "playground_templates.json",
    }
)


@dataclass(slots=True)
class WorkspaceMutation:
    """Request-local destructive work committed only after quota validation."""

    workspace_id: str
    transaction_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    deferred_deletions: set[Path] = field(default_factory=set)
    staged_deletions: dict[Path, Path] = field(default_factory=dict)
    staged_bytes: int = 0


class WorkspaceFileDeletionError(RuntimeError):
    """Raised when workspace media cannot be staged or purged safely."""


_active_mutation: ContextVar[WorkspaceMutation | None] = ContextVar(
    "enmotion_workspace_mutation", default=None
)


def bind_workspace_mutation(workspace_id: str) -> Token[WorkspaceMutation | None]:
    return _active_mutation.set(WorkspaceMutation(workspace_id=workspace_id))


def reset_workspace_mutation(token: Token[WorkspaceMutation | None]) -> None:
    _active_mutation.reset(token)


def defer_workspace_file_deletions(
    workspace_id: str, paths: set[Path] | list[Path] | tuple[Path, ...]
) -> None:
    """Queue safe workspace-local deletes until a mutation is committed.

    Outside an authenticated request transaction (desktop mode and focused
    pipeline tests), the same operation is applied immediately.
    """

    root = workspace_output_root(workspace_id)
    safe_paths: set[Path] = set()
    for raw in paths:
        raw_path = Path(raw).expanduser()
        if raw_path.is_symlink():
            continue
        try:
            path = raw_path.resolve()
        except (OSError, RuntimeError):
            continue
        if root not in path.parents or path.is_symlink():
            continue
        safe_paths.add(path)

    mutation = _active_mutation.get()
    if mutation is not None and mutation.workspace_id == workspace_id:
        mutation.deferred_deletions.update(safe_paths)
        return
    _delete_paths(safe_paths)


def commit_workspace_mutation(workspace_id: str) -> tuple[int, int]:
    mutation = _active_mutation.get()
    if mutation is None or mutation.workspace_id != workspace_id:
        return (0, 0)
    stage_workspace_file_deletions(workspace_id)
    removed_files = 0
    failures: list[str] = []
    for original, tombstone in sorted(mutation.staged_deletions.items()):
        try:
            tombstone.unlink()
        except FileNotFoundError:
            # A missing tombstone is already physically gone. This can happen
            # when a prior cleanup attempt removed the file but was
            # interrupted before the request-local bookkeeping was cleared.
            removed_files += 1
            continue
        except OSError as exc:
            failures.append(f"{original}: {exc}")
            continue
        removed_files += 1
    if failures:
        _remove_empty_trash_directories(workspace_id, mutation.transaction_id)
        raise WorkspaceFileDeletionError(
            "Workspace media deletion reached its irreversible commit point, "
            "but some staged files could not be purged: " + "; ".join(failures)
        )
    removed_bytes = mutation.staged_bytes
    mutation.staged_deletions.clear()
    mutation.staged_bytes = 0
    _remove_empty_trash_directories(workspace_id, mutation.transaction_id)
    # Publish only after metadata and media reached their irreversible commit
    # point. The publisher switches its manifest atomically; a failed rebuild
    # therefore leaves the previous valid revision available instead of
    # exposing a partial or false-empty library.
    try:
        from ..web_runtime.workspace_snapshot import publish_workspace_snapshot

        publish_workspace_snapshot(workspace_id)
    except Exception:
        logger.exception(
            "Could not publish committed Asset Library read model workspace=%s",
            workspace_id,
        )
    return removed_files, removed_bytes


def stage_workspace_file_deletions(workspace_id: str) -> tuple[int, int]:
    """Atomically move queued files outside the live output tree.

    Tombstones live in a sibling directory on the same filesystem, so each
    ``os.replace`` is atomic. If any move fails, every move from this staging
    attempt is restored before the error escapes. Quota checks can run after
    staging and see the post-delete live usage.
    """

    mutation = _active_mutation.get()
    if mutation is None or mutation.workspace_id != workspace_id:
        return (0, 0)
    root = workspace_output_root(workspace_id)
    trash_root = root.parent / ".trash" / mutation.transaction_id
    newly_staged: list[tuple[Path, Path, int]] = []
    try:
        for original in sorted(mutation.deferred_deletions):
            if original in mutation.staged_deletions:
                continue
            try:
                relative = original.relative_to(root)
            except ValueError:
                continue
            if original.is_symlink():
                continue
            try:
                size = original.stat().st_size
            except FileNotFoundError:
                continue
            tombstone = trash_root / relative
            tombstone.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original, tombstone)
            newly_staged.append((original, tombstone, size))
    except OSError as exc:
        for original, tombstone, _size in reversed(newly_staged):
            original.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(tombstone, original)
            except OSError as restore_exc:
                raise WorkspaceFileDeletionError(
                    f"Could not stage {original} and rollback failed: {restore_exc}"
                ) from exc
        _remove_empty_trash_directories(workspace_id, mutation.transaction_id)
        raise WorkspaceFileDeletionError(
            f"Could not stage workspace media for deletion: {exc}"
        ) from exc

    for original, tombstone, size in newly_staged:
        mutation.staged_deletions[original] = tombstone
        mutation.staged_bytes += size
    mutation.deferred_deletions.clear()
    return len(newly_staged), sum(size for _original, _tombstone, size in newly_staged)


def restore_workspace_file_deletions(workspace_id: str) -> tuple[int, int]:
    """Restore every staged tombstone before rolling metadata back."""

    mutation = _active_mutation.get()
    if mutation is None or mutation.workspace_id != workspace_id:
        return (0, 0)
    restored_files = 0
    restored_bytes = 0
    for original, tombstone in sorted(
        mutation.staged_deletions.items(), reverse=True
    ):
        try:
            size = tombstone.stat().st_size
        except FileNotFoundError:
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(tombstone, original)
        except OSError as exc:
            raise WorkspaceFileDeletionError(
                f"Could not restore staged workspace media {original}: {exc}"
            ) from exc
        restored_files += 1
        restored_bytes += size
    mutation.staged_deletions.clear()
    mutation.staged_bytes = 0
    mutation.deferred_deletions.clear()
    _remove_empty_trash_directories(workspace_id, mutation.transaction_id)
    return restored_files, restored_bytes


def _remove_empty_trash_directories(workspace_id: str, transaction_id: str) -> None:
    root = workspace_output_root(workspace_id)
    transaction_root = root.parent / ".trash" / transaction_id
    trash_root = transaction_root.parent
    if transaction_root.exists():
        for directory, _subdirectories, _files in os.walk(
            transaction_root, topdown=False
        ):
            try:
                Path(directory).rmdir()
            except OSError:
                pass
    try:
        trash_root.rmdir()
    except OSError:
        pass


def defer_unreferenced_workspace_media(
    workspace_id: str,
    starting_metadata: dict[str, bytes | None],
) -> list[str]:
    """Queue media made unreachable by a successful metadata mutation.

    Every authenticated write already snapshots the authoritative workspace
    JSON files for rollback. Comparing that snapshot with the committed JSON
    makes file reclamation a request-level invariant instead of relying on
    every individual DELETE/PATCH endpoint to remember its own cleanup call.
    Shared files remain protected because the media GC scans all current
    workspace records before queuing a deletion.
    """

    from ...utils.media_gc import (
        load_workspace_reference_values,
        reclaim_unreferenced_workspace_media,
    )

    deleted_values: list[object] = []
    for content in starting_metadata.values():
        if content is None:
            continue
        try:
            deleted_values.append(json.loads(content))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # A malformed starting record cannot be compared safely. The
            # metadata transaction may still commit, but cleanup is skipped.
            return []

    root = workspace_output_root(workspace_id)
    try:
        remaining_values = load_workspace_reference_values(root)
    except RuntimeError:
        return []

    return reclaim_unreferenced_workspace_media(
        deleted_value=deleted_values,
        remaining_values=remaining_values,
        output_root=root,
        delete_callback=lambda paths: defer_workspace_file_deletions(
            workspace_id, paths
        ),
    )


def _delete_paths(paths: set[Path]) -> tuple[int, int]:
    removed_files = 0
    removed_bytes = 0
    for path in sorted(paths):
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed_files += 1
        removed_bytes += size
    return removed_files, removed_bytes


def snapshot_workspace_files(workspace_id: str) -> set[str]:
    """Return regular, non-symlink files relative to one workspace root."""

    root = workspace_output_root(workspace_id)
    if not root.exists():
        return set()
    files: set[str] = set()
    for directory, _subdirectories, names in os.walk(root, followlinks=False):
        for name in names:
            path = Path(directory) / name
            try:
                if path.is_file() and not path.is_symlink():
                    files.add(path.relative_to(root).as_posix())
            except (FileNotFoundError, ValueError):
                continue
    return files


def snapshot_workspace_metadata(workspace_id: str) -> dict[str, bytes | None]:
    """Capture the small authoritative JSON files before a mutation."""

    root = workspace_output_root(workspace_id)
    snapshot: dict[str, bytes | None] = {}
    for name in WORKSPACE_METADATA_FILES:
        path = root / name
        try:
            snapshot[name] = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        except FileNotFoundError:
            snapshot[name] = None
    return snapshot


def restore_workspace_metadata(
    workspace_id: str, snapshot: dict[str, bytes | None]
) -> None:
    """Atomically restore metadata captured by :func:`snapshot_workspace_metadata`."""

    root = workspace_output_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    for name in WORKSPACE_METADATA_FILES:
        content = snapshot.get(name)
        destination = root / name
        if content is None:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            continue

        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=root,
                prefix=f".{name}.",
                suffix=".rollback",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


def remove_new_workspace_files(
    workspace_id: str,
    starting_files: set[str],
    *,
    preserve_metadata: bool = True,
) -> tuple[int, int]:
    """Remove files created after a snapshot without following links."""

    root = workspace_output_root(workspace_id)
    current_files = snapshot_workspace_files(workspace_id)
    removed_files = 0
    removed_bytes = 0
    for relative in sorted(current_files - starting_files, reverse=True):
        if preserve_metadata and relative in WORKSPACE_METADATA_FILES:
            continue
        path = (root / relative).resolve()
        if root not in path.parents or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed_files += 1
        removed_bytes += size

    if root.exists():
        for directory, _subdirectories, _files in os.walk(root, topdown=False):
            path = Path(directory)
            if path == root:
                continue
            try:
                path.rmdir()
            except OSError:
                pass
    return removed_files, removed_bytes
