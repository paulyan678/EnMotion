"""Per-workspace Playground storage and service registry."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional

from ..playground.service import PlaygroundService
from ..playground.storage import PlaygroundStorage
from .context import get_tenant
from .pipeline_registry import WorkspacePipelineRegistry


@dataclass(frozen=True)
class WorkspacePlayground:
    storage: PlaygroundStorage
    service: PlaygroundService


class WorkspacePlaygroundRegistry:
    def __init__(self, pipelines: Optional[WorkspacePipelineRegistry] = None):
        self.pipelines = pipelines or WorkspacePipelineRegistry()
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
            return runtime

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
