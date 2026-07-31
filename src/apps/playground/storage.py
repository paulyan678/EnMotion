"""Playground storage layer — JSON-file persistence for generation history and templates."""

import json
import os
import tempfile
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ...utils import get_logger
from ..web_runtime.file_lock import interprocess_lock
from .models import PlaygroundGeneration, PlaygroundTemplate

logger = get_logger(__name__)


class PlaygroundStorage:
    HISTORY_PATH = "output/playground_history.json"
    TEMPLATES_PATH = "output/playground_templates.json"
    ORPHAN_RECOVERY_REASON = "EnMotion 在此次生成过程中重新启动。您可以点击重试再次运行。"
    TERMINAL_STATUSES = frozenset({"completed", "failed"})

    def __init__(
        self,
        output_root: Optional[str] = None,
        *,
        shared_workspace: bool = False,
        recover_orphan_tasks: Optional[bool] = None,
    ):
        if recover_orphan_tasks is None:
            from ..server.config import server_mode_enabled

            recover_orphan_tasks = not server_mode_enabled()
        self.output_root = os.path.normpath(output_root or "output")
        self.shared_workspace = shared_workspace
        self.recover_orphan_tasks = recover_orphan_tasks
        self.workspace_lock_path = os.path.join(
            os.path.dirname(self.output_root), ".workspace.lock"
        )
        self.playground_lock_path = os.path.join(
            os.path.dirname(self.output_root), ".playground.lock"
        )
        if output_root is None:
            # Preserve class-level monkeypatch seams used by desktop tests.
            self.history_path = self.HISTORY_PATH
            self.templates_path = self.TEMPLATES_PATH
        else:
            self.history_path = os.path.join(self.output_root, "playground_history.json")
            self.templates_path = os.path.join(self.output_root, "playground_templates.json")
        self._history: List[PlaygroundGeneration] = []
        self._templates: List[PlaygroundTemplate] = []
        self._lock = threading.RLock()
        self._load()

    def _shared_lock(self):
        if not self.shared_workspace:
            return nullcontext()
        # Playground history/templates are independent atomic JSON files. A
        # dedicated lock keeps their admission and lifecycle updates coherent
        # across API/worker processes without queueing behind long provider
        # calls that own the broader project workspace lock.
        return interprocess_lock(self.playground_lock_path)

    def _workspace_lock(self):
        if not self.shared_workspace:
            return nullcontext()
        return interprocess_lock(self.workspace_lock_path)

    def _refresh_history(self) -> None:
        if self.shared_workspace:
            self._history = self._load_file(self.history_path, PlaygroundGeneration)

    def _refresh_templates(self) -> None:
        if self.shared_workspace:
            self._templates = self._load_file(self.templates_path, PlaygroundTemplate)

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load both JSON files, creating them if missing."""
        with self._shared_lock():
            self._history = self._load_file(self.history_path, PlaygroundGeneration)
            self._templates = self._load_file(self.templates_path, PlaygroundTemplate)
            if self.recover_orphan_tasks and self._recover_orphan_generations():
                self._save_history()
            if os.path.exists(self.templates_path):
                # Persist any stale template model migration performed by the
                # PlaygroundTemplate validator.
                self._save_templates()

    def _recover_orphan_generations(self) -> int:
        """Fail unfinished non-durable work after a desktop process restart."""

        recovered = 0
        for generation in self._history:
            if str(generation.status).lower() not in {"pending", "processing"}:
                continue
            now = datetime.now(timezone.utc).isoformat()
            generation.status = "failed"
            if not generation.error:
                generation.error = self.ORPHAN_RECOVERY_REASON
            generation.updated_at = now
            generation.finished_at = now
            recovered += 1
        if recovered:
            logger.warning("Recovered %s interrupted Playground generation(s)", recovered)
        return recovered

    @staticmethod
    def _load_file(path: str, model_cls):
        """Read a JSON array file and parse each element into *model_cls*."""
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError("persisted playground data must be a JSON array")
            return [model_cls.model_validate(item) for item in raw]
        except Exception as e:
            logger.error("Failed to load %s: %s", path, e)
            raise RuntimeError(f"Failed to load playground data from {path}: {e}") from e

    def _save_history(self) -> None:
        self._save_file(self.history_path, self._history)

    def _save_templates(self) -> None:
        self._save_file(self.templates_path, self._templates)

    def _save_file(self, path: str, items: list) -> None:
        """Atomically replace *path* with the serialized model list.

        The temporary file is created beside the destination so ``os.replace``
        is atomic.  Failures intentionally propagate: callers must never report
        a mutation as successful when it was not persisted.
        """
        with self._lock:
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=directory,
                    prefix=f".{os.path.basename(path)}.",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    temp_path = temp_file.name
                    json.dump(
                        [item.model_dump() for item in items],
                        temp_file,
                        indent=2,
                        ensure_ascii=False,
                    )
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
            except Exception:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except FileNotFoundError:
                        pass
                raise

    # ------------------------------------------------------------------
    # History CRUD
    # ------------------------------------------------------------------

    def add_generation(self, gen: PlaygroundGeneration) -> None:
        """Append a generation record and persist."""
        with self._lock, self._shared_lock():
            self._refresh_history()
            stored = gen.model_copy(deep=True)
            if str(stored.status).lower() in self.TERMINAL_STATUSES and not stored.finished_at:
                stored.finished_at = stored.updated_at or stored.created_at
            candidate = [*self._history, stored]
            self._save_file(self.history_path, candidate)
            self._history = candidate

    def get_generation(self, gen_id: str) -> Optional[PlaygroundGeneration]:
        """Look up a generation by its id."""
        with self._lock, self._shared_lock():
            self._refresh_history()
            for gen in self._history:
                if gen.id == gen_id:
                    return gen.model_copy(deep=True)
        return None

    def list_history(self, limit: int = 50, offset: int = 0) -> List[PlaygroundGeneration]:
        """Return paginated history, newest first."""
        with self._lock, self._shared_lock():
            self._refresh_history()
            ordered = list(reversed(self._history))
            return [item.model_copy(deep=True) for item in ordered[offset : offset + limit]]

    def active_generation_ids(self) -> list[str]:
        """Return persisted work that must finish before a desktop update."""

        with self._lock, self._shared_lock():
            self._refresh_history()
            return sorted(
                generation.id
                for generation in self._history
                if str(generation.status).lower() in {"pending", "processing"}
            )

    def update_generation(self, gen: PlaygroundGeneration) -> None:
        """Replace an existing generation record (matched by id) and persist."""
        with self._lock, self._shared_lock():
            self._refresh_history()
            for i, existing in enumerate(self._history):
                if existing.id == gen.id:
                    now = datetime.now(timezone.utc).isoformat()
                    existing_terminal = str(existing.status).lower() in self.TERMINAL_STATUSES
                    next_terminal = str(gen.status).lower() in self.TERMINAL_STATUSES
                    if next_terminal:
                        gen.finished_at = (
                            (existing.finished_at or existing.updated_at or existing.created_at)
                            if existing_terminal
                            else now
                        )
                    else:
                        # A future retry that moves a terminal generation back
                        # to active work starts a fresh lifecycle.
                        gen.finished_at = None
                    gen.updated_at = now
                    candidate = list(self._history)
                    candidate[i] = gen.model_copy(deep=True)
                    self._save_file(self.history_path, candidate)
                    self._history = candidate
                    return
        logger.warning("update_generation: id %s not found", gen.id)

    def mark_output_saved(
        self,
        generation_id: str,
        output_id: str,
        category: str,
    ) -> Optional[str]:
        """Atomically persist only one output's library metadata.

        This deliberately avoids replacing a generation snapshot captured
        before a long file copy or library registration. It therefore cannot
        overwrite concurrent lifecycle/output updates. The returned category
        is the value that is actually persisted, including on idempotent
        replay.
        """

        if category not in {"character", "scene", "prop"}:
            raise ValueError(f"Unsupported library category: {category}")
        with self._lock, self._shared_lock():
            self._refresh_history()
            for generation_index, existing in enumerate(self._history):
                if existing.id != generation_id:
                    continue
                stored = existing.model_copy(deep=True)
                for output in stored.outputs:
                    if output.id != output_id:
                        continue
                    if output.saved_to_library:
                        return output.library_category or "prop"
                    persisted_category = output.library_category or category
                    output.saved_to_library = True
                    output.library_category = persisted_category
                    stored.updated_at = datetime.now(timezone.utc).isoformat()
                    candidate = list(self._history)
                    candidate[generation_index] = stored
                    self._save_file(self.history_path, candidate)
                    self._history = candidate
                    return persisted_category
                return None
        return None

    def prepare_output_library_save(
        self,
        generation_id: str,
        output_id: str,
        category: str,
        asset_id: str,
        media_path: str,
    ) -> Optional[tuple[str, str, str]]:
        """Durably journal deterministic save identities before side effects.

        If a process exits after copying or registering but before the final
        saved flag is persisted, the next retry reuses this exact category,
        asset id, and media path rather than creating duplicates.
        """

        if category not in {"character", "scene", "prop"}:
            raise ValueError(f"Unsupported library category: {category}")
        with self._lock, self._shared_lock():
            self._refresh_history()
            for generation_index, existing in enumerate(self._history):
                if existing.id != generation_id:
                    continue
                stored = existing.model_copy(deep=True)
                for output in stored.outputs:
                    if output.id != output_id:
                        continue
                    persisted_category = output.library_category or category
                    persisted_asset_id = output.library_asset_id or asset_id
                    persisted_media_path = output.library_media_path or media_path
                    changed = (
                        output.library_category != persisted_category
                        or output.library_asset_id != persisted_asset_id
                        or output.library_media_path != persisted_media_path
                    )
                    if changed:
                        output.library_category = persisted_category
                        output.library_asset_id = persisted_asset_id
                        output.library_media_path = persisted_media_path
                        stored.updated_at = datetime.now(timezone.utc).isoformat()
                        candidate = list(self._history)
                        candidate[generation_index] = stored
                        self._save_file(self.history_path, candidate)
                        self._history = candidate
                    return (
                        persisted_category,
                        persisted_asset_id,
                        persisted_media_path,
                    )
                return None
        return None

    def delete_generation(self, gen_id: str) -> bool:
        """Remove a generation and reclaim outputs unreferenced workspace-wide."""
        # Destructive media reclamation still needs the broad workspace lock.
        # Acquire it before the Playground lock to match save-to-library and
        # avoid lock-order inversions.
        with self._workspace_lock(), self._lock, self._shared_lock():
            self._refresh_history()
            for i, gen in enumerate(self._history):
                if gen.id == gen_id:
                    candidate = [*self._history[:i], *self._history[i + 1 :]]
                    self._save_file(self.history_path, candidate)
                    self._history = candidate
                    self._reclaim_deleted_value(gen)
                    return True
        return False

    def _deletable_output_path(self, media_path: str) -> Optional[str]:
        """Resolve only files beneath this storage's Playground directory."""

        from ...utils.media_security import (
            UnsafeMediaReferenceError,
            resolve_workspace_media_path,
        )

        try:
            candidate = Path(
                resolve_workspace_media_path(self.output_root, media_path, require_file=False)
            )
        except UnsafeMediaReferenceError:
            return None
        playground_root = (Path(self.output_root).resolve() / "playground").resolve()
        if candidate == playground_root or playground_root not in candidate.parents:
            return None
        return str(candidate)

    def delete_upload(self, media_path: str) -> bool:
        """Reclaim one unreferenced file owned by Playground's upload area."""

        from ...utils.media_security import (
            UnsafeMediaReferenceError,
            resolve_workspace_media_path,
        )

        with self._workspace_lock(), self._lock, self._shared_lock():
            candidate = Path(
                resolve_workspace_media_path(
                    self.output_root,
                    media_path,
                    require_file=False,
                )
            )
            uploads_root = (Path(self.output_root).resolve() / "playground" / "uploads").resolve()
            if candidate == uploads_root or uploads_root not in candidate.parents:
                raise UnsafeMediaReferenceError(
                    "Only files in the Playground upload directory can be deleted"
                )
            if not candidate.exists():
                return True
            if not candidate.is_file() or candidate.is_symlink():
                raise UnsafeMediaReferenceError("Playground upload must be a regular file")
            reclaimed = self._reclaim_deleted_value(str(candidate))
            if reclaimed is None:
                raise RuntimeError("Could not verify workspace references before deleting upload")
            return str(candidate) in reclaimed

    def _reclaim_deleted_value(self, deleted_value) -> Optional[list[str]]:
        from ...utils.media_gc import (
            load_workspace_reference_values,
            reclaim_unreferenced_workspace_media,
        )

        try:
            remaining = load_workspace_reference_values(self.output_root)
        except RuntimeError as exc:
            logger.warning("Skipping Playground media reclamation: %s", exc)
            return None

        delete_callback = None
        from ..server.config import server_mode_enabled

        if server_mode_enabled():
            from ..server.workspace_storage import defer_workspace_file_deletions
            from ..web_runtime.context import get_tenant

            tenant = get_tenant(required=True)
            assert tenant is not None
            delete_callback = lambda paths: defer_workspace_file_deletions(
                tenant.workspace_id, paths
            )
        return reclaim_unreferenced_workspace_media(
            deleted_value=deleted_value,
            remaining_values=remaining,
            output_root=self.output_root,
            delete_callback=delete_callback,
        )

    # ------------------------------------------------------------------
    # Template CRUD
    # ------------------------------------------------------------------

    def add_template(self, template: PlaygroundTemplate) -> None:
        """Append a template record and persist."""
        with self._lock, self._shared_lock():
            self._refresh_templates()
            candidate = [*self._templates, template.model_copy(deep=True)]
            self._save_file(self.templates_path, candidate)
            self._templates = candidate

    def get_template(self, template_id: str) -> Optional[PlaygroundTemplate]:
        """Look up a template by its id."""
        with self._lock, self._shared_lock():
            self._refresh_templates()
            for template in self._templates:
                if template.id == template_id:
                    return template.model_copy(deep=True)
        return None

    def list_templates(self) -> List[PlaygroundTemplate]:
        """Return all templates."""
        with self._lock, self._shared_lock():
            self._refresh_templates()
            return [template.model_copy(deep=True) for template in self._templates]

    def update_template(self, template: PlaygroundTemplate) -> None:
        """Replace an existing template (matched by id) and persist."""
        with self._lock, self._shared_lock():
            self._refresh_templates()
            for i, existing in enumerate(self._templates):
                if existing.id == template.id:
                    candidate = list(self._templates)
                    candidate[i] = template.model_copy(deep=True)
                    self._save_file(self.templates_path, candidate)
                    self._templates = candidate
                    return
        logger.warning("update_template: id %s not found", template.id)

    def delete_template(self, template_id: str) -> bool:
        """Remove a template by id. Returns True if found and deleted."""
        with self._workspace_lock(), self._lock, self._shared_lock():
            self._refresh_templates()
            for i, template in enumerate(self._templates):
                if template.id == template_id:
                    candidate = [*self._templates[:i], *self._templates[i + 1 :]]
                    self._save_file(self.templates_path, candidate)
                    self._templates = candidate
                    self._reclaim_deleted_value(template)
                    return True
        return False
