"""Lazy, request-scoped ComicGenPipeline registry for private workspaces."""

from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from ..comic_gen.pipeline import ComicGenPipeline
from ..hybrid.config import workspace_isolation_enabled
from ..server.config import server_mode_enabled
from .context import get_tenant
from .file_lock import interprocess_lock, nonblocking_read_active
from .workspace_snapshot import publish_workspace_snapshot, resolve_workspace_snapshot

_SAFE_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
logger = logging.getLogger(__name__)


class WorkspacePipelineRegistry:
    """Own one isolated pipeline and output root per authenticated workspace."""

    def __init__(self, workspace_root: Optional[str] = None):
        import os

        configured = workspace_root or os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")
        self.workspace_root = Path(configured).expanduser().resolve()
        self._lock = threading.RLock()
        self._writer_pipelines: Dict[
            str, tuple[ComicGenPipeline, tuple[tuple[int, int, int, int], ...]]
        ] = {}
        self._reader_pipelines: Dict[str, tuple[ComicGenPipeline, int]] = {}
        self._build_locks: Dict[str, threading.Lock] = {}

    @staticmethod
    def validate_workspace_id(workspace_id: str) -> str:
        value = str(workspace_id)
        if not _SAFE_WORKSPACE_ID.fullmatch(value):
            raise ValueError("Invalid workspace id")
        return value

    def output_root_for(self, workspace_id: str) -> Path:
        safe_id = self.validate_workspace_id(workspace_id)
        output_root = (self.workspace_root / safe_id / "output").resolve()
        if self.workspace_root not in output_root.parents:
            raise ValueError("Workspace path escapes the configured root")
        return output_root

    def lock_path_for(self, workspace_id: str) -> Path:
        return self.output_root_for(workspace_id).parent / ".workspace.lock"

    def _fingerprint(self, workspace_id: str) -> tuple[tuple[int, int, int, int], ...]:
        root = self.output_root_for(workspace_id)
        values: list[tuple[int, int, int, int]] = []
        for name in ("projects.json", "series.json", "library_assets.json"):
            try:
                stat = (root / name).stat()
                # Atomic save replaces a file. Include inode and ctime so a
                # same-size replacement cannot be mistaken for the cached
                # object even on a coarse or deliberately preserved mtime.
                values.append((stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
            except FileNotFoundError:
                values.append((-1, -1, -1, -1))
        return tuple(values)

    def _build_lock_for(self, workspace_id: str) -> threading.Lock:
        with self._lock:
            return self._build_locks.setdefault(workspace_id, threading.Lock())

    def _writer(self, workspace_id: str) -> ComicGenPipeline:
        """Return a live mutable pipeline after a stable pre/post fingerprint.

        Pipeline construction performs startup migrations in writer contexts.
        Those migrations may legitimately change the first fingerprint, so the
        build is retried.  An object is never cached under a fingerprint that
        describes different source files.
        """

        with self._lock:
            cached = self._writer_pipelines.get(workspace_id)
        fingerprint = self._fingerprint(workspace_id)
        if cached is not None and cached[1] == fingerprint:
            return cached[0]

        with self._build_lock_for(workspace_id):
            with self._lock:
                cached = self._writer_pipelines.get(workspace_id)
            fingerprint = self._fingerprint(workspace_id)
            if cached is not None and cached[1] == fingerprint:
                return cached[0]

            output_root = self.output_root_for(workspace_id)
            output_root.mkdir(parents=True, exist_ok=True)
            for _attempt in range(3):
                before = self._fingerprint(workspace_id)
                pipeline = ComicGenPipeline(
                    {
                        "output_root": str(output_root),
                        # Server jobs have their own durable recovery state,
                        # while hybrid tasks use in-process BackgroundTasks and
                        # must be released from pending/processing after a
                        # process restart.
                        "recover_orphan_tasks": not server_mode_enabled(),
                    }
                )
                after = self._fingerprint(workspace_id)
                if before == after:
                    with self._lock:
                        self._writer_pipelines[workspace_id] = (pipeline, after)
                    return pipeline
            raise RuntimeError(
                "Workspace metadata changed repeatedly while constructing the writer"
            )

    def _reader(self, workspace_id: str) -> ComicGenPipeline:
        """Return a read-only pipeline backed by one immutable revision."""

        reference = resolve_workspace_snapshot(workspace_id)
        with self._lock:
            cached = self._reader_pipelines.get(workspace_id)
        if cached is not None and cached[1] == reference.revision:
            return cached[0]

        with self._build_lock_for(workspace_id):
            reference = resolve_workspace_snapshot(workspace_id)
            with self._lock:
                cached = self._reader_pipelines.get(workspace_id)
            if cached is not None and cached[1] == reference.revision:
                return cached[0]

            output_root = self.output_root_for(workspace_id)
            # Revision zero is a genuine, never-persisted empty workspace.
            # Pointing at a non-existent metadata directory lets the pipeline
            # construct an empty read model without creating files or folders.
            metadata_root = reference.metadata_root or (output_root.parent / ".empty-read-model")
            pipeline = ComicGenPipeline(
                {
                    "output_root": str(output_root),
                    "metadata_root": str(metadata_root),
                    "read_only": True,
                    "recover_orphan_tasks": False,
                }
            )
            with self._lock:
                self._reader_pipelines[workspace_id] = (
                    pipeline,
                    reference.revision,
                )
            return pipeline

    def get(self, workspace_id: str) -> ComicGenPipeline:
        safe_id = self.validate_workspace_id(workspace_id)
        if nonblocking_read_active(self.lock_path_for(safe_id)):
            return self._reader(safe_id)
        return self._writer(safe_id)

    def _refresh_writer_fingerprint(
        self,
        workspace_id: str,
        pipeline: ComicGenPipeline,
    ) -> None:
        """Keep in-memory task state attached after this writer persists JSON."""

        fingerprint = self._fingerprint(workspace_id)
        with self._lock:
            cached = self._writer_pipelines.get(workspace_id)
            if cached is not None and cached[0] is pipeline:
                self._writer_pipelines[workspace_id] = (pipeline, fingerprint)

    @contextmanager
    def locked(self, workspace_id: str) -> Iterator[ComicGenPipeline]:
        """Yield a freshly synchronized pipeline under the workspace lock."""

        safe_id = self.validate_workspace_id(workspace_id)
        lock_path = self.lock_path_for(safe_id)
        read_only = nonblocking_read_active(lock_path)
        before = self._fingerprint(safe_id)
        succeeded = False
        with interprocess_lock(lock_path):
            pipeline = self.get(safe_id)
            try:
                yield pipeline
                succeeded = True
            finally:
                after = self._fingerprint(safe_id)
                if succeeded and not read_only and after != before:
                    try:
                        # Hybrid desktop mutations do not pass through the
                        # server transaction middleware. Publish here while
                        # the writer lock is still held so non-blocking GETs
                        # immediately advance to one coherent revision after
                        # both request and background-task mutations.
                        publish_workspace_snapshot(safe_id)
                    except Exception:
                        logger.exception(
                            "Could not publish committed workspace read model workspace=%s",
                            safe_id,
                        )
                # Pipeline methods atomically replace metadata files while also
                # retaining transient task maps in memory. Refreshing the
                # cached fingerprint here prevents the next status poll from
                # rebuilding the writer and losing those task records.
                self._refresh_writer_fingerprint(safe_id, pipeline)

    def discard(self, workspace_id: str) -> None:
        """Drop only the in-memory instance; persisted workspace data remains."""

        safe_id = self.validate_workspace_id(workspace_id)
        with self._lock:
            self._writer_pipelines.pop(safe_id, None)
            self._reader_pipelines.pop(safe_id, None)

    def transient_task_status(self, workspace_id: str, task_id: str) -> dict[str, Any] | None:
        """Read one in-memory task without waiting for its provider-call lock.

        Hybrid background jobs keep their lifecycle in the writer pipeline.
        The provider call mutates only primitive fields in that task dictionary,
        so taking a bounded snapshot is safe under CPython's object semantics and
        avoids blocking the status poll on the same multi-minute workspace lock.
        """

        safe_id = self.validate_workspace_id(workspace_id)
        with self._lock:
            cached = self._writer_pipelines.get(safe_id)
        if cached is None:
            return None
        return cached[0].get_asset_generation_task_status(task_id)


class PipelineProxy:
    """Delegate to the desktop pipeline or the current tenant's private one."""

    def __init__(
        self,
        local_pipeline: ComicGenPipeline | Callable[[], ComicGenPipeline],
        registry: Optional[WorkspacePipelineRegistry] = None,
    ):
        if callable(local_pipeline) and not isinstance(local_pipeline, ComicGenPipeline):
            object.__setattr__(self, "_local_pipeline", None)
            object.__setattr__(self, "_local_factory", local_pipeline)
        else:
            object.__setattr__(self, "_local_pipeline", local_pipeline)
            object.__setattr__(self, "_local_factory", None)
        object.__setattr__(self, "_local_lock", threading.Lock())
        object.__setattr__(self, "_registry", registry or WorkspacePipelineRegistry())

    def _local(self) -> ComicGenPipeline:
        current = object.__getattribute__(self, "_local_pipeline")
        if current is not None:
            return current
        with object.__getattribute__(self, "_local_lock"):
            current = object.__getattribute__(self, "_local_pipeline")
            if current is None:
                factory = object.__getattribute__(self, "_local_factory")
                if factory is None:
                    raise RuntimeError("Local pipeline factory is unavailable")
                current = factory()
                object.__setattr__(self, "_local_pipeline", current)
            return current

    def current(self) -> ComicGenPipeline:
        if not workspace_isolation_enabled():
            return self._local()
        tenant = get_tenant(required=True)
        assert tenant is not None
        registry = object.__getattribute__(self, "_registry")
        return registry.get(tenant.workspace_id)

    def __getattr__(self, name: str) -> Any:
        if not workspace_isolation_enabled():
            return getattr(self._local(), name)

        local_template = getattr(ComicGenPipeline, name, None)
        if callable(local_template):

            @wraps(local_template)
            def tenant_call(*args: Any, **kwargs: Any) -> Any:
                tenant = get_tenant(required=True)
                assert tenant is not None
                registry = object.__getattribute__(self, "_registry")
                with registry.locked(tenant.workspace_id) as current:
                    return getattr(current, name)(*args, **kwargs)

            return tenant_call

        tenant = get_tenant(required=True)
        assert tenant is not None
        registry = object.__getattribute__(self, "_registry")
        with registry.locked(tenant.workspace_id) as current:
            return getattr(current, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if not workspace_isolation_enabled():
            setattr(self._local(), name, value)
            return
        tenant = get_tenant(required=True)
        assert tenant is not None
        registry = object.__getattribute__(self, "_registry")
        with registry.locked(tenant.workspace_id) as current:
            setattr(current, name, value)
