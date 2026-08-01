import copy
import json
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

from ...models.newapi import NewAPIProviderError
from ...utils import get_logger
from ...utils.newapi_models import (
    CHAT,
    IMAGE,
    VIDEO,
    get_model_spec,
    get_selected_model,
    resolve_model_api_key,
)
from ...utils.oss_utils import authoritative_media_reference, is_object_key
from ...utils.system_check import get_ffmpeg_install_instructions, get_ffmpeg_path
from .assets import AssetGenerator
from .export import ExportManager
from .llm import ScriptProcessor
from .models import (
    ArtDirection,
    AssetUnit,
    Character,
    GenerationStatus,
    GlobalAssetLibrary,
    ImageAsset,
    ImageVariant,
    ModelSettings,
    PromptConfig,
    Prop,
    Scene,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
    canonical_model_setting_overrides,
)
from .storyboard import StoryboardGenerator
from .video import VideoGenerator

logger = get_logger(__name__)


def _public_background_failure(exc: BaseException, *, media: str) -> str:
    """Return a stable UI message while the protected log retains diagnostics."""

    if isinstance(exc, NewAPIProviderError):
        return str(exc)
    return f"{media}生成失败，请稍后重试。"


FICTIONAL_CHARACTER_PROMPT_NOTICE = (
    "This is a fictional character created for animation and does not depict, "
    "identify, or imitate any real person."
)

_FRAME_TYPE_GUIDANCE = {
    "static": "Keep the camera locked and static throughout the clip.",
    "push_in": "Use a smooth camera push in toward the subject.",
    "pull_out": "Use a smooth camera pull out away from the subject.",
    "pan_left": "Pan the camera smoothly to the left.",
    "pan_right": "Pan the camera smoothly to the right.",
    "tilt_up": "Tilt the camera smoothly upward.",
    "tilt_down": "Tilt the camera smoothly downward.",
    "orbit": "Orbit the camera smoothly around the subject.",
    "follow": "Use a stable tracking shot that follows the subject.",
    "crane_up": "Raise the camera smoothly in a crane-up movement.",
    "crane_down": "Lower the camera smoothly in a crane-down movement.",
    "handheld": "Use controlled, natural handheld camera movement.",
    "zoom_in": "Zoom in smoothly while preserving subject continuity.",
    "zoom_out": "Zoom out smoothly while preserving subject continuity.",
}

_FRAME_TYPE_ALIASES = {
    "静止": "static",
    "固定": "static",
    "固定镜头": "static",
    "固定机位": "static",
    "static_camera": "static",
    "推进": "push_in",
    "推镜": "push_in",
    "推镜头": "push_in",
    "缓慢推进": "push_in",
    "快速推镜": "push_in",
    "dolly_in": "push_in",
    "拉远": "pull_out",
    "拉镜": "pull_out",
    "拉镜头": "pull_out",
    "快速拉远": "pull_out",
    "dolly_out": "pull_out",
    "左摇": "pan_left",
    "向左摇摄": "pan_left",
    "右摇": "pan_right",
    "向右摇摄": "pan_right",
    "上摇": "tilt_up",
    "向上摇摄": "tilt_up",
    "下摇": "tilt_down",
    "向下摇摄": "tilt_down",
    "环绕": "orbit",
    "环绕旋转": "orbit",
    "跟拍": "follow",
    "跟随": "follow",
    "跟随平移": "follow",
    "tracking": "follow",
    "tracking_shot": "follow",
    "升镜": "crane_up",
    "缓慢上升": "crane_up",
    "降镜": "crane_down",
    "手持": "handheld",
    "手持拍摄": "handheld",
    "变焦推": "zoom_in",
    "变焦推近": "zoom_in",
    "变焦拉": "zoom_out",
    "变焦拉远": "zoom_out",
}


def _normalize_frame_type(value: Optional[str]) -> str:
    """Return one language-independent camera movement value."""

    raw = (value or "static").strip()
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    if normalized in _FRAME_TYPE_GUIDANCE:
        return normalized
    return _FRAME_TYPE_ALIASES.get(normalized, "static")


def _normalize_clip_image_url(value: str) -> str:
    """Normalize signed/public file URLs to their durable media reference."""

    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("data:"):
        return raw
    parsed = urlsplit(raw)
    path = unquote(parsed.path or raw).replace("\\", "/")
    marker = "/files/"
    if marker in path:
        path = path.split(marker, 1)[1]
    return path.lstrip("/")


def clip_image_id(url: str) -> str:
    """Build a stable ID for legacy/T2I candidates which only store a URL."""

    durable = _normalize_clip_image_url(url)
    # FNV-1a is intentionally mirrored in the browser helper. It is not a
    # security primitive; it only gives URL-only legacy variants a compact,
    # deterministic identity that survives refreshes and signed-URL changes.
    value = 0x811C9DC5
    encoded = durable.encode("utf-8")
    for byte in encoded:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"clip-image-{value:08x}-{len(encoded)}"


def _frame_type_from_storyboard(frame: StoryboardFrame) -> str:
    structured = getattr(frame, "camera_movement_structured", None)
    primary = getattr(structured, "primary", None) if structured else None
    description = getattr(structured, "description", None) if structured else None
    for candidate in (getattr(frame, "camera_movement", None), primary, description):
        if not candidate:
            continue
        normalized = str(candidate).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in _FRAME_TYPE_GUIDANCE or normalized in _FRAME_TYPE_ALIASES:
            return _normalize_frame_type(str(candidate))
    return "static"


def _motion_prompt_with_frame_type(prompt: str, frame_type: Optional[str]) -> str:
    """Append camera guidance without changing or replacing the user's prompt."""

    canonical = _normalize_frame_type(frame_type)
    guidance = _FRAME_TYPE_GUIDANCE[canonical]
    clean = (prompt or "").strip()
    return f"{clean}\n\nCamera direction: {guidance}" if clean else guidance


@dataclass(frozen=True, slots=True)
class StoryboardRenderPlan:
    """Detached inputs for a provider call and its merge-safe result commit."""

    script_id: str
    frame_id: str
    frame: StoryboardFrame
    characters: List[Character]
    scene: Optional[Scene]
    ref_image_paths: List[str]
    prompt: str
    batch_size: int
    size: str
    model_name: str
    existing_variant_ids: frozenset[str]
    prepared_at: float


class InvalidAssetAttributesError(ValueError):
    """Raised when generic asset editing attempts an unsafe/unknown field."""


class AssemblyOperationInProgressError(RuntimeError):
    """Raised when the same project already has an assembly mutation in flight."""


class AssemblyMutationConflictError(RuntimeError):
    """Raised when provider inputs changed before its result could commit."""


ASSEMBLY_OPERATION_BUSY_MESSAGE = "Another assembly operation is already running for this project"
CUSTOM_BGM_ROOT = "audio/custom_bgm"


@dataclass
class _AssemblyMutation:
    """One project mutation committed by the assembly transaction."""

    script: Script
    changed: bool = False
    replacement: Optional[Script] = None
    force_assembly_invalidation: bool = False

    def mark_changed(self) -> None:
        self.changed = True

    def replace_script(self, script: Script, *, assembly_affecting: bool = True) -> None:
        self.replacement = script
        self.changed = True
        self.force_assembly_invalidation = assembly_affecting


MUTABLE_ASSET_FIELDS = {
    "character": frozenset(
        {"name", "description", "persona", "age", "gender", "clothing", "visual_weight"}
    ),
    "scene": frozenset({"name", "description", "visual_weight", "time_of_day", "lighting_mood"}),
    "prop": frozenset({"name", "description"}),
}

MUTABLE_ASSET_PROMPT_FIELDS = {
    "character": frozenset(
        {
            "image_prompt",
            "reference_sheet_prompt",
            "full_body_prompt",
            "three_view_prompt",
            "headshot_prompt",
            "video_prompt",
            "full_body_video_prompt",
            "headshot_video_prompt",
        }
    ),
    "scene": frozenset({"image_prompt", "video_prompt"}),
    "prop": frozenset({"image_prompt", "video_prompt"}),
}


def _validated_asset_attribute_values(
    target_asset: Any, asset_type: str, attributes: Dict[str, Any]
) -> Dict[str, Any]:
    allowed = MUTABLE_ASSET_FIELDS.get(asset_type)
    if allowed is None:
        raise InvalidAssetAttributesError(f"Unsupported asset type: {asset_type}")
    rejected = sorted(set(attributes) - allowed)
    if rejected:
        raise InvalidAssetAttributesError(
            f"Asset fields are immutable or unsupported: {', '.join(rejected)}"
        )
    if not attributes:
        return {}
    if "visual_weight" in attributes:
        weight = attributes["visual_weight"]
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 5:
            raise InvalidAssetAttributesError("visual_weight must be an integer from 1 to 5")
    limits = {
        "name": 200,
        "description": 20_000,
        "persona": 1_000,
        "age": 1_000,
        "gender": 1_000,
        "clothing": 20_000,
        "time_of_day": 1_000,
        "lighting_mood": 20_000,
    }
    for key, limit in limits.items():
        value = attributes.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise InvalidAssetAttributesError(
                f"{key} must be text no longer than {limit} characters"
            )
    try:
        candidate = type(target_asset).model_validate({**target_asset.model_dump(), **attributes})
    except Exception as exc:
        raise InvalidAssetAttributesError(f"Invalid asset attributes: {exc}") from exc
    return {key: getattr(candidate, key) for key in attributes}


def _validated_asset_prompt_values(
    asset_type: str, prompts: Dict[str, Any]
) -> Dict[str, Optional[str]]:
    """Validate editor-owned generation prompts without exposing arbitrary fields."""

    allowed = MUTABLE_ASSET_PROMPT_FIELDS.get(asset_type)
    if allowed is None:
        raise InvalidAssetAttributesError(f"Unsupported asset type: {asset_type}")
    rejected = sorted(set(prompts) - allowed)
    if rejected:
        raise InvalidAssetAttributesError(
            f"Asset prompt fields are immutable or unsupported: {', '.join(rejected)}"
        )
    validated: Dict[str, Optional[str]] = {}
    for key, value in prompts.items():
        if value is not None and (not isinstance(value, str) or len(value) > 50_000):
            raise InvalidAssetAttributesError(f"{key} must be text no longer than 50000 characters")
        validated[key] = value
    return validated


# --- Security helpers ---

# Allowed pattern for IDs used in file paths (UUID hex + hyphens)
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_safe_id(value: str, label: str = "id") -> str:
    """Ensure a value is safe to embed in file paths / command args (UUID-like)."""
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {label}: contains unsafe characters")
    return value


def _safe_resolve_path(base_dir: str, untrusted_rel: str) -> str:
    """Resolve *untrusted_rel* under *base_dir* and ensure the result stays inside it.

    Prevents path-traversal attacks (e.g. ``../../etc/passwd``).
    Returns the resolved absolute path; raises ValueError on escape attempts.
    """
    base = os.path.realpath(base_dir)
    resolved = os.path.realpath(os.path.join(base, untrusted_rel))
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ValueError(f"Path escapes base directory: {untrusted_rel}")
    return resolved


def _delete_or_defer_workspace_media(output_root: str, paths: List[str]) -> None:
    """Retire media only when the surrounding server transaction commits."""

    safe_paths = {
        Path(path).expanduser().resolve() for path in paths if path and os.path.isfile(path)
    }
    if not safe_paths:
        return
    from ...utils.media_gc import (
        collect_workspace_media_paths,
        load_workspace_reference_values,
    )

    try:
        remaining_values = load_workspace_reference_values(output_root)
    except RuntimeError:
        return
    retained: set[Path] = set()
    for value in remaining_values:
        retained.update(collect_workspace_media_paths(value, output_root))
    safe_paths.difference_update(retained)
    if not safe_paths:
        return
    from ..server.config import server_mode_enabled

    if server_mode_enabled():
        from ..server.workspace_storage import defer_workspace_file_deletions
        from ..web_runtime.context import get_tenant

        tenant = get_tenant(required=True)
        assert tenant is not None
        defer_workspace_file_deletions(tenant.workspace_id, safe_paths)
        return
    for path in safe_paths:
        try:
            path.unlink()
        except OSError:
            pass


def _atomic_json_dump(path: str, payload: Any) -> None:
    """Persist JSON without exposing readers to a truncated target file.

    The temporary file lives beside the destination so ``os.replace`` stays
    atomic on the same filesystem.  Any write/replace failure is propagated to
    the caller; mutation endpoints must not report success for data that was
    never durably stored.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path: Optional[str] = None
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
            json.dump(payload, temp_file, indent=2)
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


class LibraryAssetInUseError(Exception):
    """Raised when an asset cannot be deleted because it is still referenced.

    The exception name is retained for compatibility with the original global
    library endpoint, but source-aware deletes use it for project and series
    assets too.  ``references`` may describe storyboard frames or generation
    tasks and is surfaced by the API as an HTTP 409 impact report.

    ``references`` is a list of dicts, each:
        {"owner_kind": "project"|"series", "owner_id": str,
         "owner_title": Optional[str], "frame_id": str}
    """

    def __init__(self, asset_type: str, asset_id: str, references: List[Dict[str, Any]]):
        self.asset_type = asset_type
        self.asset_id = asset_id
        self.references = references
        super().__init__(
            f"Asset {asset_type} {asset_id} has {len(references)} active "
            "reference(s); refusing to delete (pass force=True after user "
            "confirmation)."
        )


class AssetTypeChangeConflictError(Exception):
    """Raised when changing an asset's collection would break ownership or refs."""

    def __init__(
        self,
        source_kind: str,
        asset_type: str,
        asset_id: str,
        target_asset_type: str,
        references: Optional[List[Dict[str, Any]]] = None,
        *,
        reason: str = "referenced",
        unsupported_media: Optional[List[str]] = None,
    ):
        self.source_kind = source_kind
        self.asset_type = asset_type
        self.asset_id = asset_id
        self.target_asset_type = target_asset_type
        self.references = references or []
        self.reason = reason
        self.unsupported_media = unsupported_media or []
        if reason == "unsupported_media":
            message = (
                f"{asset_type.title()} {asset_id} contains motion or audio media "
                f"that cannot be represented as {target_asset_type}: "
                f"{', '.join(self.unsupported_media)}"
            )
        else:
            message = (
                f"{source_kind.title()} {asset_type} {asset_id} is referenced by "
                f"{len(self.references)} storyboard frame(s); its type cannot be changed"
            )
        super().__init__(message)


