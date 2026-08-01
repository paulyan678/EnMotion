"""Per-workspace Playground storage and service registry."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional

from ..playground.service import PlaygroundService
from ..playground.storage import PlaygroundStorage
from .background_dispatch import DetachedTaskDispatcher
from .context import get_tenant
from .pipeline_registry import WorkspacePipelineRegistry


@dataclass(frozen=True)
class WorkspacePlayground:
    storage: PlaygroundStorage
    service: PlaygroundService


class WorkspacePlaygroundRegistry:
    def __init__(
        self,
        pipelines: Optional[WorkspacePipelineRegistry] = None,
        dispatcher: Optional[DetachedTaskDispatcher] = None,
    ):
        self.pipelines = pipelines or WorkspacePipelineRegistry()
        self._dispatcher = dispatcher
        self._lock = threading.RLock()
        self._runtimes: Dict[str, WorkspacePlayground] = {}

    def get(self, workspace_id: str) -> WorkspacePlayground:
        safe_id = self.pipelines.validate_workspace_id(workspace_id)
        with self._lock:
            runtime = self._runtimes.get(safe_id)
            if runtime is None:
                output_root = str(self.pipelines.output_root_for(safe_id))
                storage = PlaygroundStorage(output_root=output_root, shared_workspace=True)
                runtime = WorkspacePlayground(
                    storage=storage,
                    service=PlaygroundService(storage),
                )
                self._runtimes[safe_id] = runtime
                for generation_id in storage.resumable_generation_ids():
                    self._task_dispatcher().submit(
                        runtime.service.process_generation,
                        generation_id,
                    )
            return runtime

    def _task_dispatcher(self) -> DetachedTaskDispatcher:
        with self._lock:
            if self._dispatcher is None:
                self._dispatcher = DetachedTaskDispatcher(
                    worker_count=4,
                    name_prefix="enmotion-playground",
                )
            return self._dispatcher

    def dispatch_current(self, generation_id: str) -> None:
        """Run one current-tenant generation outside the HTTP lifecycle."""

        runtime = self.current()
        self._task_dispatcher().submit(runtime.service.process_generation, generation_id)

    def current(self) -> WorkspacePlayground:
        tenant = get_tenant(required=True)
        assert tenant is not None
        return self.get(tenant.workspace_id)

    def snapshot(self) -> dict[str, WorkspacePlayground]:
        """Return loaded workspace runtimes for shutdown/update safety checks."""

        with self._lock:
            return dict(self._runtimes)

    def discard(self, workspace_id: str) -> None:
        """Drop cached history after a transactional workspace rollback."""

        safe_id = self.pipelines.validate_workspace_id(workspace_id)
        with self._lock:
            self._runtimes.pop(safe_id, None)
