"""Lazy, request-scoped ComicGenPipeline registry for private workspaces."""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ..comic_gen.pipeline import ComicGenPipeline
from ..hybrid.config import workspace_isolation_enabled
from ..server.config import server_mode_enabled
from .context import get_tenant
from .file_lock import interprocess_lock, nonblocking_read_active
from .workspace_snapshot import resolve_workspace_snapshot

_SAFE_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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

    @contextmanager
    def locked(self, workspace_id: str) -> Iterator[ComicGenPipeline]:
        """Yield a freshly synchronized pipeline under the workspace lock."""

        safe_id = self.validate_workspace_id(workspace_id)
        with interprocess_lock(self.lock_path_for(safe_id)):
            yield self.get(safe_id)

    def discard(self, workspace_id: str) -> None:
        """Drop only the in-memory instance; persisted workspace data remains."""

        safe_id = self.validate_workspace_id(workspace_id)
        with self._lock:
            self._writer_pipelines.pop(safe_id, None)
            self._reader_pipelines.pop(safe_id, None)


class PipelineProxy:
    """Delegate to the desktop pipeline or the current tenant's private one."""

    def __init__(
        self,
        local_pipeline: ComicGenPipeline,
        registry: Optional[WorkspacePipelineRegistry] = None,
    ):
        object.__setattr__(self, "_local_pipeline", local_pipeline)
        object.__setattr__(self, "_registry", registry or WorkspacePipelineRegistry())

    def current(self) -> ComicGenPipeline:
        if not workspace_isolation_enabled():
            return object.__getattribute__(self, "_local_pipeline")
        tenant = get_tenant(required=True)
        assert tenant is not None
        registry = object.__getattribute__(self, "_registry")
        return registry.get(tenant.workspace_id)

    def __getattr__(self, name: str) -> Any:
        local = object.__getattribute__(self, "_local_pipeline")
        local_value = getattr(local, name)
        if not workspace_isolation_enabled():
            return local_value

        if callable(local_value):

            @wraps(local_value)
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
            setattr(object.__getattribute__(self, "_local_pipeline"), name, value)
            return
        tenant = get_tenant(required=True)
        assert tenant is not None
        registry = object.__getattribute__(self, "_registry")
        with registry.locked(tenant.workspace_id) as current:
            setattr(current, name, value)
