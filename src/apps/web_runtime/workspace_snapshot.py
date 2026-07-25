"""Committed, immutable workspace metadata snapshots.

Each workspace mutation still writes the existing JSON files for compatibility.
After that mutation commits, this module copies the three Asset Library source
documents into a revision directory, builds a compact feed, and atomically
switches one manifest.  Readers therefore consume one logical revision instead
of racing three independently replaced files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..comic_gen.models import GlobalAssetLibrary, Script, Series
from ..server.quotas import workspace_output_root
from .asset_library_feed import AssetLibrarySnapshot, build_asset_library_snapshot

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = 2
SNAPSHOT_METADATA_FILES = ("projects.json", "series.json", "library_assets.json")
SNAPSHOT_ROOT_NAME = ".asset-library-read-model"
SNAPSHOT_MANIFEST_NAME = "current.json"
SNAPSHOT_FEED_NAME = "asset_library_feed.json"
RETAINED_REVISIONS = 3


class WorkspaceSnapshotUnavailable(RuntimeError):
    """Raised when live metadata exists but no valid committed read model does."""


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshotRef:
    workspace_id: str
    revision: int
    metadata_root: Optional[Path]
    feed_path: Optional[Path]
    generated_at: float
    source_fingerprint: str

    @property
    def is_empty(self) -> bool:
        return self.revision == 0 and self.metadata_root is None


def snapshot_root_for(workspace_id: str) -> Path:
    return workspace_output_root(workspace_id).parent / SNAPSHOT_ROOT_NAME


def _manifest_path(workspace_id: str) -> Path:
    return snapshot_root_for(workspace_id) / SNAPSHOT_MANIFEST_NAME


def _revision_dir(root: Path, revision: int) -> Path:
    return root / "revisions" / f"{revision:020d}"


def _source_bytes(output_root: Path) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for name in SNAPSHOT_METADATA_FILES:
        path = output_root / name
        try:
            values[name] = path.read_bytes()
        except FileNotFoundError:
            values[name] = b"{}"
    return values


def _has_live_metadata(output_root: Path) -> bool:
    return any((output_root / name).is_file() for name in SNAPSHOT_METADATA_FILES)


def _fingerprint(values: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in SNAPSHOT_METADATA_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(values[name])
        digest.update(b"\0")
    return digest.hexdigest()


def _decode_mapping(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceSnapshotUnavailable(f"Invalid {label} metadata") from exc
    if not isinstance(value, dict):
        raise WorkspaceSnapshotUnavailable(f"Invalid {label} metadata")
    return value


def _build_feed(
    values: dict[str, bytes], revision: int, generated_at: float
) -> AssetLibrarySnapshot:
    projects_raw = _decode_mapping(values["projects.json"], label="project")
    series_raw = _decode_mapping(values["series.json"], label="series")
    library_raw = _decode_mapping(values["library_assets.json"], label="library")
    try:
        projects = [Script.model_validate(value) for value in projects_raw.values()]
        series = [Series.model_validate(value) for value in series_raw.values()]
        library = GlobalAssetLibrary.model_validate(library_raw)
    except Exception as exc:
        raise WorkspaceSnapshotUnavailable("Workspace asset metadata is invalid") from exc
    return build_asset_library_snapshot(
        revision=revision,
        series=series,
        projects=projects,
        library=library,
        generated_at=generated_at,
    )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(
        path,
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceSnapshotUnavailable("Committed workspace snapshot is unavailable") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(raw.get("revision"), int)
        or raw["revision"] < 1
        or not isinstance(raw.get("generated_at"), (int, float))
        or not isinstance(raw.get("source_fingerprint"), str)
    ):
        raise WorkspaceSnapshotUnavailable("Committed workspace snapshot is invalid")
    return raw


def _load_feed(path: str, revision: int) -> AssetLibrarySnapshot:
    try:
        snapshot = AssetLibrarySnapshot.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (
        FileNotFoundError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise WorkspaceSnapshotUnavailable("Committed Asset Library feed is invalid") from exc
    if snapshot.revision != revision:
        raise WorkspaceSnapshotUnavailable("Committed workspace snapshot revision does not match")
    return snapshot


@lru_cache(maxsize=128)
def _read_feed_cached(
    path: str,
    revision: int,
    file_token: tuple[int, int, int],
) -> AssetLibrarySnapshot:
    """Cache one already-derived immutable revision inside each worker."""

    del file_token
    return _load_feed(path, revision)


def _read_feed(
    reference: WorkspaceSnapshotRef,
    *,
    use_memory_cache: bool = False,
) -> AssetLibrarySnapshot:
    if reference.is_empty:
        return AssetLibrarySnapshot(revision=0, generated_at=0.0, items=[])
    assert reference.feed_path is not None
    path = str(reference.feed_path)
    if use_memory_cache:
        try:
            stat = reference.feed_path.stat()
        except OSError as exc:
            raise WorkspaceSnapshotUnavailable(
                "Committed Asset Library feed is unavailable"
            ) from exc
        return _read_feed_cached(
            path,
            reference.revision,
            (stat.st_ino, stat.st_mtime_ns, stat.st_size),
        )
    return _load_feed(path, reference.revision)


def _atomic_manifest(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".current.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _prune_revisions(root: Path, *, keep: int = RETAINED_REVISIONS) -> None:
    revisions_root = root / "revisions"
    try:
        revisions = sorted(
            (path for path in revisions_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
    except FileNotFoundError:
        return
    for path in revisions[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def _next_revision(root: Path, current: Optional[dict[str, Any]]) -> int:
    """Choose a revision that cannot collide with an interrupted publisher.

    A process can finish renaming a fully written revision and then stop before
    switching ``current.json``.  Such a directory is harmless but must not make
    the next publish fail or overwrite immutable data.
    """

    latest = int(current["revision"]) if current else 0
    revisions_root = root / "revisions"
    try:
        for path in revisions_root.iterdir():
            if not path.is_dir() or not path.name.isdigit():
                continue
            latest = max(latest, int(path.name))
    except FileNotFoundError:
        pass
    return latest + 1


def publish_workspace_snapshot(
    workspace_id: str,
    *,
    force: bool = False,
) -> WorkspaceSnapshotRef:
    """Publish one complete revision.

    The caller must hold the workspace writer lock.  A failed build never
    changes ``current.json``, so readers retain the last committed revision.
    """

    output_root = workspace_output_root(workspace_id)
    values = _source_bytes(output_root)
    source_fingerprint = _fingerprint(values)
    root = snapshot_root_for(workspace_id)
    current: Optional[dict[str, Any]] = None
    try:
        current = _read_manifest(root / SNAPSHOT_MANIFEST_NAME)
    except WorkspaceSnapshotUnavailable:
        pass
    if (
        not force
        and current is not None
        and current.get("source_fingerprint") == source_fingerprint
    ):
        try:
            reference = resolve_workspace_snapshot(workspace_id)
            feed = _read_feed(reference)
            logger.info(
                "Asset usage index cache=hit workspace=%s revision=%s items=%s",
                workspace_id,
                reference.revision,
                len(feed.items),
            )
            return reference
        except WorkspaceSnapshotUnavailable:
            logger.warning(
                "Asset usage index cache=stale workspace=%s; rebuilding",
                workspace_id,
            )

    revision = _next_revision(root, current)
    generated_at = time.time()
    feed = _build_feed(values, revision, generated_at)
    revisions_root = root / "revisions"
    revisions_root.mkdir(parents=True, exist_ok=True)
    building = revisions_root / f".building-{uuid.uuid4().hex}"
    destination = _revision_dir(root, revision)
    building.mkdir(mode=0o700)
    try:
        for name, content in values.items():
            _write_bytes(building / name, content)
        _write_json(building / SNAPSHOT_FEED_NAME, feed.model_dump(mode="json"))
        _fsync_directory(building)
        os.replace(building, destination)
        _fsync_directory(revisions_root)
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise

    manifest = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "revision": revision,
        "generated_at": generated_at,
        "source_fingerprint": source_fingerprint,
    }
    _atomic_manifest(root / SNAPSHOT_MANIFEST_NAME, manifest)
    _prune_revisions(root)
    logger.info(
        "Published workspace asset read model workspace=%s revision=%s items=%s",
        workspace_id,
        revision,
        len(feed.items),
    )
    return WorkspaceSnapshotRef(
        workspace_id=workspace_id,
        revision=revision,
        metadata_root=destination,
        feed_path=destination / SNAPSHOT_FEED_NAME,
        generated_at=generated_at,
        source_fingerprint=source_fingerprint,
    )


def resolve_workspace_snapshot(
    workspace_id: str,
    *,
    attempts: int = 3,
) -> WorkspaceSnapshotRef:
    """Resolve a stable immutable revision with bounded retry.

    A genuinely new workspace with no metadata is represented by revision zero
    in memory.  Existing metadata without a committed revision is an explicit,
    retryable failure rather than an empty library.
    """

    output_root = workspace_output_root(workspace_id)
    manifest_path = _manifest_path(workspace_id)
    last_error: Optional[Exception] = None
    for _attempt in range(max(1, attempts)):
        try:
            manifest = _read_manifest(manifest_path)
            revision = int(manifest["revision"])
            directory = _revision_dir(snapshot_root_for(workspace_id), revision)
            feed_path = directory / SNAPSHOT_FEED_NAME
            if not directory.is_dir() or not feed_path.is_file():
                raise WorkspaceSnapshotUnavailable(
                    "Committed workspace snapshot files are unavailable"
                )
            return WorkspaceSnapshotRef(
                workspace_id=workspace_id,
                revision=revision,
                metadata_root=directory,
                feed_path=feed_path,
                generated_at=float(manifest["generated_at"]),
                source_fingerprint=str(manifest["source_fingerprint"]),
            )
        except WorkspaceSnapshotUnavailable as exc:
            last_error = exc
            # A publisher may have switched the manifest while pruning an older
            # revision. Re-reading is bounded and never waits on provider work.
            continue
    if not _has_live_metadata(output_root):
        return WorkspaceSnapshotRef(
            workspace_id=workspace_id,
            revision=0,
            metadata_root=None,
            feed_path=None,
            generated_at=0.0,
            source_fingerprint="empty",
        )
    raise WorkspaceSnapshotUnavailable(
        "Committed workspace snapshot is temporarily unavailable"
    ) from last_error


def read_asset_library_snapshot(workspace_id: str) -> AssetLibrarySnapshot:
    reference = resolve_workspace_snapshot(workspace_id)
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        try:
            prior_hits = _read_feed_cached.cache_info().hits
            snapshot = _read_feed(reference, use_memory_cache=True)
            cache_state = "hit" if _read_feed_cached.cache_info().hits > prior_hits else "miss"
            logger.info(
                "Asset Library feed memory_cache=%s workspace=%s revision=%s items=%s",
                cache_state,
                workspace_id,
                reference.revision,
                len(snapshot.items),
            )
            return snapshot
        except WorkspaceSnapshotUnavailable as exc:
            last_error = exc
            reference = resolve_workspace_snapshot(workspace_id)
    raise WorkspaceSnapshotUnavailable(
        "Committed Asset Library feed is temporarily unavailable"
    ) from last_error


def workspace_ids_with_metadata(workspace_root: Path) -> list[str]:
    try:
        children = list(workspace_root.iterdir())
    except FileNotFoundError:
        return []
    workspace_ids: list[str] = []
    for child in children:
        if not child.is_dir() or child.is_symlink():
            continue
        output_root = child / "output"
        if _has_live_metadata(output_root):
            workspace_ids.append(child.name)
    return sorted(workspace_ids)