class ComicGenPipeline:
    # Compatibility default for lightweight test/integration doubles that
    # construct the pipeline with ``__new__`` and populate only the fields
    # needed by the method under test.
    output_root = "output"

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.read_only = bool(self.config.get("read_only", False))
        self.output_root = os.path.normpath(self.config.get("output_root", "output"))
        self.metadata_root = os.path.normpath(self.config.get("metadata_root", self.output_root))

        def component_config(name: str) -> Dict[str, Any]:
            value = dict(self.config.get(name) or {})
            value.setdefault("output_root", self.output_root)
            value.setdefault("create_output_dir", not self.read_only)
            return value

        self.script_processor = ScriptProcessor()
        self.asset_generator = AssetGenerator(component_config("assets"))
        self.storyboard_generator = StoryboardGenerator(component_config("storyboard"))
        self.video_generator = VideoGenerator(component_config("video"))
        self.export_manager = ExportManager(component_config("export"))

        self.data_file = os.path.join(self.metadata_root, "projects.json")
        self.series_data_file = os.path.join(self.metadata_root, "series.json")
        self.library_data_file = os.path.join(self.metadata_root, "library_assets.json")
        self._save_lock = threading.RLock()  # Reentrant lock to prevent concurrent file writes
        self._assembly_operation_locks_guard = threading.Lock()
        self._assembly_operation_locks: Dict[str, threading.RLock] = {}
        self._asset_library_data_revision = 0
        self._asset_library_snapshot_cache = None
        self.scripts: Dict[str, Script] = self._load_data()
        self.series_store: Dict[str, Series] = self._load_series_data()
        # Project-independent global asset library (lowest resolver layer).
        self.library_store: GlobalAssetLibrary = self._load_library_data()
        if not self.read_only:
            if self._normalize_library_primary_images():
                self._save_library_data()
            if self._normalize_owner_primary_images(self.series_store.values()):
                self._save_series_data()
            if self._normalize_owner_primary_images(self.scripts.values()):
                self._save_data()
            self._repair_series_bindings()
            self._repair_series_asset_ownership()

        # Entity extraction is a two-phase operation: a reviewed preview must
        # be applied with its opaque revision. Consumed revisions are retained
        # briefly so a response-loss retry is idempotent instead of triggering
        # another LLM call or reporting a false failure.
        self._extraction_cache: Dict[str, Tuple[float, str, Script]] = {}
        self._consumed_extraction_revisions: Dict[str, Tuple[float, str]] = {}

        # Task management for async asset generation
        # Format: { task_id: { status: str, progress: int, error: str, script_id: str, asset_id: str, created_at: float } }
        self.asset_generation_tasks: Dict[str, Dict[str, Any]] = {}
        self.video_generation_tasks: Dict[str, Dict[str, Any]] = {}
        # Temporary cache for file import previews (import_id -> text)
        self._import_cache: Dict[str, str] = {}
        # Cached model instances (lazily initialized)
        self._newapi_video_model = None

        # Demucs is large and may download model weights. Keep startup offline by
        # default; operators can opt into preloading, while the first dub request
        # still initializes it lazily when preload is disabled.
        self._demucs_ready = threading.Event()
        self._demucs_error: Optional[str] = None
        self._demucs_warmup_lock = threading.Lock()
        self._demucs_warmup_started = False
        if os.getenv("ENMOTION_PRELOAD_DEMUCS", "").strip() == "1":
            self._start_demucs_warmup()

        # Recover orphan async tasks. Detached desktop workers live in process
        # memory — any restart between submit + execute leaves
        # them permanently `pending` (or `processing` if interrupted
        # mid-call) on disk. We mark such tasks `failed` with a clear
        # reason so the user sees a Retry affordance instead of an
        # eternal spinner. We do NOT auto-resume because re-running a
        # half-completed video task could double-charge providers.
        if not self.read_only and self.config.get("recover_orphan_tasks", True):
            try:
                self._recover_orphan_tasks()
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Orphan task recovery failed: %s", exc)

    def _assert_writable(self) -> None:
        """Fail closed if a safe GET accidentally reaches a mutation path."""

        if getattr(self, "read_only", False):
            raise RuntimeError("Immutable workspace snapshots cannot be modified")

    _ORPHAN_RECOVERY_REASON = "EnMotion 在此任务运行期间重新启动。您可以点击重试再次运行。"

    def _recover_orphan_tasks(self) -> None:
        """Sweep persisted video tasks and storyboard renders left in progress.

        FastAPI's BackgroundTasks queue lives entirely in process memory:
        if uvicorn restarts (dev --reload, OOM, OS reboot, ctrl-C) every
        queued processor is gone but the task records on disk still say
        "pending" or "processing". The frontend then shows an eternal
        spinner and the user has no recovery path.

        Strategy: on boot, find every such record and stamp it `failed`
        with a clear, user-readable reason so the existing Retry button
        becomes usable. Auto-resume is intentionally NOT done — a
        half-run video generation may have already incurred provider
        cost and re-running could double-charge.

        Asset / motion-ref tasks live in transient in-process dicts
        (self.asset_generation_tasks etc.) and never persist, so they
        die naturally with the process and don't need recovery.
        """
        from .video_failures import (
            VIDEO_INTERRUPTED_CODE,
            VIDEO_INTERRUPTED_MESSAGE,
        )

        STUCK = ("pending", "processing")
        recovered = 0

        for script in self.scripts.values():
            tasks = getattr(script, "video_tasks", None) or []
            for task in tasks:
                if getattr(task, "status", None) in STUCK:
                    task.status = "failed"
                    if not getattr(task, "error", None):
                        try:
                            task.error = VIDEO_INTERRUPTED_MESSAGE
                        except Exception:
                            pass
                    if not getattr(task, "error_code", None):
                        task.error_code = VIDEO_INTERRUPTED_CODE
                    if not getattr(task, "error_diagnostic", None):
                        task.error_diagnostic = self._ORPHAN_RECOVERY_REASON
                    recovered += 1

            # Storyboard rendering has no separately persisted task object. Its
            # processing marker lives on the frame itself, so an interrupted
            # desktop process must release that marker on the next launch. A
            # prior selected image remains valid; otherwise expose a retryable
            # failed frame instead of an eternal spinner.
            for frame in getattr(script, "frames", None) or []:
                if frame.status != GenerationStatus.PROCESSING:
                    continue
                has_image = bool(frame.rendered_image_url or frame.image_url)
                frame.status = GenerationStatus.COMPLETED if has_image else GenerationStatus.FAILED
                frame.updated_at = time.time()
                recovered += 1

        if recovered > 0:
            try:
                self._save_data()
            except Exception:
                logger.warning("Orphan recovery: failed to persist sweep")
            logger.warning(
                "Orphan task recovery: marked %d stuck task(s) as failed.",
                recovered,
            )
        else:
            logger.debug("Orphan task recovery: no stuck tasks found.")

    _MAX_LABEL_LEN = 20

    def annotate_video_task(
        self,
        script_id: str,
        task_id: str,
        is_starred: Optional[bool] = None,
        label: Optional[str] = None,
        clear_label: bool = False,
    ) -> Optional["VideoTask"]:
        """Set the user's review annotations on a video task. Two fields,
        both optional so callers can update either independently:
          - is_starred: shortlist flag, multi-select per shot
          - label: short free-text note (≤20 chars). Pass clear_label=True
            to explicitly remove the label (None on its own means "don't
            change").
        Returns the updated VideoTask, or None if script/task not found
        (caller can decide whether that's a 404)."""
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                return None
            tasks = getattr(script, "video_tasks", None) or []
            task = next((t for t in tasks if getattr(t, "id", None) == task_id), None)
            if not task:
                return None
            if is_starred is not None:
                task.is_starred = bool(is_starred)
            if clear_label:
                task.label = None
            elif label is not None:
                trimmed = label.strip()[: self._MAX_LABEL_LEN]
                task.label = trimmed or None
            self._save_data()
            return task

    _T2I_HISTORY_LIMIT = 10
    _MAX_GENERATE_COUNT = 6
    _WORKBENCH_TAB_VALUES = ("t2i_i2v", "direct_r2v")

    @staticmethod
    def _frame_clip_image_candidates(frame: StoryboardFrame) -> Dict[str, str]:
        """Return every selectable start image for one storyboard frame.

        New image assets already have durable variant IDs. Older rendered,
        generated, and uploaded T2I images only persisted a URL, so they use a
        deterministic ID derived from the durable URL. The same algorithm is
        mirrored by the frontend.
        """

        candidates: Dict[str, str] = {}

        def add(image_id: Optional[str], url: Optional[str]) -> None:
            if not url:
                return
            resolved_id = image_id or clip_image_id(url)
            candidates.setdefault(resolved_id, url)
            # The URL-derived alias keeps older callers compatible while the
            # canonical UI preserves the provider/generated variant ID.
            candidates.setdefault(clip_image_id(url), url)

        for asset_name in ("rendered_image_asset", "image_asset"):
            asset = getattr(frame, asset_name, None)
            for variant in getattr(asset, "variants", None) or []:
                add(getattr(variant, "id", None), getattr(variant, "url", None))
        for url in getattr(frame, "t2i_image_urls", None) or []:
            add(None, url)
        add(None, getattr(frame, "rendered_image_url", None))
        add(None, getattr(frame, "image_url", None))
        return candidates

    @staticmethod
    def _set_frame_clip_start_image(
        frame: StoryboardFrame,
        image_id: Optional[str],
        image_url: Optional[str],
    ) -> None:
        """Persist one exact clip-start selection on a storyboard frame."""

        if not image_url:
            frame.clip_start_image_id = None
            frame.clip_start_image_url = None
            return
        frame.clip_start_image_id = image_id or clip_image_id(image_url)
        frame.clip_start_image_url = image_url

    @classmethod
    def _selected_frame_image_candidate(
        cls, frame: StoryboardFrame
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve the frame editor's current selection after a mutation."""

        for asset_name in ("rendered_image_asset", "image_asset"):
            asset = getattr(frame, asset_name, None)
            selected_id = getattr(asset, "selected_id", None) if asset else None
            if selected_id:
                variant = next(
                    (
                        item
                        for item in (getattr(asset, "variants", None) or [])
                        if getattr(item, "id", None) == selected_id
                    ),
                    None,
                )
                if variant and getattr(variant, "url", None):
                    return selected_id, variant.url

        urls = list(getattr(frame, "t2i_image_urls", None) or [])
        if urls:
            index = max(
                0,
                min(int(getattr(frame, "t2i_selected_index", 0) or 0), len(urls) - 1),
            )
            return clip_image_id(urls[index]), urls[index]

        for field_name in ("rendered_image_url", "image_url"):
            url = getattr(frame, field_name, None)
            if url:
                return clip_image_id(url), url
        return None, None

    @classmethod
    def _reconcile_frame_clip_start_image(cls, frame: StoryboardFrame) -> None:
        """Keep Motion and the Storyboard Frame Editor on one selection."""

        image_id, image_url = cls._selected_frame_image_candidate(frame)
        cls._set_frame_clip_start_image(frame, image_id, image_url)

    def validate_clip_generation_request(
        self,
        script_id: str,
        frame_id: Optional[str],
        source_image_id: Optional[str],
        image_url: Optional[str],
        frame_type: Optional[str],
    ) -> Tuple[StoryboardFrame, str, str]:
        """Validate and resolve one exact shot + image selection.

        This is deliberately server-authoritative: clients cannot submit an
        arbitrary image URL while claiming it belongs to a storyboard shot.
        """

        if not frame_id:
            raise ValueError("Select a storyboard shot before generating a clip")
        if not source_image_id:
            raise ValueError("Select a clip start image before generating a clip")
        if not image_url:
            raise ValueError("Clip generation requires a source image URL")
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        frame = next((item for item in script.frames if item.id == frame_id), None)
        if frame is None:
            raise ValueError("Storyboard frame not found")

        candidates = self._frame_clip_image_candidates(frame)
        expected_url = candidates.get(source_image_id)
        if expected_url is None:
            raise ValueError("The selected image variant does not belong to this storyboard frame")
        if _normalize_clip_image_url(expected_url) != _normalize_clip_image_url(image_url):
            raise ValueError("The selected image ID and image URL do not match")

        if frame.clip_start_image_url:
            if _normalize_clip_image_url(frame.clip_start_image_url) != _normalize_clip_image_url(
                expected_url
            ):
                raise ValueError(
                    "The submitted image is not this storyboard frame's selected clip start image"
                )
        else:
            # Legacy frames did not persist an explicit Motion selection. The
            # first validated submission upgrades that frame in place; the
            # task creation save immediately below makes it durable.
            self._set_frame_clip_start_image(frame, source_image_id, expected_url)

        canonical_type = _frame_type_from_storyboard(frame)
        if frame_type and _normalize_frame_type(frame_type) != canonical_type:
            raise ValueError("The submitted frame type is stale; reopen the shot and try again")
        return frame, expected_url, canonical_type

    def update_frame_workbench(
        self,
        script_id: str,
        frame_id: str,
        workbench_tab_mode: Optional[str] = None,
        t2i_image_urls: Optional[List[str]] = None,
        t2i_selected_index: Optional[int] = None,
        workbench_generate_count: Optional[int] = None,
        clip_start_image_id: Optional[str] = None,
        clip_start_image_url: Optional[str] = None,
        video_prompt: Optional[str] = None,
    ) -> Optional["StoryboardFrame"]:
        """Persist Storyboard R2V workbench state onto a frame.

        Each field is optional; only the ones the caller passes get
        written. The four fields cover everything the per-shot panel
        carries that needs to survive refresh/cross-device:
          - workbench_tab_mode: 't2i_i2v' | 'direct_r2v'
          - t2i_image_urls: full ordered history (caller is the source
            of truth, server clamps to _T2I_HISTORY_LIMIT FIFO)
          - t2i_selected_index: active首帧 index, clamped to range
          - workbench_generate_count: per-shot batch size, clamped to
            [1, _MAX_GENERATE_COUNT]

        Returns the updated StoryboardFrame, or None if the
        script/frame can't be found (caller maps to 404).
        Unknown enum values for workbench_tab_mode are rejected with
        ValueError so a typo doesn't silently persist garbage."""
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                return None
            frames = getattr(script, "frames", None) or []
            frame = next((f for f in frames if getattr(f, "id", None) == frame_id), None)
            if not frame:
                return None
            if workbench_tab_mode is not None:
                if workbench_tab_mode not in self._WORKBENCH_TAB_VALUES:
                    raise ValueError(
                        f"workbench_tab_mode must be one of {self._WORKBENCH_TAB_VALUES}, "
                        f"got {workbench_tab_mode!r}",
                    )
                frame.workbench_tab_mode = workbench_tab_mode
            if t2i_image_urls is not None:
                # Filter empties + cap FIFO so the client can't grow the
                # list unbounded by repeated calls. The client also caps
                # at the same limit, but defense in depth.
                cleaned = [u for u in t2i_image_urls if isinstance(u, str) and u.strip()]
                if len(cleaned) > self._T2I_HISTORY_LIMIT:
                    cleaned = cleaned[-self._T2I_HISTORY_LIMIT :]
                frame.t2i_image_urls = cleaned
            if t2i_selected_index is not None:
                # Clamp against the resulting URL list, not whatever was
                # there before — t2i_image_urls may have been written
                # this same call.
                urls = frame.t2i_image_urls or []
                if not urls:
                    frame.t2i_selected_index = 0
                else:
                    frame.t2i_selected_index = max(0, min(int(t2i_selected_index), len(urls) - 1))
                    selected_url = urls[frame.t2i_selected_index]
                    frame.clip_start_image_id = clip_image_id(selected_url)
                    frame.clip_start_image_url = selected_url
            if workbench_generate_count is not None:
                frame.workbench_generate_count = max(
                    1, min(int(workbench_generate_count), self._MAX_GENERATE_COUNT)
                )
            if clip_start_image_id is not None or clip_start_image_url is not None:
                if not clip_start_image_id or not clip_start_image_url:
                    raise ValueError(
                        "clip_start_image_id and clip_start_image_url must be saved together"
                    )
                candidates = self._frame_clip_image_candidates(frame)
                expected_url = candidates.get(clip_start_image_id)
                if expected_url is None or (
                    _normalize_clip_image_url(expected_url)
                    != _normalize_clip_image_url(clip_start_image_url)
                ):
                    raise ValueError("The selected clip start image does not belong to this frame")
                frame.clip_start_image_id = clip_start_image_id
                frame.clip_start_image_url = expected_url
                normalized_expected = _normalize_clip_image_url(expected_url)
                for index, candidate_url in enumerate(frame.t2i_image_urls or []):
                    if _normalize_clip_image_url(candidate_url) == normalized_expected:
                        frame.t2i_selected_index = index
                        break
            if video_prompt is not None:
                frame.video_prompt = video_prompt.strip()
            if (
                clip_start_image_id is None
                and clip_start_image_url is None
                and frame.clip_start_image_url
                and _normalize_clip_image_url(frame.clip_start_image_url)
                not in {
                    _normalize_clip_image_url(url)
                    for url in self._frame_clip_image_candidates(frame).values()
                }
            ):
                self._reconcile_frame_clip_start_image(frame)
            frame.updated_at = time.time()
            self._save_data()
            return frame

    def delete_frame_t2i_image(
        self,
        script_id: str,
        frame_id: str,
        image_index: int,
    ) -> Tuple["StoryboardFrame", str]:
        """Remove one persisted T2I candidate and return its media reference."""

        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            frame = next((item for item in script.frames if item.id == frame_id), None)
            if frame is None:
                raise ValueError("Frame not found")
            urls = list(frame.t2i_image_urls or [])
            if image_index < 0 or image_index >= len(urls):
                raise ValueError("T2I image not found")

            removed_url = urls.pop(image_index)
            selected_index = frame.t2i_selected_index or 0
            if not urls:
                selected_index = 0
            elif selected_index == image_index:
                selected_index = min(image_index, len(urls) - 1)
            elif selected_index > image_index:
                selected_index -= 1
            if urls:
                selected_index = max(0, min(selected_index, len(urls) - 1))

            removed_was_clip_start = bool(
                frame.clip_start_image_url
                and _normalize_clip_image_url(frame.clip_start_image_url)
                == _normalize_clip_image_url(removed_url)
            )

            frame.t2i_image_urls = urls
            frame.t2i_selected_index = selected_index
            if removed_was_clip_start:
                if urls:
                    selected_url = urls[selected_index]
                    self._set_frame_clip_start_image(frame, None, selected_url)
                else:
                    self._reconcile_frame_clip_start_image(frame)
            frame.updated_at = time.time()
            self._save_data()
            return frame, removed_url

    def upload_t2i_frame(
        self,
        script_id: str,
        frame_id: str,
        file_path: str,
    ) -> Optional["StoryboardFrame"]:
        """Append an uploaded image to a frame's T2I history and auto-select it.

        Mirrors `update_frame_workbench`'s clamping rules (≤ _T2I_HISTORY_LIMIT
        FIFO; t2i_selected_index → index of the newly appended URL). Caller is
        expected to have already saved the file under output/uploads/ and pass
        the relative URL path the frontend can resolve via /files.

        Returns the updated frame, or None if script/frame can't be found.
        """
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                return None
            frames = getattr(script, "frames", None) or []
            frame = next((f for f in frames if getattr(f, "id", None) == frame_id), None)
            if not frame:
                return None
            current = list(getattr(frame, "t2i_image_urls", None) or [])
            current.append(file_path)
            # Same FIFO cap as update_frame_workbench so uploads can't grow
            # the history unbounded either.
            if len(current) > self._T2I_HISTORY_LIMIT:
                current = current[-self._T2I_HISTORY_LIMIT :]
            frame.t2i_image_urls = current
            # Newly uploaded image becomes the active首帧 — Issue 10 design
            # requires the upload immediately unlocks Step 2.
            frame.t2i_selected_index = len(current) - 1
            self._set_frame_clip_start_image(frame, None, file_path)
            frame.updated_at = time.time()
            self._save_data()
            return frame

    def mark_video_task_failed(
        self,
        script_id: str,
        task_id: str,
        error_message: str,
        *,
        error_code: Optional[str] = None,
        error_diagnostic: Optional[str] = None,
        overwrite: bool = False,
        allow_completed: bool = False,
        clear_provider_ids: bool = False,
    ) -> bool:
        """Belt-and-suspenders setter used by BG-task wrappers when an
        exception escapes the pipeline's own try/except. Writes
        status='failed' + error so the UI never sees an eternal
        spinner. Also used by the cancel endpoint. Returns True when a
        task was found and marked."""
        try:
            with self._assembly_mutation(script_id, blocking=True) as mutation:
                script = mutation.script
                tasks = getattr(script, "video_tasks", None) or []
                task = next((t for t in tasks if getattr(t, "id", None) == task_id), None)
                if not task:
                    return False
                if getattr(task, "status", None) == "completed" and not allow_completed:
                    # Already successfully completed — don't downgrade on a
                    # spurious wrapper exception or a late cancel.
                    return False
                task.status = "failed"
                try:
                    if overwrite or not getattr(task, "error", None):
                        task.error = error_message
                    if overwrite or not getattr(task, "error_code", None):
                        task.error_code = error_code
                    if overwrite or not getattr(task, "error_diagnostic", None):
                        task.error_diagnostic = error_diagnostic
                    if clear_provider_ids:
                        task.provider_name = None
                        task.provider_task_id = None
                        task.provider_request_id = None
                except Exception:
                    pass
                if getattr(task, "asset_id", None):
                    self._sync_asset_video_task(script, task)
                mutation.mark_changed()
                return True
        except ValueError as exc:
            if str(exc) == "Script not found":
                return False
            raise

    def mark_video_task_canceled(self, script_id: str, task_id: str) -> bool:
        """Persist an explicit canceled state without presenting it as a failure."""
        from .video_failures import VIDEO_CANCELED_CODE, VIDEO_CANCELED_MESSAGE

        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                return False
            task = next(
                (
                    item
                    for item in (getattr(script, "video_tasks", None) or [])
                    if getattr(item, "id", None) == task_id
                ),
                None,
            )
            if task is None or task.status == "completed":
                return False
            task.status = "canceled"
            task.error = VIDEO_CANCELED_MESSAGE
            task.error_code = VIDEO_CANCELED_CODE
            task.error_diagnostic = None
            if getattr(task, "asset_id", None):
                self._sync_asset_video_task(script, task)
            self._save_data()
            return True

    def prepare_video_task_retry(self, script_id: str, task_id: str) -> bool:
        """Reset a failed/canceled task while preserving its original recipe."""
        try:
            with self._assembly_mutation(script_id) as mutation:
                script = mutation.script
                task = next(
                    (
                        item
                        for item in (getattr(script, "video_tasks", None) or [])
                        if getattr(item, "id", None) == task_id
                    ),
                    None,
                )
                if task is None or task.status not in {"failed", "canceled"}:
                    return False
                task.status = "pending"
                task.error = None
                task.error_code = None
                task.error_diagnostic = None
                task.video_url = None
                if getattr(task, "asset_id", None):
                    self._sync_asset_video_task(script, task)
                mutation.mark_changed()
                return True
        except ValueError as exc:
            if str(exc) == "Script not found":
                return False
            raise

    # ... (existing methods)

    def _assembly_operation_lock(self, script_id: str) -> threading.RLock:
        """Return the per-project lock shared by merge and export."""

        guard = getattr(self, "_assembly_operation_locks_guard", None)
        if guard is None:
            guard = threading.Lock()
            self._assembly_operation_locks_guard = guard
        with guard:
            locks = getattr(self, "_assembly_operation_locks", None)
            if locks is None:
                locks = {}
                self._assembly_operation_locks = locks
            return locks.setdefault(script_id, threading.RLock())

    def _assembly_source_signature(self, script: Script) -> Tuple[Any, ...]:
        """Return only state that can change merge or subtitle output.

        Preview media, task annotations, pin state, prompts, and other editor
        metadata are deliberately excluded.  An applied dub counts only when
        it belongs to the currently selected take, matching ``merge_videos``.
        """

        tasks = list(script.video_tasks or [])
        tasks_by_id = {task_id: task for task in tasks if (task_id := getattr(task, "id", None))}
        frame_sources = []
        subtitles = []
        for frame in script.frames:
            selected_id = frame.selected_video_id
            source_url = None
            if (
                frame.dubbed_video_url
                and frame.dubbed_video_task_id
                and frame.dubbed_video_task_id == selected_id
            ):
                try:
                    dubbed_path = _safe_resolve_path(
                        self.output_root,
                        frame.dubbed_video_url,
                    )
                except ValueError:
                    dubbed_path = ""
                if dubbed_path and os.path.exists(dubbed_path):
                    source_url = frame.dubbed_video_url

            if source_url is None and selected_id:
                selected_task = tasks_by_id.get(selected_id)
                source_url = getattr(selected_task, "video_url", None) if selected_task else None
            elif source_url is None:
                fallback = next(
                    (
                        task
                        for task in tasks
                        if getattr(task, "frame_id", None) == frame.id
                        and getattr(task, "status", None) == "completed"
                        and getattr(task, "video_url", None)
                    ),
                    None,
                )
                source_url = getattr(fallback, "video_url", None) if fallback else None
            frame_sources.append((frame.id, source_url))

            structured = frame.dialogue_structured
            subtitle_text = structured.line if structured else frame.dialogue
            subtitle_speaker = structured.speaker if structured else frame.speaker
            subtitles.append(
                (
                    frame.id,
                    max(0.1, float(frame.duration or 5)),
                    subtitle_speaker or "",
                    subtitle_text or "",
                )
            )

        return (
            tuple(frame_sources),
            tuple(subtitles),
            script.bgm_url,
            tuple(sorted((script.mix_settings or {}).items())),
        )

    @staticmethod
    def _restore_script_state(target: Script, snapshot: Script) -> None:
        """Restore a failed in-place transaction while preserving identity."""

        for field_name in type(target).model_fields:
            setattr(target, field_name, copy.deepcopy(getattr(snapshot, field_name)))
        target.__pydantic_fields_set__ = set(snapshot.__pydantic_fields_set__)
        target.__pydantic_extra__ = copy.deepcopy(snapshot.__pydantic_extra__)

    @contextmanager
    def _assembly_mutation(
        self,
        script_id: str,
        *,
        blocking: bool = False,
    ) -> Iterator[_AssemblyMutation]:
        """Serialize, persist, and retire output for an assembly mutation.

        Lock ordering is always the per-project operation lock followed by the
        global save lock.  The previous merged file is retired only after the
        authoritative JSON commit succeeds.  A failed commit restores both
        the original project object (for in-place mutations) and project map.
        """

        _validate_safe_id(script_id, "script_id")
        operation_lock = self._assembly_operation_lock(script_id)
        if not operation_lock.acquire(blocking=blocking):
            raise AssemblyOperationInProgressError(ASSEMBLY_OPERATION_BUSY_MESSAGE)

        retired_merged_path: Optional[str] = None
        mutation: Optional[_AssemblyMutation] = None
        original_script: Optional[Script] = None
        snapshot: Optional[Script] = None
        try:
            save_lock = getattr(self, "_save_lock", None)
            if save_lock is None:
                save_lock = threading.RLock()
                self._save_lock = save_lock
            with save_lock:
                original_script = self.scripts.get(script_id)
                if not original_script:
                    raise ValueError("Script not found")
                snapshot = original_script.model_copy(deep=True)
                before_signature = self._assembly_source_signature(snapshot)
                mutation = _AssemblyMutation(script=original_script)
                try:
                    yield mutation
                    committed_script = mutation.replacement or mutation.script
                    assembly_changed = mutation.force_assembly_invalidation or (
                        self._assembly_source_signature(committed_script) != before_signature
                    )
                    if not mutation.changed and not assembly_changed:
                        return

                    if assembly_changed:
                        previous_merged_url = snapshot.merged_video_url
                        committed_script.merged_video_url = None
                        committed_script.updated_at = time.time()
                        if previous_merged_url:
                            from ...utils.media_security import (
                                resolve_workspace_media_path,
                            )

                            try:
                                retired_merged_path = resolve_workspace_media_path(
                                    self.output_root,
                                    previous_merged_url,
                                    require_file=False,
                                )
                            except ValueError:
                                retired_merged_path = None

                    self.scripts[script_id] = committed_script
                    self._save_data()
                except BaseException:
                    self.scripts[script_id] = original_script
                    if mutation.replacement is None:
                        self._restore_script_state(original_script, snapshot)
                    raise

            if retired_merged_path:
                _delete_or_defer_workspace_media(
                    self.output_root,
                    [retired_merged_path],
                )
        finally:
            operation_lock.release()

    def export_project(self, script_id: str, options: Dict[str, Any]) -> str:
        """Step 7: Export project to final video."""
        _validate_safe_id(script_id, "script_id")
        operation_lock = self._assembly_operation_lock(script_id)
        if not operation_lock.acquire(blocking=False):
            raise AssemblyOperationInProgressError(ASSEMBLY_OPERATION_BUSY_MESSAGE)
        try:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")

            if not script.merged_video_url:
                script = self.merge_videos(script_id)

            return self.export_manager.render_project(script, options)
        finally:
            operation_lock.release()

    def get_script(self, script_id: str) -> Optional[Script]:
        return self.scripts.get(script_id)

    def _load_data(self) -> Dict[str, Script]:
        if not os.path.exists(self.data_file):
            return {}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                scripts = {k: Script(**v) for k, v in data.items()}
            migrated = any(
                (raw.get("model_settings") or {}) != script.model_settings.model_dump()
                or raw.get("model_settings_overrides") != script.model_settings_overrides
                for key, script in scripts.items()
                for raw in [data.get(key) or {}]
            )
            if migrated and not self.read_only:
                _atomic_json_dump(
                    self.data_file,
                    {k: v.model_dump() for k, v in scripts.items()},
                )
            return scripts
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            raise RuntimeError(f"Failed to load project data from {self.data_file}") from e

    def _save_data(self):
        """Save data with thread lock to prevent concurrent write issues."""
        self._assert_writable()
        with self._save_lock:
            try:
                _atomic_json_dump(
                    self.data_file,
                    {k: v.model_dump() for k, v in self.scripts.items()},
                )
                self._mark_asset_library_changed()
            except Exception as e:
                logger.error(f"Failed to save data: {e}")
                raise

    def _mark_asset_library_changed(self) -> None:
        """Advance the desktop feed revision after authoritative JSON commits."""

        self._asset_library_data_revision = (
            int(getattr(self, "_asset_library_data_revision", 0)) + 1
        )
        self._asset_library_snapshot_cache = None

    def asset_library_snapshot(self):
        """Reuse one coherent desktop feed until authoritative metadata changes."""

        from ..web_runtime.asset_library_feed import build_asset_library_snapshot

        with self._save_lock:
            revision = int(getattr(self, "_asset_library_data_revision", 0))
            cached = getattr(self, "_asset_library_snapshot_cache", None)
            if cached is not None and cached.revision == revision:
                logger.info(
                    "Asset usage index cache=hit revision=%s items=%s",
                    revision,
                    len(cached.items),
                )
                return cached
            snapshot = build_asset_library_snapshot(
                revision=revision,
                series=self.series_store.values(),
                projects=self.scripts.values(),
                library=self.library_store,
            )
            self._asset_library_snapshot_cache = snapshot
            return snapshot

    def _repair_series_bindings(self):
        """Repair episodes listed in series.episode_ids that have series_id=None."""
        repaired = False
        for series_id, series in self.series_store.items():
            for ep_id in series.episode_ids:
                script = self.scripts.get(ep_id)
                if script and not script.series_id:
                    script.series_id = series_id
                    if not script.episode_number:
                        script.episode_number = series.episode_ids.index(ep_id) + 1
                    repaired = True
                    logger.info(f"Repaired series binding: episode {ep_id} → series {series_id}")
        if repaired:
            self._save_data()

    @staticmethod
    def _asset_name_key(asset: Any) -> str:
        """Return the stable human-name key used for extraction reconciliation."""
        return " ".join((getattr(asset, "name", "") or "").split()).casefold()

    @staticmethod
    def _dedupe_ids(values: List[str]) -> List[str]:
        """Preserve reference order while removing duplicate asset ids."""
        return list(dict.fromkeys(values))

    def _promote_episode_assets_to_series(
        self,
        script: Script,
        series: Series,
        *,
        match_by_name: bool,
    ) -> bool:
        """Move episode-owned assets into the canonical parent-series pool.

        Series-bound episodes and the Home/Series libraries must read and mutate
        one source of truth. Fresh extraction results can safely reconcile by
        normalized name because they do not contain generated media yet. The
        startup repair deliberately matches only ids so two existing, generated
        assets with the same display name are never collapsed or lost.

        All storyboard, character-variant, and video-task references are
        rewritten when a fresh extraction id maps onto an existing series id.
        """
        id_maps: Dict[str, Dict[str, str]] = {
            "characters": {},
            "scenes": {},
            "props": {},
        }
        changed = False

        for attr in ("characters", "scenes", "props"):
            local_assets = list(getattr(script, attr))
            if not local_assets:
                continue

            series_assets = getattr(series, attr)
            by_id = {asset.id: asset for asset in series_assets}
            by_name = {
                key: asset for asset in series_assets if (key := self._asset_name_key(asset))
            }

            for local_asset in local_assets:
                target = by_id.get(local_asset.id)
                if target is None and match_by_name:
                    name_key = self._asset_name_key(local_asset)
                    target = by_name.get(name_key) if name_key else None
                if target is None:
                    target = local_asset
                    series_assets.append(target)
                    by_id[target.id] = target
                    name_key = self._asset_name_key(target)
                    if name_key:
                        by_name.setdefault(name_key, target)
                id_maps[attr][local_asset.id] = target.id

            setattr(script, attr, [])
            changed = True

        if not changed:
            return False

        character_ids = id_maps["characters"]
        scene_ids = id_maps["scenes"]
        prop_ids = id_maps["props"]

        for frame in script.frames:
            frame.scene_id = scene_ids.get(frame.scene_id, frame.scene_id)
            frame.character_ids = self._dedupe_ids(
                [character_ids.get(asset_id, asset_id) for asset_id in frame.character_ids]
            )
            frame.prop_ids = self._dedupe_ids(
                [prop_ids.get(asset_id, asset_id) for asset_id in frame.prop_ids]
            )

        for character in series.characters:
            if character.base_character_id:
                character.base_character_id = character_ids.get(
                    character.base_character_id, character.base_character_id
                )

        all_ids = {**character_ids, **scene_ids, **prop_ids}
        for task in script.video_tasks:
            if task.asset_id:
                task.asset_id = all_ids.get(task.asset_id, task.asset_id)

        now = time.time()
        script.updated_at = now
        series.updated_at = now
        return True

    def _repair_series_asset_ownership(self) -> None:
        """Migrate legacy episode assets into their parent Series, idempotently."""
        repaired = False
        for script in self.scripts.values():
            if not script.series_id:
                continue
            series = self.series_store.get(script.series_id)
            if not series:
                continue
            if self._promote_episode_assets_to_series(script, series, match_by_name=False):
                repaired = True
                logger.info(
                    "Migrated episode assets into series: episode %s → series %s",
                    script.id,
                    series.id,
                )

        if repaired:
            # Persist the new canonical owner first. If the process exits between
            # these writes, the next startup sees duplicate ids and repairs the
            # episode side without losing the series copy.
            self._save_series_data()
            self._save_data()

    def create_project(
        self,
        title: str,
        text: str,
        skip_analysis: bool = False,
        workflow_mode: str = "i2v_legacy",
        series_id: Optional[str] = None,
        model_settings: Optional[ModelSettings] = None,
        prompt_config: Optional[PromptConfig] = None,
    ) -> Script:
        """Step 1: Parse novel and create project.

        When `series_id` is provided the new project is bound as the next
        episode of that existing series (episode_number = current max
        episode number in the series + 1) via the same
        `add_episode_to_series` mechanism used elsewhere. When `series_id`
        is None the behavior is the original standalone-project path,
        bit-for-bit unchanged.
        """
        # Resolve and validate the parent before any billable extraction. A
        # draft gives model/prompt inheritance a real Pydantic Script to
        # inspect, with series_id already bound, instead of allowing the
        # Script.model_settings defaults to hide the Series selection.
        series = None
        episode_number = None
        if series_id:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            existing = self.get_series_episodes(series_id)
            max_ep = max([ep.episode_number for ep in existing if ep.episode_number] or [0])
            episode_number = max_ep + 1

        draft = self.script_processor.create_draft_script(title, text)
        draft.workflow_mode = workflow_mode
        draft.series_id = series_id
        draft.episode_number = episode_number
        if series:
            # Several image/video generation paths still consume the episode
            # ModelSettings object directly. Seed it with the current Series
            # values while leaving override provenance empty, so chat/polish
            # inheritance stays live and downstream generation starts with
            # the intended Series selections.
            draft.model_settings = copy.deepcopy(series.model_settings)
            draft.model_settings_overrides = []
        else:
            selected_settings = model_settings or ModelSettings()
            draft.model_settings = copy.deepcopy(selected_settings)
            draft.model_settings_overrides = (
                canonical_model_setting_overrides(selected_settings.model_fields_set)
                if model_settings is not None
                else []
            )
            draft.prompt_config = copy.deepcopy(prompt_config or PromptConfig())

        if skip_analysis:
            script = draft
        else:
            extraction_prompt = self._effective_prompt_override(
                "entity_extraction",
                draft,
                series,
            )
            script = self.script_processor.parse_novel(
                title,
                text,
                extraction_prompt,
                model=self._effective_chat_model(draft),
            )
            # Keep the pre-extraction identity and association. Besides making
            # the ordering explicit, this prevents the parsed replacement from
            # momentarily existing as a standalone project.
            script.id = draft.id
            script.workflow_mode = workflow_mode
            script.series_id = series_id
            script.episode_number = episode_number
            script.model_settings = copy.deepcopy(draft.model_settings)
            script.model_settings_overrides = list(draft.model_settings_overrides)
            script.prompt_config = copy.deepcopy(draft.prompt_config)

        self.scripts[script.id] = script

        # Optional series binding (T9). Reuses add_episode_to_series so the
        # episode_ids / series_id / episode_number wiring matches every other
        # "attach episode to series" path. It persists both stores.
        if series_id:
            self.add_episode_to_series(
                series_id,
                script.id,
                episode_number=episode_number,
            )
        else:
            self._save_data()
        return script

    @staticmethod
    def _overlay_model_settings(
        base: ModelSettings,
        selected: ModelSettings,
        override_fields: List[str],
    ) -> ModelSettings:
        """Apply only explicitly-owned fields to an inherited settings value."""
        values = base.model_dump()
        for field in canonical_model_setting_overrides(override_fields):
            values[field] = getattr(selected, field)
        return ModelSettings.model_validate(values)

    def _global_model_settings(self) -> ModelSettings:
        """Build the capability defaults currently selected by the admin."""
        return ModelSettings(
            chat_model=get_selected_model(CHAT),
            image_model=get_selected_model(IMAGE),
            video_model=get_selected_model(VIDEO),
        )

    def _effective_series_model_settings(
        self,
        series: Optional[Series],
    ) -> ModelSettings:
        """Resolve Series overrides on top of current global selections."""
        settings = self._global_model_settings()
        if not series:
            return settings
        return self._overlay_model_settings(
            settings,
            series.model_settings,
            series.model_settings_overrides,
        )

    def _effective_model_settings(self, script: Script) -> ModelSettings:
        """Resolve project override -> Series override -> global per field.

        ModelSettings is default-filled by Pydantic, so merely having the
        object cannot mean that a user persisted an episode override. Durable
        per-field provenance distinguishes inheritance from an explicit choice
        even when that choice equals the catalog default.
        """
        series_id = getattr(script, "series_id", None)
        series = getattr(self, "series_store", {}).get(series_id) if series_id else None
        inherited = self._effective_series_model_settings(series)
        project_settings = getattr(script, "model_settings", None) or ModelSettings()
        project_overrides = getattr(script, "model_settings_overrides", None) or []
        return self._overlay_model_settings(
            inherited,
            project_settings,
            project_overrides,
        )

    def _effective_chat_model(self, script: Script) -> str:
        """Resolve the effective chat model for the current episode."""
        return self._effective_model_settings(script).chat_model

    def _effective_prompt_override(
        self,
        prompt_type: str,
        episode: Script,
        series: Optional[Series] = None,
    ) -> str:
        """Resolve a custom prompt as Episode -> Series -> no override.

        Callers whose adapters own a built-in prompt need the empty-string
        sentinel rather than a copied system default. This also keeps later
        system-prompt updates live for projects without a customization.
        """
        valid_prompt_types = (
            "storyboard_polish",
            "video_polish",
            "entity_extraction",
            "style_analysis",
            "storyboard_extraction",
        )
        if prompt_type not in valid_prompt_types:
            raise ValueError(
                f"Invalid prompt_type: {prompt_type}. " f"Must be one of {valid_prompt_types}"
            )

        episode_config = getattr(episode, "prompt_config", None)
        episode_value = getattr(episode_config, prompt_type, "") if episode_config else ""
        if episode_value and episode_value.strip():
            return episode_value

        if series is None:
            series_id = getattr(episode, "series_id", None)
            series = getattr(self, "series_store", {}).get(series_id) if series_id else None
        series_config = getattr(series, "prompt_config", None) if series else None
        series_value = getattr(series_config, prompt_type, "") if series_config else ""
        return series_value if series_value and series_value.strip() else ""

    def _effective_polish_model(self, script: Script) -> str:
        """Resolve polish model with the same inheritance for every surface."""
        project_config = getattr(script, "prompt_config", None)
        project_model = (
            getattr(project_config, "polish_model", "").strip() if project_config else ""
        )
        if project_model:
            return get_model_spec(project_model, CHAT).model_id

        series_id = getattr(script, "series_id", None)
        series = getattr(self, "series_store", {}).get(series_id) if series_id else None
        series_config = getattr(series, "prompt_config", None) if series else None
        series_model = getattr(series_config, "polish_model", "").strip() if series_config else ""
        if series_model:
            return get_model_spec(series_model, CHAT).model_id

        return self._effective_chat_model(script)

    def extract_preview(self, script_id: str, text: str) -> tuple[Script, str]:
        """Run entity extraction without saving. Cache result for subsequent apply."""
        existing_script = self.scripts.get(script_id)
        if not existing_script:
            raise ValueError("Script not found")
        custom_extraction = self._effective_prompt_override(
            "entity_extraction",
            existing_script,
        )
        new_script = self.script_processor.parse_novel(
            existing_script.title,
            text,
            custom_extraction,
            model=self._effective_chat_model(existing_script),
        )
        # The revision is opaque to clients.  Applying an old preview must
        # never silently trigger a new LLM parse or overwrite newer text.
        revision = uuid.uuid4().hex
        self._consumed_extraction_revisions.pop(script_id, None)
        self._extraction_cache[script_id] = (time.time(), revision, new_script)
        return new_script, revision

    def reparse_project(self, script_id: str, text: str, preview_revision: str = "") -> Script:
        """Re-parse the text for an existing project, replacing all entities."""
        existing_script = self.scripts.get(script_id)
        if not existing_script:
            raise ValueError("Script not found")

        if not preview_revision:
            raise ValueError("Entity preview is required before applying changes")

        # Use only the exact reviewed extraction cached by extract_preview.
        # A missing cache must never fall back to an unreviewed, billable LLM
        # parse. A recently consumed revision is a response-loss retry and can
        # safely return the current persisted project.
        cached = self._extraction_cache.get(script_id)
        now = time.time()
        if not cached:
            consumed = self._consumed_extraction_revisions.get(script_id)
            if consumed:
                consumed_at, consumed_revision = consumed
                if now - consumed_at >= 300:
                    self._consumed_extraction_revisions.pop(script_id, None)
                elif preview_revision == consumed_revision:
                    return existing_script
            raise ValueError("Entity preview expired; run extraction again")

        cached_at, cached_revision, new_script = cached
        if now - cached_at >= 300:
            self._extraction_cache.pop(script_id, None)
            raise ValueError("Entity preview expired; run extraction again")
        if preview_revision != cached_revision:
            raise ValueError("Entity preview changed; review the latest extraction before applying")
        if (new_script.original_text or "") != (text or ""):
            raise ValueError("Entity preview text changed; run extraction again")

        # Parsing happens before the assembly lock. Only the short, durable
        # replacement commit is serialized with merge/export.
        replacement = new_script.model_copy(deep=True)
        with self._assembly_mutation(script_id) as mutation:
            existing_script = mutation.script

            # Preserve the original script ID and project-level settings.
            replacement.id = existing_script.id
            replacement.created_at = existing_script.created_at
            replacement.updated_at = time.time()
            replacement.art_direction = existing_script.art_direction
            replacement.model_settings = existing_script.model_settings
            replacement.model_settings_overrides = existing_script.model_settings_overrides
            replacement.style_preset = existing_script.style_preset
            replacement.style_prompt = existing_script.style_prompt
            replacement.workflow_mode = existing_script.workflow_mode
            # Preserve series binding — the freshly parsed Script defaults
            # series_id/episode_number to None, which would orphan an episode
            # mid-reparse and break the Reconcile suggestions endpoint.
            replacement.series_id = existing_script.series_id
            replacement.episode_number = existing_script.episode_number
            replacement.prompt_config = existing_script.prompt_config
            replacement.default_generation_mode = existing_script.default_generation_mode
            replacement.bgm_url = existing_script.bgm_url
            replacement.mix_settings = existing_script.mix_settings

            parent_series = None
            if replacement.series_id:
                parent_series = self.series_store.get(replacement.series_id)
                if not parent_series:
                    raise ValueError("Parent series not found")

            if parent_series:
                self._promote_episode_assets_to_series(
                    replacement,
                    parent_series,
                    match_by_name=True,
                )
                # Series is the canonical owner for series-bound episode assets.
                self._save_series_data()
            mutation.replace_script(replacement)
        # Consume only after the replacement was durably persisted. This makes
        # duplicate Apply clicks deterministic instead of reparsing remotely.
        self._extraction_cache.pop(script_id, None)
        self._consumed_extraction_revisions[script_id] = (time.time(), preview_revision)
        return replacement

    def generate_assets(self, script_id: str) -> Script:
        """Step 2: Generate character and scene assets (Batch)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        asset_owner: Any = script
        if script.series_id:
            asset_owner = self.series_store.get(script.series_id)
            if asset_owner is None:
                raise ValueError("Parent series not found")

        logger.info(f"Generating assets for script {script.id}")

        # Sort characters: Base characters first (those without base_character_id)
        sorted_chars = sorted(
            asset_owner.characters,
            key=lambda c: 0 if not c.base_character_id else 1,
        )

        for char in sorted_chars:
            self.generate_asset(script_id, char.id, "character")

        for scene in asset_owner.scenes:
            self.generate_asset(script_id, scene.id, "scene")

        for prop in asset_owner.props:
            self.generate_asset(script_id, prop.id, "prop")

        self._save_data()
        return script

    def generate_asset(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        style_preset: str = None,
        reference_image_url: str = None,
        style_prompt: str = None,
        generation_type: str = "all",
        prompt: str = None,
        apply_style: bool = True,
        negative_prompt: str = None,
        batch_size: int = 1,
        model_name: str = None,
        aspect_ratio: str = None,
    ) -> Script:
        """Step 2: Generate a specific asset (character/scene/prop).
        If style_preset is None, uses the project's global style."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Get effective model names from project settings if not overridden
        effective_settings = self._effective_model_settings(script)
        t2i_model = model_name or effective_settings.t2i_model
        i2i_model = effective_settings.i2i_model
        get_model_spec(t2i_model, IMAGE)
        get_model_spec(i2i_model, IMAGE)
        resolve_model_api_key(t2i_model, IMAGE)

        # Get effective size based on asset type (aspect_ratio param overrides model_settings)
        from .assets import ASPECT_RATIO_TO_SIZE

        if aspect_ratio:
            effective_aspect = aspect_ratio
        elif asset_type == "character":
            effective_aspect = effective_settings.character_aspect_ratio
        elif asset_type == "scene":
            effective_aspect = effective_settings.scene_aspect_ratio
        elif asset_type == "prop":
            effective_aspect = effective_settings.prop_aspect_ratio
        else:
            effective_aspect = "9:16"

        if asset_type == "character":
            default_size = "1024x1536"
        elif asset_type == "scene":
            default_size = "1536x1024"
        else:
            default_size = "1024x1024"

        effective_size = ASPECT_RATIO_TO_SIZE.get(effective_aspect, default_size)

        # Determine effective style: Art Direction > passed style > legacy style
        effective_positive_prompt = ""
        effective_negative_prompt = negative_prompt or ""

        # Resolve art_direction: episode own > series inherited
        resolved_art_direction = script.art_direction
        if not resolved_art_direction and script.series_id:
            series = self.series_store.get(script.series_id)
            if series and series.art_direction:
                resolved_art_direction = series.art_direction
        if isinstance(resolved_art_direction, dict):
            resolved_art_direction = ArtDirection(**resolved_art_direction)

        if apply_style:
            if resolved_art_direction and resolved_art_direction.style_config:
                effective_positive_prompt = resolved_art_direction.style_config.get(
                    "positive_prompt", ""
                )
                global_neg = resolved_art_direction.style_config.get("negative_prompt", "")
                if global_neg:
                    effective_negative_prompt = (
                        f"{effective_negative_prompt}, {global_neg}"
                        if effective_negative_prompt
                        else global_neg
                    )
            elif style_prompt:
                effective_positive_prompt = style_prompt
            elif style_preset:
                effective_positive_prompt = f"{style_preset} style"
            elif script.style_preset:
                effective_positive_prompt = f"{script.style_preset} style"
                if script.style_prompt:
                    effective_positive_prompt += f", {script.style_prompt}"

        if asset_type not in {"character", "scene", "prop"}:
            raise ValueError(f"Invalid asset_type: {asset_type}")

        # `/projects/{id}` exposes the resolved Episode > Series > Global
        # asset stack.  Resolve mutations through the same ownership helper so
        # a visible global asset is not a read-only phantom in the editor.
        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if target_asset is None or source is None:
            raise ValueError(f"{asset_type.capitalize()} {asset_id} not found")

        target_asset.status = GenerationStatus.PROCESSING
        self._save_after_asset_mutation(source)

        try:
            # Generate with Art Direction style injected
            if asset_type == "character":
                # Pass generation_type and specific prompt if available
                # If prompt is provided (from Workbench), use it directly.
                # Otherwise, asset_generator will construct it using effective_positive_prompt.
                # Note: If prompt is provided, we might still want to append style if it's not included?
                # For now, let's assume the Workbench passes the FULL prompt or we pass style separately.
                # The asset_generator.generate_character expects 'prompt' as the specific prompt.
                # If 'prompt' is None, it constructs one.
                # We should pass effective_positive_prompt as 'positive_prompt' (style suffix) to be appended if needed.
                self.asset_generator.generate_character(
                    target_asset,
                    generation_type=generation_type,
                    prompt=prompt,
                    positive_prompt=effective_positive_prompt,  # Used as style suffix if prompt is auto-generated
                    negative_prompt=effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=t2i_model,
                    i2i_model_name=i2i_model,
                    size=effective_size,
                )
            elif asset_type == "scene":
                self.asset_generator.generate_scene(
                    target_asset,
                    effective_positive_prompt,
                    effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=t2i_model,
                    size=effective_size,
                    prompt=prompt,
                )
            elif asset_type == "prop":
                self.asset_generator.generate_prop(
                    target_asset,
                    effective_positive_prompt,
                    effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=t2i_model,
                    size=effective_size,
                    prompt=prompt,
                )

            self._synchronize_generated_primary_image(
                asset_type,
                target_asset,
                generation_type,
            )
            target_asset.status = GenerationStatus.COMPLETED
        except Exception as e:
            target_asset.status = GenerationStatus.FAILED
            raise e
        finally:
            self._save_after_asset_mutation(source)

        return script

    def create_asset_generation_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        style_preset: str = None,
        reference_image_url: str = None,
        style_prompt: str = None,
        generation_type: str = "all",
        prompt: str = None,
        apply_style: bool = True,
        negative_prompt: str = None,
        batch_size: int = 1,
        model_name: str = None,
        aspect_ratio: str = None,
        task_id: str = None,
    ) -> Tuple[Script, str]:
        """Creates an async asset generation task and returns (script, task_id) immediately."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        selected_model = model_name or self._effective_model_settings(script).image_model
        get_model_spec(selected_model, IMAGE)
        resolve_model_api_key(selected_model, IMAGE)

        # Find the resolved owner and set it to PROCESSING.  This must mirror
        # get_project's Episode > Series > Global response stack.
        if asset_type not in {"character", "scene", "prop"}:
            raise ValueError(f"Invalid asset_type: {asset_type}")
        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if target_asset is None or source is None:
            raise ValueError(f"{asset_type.capitalize()} {asset_id} not found")
        if source == "script":
            asset_owner_kind = "project"
            asset_owner_id = script.id
        elif source == "series":
            if not script.series_id:
                raise RuntimeError("Series-owned asset has no series id")
            asset_owner_kind = "series"
            asset_owner_id = script.series_id
        elif source == "global":
            asset_owner_kind = "global"
            asset_owner_id = "global"
        else:
            raise RuntimeError(f"Unsupported asset owner: {source}")

        previous_status = target_asset.status
        target_asset.status = GenerationStatus.PROCESSING
        self._save_after_asset_mutation(source)

        # Create task
        task_id = task_id or str(uuid.uuid4())
        _validate_safe_id(task_id, "task_id")
        if task_id in self.asset_generation_tasks:
            raise ValueError("Asset generation task already exists")
        self.asset_generation_tasks[task_id] = {
            "status": "pending",  # pending -> processing -> completed/failed
            "progress": 0,
            "error": None,
            "script_id": script_id,
            "asset_id": asset_id,
            "asset_type": asset_type,
            "asset_source": source,
            "asset_owner_kind": asset_owner_kind,
            "asset_owner_id": asset_owner_id,
            # Retain the existing flag for compatibility with callers/tests
            # that inspect the in-memory task record.
            "asset_is_series_level": source == "series",
            "asset_is_global_level": source == "global",
            "previous_asset_status": previous_status,
            "created_at": time.time(),
            # Store all params for later processing
            "params": {
                "style_preset": style_preset,
                "reference_image_url": reference_image_url,
                "style_prompt": style_prompt,
                "generation_type": generation_type,
                "prompt": prompt,
                "apply_style": apply_style,
                "negative_prompt": negative_prompt,
                "batch_size": batch_size,
                "model_name": selected_model,
                "aspect_ratio": aspect_ratio,
            },
        }

        self._save_data()
        return script, task_id

    def rollback_asset_generation_task(self, task_id: str) -> bool:
        """Remove an unpublished asset task and restore its prior UI status."""

        with self._save_lock:
            task = self.asset_generation_tasks.pop(task_id, None)
            if task is None:
                return False
            asset_type = task.get("asset_type")
            asset_id = task.get("asset_id")
            previous_status = task.get("previous_asset_status", GenerationStatus.PENDING)
            if task.get("is_series"):
                series = self.series_store.get(task.get("script_id"))
                assets = (
                    series.characters
                    if series and asset_type == "character"
                    else (
                        series.scenes
                        if series and asset_type == "scene"
                        else series.props if series and asset_type == "prop" else []
                    )
                )
                target = next((item for item in assets if item.id == asset_id), None)
                if target is not None and "previous_asset_status" in task:
                    target.status = previous_status
                self._save_series_data_unlocked()
                return True

            script = self.scripts.get(task.get("script_id"))
            source = task.get("asset_source")
            if source is None:
                source = (
                    "global"
                    if task.get("asset_is_global_level")
                    else "series" if task.get("asset_is_series_level") else "script"
                )

            if source == "series":
                owner = self.series_store.get(script.series_id) if script else None
            elif source == "global":
                owner = self.library_store
            else:
                owner = script
            assets = (
                owner.characters
                if owner and asset_type == "character"
                else (
                    owner.scenes
                    if owner and asset_type == "scene"
                    else owner.props if owner and asset_type == "prop" else []
                )
            )
            target = next((item for item in assets if item.id == asset_id), None)
            if target is not None:
                target.status = previous_status
                self._save_after_asset_mutation(source)
            return True

    def forget_asset_generation_task(self, task_id: str) -> bool:
        """Drop API/worker bookkeeping after durable ownership is transferred."""

        with self._save_lock:
            return self.asset_generation_tasks.pop(task_id, None) is not None

    def fail_orphaned_asset_reservation(
        self, script_id: str, asset_id: str, asset_type: str
    ) -> bool:
        """Make a crash-orphaned PROCESSING asset retryable after API restart."""

        return self.restore_asset_reservation(
            script_id,
            asset_id,
            asset_type,
            GenerationStatus.FAILED,
        )

    def restore_asset_reservation(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        previous_status: Any,
    ) -> bool:
        """Restore a resolved Episode asset reservation if it is still active.

        The conditional PROCESSING check prevents a late cancel request from
        overwriting a worker result that completed between the database state
        transition and workspace cleanup.
        """

        with self._save_lock:
            script = self.scripts.get(script_id)
            if script is None:
                return False
            target, source = self._find_asset_with_source(script, asset_id, asset_type)
            if target is None or source is None or target.status != GenerationStatus.PROCESSING:
                return False
            try:
                target.status = GenerationStatus(previous_status)
            except (TypeError, ValueError):
                target.status = GenerationStatus.FAILED
            self._save_after_asset_mutation(source)
            return True

    def fail_orphaned_source_asset_reservation(
        self,
        source_kind: str,
        source_id: str,
        asset_id: str,
        asset_type: str,
    ) -> bool:
        """Make an exact-owner durable reservation retryable after API restart."""

        return self.restore_source_asset_reservation(
            source_kind,
            source_id,
            asset_id,
            asset_type,
            GenerationStatus.FAILED,
        )

    def restore_source_asset_reservation(
        self,
        source_kind: str,
        source_id: str,
        asset_id: str,
        asset_type: str,
        previous_status: Any,
    ) -> bool:
        """Restore one exact-owner reservation if it remains PROCESSING."""

        with self._save_lock:
            try:
                target, storage_source, _, _ = self.find_source_asset(
                    source_kind, source_id, asset_type, asset_id
                )
            except ValueError:
                return False
            if target.status != GenerationStatus.PROCESSING:
                return False
            try:
                target.status = GenerationStatus(previous_status)
            except (TypeError, ValueError):
                target.status = GenerationStatus.FAILED
            self._save_after_asset_mutation(storage_source)
            return True

    def process_asset_generation_task(self, task_id: str):
        """Processes an asset generation task in the background."""
        task = self.asset_generation_tasks.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        task["status"] = "processing"

        try:
            params = task["params"]
            if task.get("is_series"):
                # Series asset generation — operate on series_store
                self._process_series_asset_task(task, params)
            elif task.get("is_global"):
                self._process_global_asset_task(task, params)
            else:
                # Project asset generation — existing logic
                self.generate_asset(
                    task["script_id"],
                    task["asset_id"],
                    task["asset_type"],
                    params["style_preset"],
                    params["reference_image_url"],
                    params["style_prompt"],
                    params["generation_type"],
                    params["prompt"],
                    params["apply_style"],
                    params["negative_prompt"],
                    params["batch_size"],
                    params["model_name"],
                    params.get("aspect_ratio"),
                )
            task["status"] = "completed"
            task["progress"] = 100
            logger.info(f"Task {task_id} completed successfully")
        except Exception as e:
            task["status"] = "failed"
            task["error"] = _public_background_failure(e, media="素材")
            logger.error(f"Task {task_id} failed: {e}")

    def _process_series_asset_task(self, task: Dict, params: Dict):
        """Process a Series asset generation task."""
        series_id = task["script_id"]  # stored as script_id for compatibility
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")

        asset_id = task["asset_id"]
        asset_type = task["asset_type"]
        positive_prompt = params.get("effective_positive_prompt", "")
        negative_prompt = params.get("effective_negative_prompt", "")
        t2i_model = (
            params.get("t2i_model") or self._effective_series_model_settings(series).image_model
        )
        effective_size = params.get("effective_size", "1024x1536")
        batch_size = params.get("batch_size", 1)
        generation_type = params.get("generation_type", "all")
        prompt = params.get("prompt")
        reference_image_url = params.get("reference_image_url")

        if asset_type == "character":
            target = next((c for c in series.characters if c.id == asset_id), None)
            if not target:
                raise ValueError(f"Character {asset_id} not found in series")
            self.asset_generator.generate_character(
                target,
                generation_type=generation_type,
                prompt=prompt or "",
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                batch_size=batch_size,
                model_name=t2i_model,
                size=effective_size,
            )
        elif asset_type == "scene":
            target = next((s for s in series.scenes if s.id == asset_id), None)
            if not target:
                raise ValueError(f"Scene {asset_id} not found in series")
            self.asset_generator.generate_scene(
                target,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                batch_size=batch_size,
                model_name=t2i_model,
                size=effective_size,
                prompt=prompt,
            )
        elif asset_type == "prop":
            target = next((p for p in series.props if p.id == asset_id), None)
            if not target:
                raise ValueError(f"Prop {asset_id} not found in series")
            self.asset_generator.generate_prop(
                target,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                batch_size=batch_size,
                model_name=t2i_model,
                size=effective_size,
                prompt=prompt,
            )
        else:
            raise ValueError(f"Unknown asset type: {asset_type}")

        self._synchronize_generated_primary_image(
            asset_type,
            target,
            generation_type,
        )
        self._save_series_data()

    def _process_global_asset_task(self, task: Dict, params: Dict) -> None:
        """Process an image generation task directly against the global pool."""

        asset_type = task["asset_type"]
        target = self._find_library_asset(asset_type, task["asset_id"])
        positive_prompt = params.get("effective_positive_prompt", "")
        negative_prompt = params.get("effective_negative_prompt", "")
        model_name = params["t2i_model"]
        prompt = params.get("prompt")
        try:
            if asset_type == "character":
                self.asset_generator.generate_character(
                    target,
                    generation_type=params.get("generation_type", "all"),
                    prompt=prompt or "",
                    positive_prompt=positive_prompt,
                    negative_prompt=negative_prompt,
                    batch_size=params.get("batch_size", 1),
                    model_name=model_name,
                    i2i_model_name=model_name,
                    size=params["effective_size"],
                )
            elif asset_type == "scene":
                self.asset_generator.generate_scene(
                    target,
                    positive_prompt=positive_prompt,
                    negative_prompt=negative_prompt,
                    batch_size=params.get("batch_size", 1),
                    model_name=model_name,
                    size=params["effective_size"],
                    prompt=prompt,
                )
            elif asset_type == "prop":
                self.asset_generator.generate_prop(
                    target,
                    positive_prompt=positive_prompt,
                    negative_prompt=negative_prompt,
                    batch_size=params.get("batch_size", 1),
                    model_name=model_name,
                    size=params["effective_size"],
                    prompt=prompt,
                )
            else:
                raise ValueError(f"Unknown asset type: {asset_type}")
            self._synchronize_generated_primary_image(
                asset_type,
                target,
                params.get("generation_type", "all"),
            )
            target.status = GenerationStatus.COMPLETED
        except Exception:
            target.status = GenerationStatus.FAILED
            raise
        finally:
            self._save_library_data()

    def get_asset_generation_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Returns the status of an asset generation task."""
        # Check image tasks first
        task = self.asset_generation_tasks.get(task_id)
        if not task:
            # Then check video tasks
            task = self.video_generation_tasks.get(task_id)

        if not task:
            return None

        return {
            "task_id": task_id,
            "status": task["status"],
            "progress": task.get("progress", 0),
            "error": task.get("error"),
            "asset_id": task.get("asset_id"),
            "asset_type": task.get("asset_type"),
            "script_id": task.get("script_id"),
            "asset_source": task.get("asset_source"),
            "created_at": task.get("created_at"),
        }

    def asset_generation_task_result_asset(self, task_id: str) -> Any:
        """Return the exact canonical asset owned by a completed image task."""

        task = self.asset_generation_tasks.get(task_id)
        if task is None:
            raise ValueError(f"Asset generation task {task_id} not found")
        if task.get("status") != "completed":
            raise ValueError(f"Asset generation task {task_id} is not completed")

        owner_kind = task.get("asset_owner_kind")
        owner_id = task.get("asset_owner_id")
        asset_type = task.get("asset_type")
        asset_id = task.get("asset_id")
        if owner_kind not in {"project", "series", "global"} or not isinstance(owner_id, str):
            raise RuntimeError("Asset generation task has no canonical owner")
        if asset_type not in {"character", "scene", "prop"} or not isinstance(asset_id, str):
            raise RuntimeError("Asset generation task has invalid asset metadata")

        asset, storage_source, _, _ = self.find_source_asset(
            owner_kind,
            owner_id,
            asset_type,
            asset_id,
        )
        expected_source = {
            "project": "script",
            "series": "series",
            "global": "global",
        }[owner_kind]
        if storage_source != expected_source:
            raise RuntimeError("Asset generation task resolved a different owner")
        return asset

    def create_motion_ref_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        prompt: Optional[str] = None,
        audio_url: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
        model_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Tuple[Script, str]:
        """Creates an async motion reference generation task."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        if audio_url:
            raise ValueError("New API Seedance does not support driving-audio input")

        # Validate against the same Episode > Series > Global asset stack used
        # by the worker before publishing a durable job.  Previously a shared
        # series character could be accepted by the UI but fail immediately in
        # the worker with "Character ... not found" because task creation did
        # not verify the canonical asset owner at all.
        target_asset, _source = self._resolve_motion_reference_asset(script, asset_id, asset_type)
        if not self._motion_reference_source_image_url(target_asset, asset_type):
            raise ValueError(
                f"No source image available for {asset_type}. "
                "Please generate a static image first."
            )

        selected_model = get_model_spec(
            model_id or self._effective_model_settings(script).video_model,
            VIDEO,
        ).model_id
        resolve_model_api_key(selected_model, VIDEO)

        task_id = task_id or str(uuid.uuid4())
        _validate_safe_id(task_id, "task_id")
        if task_id in self.video_generation_tasks:
            raise ValueError("Motion reference task already exists")
        self.video_generation_tasks[task_id] = {
            "status": "pending",
            "progress": 0,
            "error": None,
            "script_id": script_id,
            "asset_id": asset_id,
            "asset_type": asset_type,
            "created_at": time.time(),
            "params": {
                "prompt": prompt,
                "audio_url": audio_url,
                "duration": duration,
                "batch_size": batch_size,
                "model": selected_model,
            },
        }

        self._save_data()
        return script, task_id

    def create_source_motion_ref_task(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        *,
        motion_type: Optional[str] = None,
        prompt: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
        model_id: Optional[str] = None,
        audio_url: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Tuple[Any, str]:
        """Create a desktop-compatible transient exact-owner motion task."""

        asset, _, _, _ = self.find_source_asset(source_kind, source_id, asset_type, asset_id)
        canonical_motion_type = self._canonical_motion_type(asset_type, motion_type)
        if not self._motion_reference_source_image_url(asset, canonical_motion_type):
            raise ValueError(
                f"No source image available for {canonical_motion_type}. "
                "Please generate or select a static image first."
            )
        settings = self._source_owner_model_settings(source_kind, source_id)
        selected_model = get_model_spec(model_id or settings.video_model, VIDEO).model_id
        resolve_model_api_key(selected_model, VIDEO)
        task_id = task_id or str(uuid.uuid4())
        _validate_safe_id(task_id, "task_id")
        if task_id in self.video_generation_tasks:
            raise ValueError("Motion reference task already exists")
        self.video_generation_tasks[task_id] = {
            "status": "pending",
            "progress": 0,
            "error": None,
            "source_kind": source_kind,
            "source_id": source_id,
            "asset_id": asset_id,
            "asset_type": asset_type,
            "created_at": time.time(),
            "params": {
                "motion_type": canonical_motion_type,
                "prompt": prompt,
                "duration": duration,
                "batch_size": batch_size,
                "model": selected_model,
                "audio_url": audio_url,
            },
        }
        return asset, task_id

    def process_source_motion_ref_task(self, task_id: str) -> None:
        task = self.video_generation_tasks.get(task_id)
        if not task:
            logger.error("Exact-owner motion task %s not found", task_id)
            return
        task["status"] = "processing"
        try:
            params = task["params"]
            self.generate_source_asset_motion_ref(
                task["source_kind"],
                task["source_id"],
                task["asset_type"],
                task["asset_id"],
                motion_type=params["motion_type"],
                prompt=params["prompt"],
                duration=params["duration"],
                batch_size=params["batch_size"],
                model_id=params["model"],
                audio_url=params.get("audio_url"),
            )
            task["status"] = "completed"
            task["progress"] = 100
        except Exception as exc:
            task["status"] = "failed"
            task["error"] = _public_background_failure(exc, media="视频")
            logger.exception("Exact-owner motion task %s failed", task_id)

    def rollback_motion_ref_task(self, task_id: str) -> bool:
        """Remove an unpublished transient motion-reference task."""

        with self._save_lock:
            return self.video_generation_tasks.pop(task_id, None) is not None

    def forget_motion_ref_task(self, task_id: str) -> bool:
        """Drop transient motion-reference bookkeeping after queue publish."""

        with self._save_lock:
            return self.video_generation_tasks.pop(task_id, None) is not None

    def process_motion_ref_task(self, script_id: str, task_id: str):
        """Processes a video generation task in the background."""
        task = self.video_generation_tasks.get(task_id)
        if not task:
            logger.error(f"Video task {task_id} not found")
            return

        task["status"] = "processing"

        try:
            params = task["params"]
            # Call the synchronous generate_motion_ref method
            self.generate_motion_ref(
                script_id=script_id,
                asset_id=task["asset_id"],
                asset_type=task["asset_type"],
                prompt=params["prompt"],
                audio_url=params["audio_url"],
                duration=params["duration"],
                batch_size=params["batch_size"],
                model_id=params["model"],
            )
            task["status"] = "completed"
            task["progress"] = 100
            logger.info(f"Video task {task_id} completed successfully")
        except Exception as e:
            task["status"] = "failed"
            task["error"] = _public_background_failure(e, media="视频")
            logger.error(f"Video task {task_id} failed: {e}")

    def sync_descriptions_from_script_entities(self, script_id: str) -> Script:
        """
        Syncs entity descriptions from ScriptProcessor parsed entities.
        This clears saved prompts so the UI will regenerate them from the current description.

        Note: This only updates prompts, not generated images/videos.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        asset_owner: Any = script
        if script.series_id:
            asset_owner = self.series_store.get(script.series_id)
            if asset_owner is None:
                raise ValueError("Parent series not found")

        # Clear saved prompts for all characters so UI will regenerate from description
        for character in asset_owner.characters:
            character.full_body_prompt = None
            character.three_view_prompt = None
            character.headshot_prompt = None
            character.video_prompt = None

        # Scenes and props might also have prompts to clear (if applicable)
        for scene in asset_owner.scenes:
            if hasattr(scene, "prompt"):
                scene.prompt = None

        for prop in asset_owner.props:
            if hasattr(prop, "prompt"):
                prop.prompt = None

        if script.series_id:
            asset_owner.updated_at = time.time()
            self._save_series_data()
        else:
            script.updated_at = time.time()
            self._save_data()
        logger.info(
            "Descriptions synced for script %s: cleared prompts for %s characters, %s scenes, %s props",
            script_id,
            len(asset_owner.characters),
            len(asset_owner.scenes),
            len(asset_owner.props),
        )
        return script

    def add_character(self, script_id: str, name: str, description: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        new_char = Character(id=f"char_{uuid.uuid4().hex[:8]}", name=name, description=description)
        if script.series_id:
            series = self.series_store.get(script.series_id)
            if not series:
                raise ValueError("Parent series not found")
            series.characters.append(new_char)
            series.updated_at = time.time()
            self._save_series_data()
        else:
            script.characters.append(new_char)
            script.updated_at = time.time()
            self._save_data()
        return script

    def delete_character(self, script_id: str, char_id: str) -> Script:
        return self._delete_resolved_asset(script_id, char_id, "character")

    def add_scene(self, script_id: str, name: str, description: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        new_scene = Scene(id=f"scene_{uuid.uuid4().hex[:8]}", name=name, description=description)
        if script.series_id:
            series = self.series_store.get(script.series_id)
            if not series:
                raise ValueError("Parent series not found")
            series.scenes.append(new_scene)
            series.updated_at = time.time()
            self._save_series_data()
        else:
            script.scenes.append(new_scene)
            script.updated_at = time.time()
            self._save_data()
        return script

    def add_prop(self, script_id: str, name: str, description: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        new_prop = Prop(
            id=f"prop_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description,
            status=GenerationStatus.PENDING,
        )
        if script.series_id:
            series = self.series_store.get(script.series_id)
            if not series:
                raise ValueError("Parent series not found")
            series.props.append(new_prop)
            series.updated_at = time.time()
            self._save_series_data()
        else:
            script.props.append(new_prop)
            script.updated_at = time.time()
            self._save_data()
        return script

    def delete_scene(self, script_id: str, scene_id: str) -> Script:
        return self._delete_resolved_asset(script_id, scene_id, "scene")

    def delete_prop(self, script_id: str, prop_id: str) -> Script:
        return self._delete_resolved_asset(script_id, prop_id, "prop")

    @staticmethod
    def _asset_list(container: Any, asset_type: str) -> List[Any]:
        if asset_type == "character":
            return container.characters
        if asset_type == "scene":
            return container.scenes
        if asset_type == "prop":
            return container.props
        raise ValueError(f"Invalid asset type: {asset_type}")

    @staticmethod
    def _embedded_asset_video_ids(asset: Any) -> set[str]:
        task_ids = {task.id for task in (getattr(asset, "video_assets", None) or [])}
        for unit_name in ("reference_sheet", "full_body", "three_views", "head_shot"):
            unit = getattr(asset, unit_name, None)
            task_ids.update(variant.id for variant in (getattr(unit, "video_variants", None) or []))
        return task_ids

    def _delete_resolved_asset(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
    ) -> Script:
        """Delete the visible owner and clean only references with no fallback.

        Episode assets override series assets, which override global assets. If
        removing an override reveals an asset with the same id underneath, frame
        references stay intact. Shared asset removal is applied to every episode
        that resolved that owner, while unrelated projects are untouched.
        """

        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if target_asset is None or source is None:
                raise ValueError(f"{asset_type.title()} not found")

            if source == "script":
                owner = script
                affected_scripts = [script]
            elif source == "series":
                owner = self.series_store.get(script.series_id or "")
                if owner is None:
                    raise ValueError("Series not found")
                affected_scripts = [
                    item for item in self.scripts.values() if item.series_id == script.series_id
                ]
            else:
                owner = self.library_store
                affected_scripts = list(self.scripts.values())

            owner_assets = self._asset_list(owner, asset_type)
            self._asset_list(owner, asset_type)[:] = [
                asset for asset in owner_assets if asset.id != asset_id
            ]
            owned_video_ids = self._embedded_asset_video_ids(target_asset)

            for affected in affected_scripts:
                resolved = self.resolve_episode_assets(affected)
                fallback_exists = any(
                    asset.id == asset_id
                    for asset in resolved[f"{asset_type}s" if asset_type != "prop" else "props"]
                )
                self._detach_video_tasks(
                    affected,
                    lambda task, fallback=fallback_exists: (
                        task.id in owned_video_ids or (not fallback and task.asset_id == asset_id)
                    ),
                )
                if not fallback_exists:
                    for frame in affected.frames:
                        if asset_type == "character":
                            frame.character_ids = [
                                item for item in frame.character_ids if item != asset_id
                            ]
                        elif asset_type == "scene" and frame.scene_id == asset_id:
                            frame.scene_id = ""
                        elif asset_type == "prop":
                            frame.prop_ids = [item for item in frame.prop_ids if item != asset_id]
                affected.updated_at = time.time()

            self._save_data()
            if source == "series":
                self._save_series_data_unlocked()
            elif source == "global":
                self._save_library_data_unlocked()
            return script

    def _find_asset_with_source(
        self, script: "Script", asset_id: str, asset_type: str
    ) -> Tuple[Optional[object], Optional[str]]:
        """Locate an asset by (id, type) in either the episode's local
        list OR the parent series' shared pool. Returns
        (asset, source) where source ∈ {"script", "series", "global"} so the
        caller can mutate the right object and save the right side.

        Episode-local always wins (the user explicitly forked this
        asset to override the series version). Falls back to series
        only when the id isn't local. Returns (None, None) when the
        asset doesn't exist in either container — caller should 404.
        """
        if asset_type == "character":
            ep_list = script.characters
        elif asset_type == "scene":
            ep_list = script.scenes
        elif asset_type == "prop":
            ep_list = script.props
        else:
            return None, None
        local = next((a for a in ep_list if a.id == asset_id), None)
        if local is not None:
            return local, "script"
        # Fall back to series shared pool if this episode belongs to
        # a series.
        if script.series_id:
            series = self.series_store.get(script.series_id)
            if series:
                if asset_type == "character":
                    sh_list = series.characters
                elif asset_type == "scene":
                    sh_list = series.scenes
                else:  # prop
                    sh_list = series.props
                shared = next((a for a in sh_list if a.id == asset_id), None)
                if shared is not None:
                    return shared, "series"
            # Series miss → fall through to the global library below.
        # Fall back to the project-independent global asset library
        # (lowest layer). Empty by default, so this is a no-op until
        # the global pool is populated.
        if asset_type == "character":
            gl_list = self.library_store.characters
        elif asset_type == "scene":
            gl_list = self.library_store.scenes
        else:  # prop
            gl_list = self.library_store.props
        glob = next((a for a in gl_list if a.id == asset_id), None)
        if glob is not None:
            return glob, "global"
        return None, None

    @staticmethod
    def _asset_list_for_owner(owner: Any, asset_type: str) -> List[Any]:
        if asset_type == "character":
            return owner.characters
        if asset_type == "scene":
            return owner.scenes
        if asset_type == "prop":
            return owner.props
        raise ValueError(f"Invalid asset type: {asset_type}")

    def find_source_asset(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
    ) -> Tuple[Any, str, Optional[str], Optional[str]]:
        """Resolve one asset from its exact canonical owner.

        Unlike :meth:`_find_asset_with_source`, this method never falls back
        through Episode > Series > Global.  Home-library cards carry their
        owner explicitly, so mutations must not accidentally modify a lower
        precedence asset with the same id.
        """

        if source_kind == "project":
            owner = self.scripts.get(source_id)
            if owner is None:
                raise ValueError("Project not found")
            storage_source = "script"
            series_id = owner.series_id
            episode_id = owner.id
        elif source_kind == "series":
            owner = self.series_store.get(source_id)
            if owner is None:
                raise ValueError("Series not found")
            storage_source = "series"
            series_id = owner.id
            episode_id = None
        elif source_kind == "global":
            if source_id != "global":
                raise ValueError("Global asset source id must be 'global'")
            owner = self.library_store
            storage_source = "global"
            series_id = None
            episode_id = None
        else:
            raise ValueError(f"Invalid asset source kind: {source_kind}")

        assets = self._asset_list_for_owner(owner, asset_type)
        asset = next((item for item in assets if item.id == asset_id), None)
        if asset is None:
            raise ValueError(
                f"Asset {asset_id} of type {asset_type} not found in {source_kind} {source_id}"
            )
        return asset, storage_source, series_id, episode_id

    def source_asset_response_payload(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
    ) -> Dict[str, Any]:
        asset, _, series_id, episode_id = self.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        payload = self.asset_response_payload(
            asset,
            source=source_kind,
            source_id=source_id,
            series_id=series_id,
            episode_id=episode_id,
        )
        if source_kind == "project":
            owner = self.scripts[source_id]
            owner_label = owner.title
            affected_episode_count = 1
            art_direction = owner.art_direction
            model_settings = self._effective_model_settings(owner)
        elif source_kind == "series":
            owner = self.series_store[source_id]
            owner_label = owner.title
            affected_episode_count = sum(
                1
                for script in self.scripts.values()
                if script.series_id == source_id
                and self._find_asset_with_source(script, asset_id, asset_type)[1] == "series"
            )
            art_direction = owner.art_direction
            model_settings = self._effective_series_model_settings(owner)
        else:
            owner_label = "Global Asset Library"
            affected_episode_count = sum(
                1
                for script in self.scripts.values()
                if script.series_id is not None
                and self._find_asset_with_source(script, asset_id, asset_type)[1] == "global"
            )
            art_direction = None
            model_settings = self._global_model_settings()

        source_image = self._motion_reference_source_image_url(
            asset,
            "full_body" if asset_type == "character" else asset_type,
        )
        payload["_editor_context"] = {
            "ownerScope": source_kind,
            "ownerLabel": owner_label,
            "affectedEpisodeCount": affected_episode_count,
            "artDirection": (art_direction.model_dump() if art_direction is not None else None),
            "modelSettings": model_settings.model_dump(),
            "capabilities": {
                "staticGeneration": True,
                "motionGeneration": bool(source_image),
                "motionDisabledReason": (
                    None
                    if source_image
                    else "Generate or select a static image before creating motion."
                ),
                "uploadPrimaryImage": True,
            },
        }
        return payload

    @staticmethod
    def _set_asset_prompts(
        asset: Any,
        asset_type: str,
        prompts: Dict[str, Optional[str]],
    ) -> None:
        for key, value in prompts.items():
            if asset_type != "character":
                setattr(asset, key, value)
                continue
            if key in {"image_prompt", "reference_sheet_prompt"}:
                if asset.reference_sheet is None:
                    asset.reference_sheet = AssetUnit()
                asset.reference_sheet.image_prompt = value
                # CharacterWorkbench reads this legacy field for the master
                # panel; keep both representations synchronized.
                asset.full_body_prompt = value
            elif key == "full_body_video_prompt":
                if asset.full_body is None:
                    asset.full_body = AssetUnit()
                asset.full_body.video_prompt = value
                if asset.reference_sheet is None:
                    asset.reference_sheet = AssetUnit()
                asset.reference_sheet.video_prompt = value
            elif key == "headshot_video_prompt":
                if asset.head_shot is None:
                    asset.head_shot = AssetUnit()
                asset.head_shot.video_prompt = value
            elif key == "video_prompt":
                asset.video_prompt = value
            elif hasattr(asset, key):
                setattr(asset, key, value)

    @staticmethod
    def _canonical_image_copy(
        asset: Any,
        asset_type: str,
        *,
        include_character_derived: bool = True,
    ) -> ImageAsset:
        """Collapse every static image representation without losing media.

        Character assets accumulated several canonical and legacy containers
        over time.  A type change used to copy only ``reference_sheet``, which
        silently discarded full-body, three-view, head-shot, and direct-URL
        images.  The editor's type conversion is a storage migration, so all
        distinct variants must move into the target's canonical container.
        """

        containers: List[Any] = []
        direct_urls: List[Optional[str]] = []
        preferred_url = getattr(asset, "image_url", None)
        if asset_type == "character":
            # Older three-view/head-shot generators mirrored the derived image
            # into ``image_url``.  Startup normalization must not mistake that
            # compatibility alias for the character's full-body master.
            derived_urls: set[str] = set()
            if not include_character_derived:
                for field in (
                    "three_view_image_url",
                    "headshot_image_url",
                ):
                    value = getattr(asset, field, None)
                    if isinstance(value, str) and value.strip():
                        derived_urls.add(value.strip())
                for field in (
                    "three_views",
                    "head_shot",
                    "three_view_asset",
                    "headshot_asset",
                ):
                    container = getattr(asset, field, None)
                    if container is None:
                        continue
                    variants = (
                        getattr(container, "variants", None)
                        or getattr(container, "image_variants", None)
                        or []
                    )
                    derived_urls.update(variant.url for variant in variants if variant.url)
                if preferred_url in derived_urls:
                    preferred_url = None

            character_container_fields = [
                "reference_sheet",
                "full_body",
                "full_body_asset",
            ]
            character_url_fields = ["full_body_image_url"]
            if not (
                isinstance(getattr(asset, "image_url", None), str)
                and getattr(asset, "image_url").strip() in derived_urls
            ):
                character_url_fields.insert(0, "image_url")
            if include_character_derived:
                character_container_fields.extend(
                    (
                        "three_views",
                        "head_shot",
                        "three_view_asset",
                        "headshot_asset",
                    )
                )
                character_url_fields.extend(
                    ("three_view_image_url", "headshot_image_url", "avatar_url")
                )
            containers.extend(getattr(asset, field, None) for field in character_container_fields)
            direct_urls.extend(getattr(asset, field, None) for field in character_url_fields)
        else:
            containers.extend(
                (
                    getattr(asset, "image_asset", None),
                    getattr(asset, "image", None),
                )
            )
            direct_urls.append(getattr(asset, "image_url", None))

        candidate_groups: Dict[str, List[Tuple[ImageVariant, bool]]] = {}
        url_order: List[str] = []
        selected_urls: List[str] = []

        for container in containers:
            if container is None:
                continue
            if hasattr(container, "variants"):
                container_variants = container.variants or []
                selected_id = container.selected_id
            elif hasattr(container, "image_variants"):
                container_variants = container.image_variants or []
                selected_id = container.selected_image_id
            else:
                continue
            selected = next(
                (variant for variant in container_variants if variant.id == selected_id),
                None,
            )
            if selected is not None:
                selected_urls.append(selected.url)
            for source_variant in container_variants:
                if source_variant.url not in candidate_groups:
                    candidate_groups[source_variant.url] = []
                    url_order.append(source_variant.url)
                candidate_groups[source_variant.url].append(
                    (source_variant, source_variant.id == selected_id)
                )

        variants: List[ImageVariant] = []
        used_ids: Dict[str, str] = {}
        for url in url_order:
            candidates = candidate_groups[url]
            authoritative, _ = max(
                candidates,
                key=lambda item: (
                    item[1],
                    item[0].is_uploaded_source,
                    item[0].is_favorited,
                    bool(item[0].prompt_used),
                    item[0].created_at,
                ),
            )
            variant = copy.deepcopy(authoritative)
            variant.is_favorited = any(candidate.is_favorited for candidate, _ in candidates)
            variant.is_uploaded_source = any(
                candidate.is_uploaded_source for candidate, _ in candidates
            )
            if not variant.prompt_used:
                variant.prompt_used = next(
                    (candidate.prompt_used for candidate, _ in candidates if candidate.prompt_used),
                    None,
                )
            if not variant.upload_type:
                variant.upload_type = next(
                    (candidate.upload_type for candidate, _ in candidates if candidate.upload_type),
                    None,
                )
            if variant.id in used_ids and used_ids[variant.id] != variant.url:
                base_id = f"img_{uuid.uuid5(uuid.NAMESPACE_URL, variant.url).hex[:12]}"
                candidate_id = base_id
                suffix = 2
                while candidate_id in used_ids and used_ids[candidate_id] != variant.url:
                    candidate_id = f"{base_id}_{suffix}"
                    suffix += 1
                variant.id = candidate_id
            used_ids[variant.id] = variant.url
            variants.append(variant)

        prompt = ComicGenPipeline._saved_image_prompt(asset, asset_type)
        for raw_url in direct_urls:
            url = raw_url.strip() if isinstance(raw_url, str) else None
            if not url or any(variant.url == url for variant in variants):
                continue
            variant = ImageVariant(
                id=f"img_{uuid.uuid4().hex[:12]}",
                url=url,
                prompt_used=prompt,
            )
            used_ids[variant.id] = variant.url
            variants.append(variant)

        selected_url = preferred_url or next(iter(selected_urls), None)
        selected = next(
            (variant for variant in variants if variant.url == selected_url),
            variants[0] if variants else None,
        )
        return ImageAsset(
            selected_id=selected.id if selected is not None else None,
            variants=variants,
        )

    @staticmethod
    def _saved_image_prompt(asset: Any, asset_type: str) -> Optional[str]:
        if asset_type == "character":
            return (
                getattr(asset.reference_sheet, "image_prompt", None)
                or asset.full_body_prompt
                or asset.three_view_prompt
                or asset.headshot_prompt
            )
        return getattr(asset, "image_prompt", None)

    @staticmethod
    def _unsupported_type_change_media(
        asset: Any,
        asset_type: str,
        target_asset_type: str,
    ) -> List[str]:
        """Return populated media fields the target schema cannot retain."""

        target_model = {
            "character": Character,
            "scene": Scene,
            "prop": Prop,
        }[target_asset_type]
        unsupported: List[str] = []
        for field in ("video_url", "audio_url", "sfx_url", "bgm_url"):
            if field not in target_model.model_fields and getattr(asset, field, None):
                unsupported.append(field)

        # Character AssetUnit motion references have no representation on a
        # Scene or Prop.  Do not silently flatten or throw them away.
        if asset_type == "character" and target_asset_type != "character":
            for field in ("reference_sheet", "full_body", "three_views", "head_shot"):
                unit = getattr(asset, field, None)
                if unit is not None and (unit.video_variants or []):
                    unsupported.append(f"{field}.video_variants")
        return unsupported

    def _converted_asset(
        self,
        asset: Any,
        asset_type: str,
        target_asset_type: str,
        source_kind: str,
    ) -> Any:
        unsupported_media = self._unsupported_type_change_media(
            asset, asset_type, target_asset_type
        )
        if unsupported_media:
            raise AssetTypeChangeConflictError(
                source_kind,
                asset_type,
                asset.id,
                target_asset_type,
                reason="unsupported_media",
                unsupported_media=unsupported_media,
            )
        image_asset = self._canonical_image_copy(asset, asset_type)
        image_prompt = self._saved_image_prompt(asset, asset_type)
        image_url = self._selected_variant_url(image_asset) or getattr(asset, "image_url", None)
        common = {
            "id": asset.id,
            "name": asset.name,
            "description": asset.description,
            "locked": bool(getattr(asset, "locked", False)),
            "starred": bool(getattr(asset, "starred", False)),
            "status": getattr(asset, "status", GenerationStatus.PENDING),
        }
        if target_asset_type == "character":
            converted = Character(
                **common,
                visual_weight=int(getattr(asset, "visual_weight", 3)),
                reference_sheet=AssetUnit(
                    selected_image_id=image_asset.selected_id,
                    image_variants=copy.deepcopy(image_asset.variants),
                    image_prompt=image_prompt,
                ),
                full_body_prompt=image_prompt,
                image_url=image_url,
                video_assets=copy.deepcopy(getattr(asset, "video_assets", [])),
                video_prompt=self._saved_video_prompt(asset),
            )
        elif target_asset_type == "scene":
            converted = Scene(
                **common,
                visual_weight=int(getattr(asset, "visual_weight", 3)),
                image_prompt=image_prompt,
                image_url=image_url,
                image_asset=image_asset,
                video_assets=copy.deepcopy(getattr(asset, "video_assets", [])),
                video_prompt=self._saved_video_prompt(asset),
            )
        elif target_asset_type == "prop":
            converted = Prop(
                **common,
                image_prompt=image_prompt,
                image_url=image_url,
                image_asset=image_asset,
                video_assets=copy.deepcopy(getattr(asset, "video_assets", [])),
                video_prompt=self._saved_video_prompt(asset),
            )
        else:
            raise ValueError(f"Invalid asset type: {target_asset_type}")
        return converted

    @staticmethod
    def _saved_video_prompt(asset: Any) -> Optional[str]:
        prompt = getattr(asset, "video_prompt", None)
        if prompt:
            return prompt
        for field in ("reference_sheet", "full_body", "three_views", "head_shot"):
            unit = getattr(asset, field, None)
            if unit is not None and unit.video_prompt:
                return unit.video_prompt
        return None

    def update_source_asset(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        *,
        attributes: Optional[Dict[str, Any]] = None,
        prompts: Optional[Dict[str, Any]] = None,
        target_asset_type: Optional[str] = None,
    ) -> Tuple[Any, str]:
        """Atomically update one canonical asset and return (asset, type)."""

        with self._save_lock:
            asset, storage_source, _, _ = self.find_source_asset(
                source_kind, source_id, asset_type, asset_id
            )
            owner = (
                self.scripts[source_id]
                if source_kind == "project"
                else self.series_store[source_id] if source_kind == "series" else self.library_store
            )
            effective_type = target_asset_type or asset_type
            if effective_type not in {"character", "scene", "prop"}:
                raise ValueError(f"Invalid asset type: {effective_type}")
            if effective_type != asset_type:
                references = self._scan_source_asset_references(
                    source_kind, source_id, asset_type, asset_id
                )
                if references:
                    raise AssetTypeChangeConflictError(
                        source_kind,
                        asset_type,
                        asset_id,
                        effective_type,
                        references,
                    )
                if any(
                    item.id == asset_id
                    for item in self._asset_list_for_owner(owner, effective_type)
                ):
                    raise ValueError(f"Asset {asset_id} already exists as {effective_type}")
                candidate = self._converted_asset(asset, asset_type, effective_type, source_kind)
            else:
                # Keep the canonical object identity stable. Detached provider
                # workers retain this exact object while a long generation is
                # running. Replacing it for an ordinary metadata edit leaves
                # the worker mutating a stale copy, which can orphan completed
                # image files and make the activity report no new outputs.
                candidate = asset

            validated_attributes = _validated_asset_attribute_values(
                candidate, effective_type, attributes or {}
            )
            validated_prompts = _validated_asset_prompt_values(effective_type, prompts or {})
            rollback_snapshot = candidate.model_copy(deep=True)
            for key, value in validated_attributes.items():
                setattr(candidate, key, value)
            self._set_asset_prompts(candidate, effective_type, validated_prompts)

            if effective_type != asset_type:
                old_list = self._asset_list_for_owner(owner, asset_type)
                new_list = self._asset_list_for_owner(owner, effective_type)
                old_index = old_list.index(asset)
                old_list.pop(old_index)
                new_list.append(candidate)

            try:
                self._save_after_asset_mutation(storage_source)
            except Exception:
                if effective_type == asset_type:
                    candidate.__dict__.clear()
                    candidate.__dict__.update(copy.deepcopy(rollback_snapshot.__dict__))
                    object.__setattr__(
                        candidate,
                        "__pydantic_fields_set__",
                        set(rollback_snapshot.__pydantic_fields_set__),
                    )
                    object.__setattr__(
                        candidate,
                        "__pydantic_extra__",
                        copy.deepcopy(rollback_snapshot.__pydantic_extra__),
                    )
                    object.__setattr__(
                        candidate,
                        "__pydantic_private__",
                        copy.deepcopy(rollback_snapshot.__pydantic_private__),
                    )
                else:
                    new_list.remove(candidate)
                    old_list.insert(old_index, asset)
                raise
            return candidate, effective_type

    def select_source_asset_variant(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        variant_id: str,
        generation_type: Optional[str] = None,
    ) -> Any:
        asset, storage_source, _, _ = self.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        return self._select_variant_for_asset(
            asset, asset_type, variant_id, generation_type, storage_source
        )

    def delete_source_asset_variant(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        variant_id: str,
    ) -> Any:
        asset, storage_source, _, _ = self.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        return self._delete_variant_for_asset(asset, asset_type, variant_id, storage_source)

    def favorite_source_asset_variant(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        variant_id: str,
        is_favorited: bool,
        generation_type: Optional[str] = None,
    ) -> Any:
        asset, storage_source, _, _ = self.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        return self._set_variant_favorite_for_asset(
            asset,
            asset_type,
            variant_id,
            is_favorited,
            generation_type,
            storage_source,
        )

    def set_source_asset_starred(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        starred: bool,
    ) -> Any:
        """Set the asset-level favorite to an explicit retry-safe value."""

        with self._save_lock:
            asset, storage_source, _, _ = self.find_source_asset(
                source_kind, source_id, asset_type, asset_id
            )
            previous = asset.starred
            asset.starred = bool(starred)
            try:
                self._save_after_asset_mutation(storage_source)
            except Exception:
                asset.starred = previous
                raise
            return asset

    def _save_after_asset_mutation(self, source: str) -> None:
        """Persist after mutating an asset; pick the right save path
        based on which container the asset lives in (episode vs series
        vs global library)."""
        if source == "series":
            self._save_series_data()
        elif source == "global":
            self._save_library_data()
        else:
            self._save_data()

    def toggle_asset_lock(self, script_id: str, asset_id: str, asset_type: str) -> Script:
        """Toggle the locked status of an asset. Works on both
        episode-local and series-shared assets (A2 decision: default
        write to series, since locking a shared character should
        affect all episodes that use it)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Toggle the locked status
        target_asset.locked = not target_asset.locked
        self._save_after_asset_mutation(source)
        return script

    def toggle_asset_starred(self, script_id: str, asset_id: str, asset_type: str) -> Script:
        """Toggle the starred (asset-library shortlist) status of an asset.
        Mirrors toggle_asset_lock — works on both episode-local and
        series-shared assets via _find_asset_with_source."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        target_asset.starred = not target_asset.starred
        self._save_after_asset_mutation(source)
        return script

    def toggle_project_starred(self, script_id: str) -> Script:
        """Toggle the user-starred (featured shortlist) flag on a project.
        Starred projects get the amber-halation 'featured' treatment in the
        gallery. Mirrors toggle_asset_starred but at the Script level. The
        read-modify-write is wrapped in _save_lock so the toggle is atomic."""
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            script.starred = not script.starred
            self._save_data()
            return script

    def update_audio_mix(self, script_id: str, updates: Dict[str, Any]) -> Script:
        """Atomically patch BGM/mix fields outside any merge or export."""

        allowed = {
            "bgm_url",
            "dialogue_volume",
            "bgm_volume",
            "sfx_volume",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"Unsupported audio mix field(s): {', '.join(sorted(unknown))}")

        retired_custom_bgm: Optional[str] = None
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            previous_bgm = script.bgm_url
            previous_mix = dict(script.mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60})
            if "bgm_url" in updates:
                script.bgm_url = updates["bgm_url"] or None
            next_mix = dict(previous_mix)
            for request_field, stored_field in (
                ("dialogue_volume", "dialogue"),
                ("bgm_volume", "bgm"),
                ("sfx_volume", "sfx"),
            ):
                value = updates.get(request_field)
                if request_field in updates and value is not None:
                    next_mix[stored_field] = max(0, min(100, int(value)))
            script.mix_settings = next_mix
            if script.bgm_url != previous_bgm or script.mix_settings != previous_mix:
                custom_prefix = f"{CUSTOM_BGM_ROOT}/{script_id}/"
                if (
                    previous_bgm
                    and previous_bgm.startswith(custom_prefix)
                    and previous_bgm != script.bgm_url
                ):
                    from ...utils.media_security import resolve_workspace_media_path

                    try:
                        candidate = Path(
                            resolve_workspace_media_path(
                                self.output_root,
                                previous_bgm,
                                require_file=False,
                            )
                        ).resolve()
                        expected_parent = (
                            Path(self.output_root) / CUSTOM_BGM_ROOT / script_id
                        ).resolve()
                        if candidate.parent == expected_parent:
                            retired_custom_bgm = str(candidate)
                    except ValueError:
                        retired_custom_bgm = None
                mutation.mark_changed()
        if retired_custom_bgm:
            _delete_or_defer_workspace_media(
                self.output_root,
                [retired_custom_bgm],
            )
        return script

    def custom_bgm_relative_directory(self, script_id: str) -> str:
        """Return the safe project-owned custom-BGM directory."""

        _validate_safe_id(script_id, "script_id")
        if script_id not in self.scripts:
            raise ValueError("Script not found")
        return f"{CUSTOM_BGM_ROOT}/{script_id}"

    def set_custom_bgm(self, script_id: str, bgm_url: str) -> Script:
        """Validate and select one local project-owned custom BGM file."""

        relative_directory = self.custom_bgm_relative_directory(script_id)
        expected_prefix = f"{relative_directory}/"
        normalized = str(bgm_url or "").replace("\\", "/")
        if not normalized.startswith(expected_prefix):
            raise ValueError("Custom background music must belong to this project")

        from ...utils.media_security import resolve_workspace_media_path
        from ...utils.uploads import AUDIO_EXTENSIONS

        if Path(normalized).suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError("Unsupported background music file type")
        resolved = Path(
            resolve_workspace_media_path(
                self.output_root,
                normalized,
                require_file=True,
            )
        ).resolve()
        expected_parent = (Path(self.output_root) / relative_directory).resolve()
        if resolved.parent != expected_parent:
            raise ValueError("Custom background music must belong to this project")
        return self.update_audio_mix(script_id, {"bgm_url": normalized})

    def toggle_frame_lock(self, script_id: str, frame_id: str) -> Script:
        """Toggle the locked status of a frame."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_frame = next((f for f in script.frames if f.id == frame_id), None)
        if not target_frame:
            raise ValueError(f"Frame {frame_id} not found")

        # Toggle the locked status
        target_frame.locked = not target_frame.locked
        self._save_data()
        return script

    def update_asset_image(
        self, script_id: str, asset_id: str, asset_type: str, image_url: str
    ) -> Script:
        """Updates the image URL of an asset manually. Per A2 decision,
        series-shared assets are updated in place (shared semantics);
        episode-local assets are updated locally."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Every owner uses the same canonical primary-image representation.
        # Updating only a legacy alias leaves Home, Series, and Episode views
        # disagreeing about which generated image is active.
        self._set_library_primary_image(asset_type, target_asset, image_url)

        self._save_after_asset_mutation(source)
        return script

    def update_asset_description(
        self, script_id: str, asset_id: str, asset_type: str, description: str
    ) -> Script:
        """Updates the description of an asset."""
        return self.update_asset_attributes(
            script_id, asset_id, asset_type, {"description": description}
        )

    def update_asset_attributes(
        self, script_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]
    ) -> Script:
        """Updates allowlisted presentation attributes of an asset. Routes the write
        to either the episode-local or the parent series' shared copy
        depending on which container owns the asset (A2 decision)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        validated = _validated_asset_attribute_values(target_asset, asset_type, attributes)
        for key, value in validated.items():
            setattr(target_asset, key, value)

        self._save_after_asset_mutation(source)
        return script

    def add_uploaded_asset_variant(
        self,
        script_id: str,
        asset_type: str,
        asset_id: str,
        upload_type: str,
        image_url: str,
        description: Optional[str] = None,
    ) -> Script:
        """
        Adds an uploaded image as a new variant to an asset.
        The uploaded image is marked with is_uploaded_source=True.

        Args:
            script_id: The project ID
            asset_type: "character", "scene", or "prop"
            asset_id: The asset ID
            upload_type: "full_body", "head_shot", "three_views", or "image"
            image_url: URL of the uploaded image (OSS Object Key)
            description: Optional modified description for reverse generation
        """
        from .models import AssetUnit, ImageVariant

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if target_asset is None or source is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Create new variant with upload source flag
        new_variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used=description or target_asset.description,
            is_uploaded_source=True,
            upload_type=upload_type,
        )

        # Update description if provided
        if description:
            target_asset.description = description

        # Add variant to the appropriate asset unit
        if asset_type == "character":
            # Map upload_type to the correct asset unit
            if upload_type == "full_body":
                target_unit = target_asset.full_body
            elif upload_type == "head_shot":
                target_unit = target_asset.head_shot
            elif upload_type == "three_views":
                target_unit = target_asset.three_views
            else:
                raise ValueError(f"Invalid upload_type for character: {upload_type}")

            # Ensure AssetUnit exists
            if target_unit is None:
                target_unit = AssetUnit()
                if upload_type == "full_body":
                    target_asset.full_body = target_unit
                elif upload_type == "head_shot":
                    target_asset.head_shot = target_unit
                elif upload_type == "three_views":
                    target_asset.three_views = target_unit

            # Add variant and select it
            target_unit.image_variants.append(new_variant)
            target_unit.selected_image_id = new_variant.id
            target_unit.image_updated_at = time.time()

            # === ALSO UPDATE LEGACY FIELDS for frontend compatibility ===
            # Create variant for legacy ImageAsset structure
            legacy_variant = ImageVariant(
                id=new_variant.id,
                url=image_url,
                prompt_used=description or target_asset.description,
                is_uploaded_source=True,
                upload_type=upload_type,
            )

            if upload_type == "full_body":
                # Ensure full_body_asset exists
                if target_asset.full_body_asset is None:
                    from .models import ImageAsset

                    target_asset.full_body_asset = ImageAsset()
                target_asset.full_body_asset.variants.append(legacy_variant)
                target_asset.full_body_asset.selected_id = new_variant.id
                target_asset.full_body_image_url = image_url
            elif upload_type == "head_shot":
                # Ensure headshot_asset exists
                if target_asset.headshot_asset is None:
                    from .models import ImageAsset

                    target_asset.headshot_asset = ImageAsset()
                target_asset.headshot_asset.variants.append(legacy_variant)
                target_asset.headshot_asset.selected_id = new_variant.id
                target_asset.headshot_image_url = image_url
            elif upload_type == "three_views":
                # Ensure three_view_asset exists
                if target_asset.three_view_asset is None:
                    from .models import ImageAsset

                    target_asset.three_view_asset = ImageAsset()
                target_asset.three_view_asset.variants.append(legacy_variant)
                target_asset.three_view_asset.selected_id = new_variant.id
                target_asset.three_view_image_url = image_url

            logger.info(
                f"Added uploaded variant {new_variant.id} to character {asset_id} {upload_type}"
            )

        elif asset_type in ["scene", "prop"]:
            # Scene and Prop have a single 'image' asset unit
            if not hasattr(target_asset, "image") or target_asset.image is None:
                target_asset.image = AssetUnit()

            target_asset.image.image_variants.append(new_variant)
            target_asset.image.selected_image_id = new_variant.id
            target_asset.image.image_updated_at = time.time()

            # Also update legacy image_url field
            target_asset.image_url = image_url

            logger.info(f"Added uploaded variant {new_variant.id} to {asset_type} {asset_id}")

        # The canonical character image is the master/full-body source used by
        # downstream motion generation. Derived head-shot and three-view
        # uploads stay in their own containers and must not replace it.
        if asset_type != "character" or upload_type == "full_body":
            self._set_library_primary_image(
                asset_type,
                target_asset,
                image_url,
                source_variant=new_variant,
            )

        self._save_after_asset_mutation(source)
        return script

    def update_project_style(
        self, script_id: str, style_preset: str, style_prompt: Optional[str] = None
    ) -> Script:
        """Updates the global style settings for a project."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script.style_preset = style_preset
        script.style_prompt = style_prompt
        script.updated_at = time.time()
        self._save_data()
        return script

    def save_art_direction(
        self,
        script_id: str,
        selected_style_id: str,
        style_config: Dict[str, Any],
        custom_styles: List[Dict[str, Any]] = None,
        ai_recommendations: List[Dict[str, Any]] = None,
    ) -> Script:
        """Saves the Art Direction configuration."""
        from .models import ArtDirection

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Create Art Direction object
        art_direction = ArtDirection(
            selected_style_id=selected_style_id,
            style_config=style_config,
            custom_styles=custom_styles or [],
            ai_recommendations=ai_recommendations or [],
        )

        script.art_direction = art_direction
        script.updated_at = time.time()
        self._save_data()
        return script

    # === STORYBOARD DRAMATIZATION v2 ===

    def _storyboard_analysis_context(self, script: Script) -> Dict[str, Any]:
        """Resolve provider inputs plus a stale-result commit fingerprint."""

        resolved = self.resolve_episode_assets(script)
        entities_json = {
            "characters": [
                {"id": item.id, "name": item.name, "description": item.description}
                for item in resolved["characters"]
            ],
            "scenes": [
                {"id": item.id, "name": item.name, "description": item.description}
                for item in resolved["scenes"]
            ],
            "props": [
                {"id": item.id, "name": item.name, "description": item.description}
                for item in resolved["props"]
            ],
        }
        series = self.get_series(script.series_id) if getattr(script, "series_id", None) else None
        extraction_prompt = self.get_effective_prompt(
            "storyboard_extraction",
            script,
            series,
        )
        chat_model = self._effective_chat_model(script)
        fingerprint = json.dumps(
            {
                "original_text": script.original_text,
                "frames": [frame.model_dump(mode="json") for frame in script.frames],
                "entities": entities_json,
                "extraction_prompt": extraction_prompt,
                "chat_model": chat_model,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return {
            "resolved": resolved,
            "entities": entities_json,
            "extraction_prompt": extraction_prompt,
            "chat_model": chat_model,
            "fingerprint": fingerprint,
        }

    def _frame_refinement_fingerprint(
        self,
        script: Script,
        frame_id: str,
    ) -> Optional[str]:
        """Capture the frame and every adjacent/asset input used by refinement."""

        frame_index = next(
            (index for index, item in enumerate(script.frames) if item.id == frame_id),
            None,
        )
        if frame_index is None:
            return None
        frame = script.frames[frame_index]
        resolved = self.resolve_episode_assets(script)
        previous_frame = script.frames[frame_index - 1] if frame_index > 0 else None
        next_frame = (
            script.frames[frame_index + 1] if frame_index < len(script.frames) - 1 else None
        )
        return json.dumps(
            {
                "frame_order": [item.id for item in script.frames],
                "frame": frame.model_dump(mode="json"),
                "previous_frame": (
                    previous_frame.model_dump(mode="json") if previous_frame else None
                ),
                "next_frame": (next_frame.model_dump(mode="json") if next_frame else None),
                "characters": [
                    item.model_dump(mode="json")
                    for item in resolved["characters"]
                    if item.id in frame.character_ids
                ],
                "scenes": [
                    item.model_dump(mode="json")
                    for item in resolved["scenes"]
                    if item.id == frame.scene_id
                ],
                "chat_model": self._effective_chat_model(script),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    def analyze_text_to_frames(self, script_id: str, text: str) -> Script:
        """
        Analyzes script text and generates storyboard frames using LLM.
        Replaces existing frames with newly generated ones.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Analyzing text to frames for project {script_id}")

        # Resolve assets (merge Series + Episode if applicable) and preserve
        # the exact provider-input revision for the eventual commit.
        analysis_context = self._storyboard_analysis_context(script)
        source_fingerprint = analysis_context["fingerprint"]
        resolved = analysis_context["resolved"]
        all_characters = resolved["characters"]
        all_scenes = resolved["scenes"]
        all_props = resolved["props"]

        # Call LLM to analyze text (may raise RuntimeError on parse failure)
        raw_frames = self.script_processor.analyze_to_storyboard(
            text,
            analysis_context["entities"],
            custom_extraction_prompt=analysis_context["extraction_prompt"],
            model=analysis_context["chat_model"],
        )

        if not raw_frames:
            raise RuntimeError("AI 分镜分析未返回任何帧数据，请重试。")

        # Convert raw frame dicts to StoryboardFrame objects
        new_frames = []
        for idx, frame_data in enumerate(raw_frames):
            # Resolve scene ID by name
            scene_ref_name = frame_data.get("scene_ref_name", "")
            scene_id = None
            for scene in all_scenes:
                if scene.name == scene_ref_name or scene_ref_name in scene.name:
                    scene_id = scene.id
                    break
            if not scene_id and all_scenes:
                scene_id = all_scenes[0].id  # Fallback to first scene
            elif not scene_id:
                scene_id = str(uuid.uuid4())  # Generate a placeholder ID

            # Resolve character IDs by names (case-insensitive, bidirectional contains)
            char_ref_names = frame_data.get("character_ref_names", [])
            character_ids = []
            for char_name in char_ref_names:
                cn = char_name.strip().lower()
                for char in all_characters:
                    cname = char.name.strip().lower()
                    if cname == cn or cn in cname or cname in cn:
                        character_ids.append(char.id)
                        break

            # Resolve prop IDs by names (case-insensitive, bidirectional contains)
            prop_ref_names = frame_data.get("prop_ref_names", [])
            prop_ids = []
            for prop_name in prop_ref_names:
                pn = prop_name.strip().lower()
                for prop in all_props:
                    pname = prop.name.strip().lower()
                    if pname == pn or pn in pname or pname in pn:
                        prop_ids.append(prop.id)
                        break

            frame = StoryboardFrame(
                id=str(uuid.uuid4()),
                scene_id=scene_id,
                character_ids=character_ids,
                prop_ids=prop_ids,
                action_description=frame_data.get(
                    "action_summary", frame_data.get("action_description", "")
                ),
                visual_atmosphere=frame_data.get("visual_atmosphere"),
                shot_size=frame_data.get("shot_size"),
                camera_angle=frame_data.get("camera_angle", "平视"),
                camera_movement=frame_data.get("camera_movement"),
                dialogue=frame_data.get("dialogue"),
                speaker=frame_data.get("speaker"),
                duration=frame_data.get("duration"),
                status=GenerationStatus.PENDING,
            )
            new_frames.append(frame)

        logger.info(f"Generated {len(new_frames)} frames from text analysis")
        # The billable LLM call above intentionally runs without the assembly
        # lock. Only the short replacement commit is serialized with merge and
        # export.
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            if self._storyboard_analysis_context(script)["fingerprint"] != source_fingerprint:
                raise AssemblyMutationConflictError(
                    "The project changed while storyboard analysis was running; "
                    "review the newer edits and run analysis again"
                )
            script.frames = new_frames
            script.updated_at = time.time()
            mutation.mark_changed()
            return script

    def refine_frame(self, script_id: str, frame_id: str) -> StoryboardFrame:
        """Phase 2: Refine a single coarse frame into a rich frame."""
        from .models import (
            AudioNote,
            Blocking,
            CameraMovementData,
            DialogueStructured,
            LightingData,
            StageSubject,
        )
        from .prompt_assembly import assemble_prompt, sync_dialogue_metadata

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        source_frame = next((f for f in script.frames if f.id == frame_id), None)
        if not source_frame:
            raise ValueError(f"Frame {frame_id} not found")

        source_fingerprint = self._frame_refinement_fingerprint(script, frame_id)
        if source_fingerprint is None:
            raise ValueError(f"Frame {frame_id} not found")
        frame_idx = script.frames.index(source_frame)
        # Keep provider work detached from persisted state. The completed frame
        # is swapped in under the assembly operation lock below.
        frame = source_frame.model_copy(deep=True)
        resolved = self.resolve_episode_assets(script)
        all_characters = resolved["characters"]
        all_scenes = resolved["scenes"]

        # Build coarse frame dict for LLM
        coarse = {
            "action_summary": frame.action_description,
            "shot_size": frame.shot_size,
            "camera_angle": frame.camera_angle,
            "camera_movement": frame.camera_movement,
            "dialogue": frame.dialogue,
            "speaker": frame.speaker,
            "duration": frame.duration,
            "character_names": [c.name for c in all_characters if c.id in frame.character_ids],
            "scene_name": next((s.name for s in all_scenes if s.id == frame.scene_id), None),
        }

        # Character/scene assets
        char_assets = [
            {"name": c.name, "description": c.description, "clothing": c.clothing or ""}
            for c in all_characters
            if c.id in frame.character_ids
        ]
        scene_assets = [
            {"name": s.name, "description": s.description}
            for s in all_scenes
            if s.id == frame.scene_id
        ]

        # Adjacent frame context
        prev_ctx = None
        if frame_idx > 0:
            pf = script.frames[frame_idx - 1]
            prev_ctx = f"Action: {pf.action_description}. Shot: {pf.shot_size}, {pf.camera_angle}."
        next_ctx = None
        if frame_idx < len(script.frames) - 1:
            nf = script.frames[frame_idx + 1]
            next_ctx = f"Action: {nf.action_description}. Shot: {nf.shot_size}, {nf.camera_angle}."

        refinement_model = self._effective_chat_model(script)
        result = self.script_processor.refine_frame_to_rich(
            coarse,
            char_assets,
            scene_assets,
            prev_ctx,
            next_ctx,
            refinement_model,
        )
        if not isinstance(result, dict) or not result:
            raise RuntimeError("Rich-frame refinement returned no usable result")

        # Map result onto frame fields
        if result.get("visual_description"):
            from .prompt_assembly import inject_reference_tags

            frame.visual_description = inject_reference_tags(
                result["visual_description"], frame, all_characters, all_scenes
            )
        if result.get("shot_size"):
            frame.shot_size = result["shot_size"]
        if result.get("camera_angle"):
            frame.camera_angle = result["camera_angle"]
        if result.get("duration"):
            frame.duration = result["duration"]
        if result.get("transition_hint"):
            frame.transition_hint = result["transition_hint"]

        # Camera movement structured
        cm = result.get("camera_movement")
        if cm and isinstance(cm, dict) and cm.get("primary"):
            frame.camera_movement_structured = CameraMovementData(
                primary=cm["primary"],
                secondary=cm.get("secondary"),
                speed=cm.get("speed", "normal"),
                description=cm.get("description"),
            )

        # Blocking
        blk = result.get("blocking")
        if blk and isinstance(blk, dict) and blk.get("description"):
            stage_list = None
            if blk.get("stage") and isinstance(blk["stage"], list):
                stage_list = [
                    StageSubject(
                        ref=s.get("ref", ""),
                        zone=s.get("zone", "center"),
                        depth=s.get("depth", "mid"),
                        height=s.get("height"),
                        facing=s.get("facing"),
                        posture=s.get("posture"),
                    )
                    for s in blk["stage"]
                    if isinstance(s, dict)
                ]
            frame.blocking = Blocking(
                description=blk["description"],
                stage=stage_list,
                camera_relation=blk.get("camera_relation"),
            )

        # Dialogue structured
        ds = result.get("dialogue_structured")
        if ds and isinstance(ds, dict) and ds.get("line"):
            frame.dialogue_structured = DialogueStructured(
                speaker=ds.get("speaker", frame.speaker or ""),
                line=ds["line"],
                emotion=ds.get("emotion"),
                delivery=ds.get("delivery"),
            )

        # Audio note
        an = result.get("audio_note")
        if an and isinstance(an, dict) and (an.get("sfx") or an.get("ambience")):
            frame.audio_note = AudioNote(
                sfx=an.get("sfx"),
                ambience=an.get("ambience"),
                bgm_note=an.get("bgm_note"),
            )

        # Lighting
        lt = result.get("lighting")
        if lt and isinstance(lt, dict) and (lt.get("description") or lt.get("direction")):
            frame.lighting = LightingData(
                direction=lt.get("direction"),
                quality=lt.get("quality"),
                color_temp=lt.get("color_temp"),
                description=lt.get("description"),
            )

        # Sync dialogue performance instructions and compute the assembled prompt
        sync_dialogue_metadata(frame)
        frame.assembled_prompt = assemble_prompt(frame, all_characters)
        frame.updated_at = time.time()

        with self._assembly_mutation(script_id) as mutation:
            live_script = mutation.script
            live_index = next(
                (index for index, item in enumerate(live_script.frames) if item.id == frame_id),
                None,
            )
            if live_index is None:
                raise ValueError(f"Frame {frame_id} not found")
            if self._frame_refinement_fingerprint(live_script, frame_id) != source_fingerprint:
                raise AssemblyMutationConflictError(
                    "The frame or its refinement context changed while AI refinement "
                    "was running; keep the newer edits and run refinement again"
                )
            live_script.frames[live_index] = frame
            mutation.mark_changed()
            return frame

    def refine_batch_generator(self, script_id: str):
        """Phase 2: Generator that yields SSE events while refining all frames."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        total = len(script.frames)
        success = 0
        failed = 0

        for idx, frame in enumerate(script.frames):
            yield (
                "frame_refine_start",
                {
                    "frame_id": frame.id,
                    "frame_index": idx,
                    "total": total,
                    "label": (
                        frame.action_description[:40]
                        if frame.action_description
                        else f"Frame {idx+1}"
                    ),
                },
            )
            try:
                self.refine_frame(script_id, frame.id)
                success += 1
                yield (
                    "frame_refine_complete",
                    {
                        "frame_id": frame.id,
                        "frame_index": idx,
                        "total": total,
                    },
                )
            except Exception as exc:
                failed += 1
                logger.error(f"[refine_batch] frame={frame.id} error={exc}")
                error_event = {
                    "frame_id": frame.id,
                    "frame_index": idx,
                    "error": getattr(exc, "message", str(exc)),
                }
                if getattr(exc, "reason", None):
                    error_event["reason"] = exc.reason
                yield ("frame_refine_error", error_event)

        yield ("batch_complete", {"total": total, "success": success, "failed": failed})

    def refine_frame_prompt(
        self,
        script_id: str,
        frame_id: str,
        raw_prompt: str,
        assets: List[Dict[str, Any]],
        feedback: str = "",
    ) -> Dict[str, Any]:
        """
        Refines a raw prompt into bilingual (CN/EN) prompts using LLM.
        Also updates the frame with the refined prompts.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((item for item in script.frames if item.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")

        logger.debug(f"Refining prompt for frame {frame_id}")

        # Read custom prompt config with 3-level fallback (Episode → Series → default)
        series = self.series_store.get(script.series_id) if script.series_id else None
        custom_prompt = self.get_effective_prompt("storyboard_polish", script, series)
        # If it's the system default, pass empty so the LLM method uses its built-in default
        from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT

        if custom_prompt == DEFAULT_STORYBOARD_POLISH_PROMPT:
            custom_prompt = ""

        # Call LLM to refine prompt
        result = self.script_processor.polish_storyboard_prompt(
            raw_prompt,
            assets,
            feedback,
            custom_prompt,
            polish_model=self._effective_polish_model(script),
        )

        frame.image_prompt_cn = result.get("prompt_cn")
        frame.image_prompt_en = result.get("prompt_en")
        frame.image_prompt = result.get("prompt_en")  # Also update legacy field
        frame.updated_at = time.time()
        self._save_data()

        return {
            "prompt_cn": result.get("prompt_cn"),
            "prompt_en": result.get("prompt_en"),
            "frame_updated": True,
        }

    def generate_storyboard(self, script_id: str) -> Script:
        """Step 3: Generate storyboard images (Initial/Batch)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        resolved = self.resolve_episode_assets(script)
        script = self.storyboard_generator.generate_storyboard(
            script,
            characters=resolved["characters"],
            scenes=resolved["scenes"],
        )
        self._save_data()
        return script

    def update_frame(self, script_id: str, frame_id: str, **kwargs) -> Script:
        """Update frame data (prompt, scene_id, character_ids, etc.)."""
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError(f"Frame {frame_id} not found")
            previous_frame = frame.model_dump()

            # Update only provided fields
            if kwargs.get("image_prompt") is not None:
                frame.image_prompt = kwargs["image_prompt"]
            if kwargs.get("action_description") is not None:
                frame.action_description = kwargs["action_description"]
            if kwargs.get("dialogue") is not None:
                frame.dialogue = kwargs["dialogue"]
            if kwargs.get("camera_angle") is not None:
                frame.camera_angle = kwargs["camera_angle"]
            if kwargs.get("scene_id") is not None:
                frame.scene_id = kwargs["scene_id"]
            if kwargs.get("character_ids") is not None:
                frame.character_ids = kwargs["character_ids"]
            if kwargs.get("prop_ids") is not None:
                frame.prop_ids = kwargs["prop_ids"]
            if kwargs.get("duration") is not None:
                frame.duration = kwargs["duration"]
            if kwargs.get("shot_size") is not None:
                frame.shot_size = kwargs["shot_size"]
            if kwargs.get("camera_movement") is not None:
                movement = kwargs["camera_movement"]
                frame.camera_movement = movement
                if frame.camera_movement_structured:
                    frame.camera_movement_structured.primary = movement
                    # A preset change invalidates a free-form description of the
                    # previous movement. Prompt assembly will rebuild a readable
                    # movement phrase from the canonical type and retained speed.
                    frame.camera_movement_structured.description = None
                else:
                    from .models import CameraMovementData

                    frame.camera_movement_structured = CameraMovementData(
                        primary=movement,
                        speed="normal",
                    )
            if kwargs.get("camera_movement_description") is not None:
                if frame.camera_movement_structured:
                    frame.camera_movement_structured.description = kwargs[
                        "camera_movement_description"
                    ]
                    frame.camera_movement_structured.primary = kwargs["camera_movement_description"]
                else:
                    from .models import CameraMovementData

                    frame.camera_movement_structured = CameraMovementData(
                        primary=kwargs["camera_movement_description"],
                        speed="normal",
                        description=kwargs["camera_movement_description"],
                    )
            if kwargs.get("transition_hint") is not None:
                frame.transition_hint = kwargs["transition_hint"]

            if frame.model_dump() != previous_frame:
                mutation.mark_changed()
            return script

    def add_frame(
        self,
        script_id: str,
        scene_id: str = None,
        action_description: str = "",
        camera_angle: str = "medium_shot",
        insert_at: int = None,
        *,
        character_ids: Optional[List[str]] = None,
        prop_ids: Optional[List[str]] = None,
    ) -> Script:
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            new_frame = StoryboardFrame(
                id=f"frame_{uuid.uuid4().hex[:8]}",
                scene_id=scene_id or (script.scenes[0].id if script.scenes else ""),
                character_ids=list(character_ids or []),
                prop_ids=list(prop_ids or []),
                action_description=action_description,
                camera_angle=camera_angle,
            )

            if insert_at is not None and 0 <= insert_at <= len(script.frames):
                script.frames.insert(insert_at, new_frame)
            else:
                script.frames.append(new_frame)
            mutation.mark_changed()
            return script

    def copy_frame(self, script_id: str, frame_id: str, insert_at: int = None) -> Script:
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            original_frame = next((f for f in script.frames if f.id == frame_id), None)
            if not original_frame:
                raise ValueError(f"Frame {frame_id} not found")

            # Create a deep copy with new ID
            new_frame = original_frame.model_copy(deep=True)
            new_frame.id = f"frame_{uuid.uuid4().hex[:8]}"
            new_frame.updated_at = time.time()
            new_frame.locked = False

            if insert_at is not None and 0 <= insert_at <= len(script.frames):
                script.frames.insert(insert_at, new_frame)
            else:
                # Insert after the original frame by default
                try:
                    original_index = script.frames.index(original_frame)
                    script.frames.insert(original_index + 1, new_frame)
                except ValueError:
                    script.frames.append(new_frame)
            mutation.mark_changed()
            return script

    def delete_frame(self, script_id: str, frame_id: str) -> Script:
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            if not any(frame.id == frame_id for frame in script.frames):
                raise ValueError("Frame not found")
            script.frames = [f for f in script.frames if f.id != frame_id]
            self._detach_video_tasks(script, lambda task: task.frame_id == frame_id)
            mutation.mark_changed()
            return script

    def reorder_frames(self, script_id: str, frame_ids: List[str]) -> Script:
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            previous_ids = [frame.id for frame in script.frames]
            frame_map = {f.id: f for f in script.frames}
            new_frames = []
            for fid in frame_ids:
                if fid in frame_map:
                    new_frames.append(frame_map[fid])

            script.frames = new_frames
            if [frame.id for frame in new_frames] != previous_ids:
                mutation.mark_changed()
            return script

    def generate_motion_ref(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,  # 'full_body' | 'head_shot' for characters; 'scene' | 'prop' for scenes and props
        prompt: Optional[str] = None,
        audio_url: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
        model_id: Optional[str] = None,
    ) -> Script:
        """Generate Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop).

        Args:
            script_id: ID of the project/script
            asset_id: ID of the asset (character, scene, or prop)
            asset_type: 'full_body' | 'head_shot' for characters; 'scene' or 'prop' for scenes and props
            prompt: Custom prompt for motion generation
            audio_url: URL of driving audio for lip-sync
            duration: Video duration in seconds (5 or 10)
            batch_size: Number of videos to generate
        """
        from .models import AssetUnit, VideoTask, VideoVariant

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        effective_settings = self._effective_model_settings(script)

        target_asset, source = self._resolve_motion_reference_asset(script, asset_id, asset_type)
        source_image_url = self._motion_reference_source_image_url(target_asset, asset_type)

        # Get the appropriate AssetUnit or image URL based on the asset type
        asset_unit = None  # For characters with AssetUnit
        generated_videos = []  # Store generated videos
        generation_errors: List[Exception] = []

        if asset_type in ["full_body", "head_shot"]:
            # Motion variants remain in the legacy unit consumed by the
            # current workbench, but the unified reference sheet is the
            # canonical static source for full-body motion generation.
            asset_unit = getattr(target_asset, asset_type, None)
            if asset_unit is None:
                asset_unit = AssetUnit()
                setattr(target_asset, asset_type, asset_unit)

            # Default prompt for character
            if not prompt:
                if audio_url:
                    prompt = f"{asset_type.replace('_', ' ').title()} character reference video. {FICTIONAL_CHARACTER_PROMPT_NOTICE} {target_asset.description}. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions. Stable camera, high quality, 4k."
                else:
                    prompt = f"{asset_type.replace('_', ' ').title()} character reference video. {FICTIONAL_CHARACTER_PROMPT_NOTICE} {target_asset.description}. Looking around, breathing, slight movement, subtle gestures. Stable camera, high quality, 4k."
        else:
            # Default prompt for scene and prop
            if not prompt:
                if asset_type == "scene":
                    if audio_url:
                        prompt = f"Cinematic scene video reference of {target_asset.name}. {target_asset.description}. Ambient motion, lighting changes, natural elements moving, birds, clouds. Soundscape matching the audio. High quality, 4k."
                    else:
                        prompt = f"Cinematic scene video reference of {target_asset.name}. {target_asset.description}. Ambient motion, lighting changes, natural elements moving, birds, clouds. Slow pan across the scene. High quality, 4k."
                else:  # prop
                    if audio_url:
                        prompt = f"Cinematic prop video reference of {target_asset.name}. {target_asset.description}. Rotating object, detailed textures visible, ambient motion, subtle movements matching audio. High quality, 4k."
                    else:
                        prompt = f"Cinematic prop video reference of {target_asset.name}. {target_asset.description}. Rotating object, detailed textures visible, ambient motion, subtle movements. High quality, 4k."

        # Check if source image exists
        if not source_image_url:
            raise ValueError(
                f"No source image available for {asset_type}. Please generate a static image first."
            )

        # Generate videos based on the asset type
        for i in range(batch_size):
            try:
                # Call video generator (I2V)
                video_result = self.video_generator.generate_i2v(
                    image_url=source_image_url,
                    prompt=prompt,
                    duration=duration,
                    audio_url=audio_url,
                    model_id=model_id or effective_settings.video_model,
                )

                if video_result and video_result.get("video_url"):
                    if asset_type in ["full_body", "head_shot"]:
                        # For characters, create VideoVariant in AssetUnit
                        video_variant = VideoVariant(
                            id=f"video_{uuid.uuid4().hex[:8]}",
                            url=video_result["video_url"],
                            prompt_used=prompt,
                            audio_url=audio_url,
                            source_image_id=None,  # Don't set this to avoid complications
                        )
                        asset_unit.video_variants.append(video_variant)

                        # Auto-select the first generated video
                        if not asset_unit.selected_video_id:
                            asset_unit.selected_video_id = video_variant.id

                        generated_videos.append(video_variant)
                        logger.info(f"Generated motion ref video: {video_variant.id}")
                    else:
                        # For scenes and props, create VideoTask and add to asset's video_assets
                        video_task = VideoTask(
                            id=f"video_{uuid.uuid4().hex[:8]}",
                            project_id=script_id,
                            asset_id=asset_id,
                            image_url=source_image_url,
                            prompt=prompt,
                            status="completed",  # Since generation is done in this step
                            video_url=video_result["video_url"],
                            duration=duration,
                            created_at=time.time(),
                            generate_audio=bool(audio_url),
                            model=model_id or effective_settings.video_model,
                            generation_mode="i2v",  # Image to video (motion reference)
                        )

                        # Add to the asset's video_assets
                        target_asset.video_assets.append(video_task)
                        generated_videos.append(video_task)
                        logger.info(f"Generated motion ref video for {asset_type}: {video_task.id}")
            except NewAPIProviderError:
                # Repeating the same rejected image can create duplicate
                # provider charges and cannot change a deterministic safety
                # decision. Preserve the structured rejection for the job UI.
                raise
            except Exception as e:
                generation_errors.append(e)
                logger.error(f"Failed to generate motion ref video for {asset_type}: {e}")

        # For character assets, update the AssetUnit
        if asset_type in ["full_body", "head_shot"]:
            asset_unit.video_prompt = prompt
            asset_unit.video_updated_at = time.time()
        # For scene and prop assets, the video tasks are already added in the generation loop above

        if batch_size > 0 and not generated_videos:
            if generation_errors:
                raise generation_errors[0]
            raise RuntimeError(f"Failed to generate any motion reference videos for {asset_type}")

        self._save_after_asset_mutation(source)
        return script

    @staticmethod
    def _canonical_motion_type(asset_type: str, motion_type: Optional[str]) -> str:
        if asset_type == "character":
            normalized = (motion_type or "full_body").strip().lower()
            if normalized in {"reference_sheet", "full_body"}:
                return "full_body"
            if normalized in {"headshot", "head_shot"}:
                return "head_shot"
            raise ValueError("Character motion_type must be full_body or head_shot")
        if motion_type and motion_type not in {asset_type, "video"}:
            raise ValueError(f"{asset_type} motion_type must be {asset_type}")
        return asset_type

    def _source_owner_model_settings(self, source_kind: str, source_id: str) -> ModelSettings:
        if source_kind == "project":
            return self._effective_model_settings(self.scripts[source_id])
        if source_kind == "series":
            return self._effective_series_model_settings(self.series_store[source_id])
        return self._global_model_settings()

    def generate_source_asset_motion_ref(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        *,
        motion_type: Optional[str] = None,
        prompt: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
        model_id: Optional[str] = None,
        audio_url: Optional[str] = None,
    ) -> Any:
        """Generate motion media against one exact canonical owner.

        Unlike the legacy project route this method never resolves through an
        Episode > Series > Global fallback, so same-id assets cannot be
        animated through the wrong owner.
        """

        from .models import VideoVariant

        target_asset, storage_source, _, _ = self.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        canonical_motion_type = self._canonical_motion_type(asset_type, motion_type)
        source_image_url = self._motion_reference_source_image_url(
            target_asset, canonical_motion_type
        )
        if not source_image_url:
            raise ValueError(
                f"No source image available for {canonical_motion_type}. "
                "Please generate or select a static image first."
            )

        settings = self._source_owner_model_settings(source_kind, source_id)
        selected_model = get_model_spec(model_id or settings.video_model, VIDEO).model_id
        resolve_model_api_key(selected_model, VIDEO)

        if not prompt:
            if asset_type == "character":
                label = canonical_motion_type.replace("_", " ").title()
                prompt = (
                    f"{label} character reference video. "
                    f"{FICTIONAL_CHARACTER_PROMPT_NOTICE} "
                    f"{target_asset.description}. Looking around, breathing, "
                    "slight movement, subtle gestures. Stable camera, high "
                    "quality, 4k."
                )
            elif asset_type == "scene":
                prompt = (
                    f"Cinematic scene video reference of {target_asset.name}. "
                    f"{target_asset.description}. Ambient motion, natural "
                    "lighting changes, slow camera movement, high quality, 4k."
                )
            else:
                prompt = (
                    f"Cinematic prop video reference of {target_asset.name}. "
                    f"{target_asset.description}. Detailed textures, subtle "
                    "rotation and movement, high quality, 4k."
                )

        generated: List[Any] = []
        errors: List[Exception] = []
        for _ in range(batch_size):
            try:
                result = self.video_generator.generate_i2v(
                    image_url=source_image_url,
                    prompt=prompt,
                    duration=duration,
                    audio_url=audio_url,
                    model_id=selected_model,
                )
                if not result or not result.get("video_url"):
                    raise RuntimeError("Motion provider completed without a video output")
                if asset_type == "character":
                    unit = getattr(target_asset, canonical_motion_type, None)
                    if unit is None:
                        unit = AssetUnit()
                        setattr(target_asset, canonical_motion_type, unit)
                    variant = VideoVariant(
                        id=f"video_{uuid.uuid4().hex[:8]}",
                        url=result["video_url"],
                        prompt_used=prompt,
                        audio_url=audio_url,
                        source_image_id=None,
                    )
                    unit.video_variants.append(variant)
                    if not unit.selected_video_id:
                        unit.selected_video_id = variant.id
                    unit.video_prompt = prompt
                    unit.video_updated_at = time.time()
                    generated.append(variant)
                else:
                    task = VideoTask(
                        id=f"video_{uuid.uuid4().hex[:8]}",
                        project_id=f"{source_kind}:{source_id}",
                        asset_id=asset_id,
                        image_url=source_image_url,
                        prompt=prompt,
                        status="completed",
                        video_url=result["video_url"],
                        duration=duration,
                        created_at=time.time(),
                        model=selected_model,
                        generation_mode="i2v",
                    )
                    target_asset.video_assets.append(task)
                    if not target_asset.selected_video_id:
                        target_asset.selected_video_id = task.id
                    target_asset.video_prompt = prompt
                    generated.append(task)
            except NewAPIProviderError:
                raise
            except Exception as exc:
                errors.append(exc)
                logger.exception(
                    "Exact-owner motion generation failed for %s:%s:%s:%s",
                    source_kind,
                    source_id,
                    asset_type,
                    asset_id,
                )

        if not generated:
            if errors:
                raise errors[0]
            raise RuntimeError("Failed to generate any motion reference videos")
        self._save_after_asset_mutation(storage_source)
        return target_asset

    def _source_motion_container(
        self,
        asset: Any,
        asset_type: str,
        motion_type: Optional[str],
    ) -> Tuple[List[Any], str, Any]:
        canonical_motion_type = self._canonical_motion_type(asset_type, motion_type)
        if asset_type == "character":
            unit = getattr(asset, canonical_motion_type, None)
            if unit is None:
                unit = AssetUnit()
                setattr(asset, canonical_motion_type, unit)
            return unit.video_variants, "selected_video_id", unit
        return asset.video_assets, "selected_video_id", asset

    def select_source_asset_motion_variant(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        variant_id: str,
        motion_type: Optional[str] = None,
    ) -> Any:
        with self._save_lock:
            asset, storage_source, _, _ = self.find_source_asset(
                source_kind, source_id, asset_type, asset_id
            )
            variants, selected_field, container = self._source_motion_container(
                asset, asset_type, motion_type
            )
            if not any(item.id == variant_id for item in variants):
                raise ValueError(f"Motion variant {variant_id} not found")
            previous = getattr(container, selected_field, None)
            setattr(container, selected_field, variant_id)
            try:
                self._save_after_asset_mutation(storage_source)
            except Exception:
                setattr(container, selected_field, previous)
                raise
            return asset

    def delete_source_asset_motion_variant(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        variant_id: str,
        motion_type: Optional[str] = None,
    ) -> Any:
        with self._save_lock:
            asset, storage_source, _, _ = self.find_source_asset(
                source_kind, source_id, asset_type, asset_id
            )
            variants, selected_field, container = self._source_motion_container(
                asset, asset_type, motion_type
            )
            index = next(
                (idx for idx, item in enumerate(variants) if item.id == variant_id),
                None,
            )
            if index is None:
                raise ValueError(f"Motion variant {variant_id} not found")
            removed = variants.pop(index)
            previous = getattr(container, selected_field, None)
            if previous == variant_id:
                setattr(
                    container,
                    selected_field,
                    variants[-1].id if variants else None,
                )
            try:
                self._save_after_asset_mutation(storage_source)
            except Exception:
                variants.insert(index, removed)
                setattr(container, selected_field, previous)
                raise
            return asset

    def favorite_source_asset_motion_variant(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        variant_id: str,
        is_favorited: bool,
        motion_type: Optional[str] = None,
    ) -> Any:
        with self._save_lock:
            asset, storage_source, _, _ = self.find_source_asset(
                source_kind, source_id, asset_type, asset_id
            )
            variants, _, _ = self._source_motion_container(asset, asset_type, motion_type)
            variant = next((item for item in variants if item.id == variant_id), None)
            if variant is None:
                raise ValueError(f"Motion variant {variant_id} not found")
            favorite_field = "is_favorited" if hasattr(variant, "is_favorited") else "is_starred"
            previous = bool(getattr(variant, favorite_field, False))
            setattr(variant, favorite_field, bool(is_favorited))
            try:
                self._save_after_asset_mutation(storage_source)
            except Exception:
                setattr(variant, favorite_field, previous)
                raise
            return asset

    def _resolve_motion_reference_asset(
        self, script: Script, asset_id: str, asset_type: str
    ) -> Tuple[Any, str]:
        """Resolve the canonical owner for one motion-reference request."""

        if asset_type in {"full_body", "head_shot"}:
            resolved_asset_type = "character"
            asset_display_name = "Character"
        elif asset_type in {"scene", "prop"}:
            resolved_asset_type = asset_type
            asset_display_name = asset_type.title()
        else:
            raise ValueError(
                f"Invalid asset_type: {asset_type}. Must be 'full_body', "
                "'head_shot', 'scene', or 'prop'"
            )

        target_asset, source = self._find_asset_with_source(script, asset_id, resolved_asset_type)
        if target_asset is None or source is None:
            raise ValueError(f"{asset_display_name} {asset_id} not found")
        return target_asset, source

    def _motion_reference_source_image_url(
        self, target_asset: Any, asset_type: str
    ) -> Optional[str]:
        """Return the selected canonical image used to animate an asset."""

        if asset_type == "full_body":
            return (
                self._selected_asset_unit_image_url(target_asset.reference_sheet)
                or self._selected_asset_unit_image_url(target_asset.full_body)
                or self._selected_image_variant_url(target_asset.full_body_asset)
                or target_asset.full_body_image_url
                or target_asset.image_url
            )
        if asset_type == "head_shot":
            return (
                self._selected_asset_unit_image_url(target_asset.head_shot)
                or self._selected_image_variant_url(target_asset.headshot_asset)
                or target_asset.headshot_image_url
                or target_asset.avatar_url
            )
        if asset_type in {"scene", "prop"}:
            return (
                self._selected_image_variant_url(target_asset.image_asset) or target_asset.image_url
            )
        return None

    def prepare_storyboard_render(
        self,
        script_id: str,
        frame_id: str,
        composition_data: Optional[Dict[str, Any]],
        prompt: str,
        batch_size: int = 1,
    ) -> StoryboardRenderPlan:
        """Persist a render marker and return provider inputs detached from state."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")

        ref_image_url = None
        ref_image_urls = []
        if composition_data:
            ref_image_url = composition_data.get("reference_image_url")
            ref_image_urls = composition_data.get("reference_image_urls", [])

        ref_image_paths: List[str] = []
        for url in ref_image_urls:
            if not url:
                continue
            if is_object_key(url) or url.startswith("http"):
                ref_image_paths.append(url)
            else:
                potential_path = _safe_resolve_path(self.output_root, url)
                if os.path.exists(potential_path):
                    ref_image_paths.append(potential_path)

        if ref_image_url and ref_image_url not in ref_image_urls:
            if is_object_key(ref_image_url) or ref_image_url.startswith("http"):
                if ref_image_url not in ref_image_paths:
                    ref_image_paths.append(ref_image_url)
            else:
                potential_path = _safe_resolve_path(self.output_root, ref_image_url)
                if os.path.exists(potential_path) and potential_path not in ref_image_paths:
                    ref_image_paths.append(potential_path)

        resolved = self.resolve_episode_assets(script)
        scene = next((item for item in resolved["scenes"] if item.id == frame.scene_id), None)

        from .assets import ASPECT_RATIO_TO_SIZE

        effective_settings = self._effective_model_settings(script)
        effective_size = ASPECT_RATIO_TO_SIZE.get(
            effective_settings.storyboard_aspect_ratio,
            "1536x1024",
        )
        model_name = effective_settings.i2i_model
        rendered_asset = frame.rendered_image_asset
        existing_ids = frozenset(
            variant.id for variant in (rendered_asset.variants if rendered_asset else [])
        )

        prepared_at = time.time()
        frame.status = GenerationStatus.PROCESSING
        if composition_data:
            frame.composition_data = composition_data
        frame.image_prompt = prompt
        frame.updated_at = prepared_at
        self._save_data()

        logger.info(
            "Rendering frame %s using model %s with %s reference images",
            frame_id,
            model_name,
            len(ref_image_paths),
        )
        return StoryboardRenderPlan(
            script_id=script_id,
            frame_id=frame_id,
            frame=frame.model_copy(deep=True),
            characters=[item.model_copy(deep=True) for item in resolved["characters"]],
            scene=scene.model_copy(deep=True) if scene else None,
            ref_image_paths=list(ref_image_paths),
            prompt=prompt,
            batch_size=batch_size,
            size=effective_size,
            model_name=model_name,
            existing_variant_ids=existing_ids,
            prepared_at=prepared_at,
        )

    def execute_storyboard_render_plan(self, plan: StoryboardRenderPlan) -> StoryboardFrame:
        """Run only the slow provider phase; never save workspace metadata."""
        return self.storyboard_generator.generate_frame(
            plan.frame,
            plan.characters,
            plan.scene,
            ref_image_path=(plan.ref_image_paths[0] if plan.ref_image_paths else None),
            ref_image_paths=plan.ref_image_paths,
            prompt=plan.prompt,
            batch_size=plan.batch_size,
            size=plan.size,
            model_name=plan.model_name,
        )

    @staticmethod
    def validate_storyboard_render_result(generated_frame: StoryboardFrame) -> None:
        if generated_frame.status != GenerationStatus.COMPLETED or not (
            generated_frame.rendered_image_url or generated_frame.image_url
        ):
            raise RuntimeError("Storyboard frame generation failed without an output image")

    def storyboard_render_output_paths(
        self, plan: StoryboardRenderPlan, generated_frame: StoryboardFrame
    ) -> List[Path]:
        """Return exact local files created by this detached render."""
        rendered_asset = generated_frame.rendered_image_asset
        variants = rendered_asset.variants if rendered_asset else []
        return [
            Path(self.storyboard_generator.output_dir) / f"{plan.frame_id}_{variant.id}.png"
            for variant in variants
            if variant.id not in plan.existing_variant_ids
        ]

    def commit_storyboard_render_plan(
        self, plan: StoryboardRenderPlan, generated_frame: StoryboardFrame
    ) -> Script:
        """Merge new variants into the newest project snapshot."""
        self.validate_storyboard_render_result(generated_frame)
        script = self.scripts.get(plan.script_id)
        if not script:
            raise ValueError("Script not found")
        frame = next((item for item in script.frames if item.id == plan.frame_id), None)
        if not frame:
            raise ValueError(f"Frame {plan.frame_id} not found")

        rendered_asset = generated_frame.rendered_image_asset
        new_variants = [
            variant
            for variant in (rendered_asset.variants if rendered_asset else [])
            if variant.id not in plan.existing_variant_ids
        ]
        if not new_variants:
            raise RuntimeError("Storyboard frame generation produced no new image")

        if not frame.rendered_image_asset:
            from .models import ImageAsset

            frame.rendered_image_asset = ImageAsset()
        current_ids = {variant.id for variant in frame.rendered_image_asset.variants}
        for variant in new_variants:
            if variant.id not in current_ids:
                frame.rendered_image_asset.variants.append(variant.model_copy(deep=True))
                current_ids.add(variant.id)

        selected_id = rendered_asset.selected_id if rendered_asset else None
        if selected_id not in {variant.id for variant in new_variants}:
            selected_id = new_variants[-1].id
        selected_variant = next(
            (
                variant
                for variant in frame.rendered_image_asset.variants
                if variant.id == selected_id
            ),
            None,
        )
        if selected_variant is None:
            raise RuntimeError("Generated storyboard image could not be merged")

        frame.rendered_image_asset.selected_id = selected_id
        frame.rendered_image_url = selected_variant.url
        frame.image_url = selected_variant.url
        t2i_history = [
            url
            for url in (frame.t2i_image_urls or [])
            if _normalize_clip_image_url(url) != _normalize_clip_image_url(selected_variant.url)
        ]
        t2i_history.append(selected_variant.url)
        frame.t2i_image_urls = t2i_history[-self._T2I_HISTORY_LIMIT :]
        frame.t2i_selected_index = len(frame.t2i_image_urls) - 1
        self._set_frame_clip_start_image(frame, selected_variant.id, selected_variant.url)
        frame.status = GenerationStatus.COMPLETED
        frame.updated_at = time.time()
        self._save_data()
        return script

    def fail_storyboard_render_plan(self, plan: StoryboardRenderPlan) -> None:
        """Mark this attempt failed without clobbering newer frame edits."""
        script = self.scripts.get(plan.script_id)
        if not script:
            return
        frame = next((item for item in script.frames if item.id == plan.frame_id), None)
        if not frame:
            return
        if frame.status == GenerationStatus.PROCESSING and frame.updated_at == plan.prepared_at:
            frame.status = GenerationStatus.FAILED
            frame.updated_at = time.time()
            self._save_data()

    def generate_storyboard_render(
        self,
        script_id: str,
        frame_id: str,
        composition_data: Optional[Dict[str, Any]],
        prompt: str,
        batch_size: int = 1,
    ) -> Script:
        """Step 3b: Render a frame through a detached, merge-safe plan."""
        plan = self.prepare_storyboard_render(
            script_id, frame_id, composition_data, prompt, batch_size
        )
        try:
            generated_frame = self.execute_storyboard_render_plan(plan)
            self.validate_storyboard_render_result(generated_frame)
            return self.commit_storyboard_render_plan(plan, generated_frame)
        except Exception:
            self.fail_storyboard_render_plan(plan)
            raise

    def generate_video(self, script_id: str) -> Script:
        """Step 4: Generate video clips."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script = self.video_generator.generate_video(
            script,
            model_id=self._effective_model_settings(script).video_model,
        )
        self._save_data()
        return script

    def create_video_task(
        self,
        script_id: str,
        image_url: Optional[str],
        prompt: str,
        duration: int = 5,
        seed: int = None,
        resolution: str = "720p",
        generate_audio: bool = False,
        model: str = None,
        frame_id: str = None,
        source_image_id: Optional[str] = None,
        source_image_url: Optional[str] = None,
        frame_type: Optional[str] = None,
        generation_mode: str = "i2v",
        ratio: str = None,
        watermark: Optional[bool] = None,
        workbench_tab: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Tuple[Script, str]:
        """Creates a new video generation task."""
        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        model = model or self._effective_model_settings(script).video_model
        spec = get_model_spec(model, VIDEO)
        if generation_mode not in spec.supported_modes:
            raise ValueError(
                f"Model '{model}' does not support generation mode '{generation_mode}'"
            )
        if generation_mode == "i2v" and not image_url:
            raise ValueError("Image-to-video generation requires one source image")
        if generation_mode == "t2v" and image_url:
            raise ValueError("Text-to-video generation must not include a source image")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
            raise ValueError("Seedance video duration must be between 4 and 15 seconds")
        resolution = (resolution or "720p").strip().lower()
        if resolution not in {"720p", "1080p"}:
            raise ValueError("Seedance video resolution must be 720p or 1080p")
        if generation_mode == "i2v" and resolution != "720p":
            raise ValueError("Image-to-video clip generation supports only 720p")
        ratio = (ratio or "16:9").strip()
        if ratio not in {"16:9", "9:16", "1:1"}:
            raise ValueError("Seedance video aspect ratio must be 16:9, 9:16, or 1:1")
        # Validate the exact selected model's dedicated key before a task is
        # persisted or queued. There is no capability/shared-key fallback.
        resolve_model_api_key(model, VIDEO)

        task_id = task_id or str(uuid.uuid4())
        _validate_safe_id(task_id, "task_id")
        if any(item.id == task_id for item in (script.video_tasks or [])):
            raise ValueError("Video task already exists")

        # Snapshot the input image to ensure consistency between submission and
        # the first worker attempt. Retried asset jobs can deliberately refresh
        # this snapshot after the user selects a safer replacement image.
        snapshot_url = self._snapshot_video_input(image_url, task_id)

        # Enrich prompt with dialogue cue when a frame has dialogue text.
        # This gives the video model explicit mouth-movement instructions.
        if frame_id and prompt:
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if frame:
                from .prompt_assembly import enrich_prompt_with_dialogue

                prompt = enrich_prompt_with_dialogue(prompt, frame)

        task = VideoTask(
            id=task_id,
            project_id=script_id,
            frame_id=frame_id,
            source_image_id=source_image_id,
            source_image_url=source_image_url or image_url,
            frame_type=_normalize_frame_type(frame_type) if frame_type else None,
            image_url=snapshot_url,
            prompt=prompt,
            status="pending",
            duration=duration,
            seed=seed,
            resolution=resolution,
            generate_audio=generate_audio,
            model=model,
            generation_mode=generation_mode,
            ratio=ratio,
            watermark=watermark,
            workbench_tab=workbench_tab,
            created_at=time.time(),
        )

        if not script.video_tasks:
            script.video_tasks = []
        script.video_tasks.append(task)

        self._save_data()
        return script, task_id

    def _snapshot_video_input(self, image_url: Optional[str], task_id: str) -> str:
        """Copy one local video input into the task-owned snapshot directory."""

        snapshot_url = image_url or ""
        try:
            if image_url and not image_url.startswith(("http://", "https://", "data:")):
                src_path = _safe_resolve_path(self.output_root, image_url)
                if os.path.exists(src_path) and os.path.isfile(src_path):
                    snapshot_dir = os.path.join(self.output_root, "video_inputs")
                    os.makedirs(snapshot_dir, exist_ok=True)
                    ext = os.path.splitext(os.path.basename(image_url))[1] or ".png"
                    _validate_safe_id(task_id, "task_id")
                    snapshot_filename = f"{task_id}{ext}"
                    snapshot_path = _safe_resolve_path(snapshot_dir, snapshot_filename)

                    import shutil

                    shutil.copy2(src_path, snapshot_path)
                    snapshot_url = f"video_inputs/{snapshot_filename}"
        except Exception as exc:
            logger.error(f"Failed to snapshot input image: {exc}")
        return snapshot_url

    def refresh_asset_video_task_input(self, script_id: str, task_id: str) -> bool:
        """Refresh a retried asset-video task from the current selected artwork.

        The first attempt remains pinned to its submission snapshot. A durable
        retry calls this after the user has had a chance to select or generate
        a replacement, preventing the rejected image from being resent.
        """

        script = self.get_script(script_id)
        if script is None:
            return False
        task = next(
            (item for item in (script.video_tasks or []) if item.id == task_id),
            None,
        )
        if task is None or not task.asset_id:
            return False

        selected_url: Optional[str] = None
        for asset_type in ("character", "scene", "prop"):
            target_asset, _source = self._find_asset_with_source(script, task.asset_id, asset_type)
            if target_asset is None:
                continue
            selected_url = (
                self._motion_reference_source_image_url(target_asset, "full_body")
                if asset_type == "character"
                else self._library_primary_image_url(asset_type, target_asset)
            )
            break
        if not selected_url:
            return False

        previous_url = task.image_url
        task.image_url = self._snapshot_video_input(selected_url, task.id)
        task.error = None
        task.error_code = None
        task.error_diagnostic = None

        normalized_previous = (previous_url or "").replace("\\", "/")
        if (
            previous_url != task.image_url
            and normalized_previous.startswith("video_inputs/")
            and task.id in os.path.basename(normalized_previous)
        ):
            try:
                os.unlink(_safe_resolve_path(self.output_root, normalized_previous))
            except FileNotFoundError:
                pass
        self._save_data()
        return True

    def rollback_video_task(self, script_id: str, task_id: str) -> bool:
        """Remove an unpublished video task and its private input snapshot."""

        snapshot_path: Optional[str] = None
        try:
            with self._assembly_mutation(script_id) as mutation:
                script = mutation.script
                task = next(
                    (item for item in (script.video_tasks or []) if item.id == task_id),
                    None,
                )
                if task is None or task.status not in {"pending", "failed"}:
                    return False
                script.video_tasks = [
                    item for item in (script.video_tasks or []) if item.id != task_id
                ]
                for asset in [*script.characters, *script.scenes, *script.props]:
                    if getattr(asset, "video_assets", None):
                        asset.video_assets = [
                            item for item in asset.video_assets if item.id != task_id
                        ]

                snapshot_url = getattr(task, "image_url", "") or ""
                normalized = snapshot_url.replace("\\", "/")
                if normalized.startswith("video_inputs/") and task_id in os.path.basename(
                    normalized
                ):
                    snapshot_path = _safe_resolve_path(self.output_root, normalized)
                mutation.mark_changed()
        except ValueError as exc:
            if str(exc) == "Script not found":
                return False
            raise
        if snapshot_path:
            try:
                os.unlink(snapshot_path)
            except FileNotFoundError:
                pass
        return True

    def extract_last_frame(self, script_id: str, frame_id: str, video_task_id: str) -> Script:
        """Extract the last frame from a video task and add it as a variant of the frame's rendered_image_asset."""
        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        # Find the video task
        video_task = next((t for t in script.video_tasks if t.id == video_task_id), None)
        if not video_task or video_task.status != "completed" or not video_task.video_url:
            raise ValueError("Video task not found or not completed")

        temporary_video_path: Optional[str] = None
        try:
            # Resolve video path
            video_path = video_task.video_url
            if not video_path.startswith("/") and not video_path.startswith("http"):
                video_path = _safe_resolve_path(self.output_root, video_path)

            if video_path.startswith("http"):
                # Download to temp file first. It must not survive either a
                # successful extraction or any validation/FFmpeg/upload error.
                temporary_video_path = self._download_temp_image(video_path)
                video_path = temporary_video_path

            return self._extract_last_frame_from_path(script, frame, video_path)
        finally:
            self._delete_temporary_download(temporary_video_path)

    def _extract_last_frame_from_path(
        self,
        script: Script,
        frame: StoryboardFrame,
        video_path: str,
    ) -> Script:
        from .models import ImageAsset, ImageVariant

        if not os.path.exists(video_path):
            raise ValueError(f"Video file not found: {video_path}")

        # Extract last frame using FFmpeg
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError("FFmpeg is required for frame extraction but was not found.")

        output_dir = os.path.join(self.output_root, "storyboard")
        os.makedirs(output_dir, exist_ok=True)
        _validate_safe_id(frame.id, "frame_id")
        output_filename = f"frame_{frame.id}_lastframe_{uuid.uuid4().hex[:8]}.jpg"
        output_path = _safe_resolve_path(output_dir, output_filename)

        cmd = [
            ffmpeg_path,
            "-sseof",
            "-0.1",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg error: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg frame extraction timed out")

        if not os.path.exists(output_path):
            raise RuntimeError("Failed to extract last frame from video")

        # Upload to OSS if configured
        from ...utils.oss_utils import OSSImageUploader

        uploader = OSSImageUploader()
        oss_url = uploader.upload_image(output_path)
        image_url = authoritative_media_reference(output_path, self.output_root, oss_url)

        # Create new variant
        variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used="Extracted last frame from video",
            is_uploaded_source=True,
            upload_type="image",
        )

        # Initialize rendered_image_asset if needed
        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset()

        frame.rendered_image_asset.variants.append(variant)
        frame.rendered_image_asset.selected_id = variant.id
        # Also update rendered_image_url so VideoCreator can pick it up
        frame.rendered_image_url = image_url
        frame.image_url = image_url
        self._set_frame_clip_start_image(frame, variant.id, image_url)

        script.updated_at = time.time()
        self._save_data()
        return script

    def upload_frame_image(self, script_id: str, frame_id: str, image_path: str) -> Script:
        """Upload an image as a variant of the frame's rendered_image_asset."""
        from .models import ImageAsset, ImageVariant

        # Validate that image_path is inside the output directory
        safe_path = _safe_resolve_path(
            self.output_root,
            (
                os.path.relpath(image_path, self.output_root)
                if os.path.isabs(image_path)
                else image_path
            ),
        )

        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        # Upload to OSS if configured
        from ...utils.oss_utils import OSSImageUploader

        uploader = OSSImageUploader()
        oss_url = uploader.upload_image(safe_path)
        image_url = authoritative_media_reference(safe_path, self.output_root, oss_url)

        # Create new variant
        variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used="User uploaded image",
            is_uploaded_source=True,
            upload_type="image",
        )

        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset()

        frame.rendered_image_asset.variants.append(variant)
        frame.rendered_image_asset.selected_id = variant.id
        # Also update rendered_image_url so VideoCreator can pick it up
        frame.rendered_image_url = image_url
        frame.image_url = image_url
        self._set_frame_clip_start_image(frame, variant.id, image_url)

        script.updated_at = time.time()
        self._save_data()
        return script

    def _download_temp_image(self, url: str) -> str:
        """Downloads an image to a temporary file."""
        import tempfile

        import requests

        # If it's a local file path (relative to output)
        if not url.startswith("http"):
            local_path = _safe_resolve_path(self.output_root, url)
            if os.path.exists(local_path):
                return local_path

        from ..server.config import server_mode_enabled

        if server_mode_enabled():
            from ...utils.media_security import download_remote_media

            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                return download_remote_media(
                    url,
                    path,
                    max_bytes=max(
                        1,
                        int(os.getenv("ENMOTION_REMOTE_IMAGE_MAX_BYTES", str(25 * 1024 * 1024))),
                    ),
                    allowed_content_prefixes=("image/", "application/octet-stream"),
                )
            except Exception:
                self._delete_temporary_download(path)
                raise

        # Desktop mode retains its original permissive remote-media behavior.
        path: Optional[str] = None
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()

            # Create temp file
            fd, path = tempfile.mkstemp(suffix=".png")
            with os.fdopen(fd, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return path
        except Exception as e:
            self._delete_temporary_download(path)
            logger.error(f"Failed to download image: {e}")
            raise

    @staticmethod
    def _delete_temporary_download(path: Optional[str]) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Failed to remove temporary download %s", path, exc_info=True)

    def select_video_for_frame(self, script_id: str, frame_id: str, video_id: str) -> Script:
        """Manual select: user pins this video as the active take.

        Sets is_video_pinned=True so subsequent auto_select_latest_video
        calls (fired by polling completion) skip this frame and don't
        overwrite the user's hand-picked choice.
        """
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")

            video = next((v for v in script.video_tasks if v.id == video_id), None)
            if not video:
                raise ValueError("Video task not found")

            previous = (
                frame.selected_video_id,
                frame.video_url,
                frame.is_video_pinned,
            )
            frame.selected_video_id = video_id
            frame.video_url = video.video_url
            frame.is_video_pinned = True
            if (
                frame.selected_video_id,
                frame.video_url,
                frame.is_video_pinned,
            ) != previous:
                mutation.mark_changed()
            return script

    def auto_select_latest_video(self, script_id: str, frame_id: str) -> Script:
        """Auto select: pick the latest completed video task for this frame.

        Idempotent. Skips the update entirely if the frame is pinned by the
        user (is_video_pinned=True). Called by the frontend on every task
        completion poll — the pin check is what makes latest-wins respect
        user intent.
        """
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError("Frame not found")

            if frame.is_video_pinned:
                return script  # user has manually pinned — don't overwrite

            # Latest completed task wins. VideoTask carries created_at
            # (default_factory=time.time); we use it as the "completion order"
            # proxy. Backend doesn't track per-task completion time, but tasks
            # in the same batch are queued at roughly the same created_at and
            # complete in arrival order — close enough for "show me what just
            # came out" UX.
            frame_tasks = [
                t
                for t in script.video_tasks
                if t.frame_id == frame_id and t.status == GenerationStatus.COMPLETED and t.video_url
            ]
            if not frame_tasks:
                return script  # nothing to select yet

            latest = max(frame_tasks, key=lambda t: getattr(t, "created_at", 0) or 0)
            if frame.selected_video_id == latest.id and frame.video_url == latest.video_url:
                return script  # already selected — no-op

            frame.selected_video_id = latest.id
            frame.video_url = latest.video_url
            # is_video_pinned stays False — this is an auto-select
            mutation.mark_changed()
            return script

    def unpin_video(self, script_id: str, frame_id: str) -> Script:
        """Clear the manual pin so auto_select_latest_video resumes.

        Intentionally does NOT touch selected_video_id or video_url — the
        user keeps seeing the same take until the next generation runs
        and auto_select picks a newer one.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        if not frame.is_video_pinned:
            return script  # already unpinned — no-op

        frame.is_video_pinned = False
        self._save_data()
        return script

    def _resolve_media_path(self, url: str, suffix: str = "") -> Optional[str]:
        """Resolve a media URL to a local file path.

        Handles three cases:
        1. Local relative path (e.g. 'video/xxx.mp4') → resolve under output/
        2. OSS object key (e.g. 'enmotion/videos/xxx.mp4') → sign URL then download
        3. Full HTTP URL → download directly
        """
        if not url:
            return None

        # Case 1: Try as local path first
        if not url.startswith("http"):
            local_path = _safe_resolve_path(self.output_root, url)
            if os.path.exists(local_path):
                return local_path
            # Not found locally — might be an OSS object key
            if is_object_key(url):
                from ...utils.oss_utils import OSSImageUploader

                uploader = OSSImageUploader()
                if uploader.is_configured:
                    url = uploader.sign_url_for_api(url)
                else:
                    logger.error(f"[DUB] File not local and OSS not configured: {url}")
                    return None
            else:
                return None

        # Case 2 & 3: Download from HTTP URL
        import hashlib

        url_hash = hashlib.md5(url.split("?")[0].encode()).hexdigest()[:12]
        cache_dir = os.path.join(self.output_root, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        cached = os.path.join(cache_dir, f"{url_hash}{suffix}")
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            return cached
        try:
            from ..server.config import server_mode_enabled

            if server_mode_enabled():
                from ...utils.media_security import download_remote_media

                return download_remote_media(
                    url,
                    cached,
                    max_bytes=max(
                        1,
                        int(
                            os.getenv(
                                "ENMOTION_REMOTE_MEDIA_MAX_BYTES",
                                str(250 * 1024 * 1024),
                            )
                        ),
                    ),
                    allowed_content_prefixes=(
                        "audio/",
                        "video/",
                        "application/octet-stream",
                    ),
                )
            import requests

            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(cached, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            logger.info(f"[DUB] Downloaded remote media -> {cached}")
            return cached
        except Exception as e:
            logger.error(f"[DUB] Failed to download media: {e}")
            if os.path.exists(cached):
                os.remove(cached)
            return None

    def _start_demucs_warmup(self) -> None:
        """Start one background model initialization, if it has not started."""
        with self._demucs_warmup_lock:
            if self._demucs_warmup_started:
                return
            self._demucs_warmup_started = True
            threading.Thread(
                target=self._warmup_demucs_model,
                name="enmotion-demucs-warmup",
                daemon=True,
            ).start()

    def _warmup_demucs_model(self):
        """Load/download htdemucs after explicit preload or first dub use."""
        try:
            from demucs.pretrained import get_model

            get_model("htdemucs")
            logger.info("[DUB] Demucs htdemucs model ready")
            self._demucs_ready.set()
        except Exception as e:
            self._demucs_error = str(e)
            self._demucs_ready.set()
            logger.warning(f"[DUB] Demucs model warmup failed: {e}")

    def _ensure_demucs_model_ready(self, timeout: float = 120) -> bool:
        """Lazily initialize Demucs and report whether it is ready for use."""
        self._start_demucs_warmup()
        if not self._demucs_ready.wait(timeout=timeout):
            raise RuntimeError("Demucs 模型正在下载中（首次约需30秒），请稍后重试。")
        if self._demucs_error:
            logger.warning("[DUB] Demucs unavailable; using simple audio replacement")
            return False
        return True

    def _separate_background_audio(self, video_path: str, work_dir: str) -> Optional[str]:
        """Extract audio from video and separate background (no_vocals) using Demucs.

        Returns the path to the background audio WAV file, or None if
        separation fails (caller falls back to simple replacement).
        """
        ffmpeg_path = get_ffmpeg_path()
        extracted_audio = os.path.join(work_dir, "original_audio.wav")

        # Step 1: Extract audio from video
        extract_cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            extracted_audio,
        ]
        try:
            result = subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if result.returncode != 0 or not os.path.exists(extracted_audio):
                logger.warning("[DUB] No audio track in source video, skipping separation")
                return None
        except Exception as e:
            logger.warning(f"[DUB] Audio extraction failed: {e}")
            return None

        # Check if extracted audio has any content (some videos are silent)
        if os.path.getsize(extracted_audio) < 1000:
            logger.info("[DUB] Source video has negligible audio, skipping separation")
            return None

        try:
            worker_path = os.getenv("ENMOTION_DEMUCS_WORKER", "").strip()
            if worker_path:
                worker = os.path.abspath(os.path.expanduser(worker_path))
                if not os.path.isfile(worker):
                    raise FileNotFoundError("configured Demucs worker is unavailable")
                completed = subprocess.run(
                    [
                        worker,
                        "--input",
                        extracted_audio,
                        "--output",
                        work_dir,
                    ],
                    capture_output=True,
                    timeout=10 * 60,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"Demucs worker exited with status {completed.returncode}")
            else:
                # Development/server mode keeps the existing in-process path.
                # Packaged desktop builds use the separate worker so Torch is
                # never extracted during ordinary application launch.
                if not self._ensure_demucs_model_ready():
                    return None

                import demucs.separate

                demucs.separate.main(
                    [
                        "--two-stems",
                        "vocals",
                        "-n",
                        "htdemucs",
                        "--out",
                        work_dir,
                        extracted_audio,
                    ]
                )
        except Exception as e:
            logger.warning(
                f"[DUB] Demucs separation failed: {e}, falling back to simple replacement"
            )
            return None

        # Demucs outputs to: {work_dir}/htdemucs/original_audio/no_vocals.wav
        bg_path = os.path.join(work_dir, "htdemucs", "original_audio", "no_vocals.wav")
        if not os.path.exists(bg_path):
            # Try alternate path structures
            for root, dirs, files in os.walk(work_dir):
                if "no_vocals.wav" in files:
                    bg_path = os.path.join(root, "no_vocals.wav")
                    break

        if os.path.exists(bg_path):
            logger.info(f"[DUB] Background audio separated successfully: {bg_path}")
            return bg_path

        logger.warning("[DUB] Demucs output not found, falling back to simple replacement")
        return None

    def _ensure_bg_audio_cached(self, frame, video_path: str, video_url: str) -> Optional[str]:
        """Ensure background audio is separated and cached for this frame's video.

        Returns absolute path to bg audio WAV, or None if video has no audio.
        Caches result to a unique output/audio/bg_{frame_id}_*.wav — only re-runs Demucs
        if video source changed.
        """
        if frame.bg_audio_url and frame.bg_audio_source_video == video_url:
            cached_path = _safe_resolve_path(self.output_root, frame.bg_audio_url)
            if os.path.exists(cached_path):
                logger.info(f"[DUB] Background audio cache hit: {frame.bg_audio_url}")
                return cached_path

        import shutil
        import tempfile

        work_dir = tempfile.mkdtemp(prefix="demucs_")
        try:
            bg_path = self._separate_background_audio(video_path, work_dir)
            if not bg_path:
                frame.bg_audio_url = None
                frame.bg_audio_source_video = video_url
                return None

            cache_filename = f"bg_{frame.id}_{uuid.uuid4().hex}.wav"
            cache_path = _safe_resolve_path(os.path.join(self.output_root, "audio"), cache_filename)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            shutil.copy2(bg_path, cache_path)

            frame.bg_audio_url = f"audio/{cache_filename}"
            frame.bg_audio_source_video = video_url
            logger.info(f"[DUB] Background audio cached: {cache_filename}")
            return cache_path
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def preview_dub(
        self, script_id: str, frame_id: str, video_task_id: str, offset_ms: int = 0
    ) -> "Script":
        """Generate a preview dubbed video (Demucs cached + fast adelay+amix+mux).

        Replaces any existing preview_video_url (lazy cleanup).
        Does NOT touch dubbed_video_url.
        """
        _validate_safe_id(script_id, "script_id")
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        source_frame = next((f for f in script.frames if f.id == frame_id), None)
        if not source_frame:
            raise ValueError(f"Frame {frame_id} not found")

        if not source_frame.audio_url:
            raise ValueError(
                "Frame has no dialogue audio (audio_url). Upload an audio track first."
            )

        source_task = next((t for t in script.video_tasks if t.id == video_task_id), None)
        if not source_task or not source_task.video_url:
            raise ValueError(f"Video task {video_task_id} not found or has no video_url")
        if source_frame.selected_video_id != video_task_id:
            raise ValueError("Select this video take before previewing its dialogue mix")

        # FFmpeg/Demucs can be slow. Work only with a detached copy, then
        # revalidate and atomically install its outputs under the assembly
        # operation lock.
        frame = source_frame.model_copy(deep=True)
        source_audio_url = source_frame.audio_url
        source_video_url = source_task.video_url
        initial_background_url = source_frame.bg_audio_url

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError("FFmpeg is required for audio dubbing but was not found.")

        video_path = self._resolve_media_path(source_video_url, suffix=".mp4")
        dialogue_path = self._resolve_media_path(frame.audio_url, suffix=".mp3")

        if not video_path or not os.path.exists(video_path):
            raise ValueError(f"Video file not found: {source_video_url}")
        if not dialogue_path or not os.path.exists(dialogue_path):
            raise ValueError(f"Audio file not found: {frame.audio_url}")
        if os.path.getsize(dialogue_path) < 1000:
            raise ValueError("Dialogue audio file is invalid or empty. Please upload it again.")

        output_filename = f"preview_{frame_id}_{uuid.uuid4().hex}.mp4"
        output_path = _safe_resolve_path(os.path.join(self.output_root, "video"), output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Ensure background audio is cached (Demucs runs only on first call or video change)
        bg_audio_path = self._ensure_bg_audio_cached(frame, video_path, source_video_url)
        generated_background_path = (
            _safe_resolve_path(self.output_root, frame.bg_audio_url) if frame.bg_audio_url else None
        )
        if frame.bg_audio_url == initial_background_url:
            generated_background_path = None

        import tempfile

        work_dir = tempfile.mkdtemp(prefix="dub_mix_")
        try:
            if bg_audio_path:
                mixed_audio = os.path.join(work_dir, "mixed.wav")
                delay_str = f"{offset_ms}|{offset_ms}"

                mix_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    bg_audio_path,
                    "-i",
                    dialogue_path,
                    "-filter_complex",
                    f"[1:a]adelay={delay_str}[dialogue];[0:a][dialogue]amix=inputs=2:duration=first:weights=1 1[out]",
                    "-map",
                    "[out]",
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    mixed_audio,
                ]

                logger.info(f"[DUB] Mixing dialogue audio with background (adelay={offset_ms}ms)")
                subprocess.run(mix_cmd, check=True, capture_output=True, timeout=60)

                if not os.path.exists(mixed_audio):
                    raise RuntimeError("Audio mixing failed: output file not created")

                mux_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    video_path,
                    "-i",
                    mixed_audio,
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    output_path,
                ]
                subprocess.run(mux_cmd, check=True, capture_output=True, timeout=60)
            else:
                delay_str = f"{offset_ms}|{offset_ms}"
                cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    video_path,
                    "-i",
                    dialogue_path,
                    "-filter_complex",
                    f"[1:a]adelay={delay_str}[dialogue];[dialogue]apad[out]",
                    "-map",
                    "0:v",
                    "-map",
                    "[out]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    output_path,
                ]
                logger.info(f"[DUB] Simple replacement with adelay={offset_ms}ms")
                subprocess.run(cmd, check=True, capture_output=True, timeout=120)

        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else "No error output"
            logger.error(f"[DUB] FFmpeg failed: {stderr_msg[:400]}")
            _delete_or_defer_workspace_media(
                self.output_root,
                [path for path in (output_path, generated_background_path) if path],
            )
            raise RuntimeError(f"Audio dubbing failed: {stderr_msg[:200]}")
        except BaseException:
            _delete_or_defer_workspace_media(
                self.output_root,
                [path for path in (output_path, generated_background_path) if path],
            )
            raise
        finally:
            import shutil

            shutil.rmtree(work_dir, ignore_errors=True)

        if not os.path.exists(output_path):
            _delete_or_defer_workspace_media(
                self.output_root,
                [generated_background_path] if generated_background_path else [],
            )
            raise RuntimeError("Preview video was not created")

        retired_paths: List[str] = []
        try:
            with self._assembly_mutation(script_id) as mutation:
                script = mutation.script
                live_frame = next(
                    (item for item in script.frames if item.id == frame_id),
                    None,
                )
                live_task = next(
                    (item for item in script.video_tasks if item.id == video_task_id),
                    None,
                )
                if live_frame is None:
                    raise ValueError(f"Frame {frame_id} no longer exists")
                if (
                    live_task is None
                    or live_task.video_url != source_video_url
                    or live_frame.selected_video_id != video_task_id
                    or live_frame.audio_url != source_audio_url
                ):
                    raise AssemblyMutationConflictError(
                        "The dialogue or selected video take changed while the preview "
                        "was being generated"
                    )

                for old_url, new_url in (
                    (live_frame.preview_video_url, f"video/{output_filename}"),
                    (live_frame.bg_audio_url, frame.bg_audio_url),
                ):
                    if not old_url or old_url == new_url:
                        continue
                    try:
                        retired_paths.append(_safe_resolve_path(self.output_root, old_url))
                    except ValueError:
                        pass

                live_frame.preview_video_url = f"video/{output_filename}"
                live_frame.preview_video_task_id = video_task_id
                live_frame.dub_offset_ms = offset_ms
                live_frame.bg_audio_url = frame.bg_audio_url
                live_frame.bg_audio_source_video = frame.bg_audio_source_video
                mutation.mark_changed()
        except BaseException:
            _delete_or_defer_workspace_media(
                self.output_root,
                [path for path in (output_path, generated_background_path) if path],
            )
            raise
        _delete_or_defer_workspace_media(self.output_root, retired_paths)

        logger.info(f"[DUB] Preview generated: {output_filename}")
        return script

    def apply_dub(self, script_id: str, frame_id: str) -> "Script":
        """Promote preview_video_url to dubbed_video_url."""
        _validate_safe_id(script_id, "script_id")
        old_path: Optional[str] = None
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError(f"Frame {frame_id} not found")

            if not frame.preview_video_url:
                raise ValueError("No preview to apply. Generate a preview first.")
            if (
                not frame.preview_video_task_id
                or frame.selected_video_id != frame.preview_video_task_id
            ):
                raise ValueError(
                    "The preview belongs to a different video take; select it and preview again"
                )

            old_path = (
                _safe_resolve_path(self.output_root, frame.dubbed_video_url)
                if frame.dubbed_video_url
                else None
            )

            frame.dubbed_video_url = frame.preview_video_url
            frame.dubbed_video_task_id = frame.preview_video_task_id
            frame.preview_video_url = None
            frame.preview_video_task_id = None
            mutation.mark_changed()
        _delete_or_defer_workspace_media(self.output_root, [old_path] if old_path else [])

        logger.info(f"[DUB] Applied: {frame.dubbed_video_url}")
        return script

    def revert_dub(self, script_id: str, frame_id: str) -> "Script":
        """Revert dubbing — clear dubbed and preview, keep bg cache."""
        _validate_safe_id(script_id, "script_id")
        retired_paths: List[str] = []
        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            frame = next((f for f in script.frames if f.id == frame_id), None)
            if not frame:
                raise ValueError(f"Frame {frame_id} not found")

            previous = (
                frame.dubbed_video_url,
                frame.preview_video_url,
                frame.dub_offset_ms,
                frame.dubbed_video_task_id,
                frame.preview_video_task_id,
            )
            for url_field in ("dubbed_video_url", "preview_video_url"):
                url = getattr(frame, url_field)
                if url:
                    path = _safe_resolve_path(self.output_root, url)
                    retired_paths.append(path)
                    setattr(frame, url_field, None)

            frame.dub_offset_ms = 0
            frame.dubbed_video_task_id = None
            frame.preview_video_task_id = None
            if (
                frame.dubbed_video_url,
                frame.preview_video_url,
                frame.dub_offset_ms,
                frame.dubbed_video_task_id,
                frame.preview_video_task_id,
            ) != previous:
                mutation.mark_changed()
        _delete_or_defer_workspace_media(self.output_root, retired_paths)
        return script

    def merge_videos(self, script_id: str) -> Script:
        """Step 5b: Merge selected videos into a single file."""
        _validate_safe_id(script_id, "script_id")
        operation_lock = self._assembly_operation_lock(script_id)
        if not operation_lock.acquire(blocking=False):
            raise AssemblyOperationInProgressError(ASSEMBLY_OPERATION_BUSY_MESSAGE)
        try:
            return self._merge_videos_locked(script_id)
        finally:
            operation_lock.release()

    def _merge_videos_locked(self, script_id: str) -> Script:
        """Merge implementation; caller owns the per-project assembly lock."""

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"[MERGE] Starting video merge for script {script_id}")

        # Check if ffmpeg is available (prioritize bundled version)
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            install_instructions = get_ffmpeg_install_instructions()
            error_msg = (
                "FFmpeg is required for video merging but was not found.\n\n"
                f"{install_instructions}\n\n"
                "After installation, restart the application."
            )
            logger.error(f"[MERGE] FFmpeg not found. {error_msg}")
            raise RuntimeError(error_msg)

        # Log ffmpeg version for debugging
        try:
            version_result = subprocess.run(
                [ffmpeg_path, "-version"], capture_output=True, text=True, timeout=5
            )
            if version_result.returncode == 0:
                version_line = (
                    version_result.stdout.split("\n")[0] if version_result.stdout else "Unknown"
                )
                logger.debug(f"[MERGE] Using FFmpeg: {version_line}")
                logger.debug(f"[MERGE] FFmpeg path: {ffmpeg_path}")
            else:
                logger.warning(
                    f"[MERGE] Could not get FFmpeg version (exit code {version_result.returncode})"
                )
        except Exception as e:
            logger.warning(f"[MERGE] Could not get FFmpeg version: {e}")

        # Collect video paths
        video_paths = []
        for i, frame in enumerate(script.frames):
            logger.info(f"[MERGE] Processing frame {i+1}/{len(script.frames)}: {frame.id}")

            # Prefer a dub only when it belongs to the currently selected take.
            # Otherwise selecting take B after dubbing take A would silently
            # merge A while the UI correctly shows B.
            if (
                frame.dubbed_video_url
                and frame.dubbed_video_task_id
                and frame.dubbed_video_task_id == frame.selected_video_id
            ):
                dubbed_path = _safe_resolve_path(self.output_root, frame.dubbed_video_url)
                if os.path.exists(dubbed_path):
                    logger.debug(f"[MERGE]   -> Using dubbed video: {frame.dubbed_video_url}")
                    video_paths.append(frame.dubbed_video_url)
                    continue
                else:
                    logger.warning(
                        f"[MERGE]   -> Dubbed video file missing: {dubbed_path}, falling back"
                    )

            if not frame.selected_video_id:
                # Try to find a default completed video
                default_video = next(
                    (
                        v
                        for v in script.video_tasks
                        if v.frame_id == frame.id and v.status == "completed"
                    ),
                    None,
                )
                if default_video and default_video.video_url:
                    logger.debug(f"[MERGE]   -> Using default video: {default_video.video_url}")
                    video_paths.append(default_video.video_url)
                else:
                    logger.warning(f"[MERGE]   -> No video selected or available, skipping")
                continue

            video = next((v for v in script.video_tasks if v.id == frame.selected_video_id), None)
            if video and video.video_url:
                logger.debug(f"[MERGE]   -> Selected video: {video.video_url}")
                video_paths.append(video.video_url)
            else:
                logger.warning(
                    f"[MERGE]   -> Selected video {frame.selected_video_id} not found or has no URL"
                )

        if not video_paths:
            logger.error("[MERGE] No videos found to merge!")
            raise ValueError(
                "No videos selected to merge. Please select videos for each frame first."
            )

        logger.info(f"[MERGE] Found {len(video_paths)} videos to merge")

        # Create file list for ffmpeg
        list_path = _safe_resolve_path(self.output_root, f"merge_list_{script_id}.txt")
        abs_video_paths = []

        with open(list_path, "w") as f:
            for path in video_paths:
                # Resolve to absolute path
                if not path.startswith("http"):
                    abs_path = _safe_resolve_path(self.output_root, path)
                    if os.path.exists(abs_path):
                        f.write(f"file '{abs_path}'\n")
                        abs_video_paths.append(abs_path)
                        logger.debug(f"[MERGE] Added to list: {abs_path}")
                    else:
                        logger.warning(f"[MERGE] Video file not found: {abs_path}")

        if not abs_video_paths:
            logger.error("[MERGE] No valid video files found on disk!")
            try:
                os.remove(list_path)
            except OSError:
                pass
            raise ValueError(
                "No valid video files found. The video files may have been deleted or moved."
            )

        logger.info(f"[MERGE] Merge list created with {len(abs_video_paths)} videos")

        # Output path
        output_filename = f"merged_{script_id}_{uuid.uuid4().hex}.mp4"
        output_path = _safe_resolve_path(os.path.join(self.output_root, "video"), output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        logger.debug(f"[MERGE] Output path: {output_path}")

        # Log video file details for debugging
        for i, path in enumerate(abs_video_paths):
            try:
                size_mb = os.path.getsize(path) / (1024 * 1024)
                logger.debug(
                    f"[MERGE] Input video {i+1}: {os.path.basename(path)} ({size_mb:.2f} MB)"
                )
            except Exception as e:
                logger.warning(f"[MERGE] Could not get size for video {i+1}: {e}")

        # Run ffmpeg
        # Use re-encoding for better compatibility (slower but more reliable)
        # -c:v libx264 -c:a aac ensures consistent output format
        cmd = [
            ffmpeg_path,
            "-y",  # Use the detected ffmpeg path
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",  # Re-encode video with H.264
            "-crf",
            "23",  # Quality (lower = better, 23 is default)
            "-preset",
            "fast",  # Encoding speed
            "-c:a",
            "aac",  # Re-encode audio with AAC
            "-b:a",
            "128k",  # Audio bitrate
            "-movflags",
            "+faststart",  # Web optimization
            output_path,
        ]

        logger.debug(f"[MERGE] Running FFmpeg command: {' '.join(cmd)}")
        logger.debug(f"[MERGE] Platform: {platform.system()} {platform.release()}")

        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, timeout=600
            )  # 10 min timeout for re-encoding
            logger.debug(
                f"[MERGE] FFmpeg stdout: {result.stdout.decode()[:500] if result.stdout else 'empty'}"
            )
            logger.info(f"[MERGE] FFmpeg completed successfully")

            # Update script with merged video path
            # Use 'videos/' (plural) to match the /files/videos route
            if script.merged_video_url:
                from ...utils.media_security import resolve_workspace_media_path

                try:
                    previous_merged_path = resolve_workspace_media_path(
                        self.output_root, script.merged_video_url
                    )
                except ValueError:
                    previous_merged_path = None
            else:
                previous_merged_path = None
            # Verify file was created and log details
            if os.path.exists(output_path):
                file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
                logger.info(
                    f"[MERGE] ✅ Merged video created successfully: {output_filename} ({file_size_mb:.2f} MB)"
                )
                logger.info(f"[MERGE] ✅ Video accessible at: /files/videos/{output_filename}")
            else:
                logger.error(f"[MERGE] ❌ Merged video file NOT found at: {output_path}")
                raise RuntimeError(
                    f"Video merge completed but output file not found: {output_path}"
                )

            # PR-3l · Pass 2: BGM mux. If script.bgm_url is set and the BGM
            # file exists, overlay it under the existing audio track at the
            # configured mix level. Dialogue stays on the original track of
            # the per-frame videos (sound-driven I2V already embedded it);
            # a future enhancement can swap to per-frame dialogue overlay.
            try:
                mixed_path = self._maybe_apply_bgm_mux(
                    script,
                    output_path,
                    ffmpeg_path,
                )
                if mixed_path:
                    # Replace the concat output with the mixed one (same filename)
                    os.replace(mixed_path, output_path)
                    logger.info(f"[MERGE] ✅ BGM mux applied — final file: {output_filename}")
            except Exception as bgm_err:
                # A selected soundtrack is part of the requested export. Never
                # report success with a silent file when that track is missing
                # or cannot be mixed. Keep the last successful export available;
                # only the newly-created concat output belongs to this failed
                # attempt.
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                raise ValueError(f"Background music could not be applied: {bgm_err}") from bgm_err

            script.merged_video_url = f"videos/{output_filename}"
            self._save_data()
            _delete_or_defer_workspace_media(
                self.output_root,
                [previous_merged_path] if previous_merged_path else [],
            )

            return script
        except subprocess.TimeoutExpired:
            logger.error("[MERGE] FFmpeg timed out after 600 seconds")
            try:
                os.remove(output_path)
            except OSError:
                pass
            raise RuntimeError("FFmpeg timed out. The videos may be too large.")
        except subprocess.CalledProcessError as e:
            try:
                os.remove(output_path)
            except OSError:
                pass
            stderr_msg = e.stderr.decode() if e.stderr else "No error output"
            stdout_msg = e.stdout.decode() if e.stdout else "No output"

            # Log full details for debugging
            logger.error(f"[MERGE] FFmpeg failed with exit code {e.returncode}")
            logger.error(f"[MERGE] FFmpeg command: {' '.join(cmd)}")
            logger.error(f"[MERGE] FFmpeg stderr: {stderr_msg}")
            logger.error(f"[MERGE] FFmpeg stdout: {stdout_msg}")
            logger.error(
                f"[MERGE] Video files attempted: {[os.path.basename(p) for p in abs_video_paths]}"
            )

            # Extract user-friendly error message
            user_msg = self._extract_ffmpeg_error_message(stderr_msg, abs_video_paths)
            raise RuntimeError(user_msg)
        finally:
            try:
                os.remove(list_path)
            except OSError:
                pass

    def _maybe_apply_bgm_mux(
        self,
        script: Script,
        video_path: str,
        ffmpeg_path: str,
    ) -> Optional[str]:
        """PR-3l · Overlay BGM at the configured mix level on top of the
        already-merged video. Returns the path of the new file, or None
        when no BGM is configured.

        Strategy: 2-input filter — amix the existing video audio (volume =
        dialogue_level/100) with the looped BGM (volume = bgm_level/100).
        SFX track will be added in a later pass when SFX files exist.
        """
        bgm_rel = (script.bgm_url or "").strip()
        if not bgm_rel:
            return None
        bgm_abs = _safe_resolve_path(self.output_root, bgm_rel)
        if not os.path.exists(bgm_abs):
            raise ValueError(
                "the configured track is unavailable; select No BGM or install the track"
            )

        mix = script.mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60}
        dial = max(0, min(100, int(mix.get("dialogue", 100)))) / 100.0
        bgm_lvl = max(0, min(100, int(mix.get("bgm", 35)))) / 100.0

        stem, extension = os.path.splitext(video_path)
        mixed_path = f"{stem}_mixed{extension or '.mp4'}"
        has_dialogue_audio = self._video_has_audio_stream(video_path, ffmpeg_path)
        # -stream_loop -1 loops BGM until shortest (the video) ends.
        if has_dialogue_audio:
            # apad on the dialogue side avoids amix cutting early on silence.
            filter_complex = (
                f"[0:a:0]volume={dial:.3f},apad[a0];"
                f"[1:a:0]volume={bgm_lvl:.3f}[a1];"
                f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            )
        else:
            # Generated clips are allowed to be silent. In that case there is
            # no dialogue stream to mix, so use the looped BGM as the final
            # audio track instead of referencing a nonexistent ``0:a``.
            filter_complex = f"[1:a:0]volume={bgm_lvl:.3f}[aout]"
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            video_path,
            "-stream_loop",
            "-1",
            "-i",
            bgm_abs,
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            mixed_path,
        ]
        logger.info(
            f"[MERGE/BGM] muxing BGM dial={dial:.2f} bgm={bgm_lvl:.2f} — {os.path.basename(bgm_abs)}"
        )
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except subprocess.CalledProcessError as e:
            stderr_msg = (
                e.stderr.decode(errors="replace")
                if isinstance(e.stderr, bytes)
                else str(e.stderr or "")
            )
            logger.warning(f"[MERGE/BGM] ffmpeg failed: {stderr_msg[:400]}")
            try:
                os.remove(mixed_path)
            except OSError:
                pass
            detail = f": {stderr_msg[:200]}" if stderr_msg else ""
            raise RuntimeError(f"BGM FFmpeg failed with exit code {e.returncode}{detail}") from e
        except subprocess.TimeoutExpired as e:
            logger.warning("[MERGE/BGM] ffmpeg timed out after 300 seconds")
            try:
                os.remove(mixed_path)
            except OSError:
                pass
            raise RuntimeError("BGM FFmpeg timed out after 300 seconds") from e
        if not os.path.exists(mixed_path):
            logger.warning(f"[MERGE/BGM] mixed output not found: {mixed_path}")
            raise RuntimeError("BGM FFmpeg completed but the mixed output was not created")
        return mixed_path

    @staticmethod
    def _video_has_audio_stream(video_path: str, ffmpeg_path: str) -> bool:
        """Return whether ``video_path`` exposes a decodable first audio stream."""
        try:
            result = subprocess.run(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    video_path,
                    "-map",
                    "0:a:0",
                    "-t",
                    "0.01",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def _extract_ffmpeg_error_message(self, stderr: str, video_paths: List[str]) -> str:
        """
        Extract a user-friendly error message from ffmpeg stderr output.

        Args:
            stderr: The stderr output from ffmpeg
            video_paths: List of video file paths that were being processed

        Returns:
            A user-friendly error message
        """
        if not stderr:
            return "FFmpeg merge failed with no error output. Please check the log files."

        stderr_lower = stderr.lower()

        # Common error patterns with user-friendly messages
        if "no such file or directory" in stderr_lower:
            return (
                "One or more video files could not be found.\n"
                "The videos may have been deleted or moved.\n"
                "Please try regenerating the missing videos."
            )

        if (
            "invalid data found" in stderr_lower
            or "invalid file" in stderr_lower
            or "moov atom not found" in stderr_lower
        ):
            return (
                "One or more video files are corrupted or incomplete.\n"
                "This can happen if video generation was interrupted.\n"
                "Please try regenerating the affected videos."
            )

        if "codec" in stderr_lower and (
            "not supported" in stderr_lower or "unknown" in stderr_lower
        ):
            return (
                "Video codec compatibility issue detected.\n"
                "The video format may not be supported by your FFmpeg installation.\n"
                "Try updating FFmpeg to the latest version."
            )

        if "permission denied" in stderr_lower or "access is denied" in stderr_lower:
            return (
                "Permission denied when accessing video files.\n"
                "Please check that the application has read/write permissions\n"
                "for the output directory."
            )

        if "disk full" in stderr_lower or "no space" in stderr_lower:
            return (
                "Insufficient disk space to create the merged video.\n"
                "Please free up some space and try again."
            )

        if "height not divisible" in stderr_lower or "width not divisible" in stderr_lower:
            return (
                "Video resolution compatibility issue.\n"
                "The videos have incompatible dimensions.\n"
                "This should not happen - please report this issue."
            )

        if "invalid argument" in stderr_lower:
            # Check if it's related to file list
            if any(
                "filelist" in line.lower() or "concat" in line.lower()
                for line in stderr.split("\n")
            ):
                return (
                    "FFmpeg could not read the video file list.\n"
                    "This might be a file path encoding issue.\n"
                    "Please ensure video filenames don't contain special characters."
                )

        # Fallback: extract the most relevant error line
        # Usually the last non-empty line before the final summary
        error_lines = [line.strip() for line in stderr.split("\n") if line.strip()]
        if error_lines:
            # Look for lines that seem like actual errors (contain "error", "failed", etc.)
            for line in reversed(error_lines):
                line_lower = line.lower()
                if any(
                    keyword in line_lower
                    for keyword in ["error", "failed", "invalid", "cannot", "unable"]
                ):
                    # Truncate if too long
                    if len(line) > 200:
                        line = line[:200] + "..."
                    return f"FFmpeg error: {line}\n\nPlease check the application logs for more details."

            # If no error keyword found, use last line
            last_line = error_lines[-1]
            if len(last_line) > 200:
                last_line = last_line[:200] + "..."
            return f"FFmpeg merge failed: {last_line}\n\nPlease check the application logs for more details."

        return (
            "FFmpeg merge failed with unknown error. Please check the application logs for details."
        )

    def create_asset_video_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        prompt: str,
        duration: int = 5,
        aspect_ratio: str = None,
        model_id: str = None,
        task_id: Optional[str] = None,
    ) -> Tuple[Script, str]:
        """Create a New API image-to-video task for an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Find asset
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Use the same selected canonical artwork as the motion-reference
        # workbench. A legacy ``image_url`` can point at an older variant after
        # the user explicitly selects a replacement.
        image_url = (
            self._motion_reference_source_image_url(target_asset, "full_body")
            if asset_type == "character"
            else target_asset.image_url
        )

        if not image_url:
            raise ValueError("Asset has no reference image")

        # Save prompt to asset
        if prompt:
            target_asset.video_prompt = prompt

        default_prompt = f"Cinematic shot of {target_asset.name}"
        if asset_type == "character":
            default_prompt = f"{FICTIONAL_CHARACTER_PROMPT_NOTICE} {default_prompt}"

        script, task_id = self.create_video_task(
            script_id=script_id,
            image_url=image_url,
            prompt=prompt or default_prompt,
            duration=duration,
            model=(model_id or self._effective_model_settings(script).video_model),
            generation_mode="i2v",
            ratio=aspect_ratio,
            task_id=task_id,
        )
        task = next(task for task in script.video_tasks if task.id == task_id)
        task.asset_id = asset_id

        # Add to asset's video_assets list
        if not target_asset.video_assets:
            target_asset.video_assets = []
        target_asset.video_assets.append(task)

        self._save_data()
        return script, task_id

    def process_video_task(self, script_id: str, task_id: str):
        """Processes a video task."""
        script = self.get_script(script_id)
        if not script:
            logger.error(f"Script {script_id} not found for task {task_id}")
            return

        task = next((t for t in script.video_tasks if t.id == task_id), None)

        if not task:
            logger.error(f"Task {task_id} not found in script {script_id}")
            return

        temporary_img_path: Optional[str] = None
        generated_output_path: Optional[str] = None
        completion_committed = False
        failure_committed = False
        try:
            # Update status to processing
            task.status = "processing"
            task.error = None
            task.error_code = None
            task.error_diagnostic = None
            self._save_data()

            # Download image to temp file
            img_path = None
            if task.image_url:
                img_path = self._download_temp_image(task.image_url)
                if task.image_url.startswith("http"):
                    temporary_img_path = img_path

            # Generate video
            output_filename = f"video_{task_id}.mp4"
            output_path = os.path.join(self.output_root, "video", output_filename)
            generated_output_path = output_path
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            img_url = task.image_url
            model_name = task.model or get_selected_model(VIDEO)
            spec = get_model_spec(model_name, VIDEO)
            if task.generation_mode not in spec.supported_modes:
                raise ValueError(
                    f"Model '{model_name}' does not support generation mode '{task.generation_mode}'"
                )
            if getattr(task, "audio_url", None):
                raise ValueError("New API Seedance does not support driving-audio input")

            if self._newapi_video_model is None:
                from ...models.newapi import NewAPIVideoModel

                self._newapi_video_model = NewAPIVideoModel({})

            def _capture_newapi_provider_ids(
                provider_name: str,
                ptask_id: Optional[str],
                preq_id: Optional[str],
            ) -> None:
                task.provider_name = provider_name
                task.provider_task_id = ptask_id
                task.provider_request_id = preq_id
                self._save_data()

            video_path, _ = self._newapi_video_model.generate(
                prompt=_motion_prompt_with_frame_type(task.prompt, task.frame_type),
                output_path=output_path,
                img_url=img_url or None,
                img_path=img_path,
                model_id=model_name,
                duration=task.duration,
                resolution=task.resolution,
                aspect_ratio=task.ratio or "16:9",
                seed=task.seed,
                generate_audio=task.generate_audio,
                watermark=bool(task.watermark) if task.watermark is not None else False,
                generation_mode=task.generation_mode,
                provider_task_id=task.provider_task_id or None,
                on_provider_ids=_capture_newapi_provider_ids,
            )

            generated_url = os.path.relpath(output_path, self.output_root)
            # Provider work stays outside the assembly lock.  Only the short
            # durable completion/auto-selection commit waits for an active
            # merge or export, so the merged source cannot change underneath
            # FFmpeg.
            completion_abandoned = False
            with self._assembly_mutation(script_id, blocking=True) as mutation:
                committed_script = mutation.script
                committed_task = next(
                    (item for item in committed_script.video_tasks if item.id == task_id),
                    None,
                )
                # Cancellation/deletion can race the external provider. Re-read
                # authoritative state under the assembly lock and never
                # resurrect a task whose result is no longer wanted.
                if committed_task is None or committed_task.status != "processing":
                    completion_abandoned = True
                else:
                    committed_task.video_url = generated_url
                    committed_task.status = "completed"

                    # A completed clip belongs to exactly one storyboard shot.
                    # Keep the active result synchronized unless the user pinned
                    # an older take while this provider request was in flight.
                    if committed_task.frame_id:
                        frame = next(
                            (
                                item
                                for item in committed_script.frames
                                if item.id == committed_task.frame_id
                            ),
                            None,
                        )
                        if frame is not None and not frame.is_video_pinned:
                            frame.selected_video_id = committed_task.id
                            frame.video_url = committed_task.video_url

                    if committed_task.asset_id:
                        self._sync_asset_video_task(committed_script, committed_task)
                    mutation.mark_changed()
            if completion_abandoned:
                _delete_or_defer_workspace_media(
                    self.output_root,
                    [generated_output_path] if generated_output_path else [],
                )
                return
            completion_committed = True

        except Exception as e:
            from .video_failures import classify_video_failure

            logger.exception("Failed to process video task")
            logger.error(f"Video generation failed: {e}")
            failure = classify_video_failure(e)
            script = self.get_script(script_id)
            failed_task = (
                next((item for item in script.video_tasks if item.id == task_id), None)
                if script
                else None
            )
            if failed_task is not None and failed_task.status != "canceled":
                failure_committed = self.mark_video_task_failed(
                    script_id,
                    task_id,
                    failure.message,
                    error_code=failure.code,
                    error_diagnostic=failure.diagnostic,
                    overwrite=True,
                    clear_provider_ids=(
                        isinstance(e, NewAPIProviderError)
                        and e.error_code != "provider_outcome_ambiguous"
                    ),
                )
                authoritative_script = self.get_script(script_id)
                authoritative_task = (
                    next(
                        (item for item in authoritative_script.video_tasks if item.id == task_id),
                        None,
                    )
                    if authoritative_script
                    else None
                )
                if authoritative_task is not None and authoritative_task is not task:
                    for field_name in (
                        "status",
                        "video_url",
                        "error",
                        "error_code",
                        "error_diagnostic",
                        "provider_name",
                        "provider_task_id",
                        "provider_request_id",
                    ):
                        setattr(
                            task,
                            field_name,
                            copy.deepcopy(getattr(authoritative_task, field_name)),
                        )
            else:
                # A cancel/delete that won the race is already durable and
                # should not be overwritten by a late provider failure.
                failure_committed = True
            _delete_or_defer_workspace_media(
                self.output_root,
                [generated_output_path] if generated_output_path else [],
            )
        finally:
            self._delete_temporary_download(temporary_img_path)

        if not completion_committed and not failure_committed:
            self._save_data()

    def _sync_asset_video_task(self, script: Script, task: VideoTask):
        """Syncs the updated task status/url back to the asset's video_assets list."""
        target_asset = None
        # Search in all asset types
        for char in script.characters:
            if char.id == task.asset_id:
                target_asset = char
                break
        if not target_asset:
            for scene in script.scenes:
                if scene.id == task.asset_id:
                    target_asset = scene
                    break
        if not target_asset:
            for prop in script.props:
                if prop.id == task.asset_id:
                    target_asset = prop
                    break

        if target_asset:
            # Find and update the task in the asset's list
            for i, t in enumerate(target_asset.video_assets):
                if t.id == task.id:
                    target_asset.video_assets[i] = task
                    break
            else:
                # Not found, append it (shouldn't happen if created correctly, but good fallback)
                target_asset.video_assets.append(task)

    @staticmethod
    def _detach_video_tasks(script: Script, predicate) -> List[VideoTask]:
        """Remove matching task records and every persisted pointer to them."""

        removed = [task for task in script.video_tasks if predicate(task)]
        if not removed:
            return []
        removed_ids = {task.id for task in removed}
        removed_urls = {task.video_url for task in removed if task.video_url}
        script.video_tasks = [task for task in script.video_tasks if task.id not in removed_ids]

        for asset in [*script.characters, *script.scenes, *script.props]:
            asset.video_assets = [
                task for task in (asset.video_assets or []) if task.id not in removed_ids
            ]
            for unit_name in ("reference_sheet", "full_body", "three_views", "head_shot"):
                unit = getattr(asset, unit_name, None)
                if unit is None or not hasattr(unit, "video_variants"):
                    continue
                unit.video_variants = [
                    variant
                    for variant in (unit.video_variants or [])
                    if variant.id not in removed_ids
                ]
                if unit.selected_video_id in removed_ids:
                    unit.selected_video_id = None

        for frame in script.frames:
            if frame.selected_video_id in removed_ids:
                frame.selected_video_id = None
                frame.is_video_pinned = False
            if frame.final_take_id in removed_ids:
                frame.final_take_id = None
            if frame.dubbed_video_task_id in removed_ids:
                frame.dubbed_video_task_id = None
                frame.dubbed_video_url = None
                # Older project JSON used dubbed_video_task_id as provenance
                # for both applied and preview media.
                if frame.preview_video_task_id is None:
                    frame.preview_video_url = None
            if frame.preview_video_task_id in removed_ids:
                frame.preview_video_task_id = None
                frame.preview_video_url = None
            if frame.bg_audio_source_video in removed_urls:
                frame.bg_audio_source_video = None
                frame.bg_audio_url = None
            if frame.video_url in removed_urls:
                frame.video_url = None
        return removed

    def delete_video_task(self, script_id: str, task_id: str) -> Script:
        """Delete one completed/failed video task and all of its pointers."""

        with self._assembly_mutation(script_id) as mutation:
            script = mutation.script
            removed = self._detach_video_tasks(script, lambda task: task.id == task_id)
            if not removed:
                raise ValueError("Video task not found")
            mutation.mark_changed()
            return script

    def delete_asset_video(
        self, script_id: str, asset_id: str, asset_type: str, video_id: str
    ) -> Script:
        """Deletes a video from an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        if target_asset is None or source is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Find the task in either canonical project history or the embedded
        # asset list. Old projects can contain only one of the two copies.
        embedded_task = next(
            (task for task in (target_asset.video_assets or []) if task.id == video_id),
            None,
        )
        if source == "script":
            task_owner_scripts = [script]
        elif source == "series":
            task_owner_scripts = [
                project
                for project in self.scripts.values()
                if project.series_id == script.series_id
            ]
        else:
            task_owner_scripts = list(self.scripts.values())
        project_tasks = [
            task
            for project in task_owner_scripts
            for task in project.video_tasks
            if task.id == video_id
        ]
        if any(task.asset_id not in {None, asset_id} for task in project_tasks):
            raise ValueError("Video task does not belong to this asset")
        if embedded_task is None and not any(task.asset_id == asset_id for task in project_tasks):
            raise ValueError("Video task does not belong to this asset")
        video_task_to_delete = embedded_task or (project_tasks[0] if project_tasks else None)
        if video_task_to_delete is None:
            raise ValueError("Video task not found")
        task_copies = [task for task in [embedded_task, *project_tasks] if task is not None]
        if any(getattr(task, "status", None) in {"pending", "processing"} for task in task_copies):
            raise ValueError("Running video tasks cannot be deleted")

        if not project_tasks:
            # Normalize old projects that only persisted the embedded asset
            # copy so the shared detacher also clears frame/canonical pointers.
            script.video_tasks.append(video_task_to_delete)
        for project in task_owner_scripts:
            self._detach_video_tasks(project, lambda task: task.id == video_id)
        target_asset.video_assets = [
            task for task in (target_asset.video_assets or []) if task.id != video_id
        ]

        retired_paths: List[str] = []
        for task in task_copies:
            if not task.video_url:
                continue
            try:
                from ...utils.media_security import resolve_workspace_media_path

                retired_paths.append(resolve_workspace_media_path(self.output_root, task.video_url))
            except ValueError:
                continue

        self._save_data()
        if source == "series":
            self._save_series_data()
        elif source == "global":
            self._save_library_data()
        _delete_or_defer_workspace_media(self.output_root, retired_paths)
        return script

    def _select_variant_in_asset(self, image_asset: Any, variant_id: str) -> Any:
        """Select from legacy ImageAsset or canonical AssetUnit containers."""
        if not image_asset:
            return None
        if hasattr(image_asset, "variants"):
            variants = image_asset.variants or []
            selected_attr = "selected_id"
        elif hasattr(image_asset, "image_variants"):
            variants = image_asset.image_variants or []
            selected_attr = "selected_image_id"
        else:
            return None

        for variant in variants:
            if variant.id == variant_id:
                setattr(image_asset, selected_attr, variant_id)
                return variant
        return None

    def _delete_variant_in_asset(self, image_asset: Any, variant_id: str) -> Any:
        """Delete from legacy ImageAsset or canonical AssetUnit containers."""

        if not image_asset:
            return None
        if hasattr(image_asset, "variants"):
            variants_attr = "variants"
            selected_attr = "selected_id"
        elif hasattr(image_asset, "image_variants"):
            variants_attr = "image_variants"
            selected_attr = "selected_image_id"
        else:
            return None
        variants = list(getattr(image_asset, variants_attr) or [])
        deleted = next((item for item in variants if item.id == variant_id), None)
        if deleted is None:
            return None
        variants = [item for item in variants if item.id != variant_id]
        setattr(image_asset, variants_attr, variants)
        if getattr(image_asset, selected_attr, None) == variant_id:
            setattr(image_asset, selected_attr, variants[-1].id if variants else None)
        return deleted

    @staticmethod
    def _remove_variant_urls(image_asset: Any, urls: set[str]) -> None:
        """Remove equivalent migrated copies from either image container.

        Older records can contain one physical image under different variant
        identifiers in canonical and legacy containers. Deleting by ID alone
        leaves an alias that is selected again during fallback/migration.
        """

        if not image_asset or not urls:
            return
        if hasattr(image_asset, "variants"):
            variants_attr = "variants"
            selected_attr = "selected_id"
        elif hasattr(image_asset, "image_variants"):
            variants_attr = "image_variants"
            selected_attr = "selected_image_id"
        else:
            return
        variants = [
            item for item in (getattr(image_asset, variants_attr) or []) if item.url not in urls
        ]
        setattr(image_asset, variants_attr, variants)
        if getattr(image_asset, selected_attr, None) not in {item.id for item in variants}:
            setattr(image_asset, selected_attr, variants[-1].id if variants else None)

    @staticmethod
    def _selected_variant_url(image_asset: Any) -> Optional[str]:
        selected = ComicGenPipeline._selected_variant(image_asset)
        return selected.url if selected else None

    @staticmethod
    def _selected_variant(image_asset: Any) -> Optional[ImageVariant]:
        if not image_asset:
            return None
        if hasattr(image_asset, "variants"):
            variants = image_asset.variants or []
            selected_id = image_asset.selected_id
        elif hasattr(image_asset, "image_variants"):
            variants = image_asset.image_variants or []
            selected_id = image_asset.selected_image_id
        else:
            return None
        selected = next((item for item in variants if item.id == selected_id), None)
        return selected

    def select_asset_variant(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        variant_id: str,
        generation_type: str = None,
    ) -> Script:
        """Selects a specific variant for an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        source = "script"
        if asset_type in {"character", "scene", "prop"}:
            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if target_asset is None or source is None:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset is None:
                raise ValueError("Storyboard frame not found")
        else:
            raise ValueError(f"Invalid asset type: {asset_type}")

        self._select_variant_for_asset(
            target_asset,
            asset_type,
            variant_id,
            generation_type,
            source,
        )
        return script

    def _select_variant_for_asset(
        self,
        target_asset: Any,
        asset_type: str,
        variant_id: str,
        generation_type: Optional[str],
        source: str,
    ) -> Any:
        """Select a variant on an already-resolved canonical owner."""

        variant = None
        promote_character_primary = False

        if asset_type == "character":
            if generation_type == "full_body":
                variant = self._select_variant_in_asset(
                    target_asset.full_body_asset, variant_id
                ) or self._select_variant_in_asset(target_asset.reference_sheet, variant_id)
                if variant:
                    target_asset.full_body_image_url = variant.url
                    target_asset.image_url = variant.url
                    promote_character_primary = True
            elif generation_type == "three_view":
                variant = self._select_variant_in_asset(target_asset.three_view_asset, variant_id)
                if variant:
                    target_asset.three_view_image_url = variant.url
            elif generation_type == "headshot":
                variant = self._select_variant_in_asset(target_asset.headshot_asset, variant_id)
                if variant:
                    target_asset.headshot_image_url = variant.url
                    target_asset.avatar_url = variant.url
            elif generation_type == "reference_sheet":
                variant = self._select_variant_in_asset(target_asset.reference_sheet, variant_id)
                if variant:
                    target_asset.image_url = variant.url
                    promote_character_primary = True
            else:
                # Canonical first, then all legacy containers.
                variant = self._select_variant_in_asset(target_asset.reference_sheet, variant_id)
                if variant:
                    target_asset.image_url = variant.url
                    promote_character_primary = True
                if not variant:
                    variant = self._select_variant_in_asset(
                        target_asset.full_body_asset, variant_id
                    )
                    if variant:
                        target_asset.full_body_image_url = variant.url
                        target_asset.image_url = variant.url
                        promote_character_primary = True
                if not variant:
                    variant = self._select_variant_in_asset(
                        target_asset.three_view_asset, variant_id
                    )
                    if variant:
                        target_asset.three_view_image_url = variant.url
                if not variant:
                    variant = self._select_variant_in_asset(target_asset.headshot_asset, variant_id)
                    if variant:
                        target_asset.headshot_image_url = variant.url
                        target_asset.avatar_url = variant.url
            if variant and promote_character_primary:
                self._set_library_primary_image(
                    "character",
                    target_asset,
                    variant.url,
                    source_variant=variant,
                )
        elif asset_type in {"scene", "prop"}:
            variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
            if variant:
                self._set_library_primary_image(
                    asset_type,
                    target_asset,
                    variant.url,
                    source_variant=variant,
                )
        else:  # storyboard_frame
            variant = self._select_variant_in_asset(target_asset.rendered_image_asset, variant_id)
            if variant:
                target_asset.rendered_image_url = variant.url
                target_asset.image_url = variant.url
            else:
                variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                if variant:
                    target_asset.image_url = variant.url

            if variant:
                self._set_frame_clip_start_image(
                    target_asset, getattr(variant, "id", None), variant.url
                )

        if variant is None:
            raise ValueError(f"Variant {variant_id} not found")

        self._save_after_asset_mutation(source)
        return target_asset

    def delete_asset_variant(
        self, script_id: str, asset_id: str, asset_type: str, variant_id: str
    ) -> Script:
        """Deletes a specific variant from an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        source = "script"
        deleted_variants: List[Any] = []
        if asset_type in {"character", "scene", "prop"}:
            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                deleted_was_clip_start = target_asset.clip_start_image_id == variant_id
                for container, rendered in (
                    (target_asset.rendered_image_asset, True),
                    (target_asset.image_asset, False),
                ):
                    deleted_variant = self._delete_variant_in_asset(container, variant_id)
                    if deleted_variant:
                        deleted_variants.append(deleted_variant)
                        deleted_was_clip_start = deleted_was_clip_start or bool(
                            target_asset.clip_start_image_url
                            and _normalize_clip_image_url(target_asset.clip_start_image_url)
                            == _normalize_clip_image_url(deleted_variant.url)
                        )
                        selected_url = self._selected_variant_url(container)
                        if rendered:
                            target_asset.rendered_image_url = selected_url
                            target_asset.image_url = selected_url
                        elif not target_asset.rendered_image_url:
                            target_asset.image_url = selected_url
                if deleted_was_clip_start:
                    self._reconcile_frame_clip_start_image(target_asset)
        else:
            raise ValueError(f"Invalid asset type: {asset_type}")

        if target_asset is None:
            raise ValueError("Asset not found")

        self._delete_variant_for_asset(
            target_asset,
            asset_type,
            variant_id,
            source,
            deleted_variants=deleted_variants,
        )
        return script

    def _delete_variant_for_asset(
        self,
        target_asset: Any,
        asset_type: str,
        variant_id: str,
        source: str,
        *,
        deleted_variants: Optional[List[Any]] = None,
    ) -> Any:
        """Delete a variant on an already-resolved canonical owner."""

        deleted_variants = deleted_variants if deleted_variants is not None else []

        if asset_type == "character":
            containers = (
                target_asset.reference_sheet,
                target_asset.full_body,
                target_asset.three_views,
                target_asset.head_shot,
                target_asset.full_body_asset,
                target_asset.three_view_asset,
                target_asset.headshot_asset,
            )
            for container in containers:
                deleted = self._delete_variant_in_asset(container, variant_id)
                if deleted:
                    deleted_variants.append(deleted)

            # Legacy migrations could have copied the same URL under a
            # different identifier. Remove every equivalent alias so the
            # deleted generated image cannot reappear through fallback or the
            # next startup migration.
            deleted_urls = {item.url for item in deleted_variants}
            for container in containers:
                self._remove_variant_urls(container, deleted_urls)

            reference_url = self._selected_variant_url(target_asset.reference_sheet)
            full_body_url = self._selected_variant_url(
                target_asset.full_body
            ) or self._selected_variant_url(target_asset.full_body_asset)
            three_view_url = self._selected_variant_url(
                target_asset.three_views
            ) or self._selected_variant_url(target_asset.three_view_asset)
            headshot_url = self._selected_variant_url(
                target_asset.head_shot
            ) or self._selected_variant_url(target_asset.headshot_asset)
            target_asset.full_body_image_url = full_body_url
            target_asset.three_view_image_url = three_view_url
            target_asset.headshot_image_url = headshot_url
            target_asset.avatar_url = headshot_url
            target_asset.image_url = (
                reference_url or full_body_url or three_view_url or headshot_url
            )
        elif asset_type in {"scene", "prop"}:
            containers = (
                getattr(target_asset, "image", None),
                target_asset.image_asset,
            )
            for container in containers:
                deleted = self._delete_variant_in_asset(container, variant_id)
                if deleted:
                    deleted_variants.append(deleted)
            deleted_urls = {item.url for item in deleted_variants}
            for container in containers:
                self._remove_variant_urls(container, deleted_urls)
            target_asset.image_url = next(
                (
                    selected_url
                    for selected_url in (
                        self._selected_variant_url(target_asset.image_asset),
                        self._selected_variant_url(getattr(target_asset, "image", None)),
                    )
                    if selected_url
                ),
                None,
            )

        if not deleted_variants:
            raise ValueError("Variant not found")

        self._save_after_asset_mutation(source)
        return target_asset

    def update_model_settings(
        self,
        script_id: str,
        t2i_model: str = None,
        i2i_model: str = None,
        i2v_model: str = None,
        character_aspect_ratio: str = None,
        scene_aspect_ratio: str = None,
        prop_aspect_ratio: str = None,
        storyboard_aspect_ratio: str = None,
        image_model: str = None,
        chat_model: str = None,
        video_model: str = None,
        clear_overrides: Optional[List[str]] = None,
    ) -> Script:
        """Updates the model settings for a script."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        updates: Dict[str, Any] = {}
        selected_image = image_model or t2i_model or i2i_model
        if selected_image:
            selected_image = get_model_spec(selected_image, IMAGE).model_id
            updates.update(
                image_model=selected_image,
                t2i_model=selected_image,
                i2i_model=selected_image,
            )
        selected_video = video_model or i2v_model
        if selected_video:
            selected_video = get_model_spec(selected_video, VIDEO).model_id
            updates.update(video_model=selected_video, i2v_model=selected_video)
        if chat_model:
            updates["chat_model"] = get_model_spec(chat_model, CHAT).model_id
        for field, value in (
            ("character_aspect_ratio", character_aspect_ratio),
            ("scene_aspect_ratio", scene_aspect_ratio),
            ("prop_aspect_ratio", prop_aspect_ratio),
            ("storyboard_aspect_ratio", storyboard_aspect_ratio),
        ):
            if value:
                updates[field] = value

        script.model_settings = script.model_settings.__class__.model_validate(
            {**script.model_settings.model_dump(), **updates}
        )
        cleared = set(canonical_model_setting_overrides(clear_overrides))
        script.model_settings_overrides = canonical_model_setting_overrides(
            [
                *(field for field in script.model_settings_overrides if field not in cleared),
                *updates,
            ]
        )

        self._save_data()
        return script

    def _set_variant_favorite(self, image_asset: Any, variant_id: str, is_favorited: bool) -> bool:
        """Favorite a variant in either ImageAsset or AssetUnit containers."""
        if not image_asset:
            return False
        if hasattr(image_asset, "variants"):
            variants = image_asset.variants or []
        elif hasattr(image_asset, "image_variants"):
            variants = image_asset.image_variants or []
        else:
            return False
        for v in variants:
            if v.id == variant_id:
                v.is_favorited = is_favorited
                return True
        return False

    def toggle_variant_favorite(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        variant_id: str,
        is_favorited: bool,
        generation_type: str = None,
    ) -> Script:
        """Toggles the favorite status of a variant."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        source = "script"
        if asset_type in {"character", "scene", "prop"}:
            target_asset, source = self._find_asset_with_source(script, asset_id, asset_type)
            if target_asset is None or source is None:
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset is None:
                raise ValueError("Storyboard frame not found")
        else:
            raise ValueError(f"Invalid asset type: {asset_type}")

        self._set_variant_favorite_for_asset(
            target_asset,
            asset_type,
            variant_id,
            is_favorited,
            generation_type,
            source,
        )
        return script

    def _set_variant_favorite_for_asset(
        self,
        target_asset: Any,
        asset_type: str,
        variant_id: str,
        is_favorited: bool,
        generation_type: Optional[str],
        source: str,
    ) -> Any:
        """Set favorite state on an already-resolved canonical owner."""

        found = False

        if asset_type == "character":
            if generation_type == "reference_sheet":
                containers = (target_asset.reference_sheet,)
            elif generation_type == "full_body":
                containers = (
                    target_asset.full_body,
                    target_asset.full_body_asset,
                    target_asset.reference_sheet,
                )
            elif generation_type == "three_view":
                containers = (
                    target_asset.three_views,
                    target_asset.three_view_asset,
                )
            elif generation_type == "headshot":
                containers = (
                    target_asset.head_shot,
                    target_asset.headshot_asset,
                )
            else:
                containers = (
                    target_asset.reference_sheet,
                    target_asset.full_body,
                    target_asset.three_views,
                    target_asset.head_shot,
                    target_asset.full_body_asset,
                    target_asset.three_view_asset,
                    target_asset.headshot_asset,
                )
            for container in containers:
                found = self._set_variant_favorite(container, variant_id, is_favorited) or found
        elif asset_type in {"scene", "prop"}:
            for container in (
                getattr(target_asset, "image", None),
                target_asset.image_asset,
            ):
                found = self._set_variant_favorite(container, variant_id, is_favorited) or found
        elif asset_type == "storyboard_frame":
            for container in (
                target_asset.rendered_image_asset,
                target_asset.image_asset,
            ):
                found = self._set_variant_favorite(container, variant_id, is_favorited) or found

        if not found:
            raise ValueError(f"Variant {variant_id} not found")

        self._save_after_asset_mutation(source)
        return target_asset

    # ============================================================
    # Series Storage & CRUD
    # ============================================================

    def _load_series_data(self) -> Dict[str, Series]:
        if not os.path.exists(self.series_data_file):
            return {}
        try:
            with open(self.series_data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                series_store = {k: Series(**v) for k, v in data.items()}
            migrated = any(
                (raw.get("model_settings") or {}) != series.model_settings.model_dump()
                or raw.get("model_settings_overrides") != series.model_settings_overrides
                for key, series in series_store.items()
                for raw in [data.get(key) or {}]
            )
            if migrated and not self.read_only:
                _atomic_json_dump(
                    self.series_data_file,
                    {k: v.model_dump() for k, v in series_store.items()},
                )
            return series_store
        except Exception as e:
            logger.error(f"Failed to load series data: {e}")
            raise RuntimeError(f"Failed to load series data from {self.series_data_file}") from e

    def _save_series_data_unlocked(self):
        """Save series data without acquiring the lock (caller must hold self._save_lock)."""
        self._assert_writable()
        try:
            _atomic_json_dump(
                self.series_data_file,
                {k: v.model_dump() for k, v in self.series_store.items()},
            )
            self._mark_asset_library_changed()
        except Exception as e:
            logger.error(f"Failed to save series data: {e}")
            raise

    def _save_series_data(self):
        """Save series data with thread lock."""
        self._assert_writable()
        with self._save_lock:
            self._save_series_data_unlocked()

    # ============================================================
    # Global Asset Library Storage (project-independent shared pool)
    # ============================================================

    def _load_library_data(self) -> GlobalAssetLibrary:
        if not os.path.exists(self.library_data_file):
            return GlobalAssetLibrary()
        try:
            with open(self.library_data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return GlobalAssetLibrary(**data)
        except Exception as e:
            logger.error(f"Failed to load library data: {e}")
            raise RuntimeError(f"Failed to load library data from {self.library_data_file}") from e

    def _save_library_data_unlocked(self):
        """Save global library data without acquiring the lock (caller must hold self._save_lock)."""
        self._assert_writable()
        try:
            _atomic_json_dump(
                self.library_data_file,
                self.library_store.model_dump(),
            )
            self._mark_asset_library_changed()
        except Exception as e:
            logger.error(f"Failed to save library data: {e}")
            raise

    def _save_library_data(self):
        """Save global library data with thread lock."""
        self._assert_writable()
        with self._save_lock:
            self._save_library_data_unlocked()

    # ------------------------------------------------------------------
    # Global Asset Library — CRUD + feed channels (EnMotion Core shared pool)
    # ------------------------------------------------------------------
    # These methods are the single source of truth for mutating the
    # project-independent library. Both the /library/assets endpoints and
    # the Playground "录入资产库" flow call them, so the wiring stays
    # consistent. The library is curated/opt-in (anti-bloat): nothing is
    # auto-ingested here.

    def _library_list_for_type(self, asset_type: str) -> List:
        """Return the live list backing the given asset type in the global
        library (so callers can append/iterate). Raises on unknown type."""
        if asset_type == "character":
            return self.library_store.characters
        elif asset_type == "scene":
            return self.library_store.scenes
        elif asset_type == "prop":
            return self.library_store.props
        raise ValueError(f"Invalid asset type: {asset_type}")

    def _find_library_asset(self, asset_type: str, asset_id: str):
        """Locate a global library asset by (type, id). Raises ValueError
        when the type is invalid or the id is absent."""
        target_list = self._library_list_for_type(asset_type)
        asset = next((a for a in target_list if a.id == asset_id), None)
        if asset is None:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in library")
        return asset

    @staticmethod
    def asset_response_payload(
        asset: Any,
        *,
        source: str,
        source_id: str,
        series_id: Optional[str] = None,
        episode_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Serialize one complete asset plus its read-time ownership context.

        ``source`` metadata is deliberately presentation-only.  It lets every
        asset surface route edits to the canonical owner without copying global
        assets into a Series or Episode's persisted lists.
        """

        payload = asset.model_dump()
        asset_type = (
            "character"
            if isinstance(asset, Character)
            else (
                "scene" if isinstance(asset, Scene) else "prop" if isinstance(asset, Prop) else None
            )
        )
        payload.update(
            asset_type=asset_type,
            source=source,
            source_id=source_id,
            series_id=series_id,
            episode_id=episode_id,
        )
        return payload

    @staticmethod
    def _selected_image_variant_url(image_asset: Optional[ImageAsset]) -> Optional[str]:
        if not image_asset or not image_asset.variants:
            return None
        selected = next(
            (variant for variant in image_asset.variants if variant.id == image_asset.selected_id),
            None,
        )
        return (selected or image_asset.variants[0]).url

    @staticmethod
    def _selected_asset_unit_image_url(asset_unit: Optional[AssetUnit]) -> Optional[str]:
        if not asset_unit or not asset_unit.image_variants:
            return None
        selected = next(
            (
                variant
                for variant in asset_unit.image_variants
                if variant.id == asset_unit.selected_image_id
            ),
            None,
        )
        return (selected or asset_unit.image_variants[0]).url

    def _set_library_primary_image(
        self,
        asset_type: str,
        asset: Any,
        image_url: Optional[str],
        *,
        source_variant: Optional[ImageVariant] = None,
    ) -> None:
        """Update an asset's canonical image container and compatibility alias.

        The public library API accepts a generic ``image_url`` field, while
        runtime assets use type-specific containers.  Keeping this mapping in
        one method prevents Home, Series, and Episode responses from diverging.
        Top-level ``image_url`` remains a synchronized compatibility alias; the
        selected variant in the canonical container is authoritative.  When a
        generated/uploaded source variant is supplied, its identifier and
        metadata are preserved so selection and deletion operate on the same
        persisted record in every view.
        """

        normalized_url = image_url.strip() if isinstance(image_url, str) else None
        normalized_url = normalized_url or None

        if asset_type == "character":
            unit = asset.reference_sheet or AssetUnit()
            asset.reference_sheet = unit
            variants = unit.image_variants
            selected_field = "selected_image_id"
        elif asset_type in ("scene", "prop"):
            unit = asset.image_asset or ImageAsset()
            asset.image_asset = unit
            variants = unit.variants
            selected_field = "selected_id"
        else:
            raise ValueError(f"Invalid asset type: {asset_type}")

        previous_selected_id = getattr(unit, selected_field)
        previous_variant_urls = [variant.url for variant in variants]
        if normalized_url is None:
            variants.clear()
            setattr(unit, selected_field, None)
            # An explicit image removal must not be undone by one of the
            # compatibility fallbacks on the next read.  Older global
            # characters can carry the same primary image in several legacy
            # containers, so clear those aliases together with the canonical
            # reference sheet.
            if asset_type == "character":
                for legacy_unit_name in ("full_body", "three_views", "head_shot"):
                    legacy_unit = getattr(asset, legacy_unit_name, None)
                    if legacy_unit is not None:
                        legacy_unit.image_variants.clear()
                        legacy_unit.selected_image_id = None
                for legacy_asset_name in (
                    "full_body_asset",
                    "three_view_asset",
                    "headshot_asset",
                ):
                    legacy_asset = getattr(asset, legacy_asset_name, None)
                    if legacy_asset is not None:
                        legacy_asset.variants.clear()
                        legacy_asset.selected_id = None
                asset.full_body_image_url = None
                asset.three_view_image_url = None
                asset.headshot_image_url = None
                asset.avatar_url = None
            else:
                legacy_unit = getattr(asset, "image", None)
                if legacy_unit is not None:
                    legacy_unit.image_variants.clear()
                    legacy_unit.selected_image_id = None
        else:
            selected = None
            if source_variant is not None:
                selected = next(
                    (
                        variant
                        for variant in variants
                        if variant.id == source_variant.id and variant.url == normalized_url
                    ),
                    None,
                )
                if selected is None:
                    # A canonical duplicate under another ID makes deletion
                    # leave a stale primary image behind.  Replace same-URL
                    # copies with the exact selected source variant instead.
                    variants[:] = [variant for variant in variants if variant.url != normalized_url]
                    selected = copy.deepcopy(source_variant)
                    if any(
                        variant.id == selected.id and variant.url != selected.url
                        for variant in variants
                    ):
                        selected.id = f"img_{uuid.uuid4().hex[:12]}"
                    variants.append(selected)
            if selected is None:
                selected = next(
                    (variant for variant in variants if variant.url == normalized_url),
                    None,
                )
            if selected is None:
                selected = ImageVariant(
                    id=f"img_{uuid.uuid4().hex[:12]}",
                    url=normalized_url,
                )
                variants.append(selected)
            setattr(unit, selected_field, selected.id)

        if isinstance(unit, AssetUnit) and (
            getattr(unit, selected_field) != previous_selected_id
            or [variant.url for variant in variants] != previous_variant_urls
        ):
            unit.image_updated_at = time.time()
        asset.image_url = normalized_url

    def _synchronize_generated_primary_image(
        self,
        asset_type: str,
        asset: Any,
        generation_type: Optional[str],
    ) -> None:
        """Promote a generator's selected result into the canonical container.

        Character generation still writes several legacy containers.  The
        canonical reference sheet is what all library views consume, so copy
        the selected result immediately before the owning record is saved.
        """

        selected: Optional[ImageVariant] = None
        if asset_type == "character":
            if generation_type == "reference_sheet":
                selected = self._selected_variant(asset.reference_sheet)
            elif generation_type in {"three_view", "headshot"}:
                # The legacy generator writes a generated three-view into the
                # top-level image alias. Restore the existing master before
                # persistence so a derived view cannot become the full-body
                # motion source or enter the master list on restart.
                selected = self._selected_variant(asset.reference_sheet)
                if selected is None:
                    selected = self._selected_variant(asset.full_body_asset)
                if selected is not None:
                    self._set_library_primary_image(
                        "character",
                        asset,
                        selected.url,
                        source_variant=selected,
                    )
                return
            else:
                # ``all`` creates full body first and derives the other views
                # from it.  Keep that master as the primary library image.
                selected = self._selected_variant(asset.full_body_asset)
            if selected is None:
                selected = next(
                    (
                        candidate
                        for candidate in (
                            self._selected_variant(asset.reference_sheet),
                            self._selected_variant(asset.full_body_asset),
                            self._selected_variant(asset.three_view_asset),
                            self._selected_variant(asset.headshot_asset),
                        )
                        if candidate is not None
                    ),
                    None,
                )
        elif asset_type in {"scene", "prop"}:
            selected = self._selected_variant(asset.image_asset)
            if selected is None:
                selected = self._selected_variant(getattr(asset, "image", None))
        else:
            raise ValueError(f"Invalid asset type: {asset_type}")

        if selected is not None:
            self._set_library_primary_image(
                asset_type,
                asset,
                selected.url,
                source_variant=selected,
            )

    def _normalize_library_primary_images(self) -> bool:
        """Migrate legacy global image fields into canonical containers.

        Existing installations may contain global assets created before the
        library API learned about ``reference_sheet`` / ``image_asset``.  The
        migration is idempotent and persisted during startup so old and new
        assets resolve identically in every view.
        """
        return self._normalize_owner_primary_images((self.library_store,))

    def _normalize_owner_primary_images(self, owners: Any) -> bool:
        """Migrate Series and Episode assets to the shared image schema.

        Older generated records persisted selected images in legacy fields.
        Merge those histories without losing prompts/video metadata, prefer the
        persisted top-level selection, and save the migration at startup.
        """

        changed = False
        for owner in owners:
            for asset_type, assets in (
                ("character", owner.characters),
                ("scene", owner.scenes),
                ("prop", owner.props),
            ):
                for asset in assets:
                    before = asset.model_dump()
                    canonical = self._canonical_image_copy(
                        asset,
                        asset_type,
                        include_character_derived=False,
                    )
                    if canonical.variants:
                        if asset_type == "character":
                            unit = asset.reference_sheet or AssetUnit()
                            asset.reference_sheet = unit
                            unit.image_variants = canonical.variants
                            unit.selected_image_id = canonical.selected_id
                            selected_url = self._selected_variant_url(unit)
                        else:
                            asset.image_asset = canonical
                            selected_url = self._selected_variant_url(canonical)
                        asset.image_url = selected_url
                    if asset.model_dump() != before:
                        changed = True
        return changed

    def _library_primary_image_url(self, asset_type: str, asset: Any) -> Optional[str]:
        """Read the canonical image with compatibility fallbacks."""

        if asset_type == "character":
            return (
                self._selected_asset_unit_image_url(asset.reference_sheet)
                or asset.image_url
                or asset.full_body_image_url
                or self._selected_image_variant_url(asset.full_body_asset)
            )
        return self._selected_image_variant_url(asset.image_asset) or asset.image_url

    def list_library_assets(self) -> GlobalAssetLibrary:
        """Return the global shared asset pool container (characters /
        scenes / props). Mirrors get_series for the library scope."""
        return self.library_store

    def get_asset_library_overview(self) -> Dict[str, Any]:
        """Return the asset-only data needed by the top-level library.

        The library previously fetched ``/series``, ``/projects/`` and
        ``/library/assets`` independently.  The project response contains the
        original script, every storyboard frame and generation task, none of
        which is used by the library.  Building the overview from the pipeline
        keeps one canonical snapshot while avoiding that unrelated payload.
        """

        def has_assets(owner: Any) -> bool:
            return bool(owner.characters or owner.scenes or owner.props)

        def source_payload(owner: Any, source: str) -> Dict[str, Any]:
            series_id = owner.id if source == "series" else None
            episode_id = owner.id if source == "episode" else None
            return {
                "id": owner.id,
                "title": owner.title,
                "characters": [
                    self.asset_response_payload(
                        asset,
                        source=source,
                        source_id=owner.id,
                        series_id=series_id,
                        episode_id=episode_id,
                    )
                    for asset in owner.characters
                ],
                "scenes": [
                    self.asset_response_payload(
                        asset,
                        source=source,
                        source_id=owner.id,
                        series_id=series_id,
                        episode_id=episode_id,
                    )
                    for asset in owner.scenes
                ],
                "props": [
                    self.asset_response_payload(
                        asset,
                        source=source,
                        source_id=owner.id,
                        series_id=series_id,
                        episode_id=episode_id,
                    )
                    for asset in owner.props
                ],
            }

        def global_payload(assets: List[Any]) -> List[Dict[str, Any]]:
            return [
                self.asset_response_payload(
                    asset,
                    source="global",
                    source_id="global",
                )
                for asset in assets
            ]

        return {
            "series": [
                source_payload(series, "series")
                for series in self.series_store.values()
                if has_assets(series)
            ],
            "projects": [
                source_payload(script, "episode")
                for script in self.scripts.values()
                if not script.series_id and has_assets(script)
            ],
            "global": {
                "characters": global_payload(self.library_store.characters),
                "scenes": global_payload(self.library_store.scenes),
                "props": global_payload(self.library_store.props),
            },
        }

    def create_library_asset(
        self,
        asset_type: str,
        payload: Dict[str, Any],
        *,
        asset_id: Optional[str] = None,
    ):
        """Create a new global library asset of `asset_type`
        ("character" | "scene" | "prop") from a plain payload dict, persist
        it, and return the created asset object.

        Mirrors the series quick-create endpoints
        (create_series_character/scene/prop) but targets the
        project-independent global pool. Tolerates a partial payload (used
        by the Playground录入 flow, which calls this directly rather than
        through a request model). Recognized payload keys: name,
        description, image_url, and persona (characters)."""
        with self._save_lock:
            payload = dict(payload or {})
            name = payload.get("name") or "未命名"
            description = payload.get("description") or ""
            image_url = payload.get("image_url")
            id_prefix = {
                "character": "char",
                "scene": "scene",
                "prop": "prop",
            }.get(asset_type)
            if id_prefix is None:
                raise ValueError(f"Invalid asset type: {asset_type}")
            if asset_id is not None:
                if not re.fullmatch(rf"{id_prefix}_[0-9a-f]{{12}}", asset_id):
                    raise ValueError(f"Invalid deterministic {asset_type} asset id")
                existing = next(
                    (
                        candidate
                        for candidate in self._library_list_for_type(asset_type)
                        if candidate.id == asset_id
                    ),
                    None,
                )
                if existing is not None:
                    return existing
            resolved_asset_id = asset_id or f"{id_prefix}_{uuid.uuid4().hex[:12]}"
            if asset_type == "character":
                asset = Character(
                    id=resolved_asset_id,
                    name=name,
                    description=description,
                    persona=payload.get("persona") or "",
                )
            elif asset_type == "scene":
                asset = Scene(
                    id=resolved_asset_id,
                    name=name,
                    description=description,
                )
            elif asset_type == "prop":
                asset = Prop(
                    id=resolved_asset_id,
                    name=name,
                    description=description,
                )
            if image_url:
                self._set_library_primary_image(asset_type, asset, image_url)
            target_list = self._library_list_for_type(asset_type)
            target_list.append(asset)
            try:
                self._save_library_data_unlocked()
            except Exception:
                target_list.remove(asset)
                raise
            return asset

    def update_library_asset(self, asset_type: str, asset_id: str, patch: Dict[str, Any]):
        """Patch attributes of a global library asset and persist. Mirrors
        update_series_asset_attributes — only sets keys that exist on the
        asset, and never touches id/status (use create/delete to manage
        those)."""
        with self._save_lock:
            asset = self._find_library_asset(asset_type, asset_id)
            original = asset.model_copy(deep=True)
            for key, value in (patch or {}).items():
                if key == "image_url":
                    self._set_library_primary_image(asset_type, asset, value)
                    continue
                if hasattr(asset, key) and key not in ("id", "status"):
                    setattr(asset, key, value)
            try:
                self._save_library_data_unlocked()
            except Exception:
                target_list = self._library_list_for_type(asset_type)
                target_list[target_list.index(asset)] = original
                raise
            return asset

    @staticmethod
    def _frame_references_asset(frame: Any, asset_type: str, asset_id: str) -> bool:
        if asset_type == "scene":
            return getattr(frame, "scene_id", None) == asset_id
        if asset_type == "character":
            return asset_id in (getattr(frame, "character_ids", None) or [])
        if asset_type == "prop":
            return asset_id in (getattr(frame, "prop_ids", None) or [])
        return False

    def _scan_source_asset_references(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
    ) -> List[Dict[str, Any]]:
        """Find frame references that resolve to one exact canonical owner.

        Asset ids may exist at Episode, Series, and Global layers.  A raw id
        scan can therefore report false references to a shadowed lower-layer
        asset.  Type conversion uses the same precedence resolver as the
        editor and only blocks when a frame truly resolves to this owner.
        """

        target_asset, _, _, _ = self.find_source_asset(source_kind, source_id, asset_type, asset_id)
        references: List[Dict[str, Any]] = []

        def scan_script(script_id: str, script: Any) -> None:
            resolved_asset, resolved_source = self._find_asset_with_source(
                script, asset_id, asset_type
            )
            expected_source = {
                "project": "script",
                "series": "series",
                "global": "global",
            }[source_kind]
            if resolved_source != expected_source or resolved_asset is not target_asset:
                return
            for frame in getattr(script, "frames", None) or []:
                if self._frame_references_asset(frame, asset_type, asset_id):
                    references.append(
                        {
                            "reference_type": "storyboard",
                            "owner_kind": "project",
                            "owner_id": script_id,
                            "owner_title": getattr(script, "title", None),
                            "frame_id": getattr(frame, "id", None),
                        }
                    )
            for task in getattr(script, "video_tasks", None) or []:
                if getattr(task, "asset_id", None) != asset_id:
                    continue
                references.append(
                    {
                        "reference_type": "generation_task",
                        "owner_kind": "project",
                        "owner_id": script_id,
                        "owner_title": getattr(script, "title", None),
                        "task_id": getattr(task, "id", None),
                        "task_status": getattr(task, "status", None),
                    }
                )

        if source_kind == "project":
            scan_script(source_id, self.scripts[source_id])
        elif source_kind == "series":
            for script_id, script in (self.scripts or {}).items():
                if getattr(script, "series_id", None) == source_id:
                    scan_script(script_id, script)
            series = self.series_store[source_id]
            for frame in getattr(series, "frames", None) or []:
                if self._frame_references_asset(frame, asset_type, asset_id):
                    references.append(
                        {
                            "reference_type": "storyboard",
                            "owner_kind": "series",
                            "owner_id": source_id,
                            "owner_title": getattr(series, "title", None),
                            "frame_id": getattr(frame, "id", None),
                        }
                    )
        else:
            for script_id, script in (self.scripts or {}).items():
                scan_script(script_id, script)

        # Desktop tasks live only in memory. Server mode mirrors these in the
        # durable jobs table and adds those rows at the API boundary, where the
        # authenticated workspace database is available.
        for task_store in (
            getattr(self, "asset_generation_tasks", {}),
            getattr(self, "video_generation_tasks", {}),
        ):
            for task_id, task in (task_store or {}).items():
                if task.get("asset_id") != asset_id or task.get("asset_type") != asset_type:
                    continue
                if not self._transient_task_targets_source_asset(
                    task, source_kind, source_id, asset_type, asset_id, target_asset
                ):
                    continue
                references.append(
                    {
                        "reference_type": "generation_task",
                        "owner_kind": source_kind,
                        "owner_id": source_id,
                        "task_id": task_id,
                        "task_status": task.get("status"),
                    }
                )

        unique: List[Dict[str, Any]] = []
        seen: set[Tuple[Any, ...]] = set()
        for reference in references:
            key = (
                reference.get("reference_type"),
                reference.get("owner_kind"),
                reference.get("owner_id"),
                reference.get("frame_id"),
                reference.get("task_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(reference)
        return unique

    def _transient_task_targets_source_asset(
        self,
        task: Dict[str, Any],
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        target_asset: Any,
    ) -> bool:
        """Return whether transient task bookkeeping points at one owner."""

        task_source = task.get("asset_source")
        task_owner = task.get("script_id")
        if source_kind == "series" and task.get("is_series"):
            return task_owner == source_id
        if source_kind == "global" and (task.get("is_global") or task_source == "global"):
            return task_owner == "global" or task.get("asset_is_global_level") is True

        script = self.scripts.get(task_owner or "")
        if script is None:
            return (
                source_kind == "project"
                and task_owner == source_id
                and task_source
                in {
                    None,
                    "script",
                }
            )
        resolved_asset, resolved_source = self._find_asset_with_source(script, asset_id, asset_type)
        expected_source = {
            "project": "script",
            "series": "series",
            "global": "global",
        }[source_kind]
        if resolved_asset is not target_asset or resolved_source != expected_source:
            return False
        if source_kind == "project":
            return script.id == source_id
        if source_kind == "series":
            return script.series_id == source_id
        return True

    def source_asset_delete_impact(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
    ) -> Dict[str, Any]:
        """Return a stable, user-facing impact preview for an exact owner."""

        asset, _, _, _ = self.find_source_asset(source_kind, source_id, asset_type, asset_id)
        references = self._scan_source_asset_references(
            source_kind, source_id, asset_type, asset_id
        )
        # A project/episode or series-owned asset is itself part of that
        # owner's reusable asset set, even when no storyboard frame currently
        # points at it. Keep this delete-only relationship out of the shared
        # reference scanner because metadata/type edits must remain possible.
        if source_kind in {"project", "series"}:
            owner = (
                self.scripts[source_id]
                if source_kind == "project"
                else self.series_store[source_id]
            )
            references.insert(
                0,
                {
                    "reference_type": source_kind,
                    "owner_kind": source_kind,
                    "owner_id": source_id,
                    "owner_title": getattr(owner, "title", None),
                },
            )
        return {
            "source_kind": source_kind,
            "source_id": source_id,
            "asset_type": asset_type,
            "asset_id": asset_id,
            "asset_name": getattr(asset, "name", ""),
            "references": references,
            "reference_count": len(references),
            "has_references": bool(references),
        }

    def delete_source_asset(
        self,
        source_kind: str,
        source_id: str,
        asset_type: str,
        asset_id: str,
        *,
        force: bool = False,
    ) -> Any:
        """Delete one exact canonical asset and clean every dangling pointer.

        Home cards carry their canonical owner, so this deliberately avoids
        fallback resolution while choosing the item to remove. After removal,
        episode references are preserved only when the same id resolves to a
        lower-precedence fallback; otherwise frames and generation task links
        are removed before all affected JSON stores are persisted.
        """

        with self._save_lock:
            target_asset, storage_source, _, _ = self.find_source_asset(
                source_kind, source_id, asset_type, asset_id
            )
            references = self.source_asset_delete_impact(
                source_kind, source_id, asset_type, asset_id
            )["references"]
            if references and not force:
                raise LibraryAssetInUseError(asset_type, asset_id, references)

            owner = (
                self.scripts[source_id]
                if source_kind == "project"
                else self.series_store[source_id] if source_kind == "series" else self.library_store
            )
            if source_kind == "project":
                affected_scripts = [self.scripts[source_id]]
            elif source_kind == "series":
                affected_scripts = [
                    script for script in self.scripts.values() if script.series_id == source_id
                ]
            else:
                affected_scripts = list(self.scripts.values())

            transient_asset_tasks = {
                task_id
                for task_id, task in self.asset_generation_tasks.items()
                if task.get("asset_id") == asset_id
                and task.get("asset_type") == asset_type
                and self._transient_task_targets_source_asset(
                    task,
                    source_kind,
                    source_id,
                    asset_type,
                    asset_id,
                    target_asset,
                )
            }
            transient_video_tasks = {
                task_id
                for task_id, task in self.video_generation_tasks.items()
                if task.get("asset_id") == asset_id
                and task.get("asset_type") == asset_type
                and self._transient_task_targets_source_asset(
                    task,
                    source_kind,
                    source_id,
                    asset_type,
                    asset_id,
                    target_asset,
                )
            }
            owned_video_ids = self._embedded_asset_video_ids(target_asset)

            owner_assets = self._asset_list_for_owner(owner, asset_type)
            owner_assets[:] = [asset for asset in owner_assets if asset is not target_asset]

            resolved_key = f"{asset_type}s" if asset_type != "prop" else "props"
            for script in affected_scripts:
                resolved = self.resolve_episode_assets(script)
                fallback_exists = any(asset.id == asset_id for asset in resolved[resolved_key])
                self._detach_video_tasks(
                    script,
                    lambda task, fallback=fallback_exists: (
                        task.id in owned_video_ids or (not fallback and task.asset_id == asset_id)
                    ),
                )
                if not fallback_exists:
                    for frame in script.frames:
                        if asset_type == "character":
                            frame.character_ids = [
                                item for item in frame.character_ids if item != asset_id
                            ]
                        elif asset_type == "scene" and frame.scene_id == asset_id:
                            frame.scene_id = ""
                        elif asset_type == "prop":
                            frame.prop_ids = [item for item in frame.prop_ids if item != asset_id]
                script.updated_at = time.time()

            for task_id in transient_asset_tasks:
                self.asset_generation_tasks.pop(task_id, None)
            for task_id in transient_video_tasks:
                self.video_generation_tasks.pop(task_id, None)

            self._save_data()
            if storage_source == "series":
                self._save_series_data_unlocked()
            elif storage_source == "global":
                self._save_library_data_unlocked()
            return target_asset

    def _scan_library_asset_references(
        self, asset_type: str, asset_id: str
    ) -> List[Dict[str, Any]]:
        """Find every storyboard frame (across all projects and series) that
        references the given asset id through the type-appropriate field:
        scene -> frame.scene_id, character -> frame.character_ids,
        prop -> frame.prop_ids. Returns a list of referrer descriptors
        (empty when nothing references it). Used by delete_library_asset for
        design Q2 reference integrity.

        Note: Series currently hold no frames of their own (their frames live
        in episode Scripts, which are in self.scripts), so the series loop is
        a defensive no-op today via getattr — kept so the scan stays correct
        if Series ever gains a frames list."""
        return self._scan_source_asset_references("global", "global", asset_type, asset_id)

    def delete_library_asset(self, asset_type: str, asset_id: str, force: bool = False) -> None:
        """Hard-delete a global library asset.

        Design Q2 (reference integrity): unless ``force`` is True, scan all
        project/series storyboard frames first; if any still reference this
        asset (scene_id / character_ids / prop_ids) the delete is refused via
        ``LibraryAssetInUseError`` (API maps to HTTP 409 and lists referrers).
        With ``force=True`` the asset is removed anyway, leaving those frame
        references dangling (the asset resolver simply drops the unknown id).

        Raises ValueError when the asset (or asset type) is absent — this is
        checked BEFORE the reference scan so a missing id still maps to 404."""
        with self._save_lock:
            target_list = self._library_list_for_type(asset_type)
            if not any(a.id == asset_id for a in target_list):
                raise ValueError(f"Asset {asset_id} of type {asset_type} not found in library")
            if not force:
                refs = self._scan_library_asset_references(asset_type, asset_id)
                if refs:
                    raise LibraryAssetInUseError(asset_type, asset_id, refs)
            kept = [a for a in target_list if a.id != asset_id]
            if asset_type == "character":
                self.library_store.characters = kept
            elif asset_type == "scene":
                self.library_store.scenes = kept
            else:  # prop
                self.library_store.props = kept
            try:
                self._save_library_data_unlocked()
            except Exception:
                if asset_type == "character":
                    self.library_store.characters = target_list
                elif asset_type == "scene":
                    self.library_store.scenes = target_list
                else:
                    self.library_store.props = target_list
                raise

    def promote_asset_to_library(
        self, source_kind: str, source_id: str, asset_type: str, asset_id: str
    ):
        """Deep-copy an asset from a Project (episode) or Series into the
        global library with a fresh id, persist, and return the new asset.

        Reuses the import_assets_from_series deepcopy + new-uuid pattern.
        The source asset is left intact (D1 活引用: promotion is additive;
        fork-on-use of the original is a documented follow-up, design Q3).
        `source_kind` ∈ {"project", "series"}."""
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        with self._save_lock:
            if source_kind == "series":
                container = self.series_store.get(source_id)
                if not container:
                    raise ValueError("Source series not found")
            elif source_kind == "project":
                container = self.scripts.get(source_id)
                if not container:
                    raise ValueError("Source project not found")
            else:
                raise ValueError(f"Invalid source kind: {source_kind}")

            if asset_type == "character":
                src_list = container.characters
            elif asset_type == "scene":
                src_list = container.scenes
            else:  # prop
                src_list = container.props
            source_asset = next((a for a in src_list if a.id == asset_id), None)
            if source_asset is None:
                raise ValueError(
                    f"Asset {asset_id} of type {asset_type} not found in {source_kind} {source_id}"
                )

            new_asset = copy.deepcopy(source_asset)
            new_asset.id = str(uuid.uuid4())
            effective_url = self._library_primary_image_url(asset_type, new_asset)
            if effective_url:
                self._set_library_primary_image(asset_type, new_asset, effective_url)
            target_list = self._library_list_for_type(asset_type)
            target_list.append(new_asset)
            try:
                self._save_library_data_unlocked()
            except Exception:
                target_list.remove(new_asset)
                raise
            return new_asset

    def fork_library_asset_to_project(self, script_id: str, asset_type: str, library_asset_id: str):
        """Deep-copy a *global library* asset into a project's local asset list
        with a fresh id, persist the project, and return the new (now
        project-owned) asset.

        This is the inverse direction of promote_asset_to_library and the
        "按需 fork" of design Q3: under D1 活引用 semantics a project references
        shared library assets live; forking materializes an independent,
        editable local copy so subsequent edits no longer touch the shared
        original. The source library asset is left intact (additive).

        Raises ValueError when the project, asset type, or library asset is
        absent. ``asset_type`` ∈ {"character", "scene", "prop"}."""
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        with self._save_lock:
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError(f"Project not found: {script_id}")
            # _find_library_asset raises ValueError when the id/type is absent.
            source_asset = self._find_library_asset(asset_type, library_asset_id)
            new_asset = copy.deepcopy(source_asset)
            prefix = {"character": "char", "scene": "scene", "prop": "prop"}[asset_type]
            new_asset.id = f"{prefix}_{uuid.uuid4().hex[:12]}"
            owner: Any = script
            if script.series_id:
                owner = self.series_store.get(script.series_id)
                if owner is None:
                    raise ValueError("Parent series not found")
            self._asset_list(owner, asset_type).append(new_asset)
            owner.updated_at = time.time()
            if script.series_id:
                self._save_series_data_unlocked()
            else:
                self._save_data()
            return new_asset

    def create_series(
        self,
        title: str,
        description: str = "",
        workflow_mode: str = "i2v_legacy",
        content_mode: str = "scripted",
        default_generation_mode: str = "r2v",
        model_settings: Optional[ModelSettings] = None,
        prompt_config: Optional[PromptConfig] = None,
    ) -> Series:
        """Create a new Series."""
        with self._save_lock:
            selected_settings = model_settings or ModelSettings()
            selected_overrides = (
                canonical_model_setting_overrides(selected_settings.model_fields_set)
                if model_settings is not None
                else []
            )
            series = Series(
                id=str(uuid.uuid4()),
                title=title,
                description=description,
                workflow_mode=workflow_mode,
                content_mode=content_mode,
                default_generation_mode=default_generation_mode,
                model_settings=selected_settings,
                model_settings_overrides=selected_overrides,
                prompt_config=copy.deepcopy(prompt_config or PromptConfig()),
                created_at=time.time(),
                updated_at=time.time(),
            )
            self.series_store[series.id] = series
            self._save_series_data_unlocked()
            return series

    def get_series(self, series_id: str) -> Optional[Series]:
        return self.series_store.get(series_id)

    def list_series(self) -> List[Series]:
        return list(self.series_store.values())

    def update_series(self, series_id: str, updates: Dict[str, Any]) -> Series:
        """Update Series fields (title, description, etc.)."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            for key, value in updates.items():
                if hasattr(series, key) and key not in ("id", "created_at", "episode_ids"):
                    if key == "art_direction" and isinstance(value, dict):
                        value = ArtDirection(**value)
                    elif key == "model_settings":
                        value = (
                            value
                            if isinstance(value, ModelSettings)
                            else ModelSettings.model_validate(value)
                        )
                        changed_fields = [
                            field
                            for field in canonical_model_setting_overrides(value.model_dump())
                            if getattr(series.model_settings, field) != getattr(value, field)
                        ]
                        series.model_settings_overrides = canonical_model_setting_overrides(
                            [
                                *series.model_settings_overrides,
                                *changed_fields,
                            ]
                        )
                    setattr(series, key, value)
            series.updated_at = time.time()
            self.series_store[series_id] = series
            self._save_series_data_unlocked()
            return series

    def delete_series(self, series_id: str, *, delete_episodes: bool = False) -> List[Script]:
        """Delete a Series, optionally deleting every episode it owns.

        The historical default keeps episode projects and merely detaches
        them. ``delete_episodes=True`` is the explicit destructive operation
        used by the "delete entire series" UI. It removes both episodes listed
        by the series and any project whose ``series_id`` still points at the
        series, covering stale-but-recoverable association data.
        """
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")

            if delete_episodes:
                episode_ids = list(dict.fromkeys(series.episode_ids))
                listed_ids = set(episode_ids)
                episode_ids.extend(
                    sorted(
                        script_id
                        for script_id, script in self.scripts.items()
                        if script.series_id == series_id and script_id not in listed_ids
                    )
                )
                deleted_episodes = [
                    self.scripts[episode_id]
                    for episode_id in episode_ids
                    if episode_id in self.scripts
                ]

                for episode in deleted_episodes:
                    del self.scripts[episode.id]
                del self.series_store[series_id]

                try:
                    self._save_data()
                    self._save_series_data_unlocked()
                except Exception:
                    # Keep memory and both JSON stores aligned if either
                    # atomic persistence step rejects the cascade.
                    for episode in deleted_episodes:
                        self.scripts[episode.id] = episode
                    self.series_store[series_id] = series
                    try:
                        self._save_data()
                        self._save_series_data_unlocked()
                    except Exception:
                        logger.exception(
                            "Failed to restore series %s after cascade persistence failure",
                            series_id,
                        )
                    raise
                return deleted_episodes

            # Disassociate episodes
            for ep_id in series.episode_ids:
                script = self.scripts.get(ep_id)
                if script:
                    script.series_id = None
                    script.episode_number = None
            self._save_data()
            del self.series_store[series_id]
            self._save_series_data_unlocked()
            return []

    def add_episode_to_series(
        self, series_id: str, script_id: str, episode_number: Optional[int] = None
    ) -> Series:
        """Add an existing Script/Project as an Episode to a Series."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            # If script already belongs to another series, remove it from the old one
            if script.series_id and script.series_id != series_id:
                old_series = self.series_store.get(script.series_id)
                if old_series and script_id in old_series.episode_ids:
                    old_series.episode_ids.remove(script_id)
            if script_id not in series.episode_ids:
                series.episode_ids.append(script_id)
            script.series_id = series_id
            script.episode_number = episode_number or len(series.episode_ids)
            series.updated_at = time.time()
            self._promote_episode_assets_to_series(script, series, match_by_name=False)
            # Persist the canonical asset owner before clearing the episode copy.
            self._save_series_data_unlocked()
            self._save_data()
            return series

    def remove_episode_from_series(self, series_id: str, script_id: str) -> Series:
        """Remove an Episode from a Series (does not delete the project)."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            if script_id in series.episode_ids:
                series.episode_ids.remove(script_id)
            script = self.scripts.get(script_id)
            if script:
                script.series_id = None
                script.episode_number = None
            series.updated_at = time.time()
            self._save_data()
            self._save_series_data_unlocked()
            return series

    # ─────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────

    def get_series_episodes(self, series_id: str) -> List[Script]:
        """Get all Episodes belonging to a Series, in order."""
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")
        episodes = []
        for ep_id in series.episode_ids:
            script = self.scripts.get(ep_id)
            if script:
                episodes.append(script)
        return episodes

    def resolve_series_assets(self, series: Series) -> Dict[str, List]:
        """Merge Series-owned assets over the global library by asset id.

        The returned lists contain live model references for read/generation
        use only.  Global fallbacks are never appended to ``series`` itself,
        so serializing or saving the Series cannot accidentally duplicate
        project-independent assets into ``series.json``.
        """

        series_char_ids = {asset.id for asset in series.characters}
        series_scene_ids = {asset.id for asset in series.scenes}
        series_prop_ids = {asset.id for asset in series.props}
        return {
            "characters": list(series.characters)
            + [asset for asset in self.library_store.characters if asset.id not in series_char_ids],
            "scenes": list(series.scenes)
            + [asset for asset in self.library_store.scenes if asset.id not in series_scene_ids],
            "props": list(series.props)
            + [asset for asset in self.library_store.props if asset.id not in series_prop_ids],
        }

    def resolve_episode_assets(
        self, episode: Script, series: Optional[Series] = None
    ) -> Dict[str, List]:
        """Merge Episode-local assets with Series shared assets and the
        project-independent global asset library. Priority by ID:
        Episode > Series > Global (local always wins). The global library
        is the lowest layer and applies to every project, with or without
        a parent series. When the global library is empty this behaves
        identically to the previous two-layer (Episode/Series) merge."""
        if not series:
            # Auto-lookup series if episode has series_id
            if episode.series_id:
                series = self.series_store.get(episode.series_id)
        if not series:
            # No parent series — episode-local assets sit on top of the
            # global library (lowest layer). With an empty library this
            # yields the episode's own assets (back-compat).
            ep_char_ids = {c.id for c in episode.characters}
            ep_scene_ids = {s.id for s in episode.scenes}
            ep_prop_ids = {p.id for p in episode.props}
            return {
                "characters": list(episode.characters)
                + [c for c in self.library_store.characters if c.id not in ep_char_ids],
                "scenes": list(episode.scenes)
                + [s for s in self.library_store.scenes if s.id not in ep_scene_ids],
                "props": list(episode.props)
                + [p for p in self.library_store.props if p.id not in ep_prop_ids],
            }
        # Build lookup by ID for episode-local assets
        ep_char_ids = {c.id for c in episode.characters}
        ep_scene_ids = {s.id for s in episode.scenes}
        ep_prop_ids = {p.id for p in episode.props}

        merged_characters = list(episode.characters) + [
            c for c in series.characters if c.id not in ep_char_ids
        ]
        merged_scenes = list(episode.scenes) + [
            s for s in series.scenes if s.id not in ep_scene_ids
        ]
        merged_props = list(episode.props) + [p for p in series.props if p.id not in ep_prop_ids]

        # Fold the global library underneath as the lowest layer — only
        # ids absent from both the Episode and Series layers. No-op when
        # the library is empty (back-compat).
        merged_char_ids = {c.id for c in merged_characters}
        merged_scene_ids = {s.id for s in merged_scenes}
        merged_prop_ids = {p.id for p in merged_props}

        merged_characters += [
            c for c in self.library_store.characters if c.id not in merged_char_ids
        ]
        merged_scenes += [s for s in self.library_store.scenes if s.id not in merged_scene_ids]
        merged_props += [p for p in self.library_store.props if p.id not in merged_prop_ids]

        return {
            "characters": merged_characters,
            "scenes": merged_scenes,
            "props": merged_props,
        }

    # ============================================================
    # File Import & Episode Splitting
    # ============================================================

    def import_file_and_split(self, text: str, suggested_episodes: int = 1) -> List[Dict]:
        """Split text into episodes using LLM. Returns episode preview data."""
        return self.script_processor.split_into_episodes(text, suggested_episodes)

    def create_series_from_import(
        self,
        title: str,
        text: str,
        episodes_data: List[Dict],
        description: str = "",
        model_settings: Optional[ModelSettings] = None,
        prompt_config: Optional[PromptConfig] = None,
    ) -> Dict:
        """Create a Series with Episodes from import data.
        episodes_data: list of dicts with episode_number, title, start_marker, end_marker."""
        # Create the Series (already acquires lock internally)
        series = self.create_series(
            title,
            description,
            model_settings=model_settings,
            prompt_config=prompt_config,
        )

        # Split text into episode chunks based on markers
        episode_texts = self._split_text_by_markers(text, episodes_data)

        with self._save_lock:
            # Create Episode (Script) for each chunk
            created_episodes = []
            for idx, ep_data in enumerate(episodes_data):
                ep_text = episode_texts[idx] if idx < len(episode_texts) else ""
                ep_title = ep_data.get("title", f"第{idx+1}集")
                episode_number = ep_data.get("episode_number", idx + 1)

                # Create draft script (no LLM analysis yet — user can trigger later)
                script = self.script_processor.create_draft_script(ep_title, ep_text)
                script.series_id = series.id
                script.episode_number = episode_number
                script.model_settings = copy.deepcopy(series.model_settings)
                script.model_settings_overrides = []
                self.scripts[script.id] = script

                series.episode_ids.append(script.id)
                created_episodes.append(
                    {
                        "id": script.id,
                        "title": ep_title,
                        "episode_number": episode_number,
                        "text_length": len(ep_text),
                    }
                )

            self._save_data()
            self._save_series_data_unlocked()

        return {
            "series": series.model_dump(),
            "episodes": created_episodes,
        }

    def _split_text_by_markers(self, text: str, episodes_data: List[Dict]) -> List[str]:
        """Split text into chunks using start/end markers from LLM.
        Searches sequentially to avoid overlapping chunks."""
        chunks = []
        search_from = 0  # Track position to avoid overlap

        for ep in episodes_data:
            start_marker = ep.get("start_marker", "")
            end_marker = ep.get("end_marker", "")

            start_idx = search_from
            end_idx = len(text)

            if start_marker:
                found = text.find(start_marker, search_from)
                if found >= 0:
                    start_idx = found

            if end_marker:
                found = text.find(end_marker, start_idx)
                if found >= 0:
                    end_idx = found + len(end_marker)

            chunks.append(text[start_idx:end_idx])
            search_from = end_idx  # Next episode starts after this one

        # Fallback: if markers produced empty/overlapping chunks, do equal split
        if not chunks or all(len(c.strip()) == 0 for c in chunks):
            chunk_size = max(1, len(text) // len(episodes_data))
            chunks = []
            for i in range(len(episodes_data)):
                start = i * chunk_size
                end = start + chunk_size if i < len(episodes_data) - 1 else len(text)
                chunks.append(text[start:end])

        return chunks

    # ============================================================
    # Series Asset Operations
    # ============================================================

    def _find_series_asset(self, series_id: str, asset_id: str, asset_type: str):
        """Find an asset in a Series. Returns (series, asset) tuple."""
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in series.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in series.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in series.props if p.id == asset_id), None)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in series")
        return series, target_asset

    def toggle_series_asset_lock(self, series_id: str, asset_id: str, asset_type: str) -> Series:
        """Toggle the locked status of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            target_asset.locked = not target_asset.locked
            self._save_series_data_unlocked()
            return series

    def toggle_series_asset_starred(self, series_id: str, asset_id: str, asset_type: str) -> Series:
        """Toggle the starred (library shortlist) status of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            target_asset.starred = not target_asset.starred
            self._save_series_data_unlocked()
            return series

    def update_series_asset_image(
        self, series_id: str, asset_id: str, asset_type: str, image_url: str
    ) -> Series:
        """Updates the image URL of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            self._set_library_primary_image(asset_type, target_asset, image_url)
            self._save_series_data_unlocked()
            return series

    def update_series_asset_attributes(
        self, series_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]
    ) -> Series:
        """Updates allowlisted presentation attributes of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            validated = _validated_asset_attribute_values(target_asset, asset_type, attributes)
            for key, value in validated.items():
                setattr(target_asset, key, value)
            series.updated_at = time.time()
            self._save_series_data_unlocked()
            return series

    def generate_series_asset(
        self,
        series_id: str,
        asset_id: str,
        asset_type: str,
        style_preset: str = None,
        reference_image_url: str = None,
        style_prompt: str = None,
        generation_type: str = "all",
        prompt: str = None,
        apply_style: bool = True,
        negative_prompt: str = None,
        batch_size: int = 1,
        model_name: str = None,
        task_id: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
    ) -> tuple:
        """Generate a Series asset. Creates an async task like project asset generation.
        Returns (series, task_id)."""
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")

        effective_settings = self._effective_series_model_settings(series)
        t2i_model = model_name or effective_settings.t2i_model
        get_model_spec(t2i_model, IMAGE)
        resolve_model_api_key(t2i_model, IMAGE)

        from .assets import ASPECT_RATIO_TO_SIZE

        if asset_type == "character":
            effective_aspect_ratio = aspect_ratio or effective_settings.character_aspect_ratio
            default_size = "1024x1536"
        elif asset_type == "scene":
            effective_aspect_ratio = aspect_ratio or effective_settings.scene_aspect_ratio
            default_size = "1536x1024"
        elif asset_type == "prop":
            effective_aspect_ratio = aspect_ratio or effective_settings.prop_aspect_ratio
            default_size = "1024x1024"
        else:
            raise ValueError(f"Invalid asset type: {asset_type}")
        effective_size = ASPECT_RATIO_TO_SIZE.get(effective_aspect_ratio, default_size)

        effective_positive_prompt = ""
        effective_negative_prompt = negative_prompt or ""
        resolved_art_dir = series.art_direction
        if isinstance(resolved_art_dir, dict):
            resolved_art_dir = ArtDirection(**resolved_art_dir)
        if apply_style:
            if resolved_art_dir and resolved_art_dir.style_config:
                effective_positive_prompt = resolved_art_dir.style_config.get("positive_prompt", "")
                global_neg = resolved_art_dir.style_config.get("negative_prompt", "")
                if global_neg:
                    effective_negative_prompt = (
                        f"{effective_negative_prompt}, {global_neg}"
                        if effective_negative_prompt
                        else global_neg
                    )
            elif style_prompt:
                effective_positive_prompt = style_prompt
            elif style_preset:
                effective_positive_prompt = f"{style_preset} style"

        task_id = task_id or str(uuid.uuid4())
        _validate_safe_id(task_id, "task_id")
        if task_id in self.asset_generation_tasks:
            raise ValueError("Asset generation task already exists")
        _, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
        previous_status = target_asset.status
        target_asset.status = GenerationStatus.PROCESSING
        self._save_series_data()
        self.asset_generation_tasks[task_id] = {
            "status": "pending",
            "progress": 0,
            "error": None,
            "script_id": series_id,  # reuse field name for task lookup
            "asset_id": asset_id,
            "asset_type": asset_type,
            "asset_source": "series",
            "asset_owner_kind": "series",
            "asset_owner_id": series_id,
            "previous_asset_status": previous_status,
            "created_at": time.time(),
            "is_series": True,
            "params": {
                "style_preset": style_preset,
                "reference_image_url": reference_image_url,
                "effective_positive_prompt": effective_positive_prompt,
                "effective_negative_prompt": effective_negative_prompt,
                "generation_type": generation_type,
                "prompt": prompt,
                "apply_style": apply_style,
                "batch_size": batch_size,
                "t2i_model": t2i_model,
                "effective_size": effective_size,
            },
        }
        return series, task_id

    def generate_global_asset(
        self,
        asset_id: str,
        asset_type: str,
        style_preset: Optional[str] = None,
        reference_image_url: Optional[str] = None,
        style_prompt: Optional[str] = None,
        generation_type: str = "all",
        prompt: Optional[str] = None,
        apply_style: bool = True,
        negative_prompt: Optional[str] = None,
        batch_size: int = 1,
        model_name: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Tuple[Any, str]:
        """Reserve an async generation task owned directly by the global pool."""

        target = self._find_library_asset(asset_type, asset_id)
        selected_model = get_model_spec(model_name or get_selected_model(IMAGE), IMAGE).model_id
        resolve_model_api_key(selected_model, IMAGE)

        from .assets import ASPECT_RATIO_TO_SIZE

        default_aspect = {
            "character": "9:16",
            "scene": "16:9",
            "prop": "1:1",
        }.get(asset_type)
        if default_aspect is None:
            raise ValueError(f"Invalid asset type: {asset_type}")
        default_size = {
            "character": "1024x1536",
            "scene": "1536x1024",
            "prop": "1024x1024",
        }[asset_type]
        effective_size = ASPECT_RATIO_TO_SIZE.get(aspect_ratio or default_aspect, default_size)
        effective_positive_prompt = ""
        if apply_style:
            if style_prompt:
                effective_positive_prompt = style_prompt
            elif style_preset:
                effective_positive_prompt = f"{style_preset} style"

        identifier = task_id or str(uuid.uuid4())
        _validate_safe_id(identifier, "task_id")
        if identifier in self.asset_generation_tasks:
            raise ValueError("Asset generation task already exists")
        previous_status = target.status
        target.status = GenerationStatus.PROCESSING
        self._save_library_data()
        self.asset_generation_tasks[identifier] = {
            "status": "pending",
            "progress": 0,
            "error": None,
            "script_id": "global",
            "asset_id": asset_id,
            "asset_type": asset_type,
            "asset_source": "global",
            "asset_owner_kind": "global",
            "asset_owner_id": "global",
            "asset_is_global_level": True,
            "previous_asset_status": previous_status,
            "created_at": time.time(),
            "is_global": True,
            "params": {
                "style_preset": style_preset,
                "reference_image_url": reference_image_url,
                "effective_positive_prompt": effective_positive_prompt,
                "effective_negative_prompt": negative_prompt or "",
                "generation_type": generation_type,
                "prompt": prompt,
                "apply_style": apply_style,
                "batch_size": batch_size,
                "t2i_model": selected_model,
                "effective_size": effective_size,
                "aspect_ratio": aspect_ratio,
            },
        }
        return target, identifier

    def import_assets_from_series(
        self, target_series_id: str, source_series_id: str, asset_ids: List[str]
    ) -> Tuple[Series, List[str], List[str]]:
        """Deep-copy selected assets from source Series to target Series.
        Returns (target_series, imported_ids, skipped_ids)."""
        with self._save_lock:
            target = self.series_store.get(target_series_id)
            if not target:
                raise ValueError("Target series not found")
            source = self.series_store.get(source_series_id)
            if not source:
                raise ValueError("Source series not found")

            # Build lookup of all source assets
            source_assets = {}
            for c in source.characters:
                source_assets[c.id] = ("character", c)
            for s in source.scenes:
                source_assets[s.id] = ("scene", s)
            for p in source.props:
                source_assets[p.id] = ("prop", p)

            imported_ids = []
            skipped_ids = []
            for aid in asset_ids:
                if aid not in source_assets:
                    skipped_ids.append(aid)
                    continue
                asset_type, asset = source_assets[aid]
                # Deep copy with new ID
                new_asset = copy.deepcopy(asset)
                new_asset.id = str(uuid.uuid4())
                if asset_type == "character":
                    target.characters.append(new_asset)
                elif asset_type == "scene":
                    target.scenes.append(new_asset)
                elif asset_type == "prop":
                    target.props.append(new_asset)
                imported_ids.append(aid)

            target.updated_at = time.time()
            self._save_series_data_unlocked()
            return target, imported_ids, skipped_ids

    def get_effective_prompt(
        self, prompt_type: str, episode: Script, series: Optional[Series] = None
    ) -> str:
        """Three-level fallback: Episode -> Series -> system default."""
        valid_prompt_types = ("storyboard_polish", "video_polish", "storyboard_extraction")
        if prompt_type not in valid_prompt_types:
            raise ValueError(
                f"Invalid prompt_type: {prompt_type}. Must be one of {valid_prompt_types}"
            )
        from .llm import (
            DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
            DEFAULT_STORYBOARD_POLISH_PROMPT,
            DEFAULT_VIDEO_POLISH_PROMPT,
        )

        defaults = {
            "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
            "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
            "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
        }
        override = self._effective_prompt_override(
            prompt_type,
            episode,
            series,
        )
        if override:
            return override
        return defaults.get(prompt_type, "")
