# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT — handler async/sync convention (audited 2026-05-21):
#
# DEFAULT every new endpoint to `def`, NOT `async def`.
#
# FastAPI's contract:
#   - `async def` handlers run on the asyncio event loop. If the body does any
#     blocking I/O (sync HTTP call, json.dump, requests.post, openai sync
#     client, ffmpeg via subprocess.run, etc.) it FREEZES every other request
#     until the blocking call returns — including unrelated GETs the modal
#     uses to load config. Symptom: "modal stuck on 加载配置中...".
#   - `def` handlers get auto-dispatched to anyio's threadpool, so blocking
#     I/O doesn't touch the event loop. Concurrent requests stay responsive.
#
# Use `async def` ONLY when the body has `await` (streaming uploads, real
# asyncio primitives, awaiting `call_next` in middleware, asyncio.to_thread).
#
# The 7 legitimate async endpoints (verified to use await): middleware
# add_cache_control_header, create_project, reparse_project,
# import_file_preview, import_file_confirm, upload_t2i_frame,
# analyze_script_for_styles. All others are `def` for a reason.
# ─────────────────────────────────────────────────────────────────────────────
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, Dict, List, Any
from pathlib import Path
import asyncio
import hashlib
import mimetypes
import time
from concurrent.futures import ThreadPoolExecutor
import json
import os
import tempfile
import threading
import uuid
import logging
import traceback
from .pipeline import (
    AssetTypeChangeConflictError,
    ComicGenPipeline,
    InvalidAssetAttributesError,
    LibraryAssetInUseError,
)
from .models import (
    ArtDirection,
    CameraMovementType,
    PromptConfig,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
)
from .llm import (
    DEFAULT_ENTITY_EXTRACTION_PROMPT,
    DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
    DEFAULT_STORYBOARD_POLISH_PROMPT,
    DEFAULT_STYLE_ANALYSIS_PROMPT,
    DEFAULT_VIDEO_POLISH_PROMPT,
    FrameRefineError,
    ScriptProcessor,
    StyleAnalysisError,
)
from ...utils.oss_utils import (
    OSSImageUploader,
    authoritative_media_reference,
    sign_oss_urls_in_data,
)
from ...utils.uploads import (
    GENERIC_MEDIA_UPLOAD_POLICY,
    IMAGE_UPLOAD_POLICY,
    TEXT_IMPORT_UPLOAD_POLICY,
    extract_import_text,
    read_upload_bytes,
    save_upload_file,
)
from ...utils.deployment_security import (
    cors_middleware_options,
    diagnostic_access_allowed,
    is_diagnostic_path,
)
from ...utils import setup_logging
from ...utils.newapi_models import (
    CHAT,
    IMAGE,
    VIDEO,
    MODEL_API_KEY_FIELDS,
    MissingNewAPIKeyError,
    get_model_spec,
    get_selected_model,
    migrate_legacy_newapi_environment,
    public_model_status,
)
from ..web_runtime.context import get_tenant
from ..web_runtime.pipeline_registry import (
    PipelineProxy,
    WorkspacePipelineRegistry,
)
from ..web_runtime.asset_library_feed import (
    AssetLibraryFeedResponse,
    AssetLibraryFeedResponseV3,
    AssetOrder,
    AssetSort,
    AssetSourceFilter,
    AssetSourceKind,
    AssetType,
    build_asset_library_snapshot,
    query_asset_library_snapshot,
    responsive_asset_library_page,
)
from ..web_runtime.file_lock import interprocess_lock
from ..web_runtime.asset_library_metrics import ASSET_LIBRARY_FEED_LATENCY
from ..web_runtime.workspace_snapshot import (
    WorkspaceSnapshotUnavailable,
    publish_workspace_snapshot,
    read_asset_library_snapshot,
    workspace_ids_with_metadata,
)
from ..hybrid import include_hybrid_mode, workspace_isolation_enabled
from ..server import include_server_mode, server_mode_enabled
from fastapi.responses import JSONResponse
from dotenv import load_dotenv, set_key

app = FastAPI(title="EnMotion Local API")
logger = logging.getLogger(__name__)

# Setup logging to user directory
setup_logging()

# Use absolute path for .env file (api.py is in src/apps/comic_gen/)
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
env_path = os.path.join(_project_root, ".env")


def _load_project_environment(path: str = env_path) -> None:
    """Load development defaults without overriding explicit process config."""
    if os.path.exists(path):
        load_dotenv(path, override=False)


_load_project_environment()

# Mount playground router AFTER .env is loaded (adapters read API keys from env)
from ..playground.api import router as playground_router

app.include_router(playground_router, prefix="/playground")

# In server mode this adds fail-closed session/CSRF middleware plus auth and
# job routes. It is intentionally a no-op for the original desktop runtime.
include_server_mode(app)
include_hybrid_mode(app)

logger.info("EnMotion API initialized")


app.add_middleware(
    CORSMiddleware,
    **cors_middleware_options(),
)


@app.middleware("http")
async def restrict_diagnostic_access(request: Request, call_next):
    """Keep diagnostics local unless the operator explicitly enables them."""

    client_host = request.client.host if request.client else None
    if is_diagnostic_path(request.url.path) and not diagnostic_access_allowed(client_host):
        # A generic 404 avoids advertising deployment internals to remote callers.
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return await call_next(request)


_IMMUTABLE_DERIVATIVE_PATHS = ("/files/derivatives/",)
_CACHEABLE_ASSET_PATHS = ("/files/assets/", "/files/outputs/assets/")
_PRIVATE_MEDIA_TYPES = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def _media_cache_control(path: str) -> str:
    """Return a tenant-safe cache policy for authenticated media.

    Generated asset filenames contain a UUID and are never overwritten.  A
    private browser cache avoids repeatedly transferring the same full-size
    PNG while ``Vary: Cookie`` below prevents reuse across authenticated
    sessions.  Other private media retains the stricter no-store policy.
    """

    if not workspace_isolation_enabled():
        return "public, max-age=86400"
    if path.startswith(_IMMUTABLE_DERIVATIVE_PATHS):
        return "private, max-age=31536000, immutable"
    if path.startswith(_CACHEABLE_ASSET_PATHS):
        return "private, max-age=3600, must-revalidate"
    return "private, no-store"


def _if_none_match_matches(raw_header: str, etag: str) -> bool:
    for candidate in raw_header.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == etag:
            return True
    return False


def _private_media_response(
    request: Request,
    candidate: Path,
    *,
    cache_control: str,
) -> Response:
    """Serve one contained file with conditional and byte-range semantics."""

    try:
        stat_result = candidate.stat()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    media_type = _PRIVATE_MEDIA_TYPES.get(candidate.suffix.casefold())
    if media_type is None:
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    response = FileResponse(
        candidate,
        media_type=media_type,
        stat_result=stat_result,
        headers={
            "Cache-Control": cache_control,
            "X-Content-Type-Options": "nosniff",
        },
    )
    if_none_match = request.headers.get("If-None-Match", "")
    etag = response.headers.get("ETag", "")
    if if_none_match and etag and _if_none_match_matches(if_none_match, etag):
        return Response(
            status_code=304,
            headers={
                "Cache-Control": cache_control,
                "ETag": etag,
                "Last-Modified": response.headers["Last-Modified"],
                "X-Content-Type-Options": "nosniff",
            },
        )
    return response


# Middleware to add cache headers to static files
@app.middleware("http")
async def add_cache_control_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/files/"):
        response.headers["Cache-Control"] = _media_cache_control(request.url.path)
        if workspace_isolation_enabled():
            existing_vary = response.headers.get("Vary")
            vary_values = {
                value.strip()
                for value in (existing_vary or "").split(",")
                if value.strip()
            }
            vary_values.add("Cookie")
            response.headers["Vary"] = ", ".join(sorted(vary_values))
    return response


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    """Correlate safe client diagnostics with server timing logs."""

    request_id = str(getattr(request.state, "request_id", "") or uuid.uuid4().hex)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# Serve only media roots. In desktop mode they remain static mounts. Server mode
# resolves the same URL shapes inside the authenticated user's private workspace.
_MEDIA_FILE_MOUNTS = (
    ("/files/outputs/videos", "output/video", "files_outputs_videos"),
    ("/files/outputs/assets", "output/assets", "files_outputs_assets"),
    ("/files/videos", "output/video", "files_videos"),
    ("/files/video", "output/video", "files_video"),
    ("/files/assets", "output/assets", "files_assets"),
    ("/files/uploads", "output/uploads", "files_uploads"),
    ("/files/storyboard", "output/storyboard", "files_storyboard"),
    ("/files/playground", "output/playground", "files_playground"),
    ("/files/audio", "output/audio", "files_audio"),
    ("/files/export", "output/export", "files_export"),
    ("/files/video_inputs", "output/video_inputs", "files_video_inputs"),
    ("/files/presets", "output/presets", "files_presets"),
    ("/files/derivatives", "output/derivatives", "files_derivatives"),
)

_workspace_pipelines = WorkspacePipelineRegistry()


def _initialize_workspace_read_models() -> None:
    """Run migrations and publish coherent snapshots before serving reads."""

    if not workspace_isolation_enabled():
        return
    for workspace_id in workspace_ids_with_metadata(_workspace_pipelines.workspace_root):
        try:
            safe_id = _workspace_pipelines.validate_workspace_id(workspace_id)
            with interprocess_lock(_workspace_pipelines.lock_path_for(safe_id)):
                # Writer construction is the startup/migration path. Safe GETs
                # only ever construct read-only pipelines from the snapshot
                # published immediately afterwards.
                _workspace_pipelines.get(safe_id)
                publish_workspace_snapshot(safe_id)
        except Exception:
            logger.exception(
                "Could not initialize Asset Library read model workspace=%s",
                workspace_id,
            )


def _register_workspace_read_model_startup(application: FastAPI) -> None:
    """Register startup work through Starlette's router compatibility API.

    FastAPI 0.139 removed ``FastAPI.add_event_handler`` while the underlying
    router still owns startup handler registration.  Using the router keeps
    server-mode startup compatible with the pinned production runtime and with
    the existing server reservation recovery hook.
    """

    application.router.add_event_handler(
        "startup",
        _initialize_workspace_read_models,
    )


if workspace_isolation_enabled():
    _register_workspace_read_model_startup(app)

if not workspace_isolation_enabled():
    os.makedirs("output", exist_ok=True)
    for _url_prefix, _media_dir, _mount_name in _MEDIA_FILE_MOUNTS:
        os.makedirs(_media_dir, exist_ok=True)
        app.mount(_url_prefix, StaticFiles(directory=_media_dir), name=_mount_name)
else:
    from ..server.provider_media import (
        ProviderMediaTokenError,
        resolve_provider_media_token,
    )

    _PRIVATE_MEDIA_ROOTS = {
        "video",
        "assets",
        "uploads",
        "storyboard",
        "playground",
        "audio",
        "export",
        "video_inputs",
        "presets",
        "derivatives",
    }

    def _normalize_private_media_path(file_path: str) -> str:
        normalized = file_path.lstrip("/")
        aliases = {
            "outputs/videos/": "video/",
            "outputs/assets/": "assets/",
            "videos/": "video/",
        }
        for prefix, replacement in aliases.items():
            if normalized.startswith(prefix):
                return replacement + normalized[len(prefix) :]
        return normalized

    @app.api_route(
        "/files/{file_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def get_private_workspace_file(request: Request, file_path: str):
        tenant = get_tenant(required=True)
        assert tenant is not None
        output_root = _workspace_pipelines.output_root_for(tenant.workspace_id)
        relative_path = _normalize_private_media_path(file_path)
        first_component = relative_path.split("/", 1)[0]
        if first_component not in _PRIVATE_MEDIA_ROOTS:
            raise HTTPException(status_code=404, detail="File not found")
        candidate = (output_root / relative_path).resolve()
        if output_root not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return _private_media_response(
            request,
            candidate,
            cache_control=_media_cache_control(f"/files/{file_path}"),
        )

    @app.api_route(
        "/provider-media/{token}/{filename}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def get_provider_media(token: str, filename: str):
        """Serve one short-lived signed image to an external video provider."""

        try:
            candidate = resolve_provider_media_token(token)
        except ProviderMediaTokenError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        if filename != candidate.name:
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(
            candidate,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )


# Unmanaged desktop mode delegates to a singleton. Server and hybrid modes
# resolve an isolated local pipeline from the authenticated workspace context.
pipeline = PipelineProxy(
    ComicGenPipeline({"recover_orphan_tasks": not server_mode_enabled()}),
    _workspace_pipelines,
)


def current_output_root() -> str:
    if not workspace_isolation_enabled():
        return "output"
    tenant = get_tenant(required=True)
    assert tenant is not None
    return str(_workspace_pipelines.output_root_for(tenant.workspace_id))


def workspace_output_path(*parts: str) -> str:
    return os.path.join(current_output_root(), *parts)


@app.post("/desktop/prepare-update")
def prepare_desktop_update() -> Dict[str, Any]:
    """Flush durable workspace state before the native shell replaces binaries."""

    current = pipeline.current()
    active_task_ids: List[str] = []
    for store_name in ("asset_generation_tasks", "video_generation_tasks"):
        for task_id, task in (getattr(current, store_name, {}) or {}).items():
            if str((task or {}).get("status") or "").lower() in {
                "pending",
                "processing",
                "running",
            }:
                active_task_ids.append(str(task_id))
    for script in (getattr(current, "scripts", {}) or {}).values():
        for task in getattr(script, "video_tasks", []) or []:
            if str(getattr(task, "status", "")).lower() in {
                "pending",
                "processing",
                "running",
            }:
                active_task_ids.append(str(getattr(task, "id", "")))

    # Persist every JSON-backed store under the pipeline's existing re-entrant
    # lock. Provider task ids are part of those stores and resume after restart.
    with current._save_lock:
        current._save_data()
        current._save_series_data_unlocked()
        current._save_library_data_unlocked()

    from ...utils import get_user_data_dir

    checkpoint_path = Path(get_user_data_dir()) / "update-checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "prepared_at": int(time.time()),
        "workspace_root": current_output_root(),
        "active_task_ids": sorted(set(filter(None, active_task_ids))),
    }
    temporary = checkpoint_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(checkpoint, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, checkpoint_path)
    return {
        "ready": not active_task_ids,
        "active_task_count": len(set(active_task_ids)),
        "message": (
            "Ready to restart"
            if not active_task_ids
            else "Restart will wait for active local generation tasks"
        ),
    }


def reclaim_deleted_media(deleted_value: Any) -> List[str]:
    """Reclaim media no longer referenced anywhere in this workspace.

    In server mode deletion is deferred until the request's JSON mutation has
    passed the storage quota transaction. Desktop mode keeps the historical
    immediate-delete behaviour.
    """

    from ...utils.media_gc import (
        load_workspace_reference_values,
        reclaim_unreferenced_workspace_media,
    )

    output_root = current_output_root()
    try:
        remaining_values = load_workspace_reference_values(output_root)
    except RuntimeError as exc:
        logger.warning("Skipping media reclamation: %s", exc)
        return []

    delete_callback = None
    if server_mode_enabled():
        tenant = get_tenant(required=True)
        assert tenant is not None
        from ..server.workspace_storage import defer_workspace_file_deletions

        delete_callback = lambda paths: defer_workspace_file_deletions(
            tenant.workspace_id, paths
        )
    return reclaim_unreferenced_workspace_media(
        deleted_value=deleted_value,
        remaining_values=remaining_values,
        output_root=output_root,
        delete_callback=delete_callback,
    )


def enqueue_workspace_job(
    job_type: str,
    payload: Dict[str, Any],
    *,
    job_id: Optional[str] = None,
):
    """Publish work for the authenticated workspace without importing the
    server-only queue stack in desktop mode."""

    tenant = get_tenant(required=True)
    assert tenant is not None
    from ..server.jobs import (
        JobLimitExceededError,
        JobPayloadTooLargeError,
        JobQueueUnavailableError,
        StorageQuotaExceededError,
        enqueue_job,
    )

    try:
        return enqueue_job(
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            job_type=job_type,
            payload=payload,
            job_id=job_id,
        )
    except JobQueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except JobLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except JobPayloadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except StorageQuotaExceededError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


def enqueue_long_workspace_job(job_type: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """Queue a long server operation while retaining desktop route contracts."""

    record = enqueue_workspace_job(job_type, payload)
    # Always return the marker shape, even if an eager/local worker completed
    # between publication and this response. The client will read the durable
    # terminal record from /jobs/{id} and preserve the original route promise.
    return {"task_id": record.id, "status": "queued"}


def require_workspace_script(script_id: str) -> Script:
    """Reject invalid durable submissions before they consume queue capacity."""

    script = pipeline.get_script(script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    return script


def reserve_workspace_jobs(specs: List[Dict[str, Any]]):
    """Atomically reserve capacity for all jobs in one API mutation."""

    tenant = get_tenant(required=True)
    assert tenant is not None
    from ..server.database import get_database
    from ..server.jobs import (
        JobLimitExceededError,
        JobPayloadTooLargeError,
        JobSpec,
        StorageQuotaExceededError,
        reserve_jobs,
    )

    try:
        return reserve_jobs(
            get_database(),
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            specs=[
                JobSpec(
                    job_type=item["job_type"],
                    payload=item["payload"],
                    job_id=item.get("job_id"),
                )
                for item in specs
            ],
        )
    except JobLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except JobPayloadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except StorageQuotaExceededError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


def abandon_workspace_job_reservations(records) -> None:
    if not records:
        return
    from ..server.database import get_database
    from ..server.jobs import abandon_reserved_jobs

    abandon_reserved_jobs(get_database(), job_ids=[record.id for record in records])


def publish_workspace_job_reservations(records):
    from ..server.database import get_database
    from ..server.jobs import JobQueueUnavailableError, publish_reserved_jobs

    try:
        return publish_reserved_jobs(
            get_database(), job_ids=[record.id for record in records]
        )
    except JobQueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _normalise_asset_type_for_job(value: Any) -> Optional[str]:
    """Map generation-specific units back to their canonical asset type."""

    if value in {"character", "full_body", "head_shot", "three_views"}:
        return "character"
    if value in {"scene", "prop"}:
        return str(value)
    return None


def _durable_source_asset_job_references(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
) -> List[Dict[str, Any]]:
    """Return queued/running server jobs that target one canonical asset."""

    if not server_mode_enabled():
        return []

    from sqlalchemy import select

    from ..server.database import get_database
    from ..server.models import GenerationJob

    tenant = get_tenant(required=True)
    assert tenant is not None
    with get_database().session() as session:
        records = list(
            session.scalars(
                select(GenerationJob).where(
                    GenerationJob.workspace_id == tenant.workspace_id,
                    GenerationJob.status.in_(("queued", "running")),
                )
            )
        )

    references: List[Dict[str, Any]] = []
    for record in records:
        payload = dict(record.payload or {})
        if payload.get("asset_id") != asset_id:
            continue
        canonical_type = _normalise_asset_type_for_job(payload.get("asset_type"))
        if canonical_type != asset_type:
            continue

        matches = False
        if record.job_type == "project_asset":
            matches = source_kind == "project" and payload.get("script_id") == source_id
        elif record.job_type == "series_asset":
            matches = source_kind == "series" and payload.get("series_id") == source_id
        elif record.job_type == "global_asset":
            matches = source_kind == "global" and payload.get("source_id") == source_id
        elif record.job_type == "motion_reference":
            script_id = payload.get("script_id")
            script = pipeline.get_script(script_id) if script_id else None
            if script is not None:
                _, resolved_source = pipeline._find_asset_with_source(script, asset_id, asset_type)
                expected_source = {
                    "project": "script",
                    "series": "series",
                    "global": "global",
                }.get(source_kind)
                matches = resolved_source == expected_source
                if source_kind == "project":
                    matches = matches and script.id == source_id
                elif source_kind == "series":
                    matches = matches and script.series_id == source_id

        if not matches:
            continue
        references.append(
            {
                "reference_type": "generation_task",
                "owner_kind": source_kind,
                "owner_id": source_id,
                "task_id": record.id,
                "task_status": record.status,
                "job_type": record.job_type,
                "durable": True,
            }
        )
    return references


def _cancel_queued_asset_jobs(references: List[Dict[str, Any]]) -> None:
    """Prevent queued jobs from recreating media after a forced deletion."""

    if not server_mode_enabled():
        return
    tenant = get_tenant(required=True)
    assert tenant is not None
    from ..server.database import get_database
    from ..server.jobs import cancel_workspace_job_with_record

    database = get_database()
    for reference in references:
        if not reference.get("durable") or reference.get("task_status") != "queued":
            continue
        task_id = reference.get("task_id")
        if task_id:
            cancel_workspace_job_with_record(
                database,
                workspace_id=tenant.workspace_id,
                job_id=str(task_id),
            )


def enforce_upload_quota(path: str) -> None:
    if not server_mode_enabled():
        return
    tenant = get_tenant(required=True)
    assert tenant is not None
    from ..server.database import get_database
    from ..server.quotas import StorageQuotaExceededError, enforce_saved_file_quota

    try:
        enforce_saved_file_quota(
            get_database(), workspace_id=tenant.workspace_id, created_path=path
        )
    except StorageQuotaExceededError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


def require_server_admin(request: Request) -> None:
    """Protect process-global operator settings while preserving desktop mode."""

    if not server_mode_enabled():
        return
    actor = getattr(request.state, "actor", None)
    if actor is None or getattr(actor, "role", None) != "admin":
        # Avoid advertising an operator-only surface to ordinary accounts.
        raise HTTPException(status_code=404, detail="Not found")


def require_server_admin_account(request: Request) -> None:
    """Require an authenticated server-mode Admin account.

    Unlike ordinary operator settings, secret inspection is intentionally not
    available in desktop mode, where there is no authenticated Admin account.
    """

    if not server_mode_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    require_server_admin(request)


@app.get("/debug/config")
def debug_config(request: Request):
    """Diagnostic endpoint to check OSS and path configuration."""
    require_server_admin(request)
    uploader = OSSImageUploader()
    return {
        "oss_configured": uploader.is_configured,
        "oss_bucket_initialized": uploader.bucket is not None,
        "oss_base_path": os.getenv("OSS_BASE_PATH", "enmotion"),
        "output_dir_exists": os.path.exists(current_output_root()),
        "output_contents": (
            os.listdir(current_output_root()) if os.path.exists(current_output_root()) else []
        ),
        "cwd": os.getcwd(),
        "env_vars_present": {
            "OSS_ENDPOINT": bool(os.getenv("OSS_ENDPOINT")),
            "OSS_BUCKET_NAME": bool(os.getenv("OSS_BUCKET_NAME")),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": bool(os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")),
        }
    }

def signed_response(data):
    """Helper to sign OSS URLs in data before returning to frontend.

    Handles Pydantic models, lists of models, and dicts.
    Returns a JSONResponse with signed URLs.
    """
    if data is None:
        return JSONResponse(content=None)

    # Convert Pydantic models to dict
    if hasattr(data, "model_dump"):
        processed_data = data.model_dump()
    elif isinstance(data, list):
        processed_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in data]
    else:
        processed_data = data

    # Check if OSS is configured
    uploader = OSSImageUploader()
    if uploader.is_configured:
        # OSS mode: sign URLs in the data
        processed_data = sign_oss_urls_in_data(processed_data, uploader)

    # Return JSONResponse directly to avoid Pydantic re-validation stripping fields
    return JSONResponse(content=processed_data)


def _serialize_resolved_assets(
    assets: List[Any],
    *,
    episode_asset_ids: Optional[set[str]] = None,
    series_asset_ids: Optional[set[str]] = None,
    episode_id: Optional[str] = None,
    series_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Attach consistent ownership/context metadata to resolved assets."""

    episode_asset_ids = episode_asset_ids or set()
    series_asset_ids = series_asset_ids or set()
    payloads: List[Dict[str, Any]] = []
    for asset in assets:
        if asset.id in episode_asset_ids:
            source = "episode"
            source_id = episode_id or asset.id
        elif asset.id in series_asset_ids:
            source = "series"
            source_id = series_id or asset.id
        else:
            source = "global"
            source_id = "global"
        payloads.append(
            pipeline.asset_response_payload(
                asset,
                source=source,
                source_id=source_id,
                series_id=series_id,
                episode_id=episode_id,
            )
        )
    return payloads


# ============================================================
# Shared Request Models (used by both Project and Series endpoints)
# ============================================================

class GenerateAssetRequest(BaseModel):
    asset_id: str = Field(max_length=128)
    asset_type: str = Field(max_length=32)
    style_preset: str = Field(default="Cinematic", max_length=200)
    reference_image_url: Optional[str] = Field(default=None, max_length=8192)
    style_prompt: Optional[str] = Field(default=None, max_length=50_000)
    generation_type: str = "all"  # 'full_body', 'three_view', 'headshot', 'all', 'reference_sheet'
    prompt: Optional[str] = Field(default=None, max_length=50_000)
    apply_style: bool = True
    negative_prompt: Optional[str] = Field(default=None, max_length=50_000)
    batch_size: int = Field(1, ge=1, le=4)
    model_name: Optional[str] = Field(default=None, max_length=128)
    aspect_ratio: Optional[str] = Field(default=None, max_length=32)
    template_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("model_name")
    @classmethod
    def validate_image_model(cls, value):
        if value is not None:
            get_model_spec(value, IMAGE)
        return value

    @field_validator("generation_type")
    @classmethod
    def validate_generation_type(cls, value):
        supported = {"all", "full_body", "three_view", "headshot", "reference_sheet"}
        if value not in supported:
            raise ValueError(
                f"Unsupported asset generation type {value!r}; choose one of {sorted(supported)}"
            )
        return value

class ToggleLockRequest(BaseModel):
    asset_id: str
    asset_type: str

class UpdateAssetImageRequest(BaseModel):
    asset_id: str
    asset_type: str
    image_url: str

class UpdateAssetAttributesRequest(BaseModel):
    asset_id: str = Field(max_length=128)
    asset_type: str = Field(max_length=32)
    attributes: Dict[str, Any] = Field(max_length=16)


@app.get("/health")
def health_check(request: Request):
    """Lightweight liveness probe used by the Diagnose UI on stuck
    tasks and by external uptime checks. Intentionally cheap: no DB hit and no
    provider call. Filesystem paths and project counts are included only for
    loopback clients or when remote diagnostics are explicitly enabled."""
    response = {
        "ok": True,
        "time": time.time(),
    }
    client_host = request.client.host if request.client else None
    if not server_mode_enabled() and diagnostic_access_allowed(client_host):
        from ...utils import get_log_dir

        log_dir = get_log_dir()
        response.update(
            {
                "log_file": os.path.join(log_dir, "app.log"),
                "log_dir": log_dir,
                # Health is public in server mode and therefore has no tenant
                # context. Never reach through the fail-closed pipeline proxy.
                "studio_projects": (
                    None if server_mode_enabled() else len(getattr(pipeline, "scripts", {}))
                ),
            }
        )
    return response


@app.get("/ready")
def readiness_check():
    """Minimal database/broker readiness for containers and deploy tooling."""

    if not server_mode_enabled():
        return {"ok": True}
    try:
        from redis import Redis
        from sqlalchemy import text as sql_text

        from ..server.database import get_database

        with get_database().session() as session:
            session.execute(sql_text("SELECT 1"))
        client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        client.close()
        return {"ok": True}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"ok": False},
            headers={"Cache-Control": "no-store"},
        )


@app.get("/diagnose/log_tail")
def diagnose_log_tail(request: Request, lines: int = 200):
    """Return the last N lines of the app log + an ERROR/Exception
    summary so the Diagnose UI on stuck tasks can show actual log
    content instead of just a path. Read-only, capped to 1000 lines so
    a runaway client can't hose the process."""
    require_server_admin(request)
    from ...utils import get_log_dir
    capped = max(1, min(int(lines or 200), 1000))
    log_path = os.path.join(get_log_dir(), "app.log")
    if not os.path.exists(log_path):
        return {"path": log_path, "lines": [], "errors": [], "missing": True}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Read all, then tail. App.log is bounded by the rotating
            # handler (≤ 5 MB active) so this is safe.
            all_lines = f.readlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read log: {exc}")
    tail = all_lines[-capped:] if len(all_lines) > capped else all_lines
    error_keywords = ("ERROR", "Exception", "Failed", "Unauthorized", "Traceback")
    errors = [
        ln.rstrip("\n")
        for ln in tail
        if any(k in ln for k in error_keywords)
    ][-30:]
    return {
        "path": log_path,
        "total_lines": len(all_lines),
        "returned_lines": len(tail),
        "lines": [ln.rstrip("\n") for ln in tail],
        "errors": errors,
        "missing": False,
    }


@app.post("/upload")
def upload_file(file: UploadFile = File(...)):
    """Uploads a file and returns its URL (OSS if configured, else local)."""
    try:
        saved = save_upload_file(
            file,
            workspace_output_path("uploads"),
            GENERIC_MEDIA_UPLOAD_POLICY,
        )
        enforce_upload_quota(saved.path)

        # Try uploading to OSS
        oss_url = OSSImageUploader().upload_image(saved.path)
        reference = authoritative_media_reference(
            saved.path, current_output_root(), oss_url
        )
        payload = {"url": reference}
        return signed_response(payload) if oss_url and reference == oss_url else payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UploadAssetRequest(BaseModel):
    upload_type: str  # "full_body" | "head_shot" | "three_views" | "image"
    description: Optional[str] = None  # User-modified description for reverse generation


@app.post("/projects/{script_id}/assets/{asset_type}/{asset_id}/upload")
def upload_asset(
    script_id: str,
    asset_type: str,
    asset_id: str,
    upload_type: str,
    description: Optional[str] = None,
    file: UploadFile = File(...)
):
    """
    Uploads an image as a new variant for an asset.
    The uploaded image is marked as the 'upload source' for reverse generation.

    - asset_type: "character", "scene", or "prop"
    - upload_type: "full_body", "head_shot", "three_views", or "image" (for scene/prop)
    - description: Optional modified description for the asset
    """
    try:
        # 1. Save file locally first
        saved = save_upload_file(file, workspace_output_path("uploads"), IMAGE_UPLOAD_POLICY)
        enforce_upload_quota(saved.path)

        # 2. Upload to OSS
        uploader = OSSImageUploader()
        oss_url = uploader.upload_image(saved.path)
        media_reference = authoritative_media_reference(
            saved.path, current_output_root(), oss_url
        )

        # 3. Update asset with new variant
        updated_script = pipeline.add_uploaded_asset_variant(
            script_id=script_id,
            asset_type=asset_type,
            asset_id=asset_id,
            upload_type=upload_type,
            image_url=media_reference,
            description=description
        )

        if not updated_script:
            raise HTTPException(status_code=404, detail="Script or asset not found")

        return signed_response(updated_script)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error uploading asset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=2_000_000)
    workflow_mode: str = Field(default="r2v", max_length=32)
    # Optional series binding (T9). When set, the new project is created as
    # the next episode of this series (episode_number = current max + 1).
    # Omit for a standalone project — behavior unchanged.
    series_id: Optional[str] = Field(default=None, max_length=128)


@app.post("/projects")
async def create_project(request: CreateProjectRequest, skip_analysis: bool = False):
    """Creates a new project from a novel text.

    When `series_id` is provided the project is bound as the next episode
    of that series; omitting it keeps the standalone-project behavior
    unchanged.
    """
    # asyncio.to_thread propagates the authenticated workspace ContextVar.
    try:
        result = await asyncio.to_thread(
            pipeline.create_project,
            request.title,
            request.text,
            skip_analysis,
            request.workflow_mode,
            request.series_id,
        )
    except (ValueError, MissingNewAPIKeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_project(result.id)


class ReparseProjectRequest(BaseModel):
    text: str = Field(max_length=2_000_000)


class UpdateScriptTextRequest(BaseModel):
    text: str = Field(max_length=2_000_000)


@app.put("/projects/{script_id}/text", response_model=Script)
def update_script_text(script_id: str, request: UpdateScriptTextRequest):
    """Persist `original_text` without re-parsing entities.

    Used by ScriptProcessor's onBlur so typing survives reload/navigation
    without triggering an LLM round-trip. Heavy reparse stays bound to the
    explicit "提取实体" CTA.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    script.original_text = request.text or ""
    script.updated_at = time.time()
    pipeline._save_data()
    return signed_response(script)


@app.put("/projects/{script_id}/reparse")
async def reparse_project(script_id: str, request: ReparseProjectRequest):
    """Re-parses the text for an existing project, replacing all entities."""
    try:
        result = await asyncio.to_thread(pipeline.reparse_project, script_id, request.text)
        return get_project(result.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/extract_preview")
async def extract_preview(script_id: str, request: ReparseProjectRequest):
    """Dry-run entity extraction — returns entities without saving."""
    try:
        result = await asyncio.to_thread(pipeline.extract_preview, script_id, request.text)
        return {
            "characters": [c.dict() for c in result.characters],
            "scenes": [s.dict() for s in result.scenes],
            "props": [p.dict() for p in result.props],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects/", response_model=List[dict])
def list_projects():
    """Lists all projects from backend storage."""
    scripts = list(pipeline.scripts.values())
    return signed_response(scripts)


@app.post("/projects/{script_id}/toggle_starred")
def toggle_project_starred(script_id: str):
    """Toggle the user-starred (featured shortlist) flag on a project."""
    try:
        script = pipeline.toggle_project_starred(script_id)
        return signed_response(script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Series CRUD
# ============================================================

class CreateSeriesRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20_000)
    workflow_mode: str = Field(default="r2v", max_length=32)
    # R2V v2 Phase 6 — content mode (scripted | freeform), defaults
    # scripted (traditional script-first flow).
    content_mode: str = Field(default="scripted", max_length=32)
    # PR-3e (r2v-workflow-v3) — Visual control preference.
    # 'r2v' (节奏优先, default) | 'i2v' (画面优先).
    default_generation_mode: str = Field(default="r2v", max_length=32)


class UpdateSeriesRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=20_000)
    workflow_mode: Optional[str] = Field(default=None, max_length=32)
    content_mode: Optional[str] = Field(default=None, max_length=32)
    default_generation_mode: Optional[str] = Field(default=None, max_length=32)
    # R2V v2: series-level art_direction baseline (Phase 2). Inherits +
    # override flows at the episode level read this as the source of truth.
    art_direction: Optional[ArtDirection] = None


@app.post("/series")
def create_series(request: CreateSeriesRequest):
    """Create a new Series."""
    series = pipeline.create_series(
        request.title,
        request.description,
        request.workflow_mode,
        request.content_mode,
        request.default_generation_mode,
    )
    return signed_response(series)


@app.get("/series")
def list_series():
    """List all Series."""
    series_list = pipeline.list_series()
    return signed_response(series_list)


@app.get("/series/{series_id}")
def get_series(series_id: str):
    """Get Series details including assets and episode list."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    resolved = pipeline.resolve_series_assets(series)
    series_character_ids = {asset.id for asset in series.characters}
    series_scene_ids = {asset.id for asset in series.scenes}
    series_prop_ids = {asset.id for asset in series.props}
    # Include episode summaries
    episodes = pipeline.get_series_episodes(series_id)
    result = series.model_dump()
    result["characters"] = _serialize_resolved_assets(
        resolved["characters"],
        series_asset_ids=series_character_ids,
        series_id=series.id,
    )
    result["scenes"] = _serialize_resolved_assets(
        resolved["scenes"],
        series_asset_ids=series_scene_ids,
        series_id=series.id,
    )
    result["props"] = _serialize_resolved_assets(
        resolved["props"],
        series_asset_ids=series_prop_ids,
        series_id=series.id,
    )
    result["episodes"] = [
        {
            "id": ep.id,
            "title": ep.title,
            "episode_number": ep.episode_number,
            "created_at": ep.created_at,
            "updated_at": ep.updated_at,
        }
        for ep in episodes
    ]
    return signed_response(result)


@app.put("/series/{series_id}")
def update_series(series_id: str, request: UpdateSeriesRequest):
    """Update Series fields. Uses `exclude_unset=True` so explicitly-null
    values (e.g. `{"art_direction": null}` to clear baseline) are honored,
    while fields the client didn't send remain untouched."""
    try:
        updates = request.model_dump(exclude_unset=True)
        series = pipeline.update_series(series_id, updates)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/series/{series_id}")
def delete_series(series_id: str, delete_episodes: bool = False):
    """Delete a Series, optionally including every episode and its media."""
    try:
        series = pipeline.get_series(series_id)
        if not series:
            raise ValueError("Series not found")
        deleted_episodes = pipeline.delete_series(
            series_id, delete_episodes=delete_episodes
        )
        deleted_snapshot = {
            "series": series.model_dump(),
            "episodes": [episode.model_dump() for episode in deleted_episodes],
        }
        reclaimed = reclaim_deleted_media(deleted_snapshot)
        return {
            "status": "deleted",
            "id": series_id,
            "deleted_episode_count": len(deleted_episodes),
            "deleted_episode_ids": [episode.id for episode in deleted_episodes],
            "reclaimed_media_files": len(reclaimed),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AddEpisodeRequest(BaseModel):
    script_id: str
    episode_number: Optional[int] = None


@app.post("/series/{series_id}/episodes")
def add_episode_to_series(series_id: str, request: AddEpisodeRequest):
    """Add an existing project as an episode to a Series."""
    try:
        series = pipeline.add_episode_to_series(series_id, request.script_id, request.episode_number)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/series/{series_id}/episodes/{script_id}")
def remove_episode_from_series(series_id: str, script_id: str):
    """Remove an episode from a Series (does not delete the project)."""
    try:
        series = pipeline.remove_episode_from_series(series_id, script_id)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/series/{series_id}/episodes")
def get_series_episodes(series_id: str):
    """Get all episodes in a Series."""
    try:
        episodes = pipeline.get_series_episodes(series_id)
        return signed_response(episodes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/series/{series_id}/prompt_config")
def get_series_prompt_config(series_id: str):
    """Get Series prompt config with system defaults."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return {
        "prompt_config": series.prompt_config.model_dump(),
        "defaults": {
            "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
            "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
            "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
        },
    }


@app.put("/series/{series_id}/prompt_config")
def update_series_prompt_config(series_id: str, config: PromptConfig):
    """Update Series-level prompt config."""
    try:
        series = pipeline.update_series(series_id, {"prompt_config": config})
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Series Model Settings
# ============================================================

class UpdateModelSettingsRequest(BaseModel):
    chat_model: Optional[str] = None
    t2i_model: Optional[str] = None
    i2i_model: Optional[str] = None
    image_model: Optional[str] = None
    i2v_model: Optional[str] = None
    video_model: Optional[str] = None
    character_aspect_ratio: Optional[str] = None
    scene_aspect_ratio: Optional[str] = None
    prop_aspect_ratio: Optional[str] = None
    storyboard_aspect_ratio: Optional[str] = None

    @field_validator("chat_model")
    @classmethod
    def validate_chat_selection(cls, value):
        if value is not None:
            get_model_spec(value, CHAT)
        return value

    @field_validator("t2i_model", "i2i_model", "image_model")
    @classmethod
    def validate_image_selection(cls, value):
        if value is not None:
            get_model_spec(value, IMAGE)
        return value

    @field_validator("i2v_model", "video_model")
    @classmethod
    def validate_video_selection(cls, value):
        if value is not None:
            get_model_spec(value, VIDEO)
        return value


def _synchronize_model_setting_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(updates)
    image_model = next(
        (normalized.get(key) for key in ("image_model", "t2i_model", "i2i_model") if normalized.get(key)),
        None,
    )
    if image_model:
        normalized.update(image_model=image_model, t2i_model=image_model, i2i_model=image_model)
    video_model = next(
        (normalized.get(key) for key in ("video_model", "i2v_model") if normalized.get(key)),
        None,
    )
    if video_model:
        normalized.update(video_model=video_model, i2v_model=video_model)
    return normalized

@app.get("/series/{series_id}/model_settings")
def get_series_model_settings(series_id: str):
    """Get Series model settings."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series.model_settings.model_dump()


@app.put("/series/{series_id}/model_settings")
def update_series_model_settings(series_id: str, settings: UpdateModelSettingsRequest):
    """Update Series-level model settings."""
    updates = _synchronize_model_setting_updates(
        {k: v for k, v in settings.model_dump().items() if v is not None}
    )
    if not updates:
        series = pipeline.get_series(series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Series not found")
        return series.model_settings.model_dump()
    try:
        current_series = pipeline.get_series(series_id)
        if not current_series:
            raise HTTPException(status_code=404, detail="Series not found")
        ms = current_series.model_settings.__class__.model_validate(
            {**current_series.model_settings.model_dump(), **updates}
        )
        series = pipeline.update_series(series_id, {"model_settings": ms})
        # The settings dialog does not consume the full Series document. A
        # full response can contain hundreds of frames/assets and requires
        # every media URL to be signed, making this tiny metadata save appear
        # to hang. Match the GET endpoint and return only the saved settings.
        return series.model_settings.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Series Asset Operations
# ============================================================

@app.get("/series/{series_id}/assets")
def get_series_assets(series_id: str):
    """Get all shared assets from a Series."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return signed_response({
        "characters": [c.model_dump() for c in series.characters],
        "scenes": [s.model_dump() for s in series.scenes],
        "props": [p.model_dump() for p in series.props],
    })


@app.post("/series/{series_id}/assets/generate")
def generate_series_asset(series_id: str, request: GenerateAssetRequest, background_tasks: BackgroundTasks):
    """Generate a single asset for a Series (async)."""
    reservations = []
    try:
        task_id = str(uuid.uuid4())
        target_asset, _, _, _ = pipeline.find_source_asset(
            "series", series_id, request.asset_type, request.asset_id
        )
        previous_asset_status = target_asset.status.value
        if server_mode_enabled():
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "series_asset",
                        "payload": {
                            "series_id": series_id,
                            **request.model_dump(),
                            "previous_asset_status": previous_asset_status,
                        },
                        "job_id": task_id,
                    }
                ]
            )
        series, task_id = pipeline.generate_series_asset(
            series_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt,
            request.batch_size,
            request.model_name,
            task_id=task_id,
        )
        if server_mode_enabled():
            publish_workspace_job_reservations(reservations)
            pipeline.forget_asset_generation_task(task_id)
        else:
            background_tasks.add_task(pipeline.process_asset_generation_task, task_id)
        response_data = series.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data)
    except HTTPException:
        if server_mode_enabled() and reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/toggle_lock")
def toggle_series_asset_lock(series_id: str, request: ToggleLockRequest):
    """Toggle the locked status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_lock(series_id, request.asset_id, request.asset_type)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/toggle_starred")
def toggle_series_asset_starred(series_id: str, request: ToggleLockRequest):
    """Toggle the starred (library shortlist) status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_starred(series_id, request.asset_id, request.asset_type)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/update_image")
def update_series_asset_image(series_id: str, request: UpdateAssetImageRequest):
    """Update a Series asset's image URL."""
    try:
        series = pipeline.update_series_asset_image(series_id, request.asset_id, request.asset_type, request.image_url)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/update_attributes")
def update_series_asset_attributes(series_id: str, request: UpdateAssetAttributesRequest):
    """Update allowlisted presentation attributes of a Series asset."""
    try:
        series = pipeline.update_series_asset_attributes(
            series_id, request.asset_id, request.asset_type, request.attributes
        )
        return signed_response(series)
    except InvalidAssetAttributesError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ImportAssetsRequest(BaseModel):
    source_series_id: str
    asset_ids: List[str]


# R2V v2 Phase 5 — quick-create CRUD for series-shared assets.
# Used by Cast step's "+ 新角色 / 新场景 / 新道具" modal. Optional
# `image_url` lets the user attach a pre-uploaded master sheet so the
# new asset isn't blank.
class CreateSeriesAssetRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""        # characters only — grouping label
    image_url: Optional[str] = None    # optional uploaded master sheet


def _new_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@app.post("/series/{series_id}/characters")
def create_series_character(series_id: str, request: CreateSeriesAssetRequest):
    """Create a new Character at series scope."""
    from .models import Character, AssetUnit, ImageVariant
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    char_id = _new_id("char")
    ref_sheet = AssetUnit()
    if request.image_url:
        variant = ImageVariant(id=_new_id("img"), url=request.image_url)
        ref_sheet.image_variants.append(variant)
        ref_sheet.selected_image_id = variant.id
    char = Character(
        id=char_id,
        name=request.name,
        description=request.description or "",
        persona=request.persona or "",
        reference_sheet=ref_sheet,
    )
    series.characters.append(char)
    series.updated_at = time.time()
    pipeline.series_store[series_id] = series
    pipeline._save_series_data()
    return signed_response(char.model_dump())


@app.post("/series/{series_id}/scenes")
def create_series_scene(series_id: str, request: CreateSeriesAssetRequest):
    from .models import Scene
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    sid = _new_id("scene")
    scene = Scene(
        id=sid,
        name=request.name,
        description=request.description or "",
        image_url=request.image_url,
    )
    series.scenes.append(scene)
    series.updated_at = time.time()
    pipeline.series_store[series_id] = series
    pipeline._save_series_data()
    return signed_response(scene.model_dump())


@app.post("/series/{series_id}/props")
def create_series_prop(series_id: str, request: CreateSeriesAssetRequest):
    from .models import Prop
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    pid = _new_id("prop")
    prop = Prop(
        id=pid,
        name=request.name,
        description=request.description or "",
        image_url=request.image_url,
    )
    series.props.append(prop)
    series.updated_at = time.time()
    pipeline.series_store[series_id] = series
    pipeline._save_series_data()
    return signed_response(prop.model_dump())


@app.post("/series/{series_id}/assets/import")
def import_series_assets(series_id: str, request: ImportAssetsRequest):
    """Deep-copy assets from another Series into this one."""
    try:
        series, imported_ids, skipped_ids = pipeline.import_assets_from_series(series_id, request.source_series_id, request.asset_ids)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Global Asset Library (project-independent shared pool) — CRUD + promote
# ============================================================
# Mirrors the /series/{id}/assets endpoints, one level up: a curated,
# project-independent pool any project can reference. All mutations route
# through the ComicGenPipeline.*_library_asset methods so the Playground
# "录入资产库" flow stays consistent with these endpoints.

class CreateLibraryAssetRequest(BaseModel):
    asset_type: str = Field(max_length=32)
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default="", max_length=20_000)
    persona: Optional[str] = Field(default="", max_length=1_000)
    image_url: Optional[str] = Field(default=None, max_length=4_096)


class UpdateLibraryAssetRequest(BaseModel):
    # Generic patch — only the fields the client actually sends are applied
    # (PATCH semantics; PUT here is lenient/partial).
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=20_000)
    persona: Optional[str] = Field(default=None, max_length=1_000)
    image_url: Optional[str] = Field(default=None, max_length=4_096)
    starred: Optional[bool] = None
    locked: Optional[bool] = None
    visual_weight: Optional[int] = None


class UpdateSourceAssetRequest(BaseModel):
    attributes: Dict[str, Any] = Field(default_factory=dict)
    prompts: Dict[str, Any] = Field(default_factory=dict)
    target_asset_type: Optional[str] = Field(default=None, max_length=32)

    @field_validator("target_asset_type")
    @classmethod
    def validate_target_asset_type(cls, value):
        if value is not None and value not in {"character", "scene", "prop"}:
            raise ValueError("target_asset_type must be character, scene, or prop")
        return value


class SourceVariantActionRequest(BaseModel):
    generation_type: Optional[str] = Field(default=None, max_length=32)


class SourceVariantFavoriteRequest(SourceVariantActionRequest):
    is_favorited: bool


class SourceAssetFavoriteRequest(BaseModel):
    starred: bool


class SourceMotionActionRequest(BaseModel):
    motion_type: str = Field(default="video", max_length=32)


class SourceMotionFavoriteRequest(SourceMotionActionRequest):
    is_favorited: bool


class GenerateSourceMotionRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motion_type: str = Field(default="video", max_length=32)
    prompt: Optional[str] = Field(default=None, max_length=50_000)
    model: Optional[str] = Field(default=None, max_length=128)
    audio_url: Optional[str] = Field(default=None, max_length=10_000)
    duration: int = Field(default=5, ge=1, le=15)
    batch_size: int = Field(default=1, ge=1, le=4)

    @field_validator("model")
    @classmethod
    def validate_video_model(cls, value):
        if value is not None:
            get_model_spec(value, VIDEO)
        return value


class PromoteAssetRequest(BaseModel):
    source_kind: str   # "project" | "series"
    source_id: str
    asset_type: str    # "character" | "scene" | "prop"
    asset_id: str


class ForkFromLibraryRequest(BaseModel):
    asset_type: str          # "character" | "scene" | "prop"
    library_asset_id: str    # id of the source asset in the global library


@app.get("/library/assets")
def get_library_assets():
    """List all assets in the global shared pool."""
    lib = pipeline.list_library_assets()
    return signed_response({
        "characters": _serialize_resolved_assets(lib.characters),
        "scenes": _serialize_resolved_assets(lib.scenes),
        "props": _serialize_resolved_assets(lib.props),
    })


@app.get("/library/overview")
def get_asset_library_overview():
    """Return one compact, canonical payload for every library source."""
    return signed_response(pipeline.get_asset_library_overview())


def _asset_feed_headers(*, etag: str, revision: int) -> Dict[str, str]:
    return {
        "Cache-Control": "private, no-cache",
        "Vary": "Cookie",
        "ETag": etag,
        "X-EnMotion-Asset-Revision": str(revision),
    }


def _asset_feed_etag(
    *,
    workspace_id: str,
    revision: int,
    generated_at: float,
    query_key: str,
    signing_epoch: int,
    content_fingerprint: str = "",
) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}\0{revision}\0{generated_at:.9f}\0"
        f"{query_key}\0{signing_epoch}\0{content_fingerprint}".encode("utf-8")
    ).hexdigest()
    return f'"asset-feed-{digest}"'


def _asset_feed_server_timing(phases: Dict[str, float]) -> str:
    """Serialize controlled phase names without tenant or media details."""

    values: List[str] = []
    for name in (
        "snapshot_ms",
        "projection_ms",
        "derivative_lookup_ms",
        "media_signing_ms",
        "serialization_ms",
    ):
        value = phases.get(name)
        if value is None:
            continue
        metric = name.removesuffix("_ms").replace("_", "-")
        values.append(f"asset-{metric};dur={max(0.0, float(value)):.3f}")
    return ", ".join(values)


def _asset_feed_latency(started: float) -> tuple[float, Dict[str, float | int]]:
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, ASSET_LIBRARY_FEED_LATENCY.observe(elapsed_ms)


def _desktop_asset_library_snapshot(current: Any):
    snapshot_reader = getattr(current, "asset_library_snapshot", None)
    if callable(snapshot_reader):
        return snapshot_reader()
    revision = int(getattr(current, "_asset_library_data_revision", 0))
    cached = getattr(current, "_asset_library_snapshot_cache", None)
    if cached is not None and cached.revision == revision:
        return cached
    snapshot = build_asset_library_snapshot(
        revision=revision,
        series=current.series_store.values(),
        projects=current.scripts.values(),
        library=current.library_store,
    )
    try:
        current._asset_library_snapshot_cache = snapshot
    except (AttributeError, TypeError):
        pass
    return snapshot


@app.get("/library/feed", response_model=AssetLibraryFeedResponse)
def get_asset_library_feed(
    request: Request,
    asset_type: Optional[AssetType] = None,
    source_kind: Optional[AssetSourceFilter] = None,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
    episode_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
    series_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
    starred: bool = False,
    q: str = Query(default="", max_length=500),
    sort: AssetSort = "default",
    order: Optional[AssetOrder] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
):
    """Return one strict page from a single committed workspace revision."""

    started = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", uuid.uuid4().hex))
    client_request_id = request.headers.get("X-EnMotion-Client-Request-ID", "")[:128]
    client_attempt_id = request.headers.get("X-EnMotion-Client-Attempt-ID", "")[:128]
    tenant = get_tenant(required=workspace_isolation_enabled())
    workspace_id = tenant.workspace_id if tenant is not None else "desktop"
    # FastAPI injects strings at runtime. Direct unit calls receive the Query
    # default objects unless they pass these optional values explicitly.
    project_id = project_id if isinstance(project_id, str) else None
    episode_id = episode_id if isinstance(episode_id, str) else None
    series_id = series_id if isinstance(series_id, str) else None
    if project_id and episode_id and project_id != episode_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_ASSET_LIBRARY_FILTER",
                "message": "project_id and episode_id must identify the same project",
            },
        )
    effective_project_id = project_id or episode_id
    effective_source_kind: Optional[AssetSourceKind] = (
        "project" if source_kind == "episode" else source_kind
    )
    phases: Dict[str, float] = {}
    try:
        phase = time.perf_counter()
        if server_mode_enabled():
            snapshot = read_asset_library_snapshot(workspace_id)
        else:
            current = pipeline.current()
            snapshot = _desktop_asset_library_snapshot(current)
        phases["snapshot_ms"] = round((time.perf_counter() - phase) * 1000, 3)

        phase = time.perf_counter()
        page = query_asset_library_snapshot(
            snapshot,
            asset_type=asset_type,
            source_kind=effective_source_kind,
            project_id=effective_project_id,
            series_id=series_id,
            starred=starred,
            search=q,
            sort=sort,
            order=order,
            offset=offset,
            limit=limit,
        )
        phases["projection_ms"] = round((time.perf_counter() - phase) * 1000, 3)

        query_key = json.dumps(
            {
                "asset_type": asset_type,
                "source_kind": effective_source_kind,
                "project_id": effective_project_id,
                "series_id": series_id,
                "starred": starred,
                "q": q,
                "sort": sort,
                "order": order,
                "offset": offset,
                "limit": limit,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        uploader = OSSImageUploader()
        # Signed object URLs expire. Rotate the conditional cache identity in a
        # bounded window so a 304 cannot strand an expired provider signature.
        signing_epoch = int(time.time() // 600) if uploader.is_configured else 0
        etag = _asset_feed_etag(
            workspace_id=workspace_id,
            revision=page.revision,
            generated_at=page.generated_at,
            query_key=query_key,
            signing_epoch=signing_epoch,
        )
        headers = _asset_feed_headers(etag=etag, revision=page.revision)
        if request.headers.get("If-None-Match") == etag:
            headers["Server-Timing"] = _asset_feed_server_timing(phases)
            elapsed_ms, latency = _asset_feed_latency(started)
            logger.info(
                "Asset Library feed not-modified request_id=%s client_request_id=%s "
                "client_attempt_id=%s "
                "workspace=%s revision=%s total=%s elapsed_ms=%.3f "
                "latency=%s phases=%s",
                request_id,
                client_request_id,
                client_attempt_id,
                workspace_id,
                page.revision,
                page.page.total,
                elapsed_ms,
                latency,
                phases,
            )
            return Response(status_code=304, headers=headers)

        phase = time.perf_counter()
        payload = page.model_dump(mode="json")
        if uploader.is_configured:
            payload = sign_oss_urls_in_data(payload, uploader)
        phases["media_signing_ms"] = round(
            (time.perf_counter() - phase) * 1000, 3
        )
        phase = time.perf_counter()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        phases["serialization_ms"] = round(
            (time.perf_counter() - phase) * 1000, 3
        )
        headers["Server-Timing"] = _asset_feed_server_timing(phases)
        elapsed_ms, latency = _asset_feed_latency(started)
        logger.info(
            "Asset Library feed success request_id=%s client_request_id=%s "
            "client_attempt_id=%s "
            "workspace=%s revision=%s count=%s total=%s bytes=%s elapsed_ms=%.3f "
            "latency=%s phases=%s",
            request_id,
            client_request_id,
            client_attempt_id,
            workspace_id,
            page.revision,
            page.page.count,
            page.page.total,
            len(body),
            elapsed_ms,
            latency,
            phases,
        )
        return Response(
            content=body,
            media_type="application/json",
            headers=headers,
        )
    except WorkspaceSnapshotUnavailable:
        elapsed_ms, latency = _asset_feed_latency(started)
        logger.warning(
            "Asset Library feed unavailable request_id=%s client_request_id=%s "
            "client_attempt_id=%s "
            "workspace=%s retryable=true elapsed_ms=%.3f latency=%s phases=%s",
            request_id,
            client_request_id,
            client_attempt_id,
            workspace_id,
            elapsed_ms,
            latency,
            phases,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The Asset Library is temporarily unavailable.",
                "error": {
                    "code": "ASSET_LIBRARY_UNAVAILABLE",
                    "message": "The Asset Library is temporarily unavailable.",
                    "request_id": request_id,
                    "retryable": True,
                },
            },
            headers={
                "Cache-Control": "private, no-cache",
                "Vary": "Cookie",
                "Retry-After": "1",
            },
        )
    except Exception:
        elapsed_ms, latency = _asset_feed_latency(started)
        logger.exception(
            "Asset Library feed failed request_id=%s client_request_id=%s "
            "client_attempt_id=%s "
            "workspace=%s elapsed_ms=%.3f latency=%s phases=%s",
            request_id,
            client_request_id,
            client_attempt_id,
            workspace_id,
            elapsed_ms,
            latency,
            phases,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The Asset Library is temporarily unavailable.",
                "error": {
                    "code": "ASSET_LIBRARY_UNAVAILABLE",
                    "message": "The Asset Library is temporarily unavailable.",
                    "request_id": request_id,
                    "retryable": True,
                },
            },
            headers={
                "Cache-Control": "private, no-cache",
                "Vary": "Cookie",
                "Retry-After": "1",
            },
        )


@app.get("/library/feed/v3", response_model=AssetLibraryFeedResponseV3)
def get_responsive_asset_library_feed(
    request: Request,
    asset_type: Optional[AssetType] = None,
    source_kind: Optional[AssetSourceFilter] = None,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
    episode_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
    series_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
    starred: bool = False,
    q: str = Query(default="", max_length=500),
    sort: AssetSort = "default",
    order: Optional[AssetOrder] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
):
    """Return responsive card media without changing the stable v2 route."""

    started = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", uuid.uuid4().hex))
    client_request_id = request.headers.get("X-EnMotion-Client-Request-ID", "")[:128]
    client_attempt_id = request.headers.get("X-EnMotion-Client-Attempt-ID", "")[:128]
    tenant = get_tenant(required=workspace_isolation_enabled())
    workspace_id = tenant.workspace_id if tenant is not None else "desktop"
    project_id = project_id if isinstance(project_id, str) else None
    episode_id = episode_id if isinstance(episode_id, str) else None
    series_id = series_id if isinstance(series_id, str) else None
    if project_id and episode_id and project_id != episode_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_ASSET_LIBRARY_FILTER",
                "message": "project_id and episode_id must identify the same project",
            },
        )
    effective_project_id = project_id or episode_id
    effective_source_kind: Optional[AssetSourceKind] = (
        "project" if source_kind == "episode" else source_kind
    )
    phases: Dict[str, float] = {}
    try:
        phase = time.perf_counter()
        if server_mode_enabled():
            snapshot = read_asset_library_snapshot(workspace_id)
        else:
            snapshot = _desktop_asset_library_snapshot(pipeline.current())
        phases["snapshot_ms"] = round((time.perf_counter() - phase) * 1000, 3)

        phase = time.perf_counter()
        page = query_asset_library_snapshot(
            snapshot,
            asset_type=asset_type,
            source_kind=effective_source_kind,
            project_id=effective_project_id,
            series_id=series_id,
            starred=starred,
            search=q,
            sort=sort,
            order=order,
            offset=offset,
            limit=limit,
        )
        phases["projection_ms"] = round((time.perf_counter() - phase) * 1000, 3)

        phase = time.perf_counter()
        responsive_page, derivative_fingerprint = responsive_asset_library_page(
            page,
            output_root=current_output_root(),
            schedule=True,
        )
        phases["derivative_lookup_ms"] = round(
            (time.perf_counter() - phase) * 1000, 3
        )
        query_key = json.dumps(
            {
                "schema_version": 3,
                "asset_type": asset_type,
                "source_kind": effective_source_kind,
                "project_id": effective_project_id,
                "series_id": series_id,
                "starred": starred,
                "q": q,
                "sort": sort,
                "order": order,
                "offset": offset,
                "limit": limit,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        uploader = OSSImageUploader()
        signing_epoch = int(time.time() // 600) if uploader.is_configured else 0
        etag = _asset_feed_etag(
            workspace_id=workspace_id,
            revision=page.revision,
            generated_at=page.generated_at,
            query_key=query_key,
            signing_epoch=signing_epoch,
            content_fingerprint=derivative_fingerprint,
        )
        headers = _asset_feed_headers(etag=etag, revision=page.revision)
        if request.headers.get("If-None-Match") == etag:
            headers["Server-Timing"] = _asset_feed_server_timing(phases)
            elapsed_ms, latency = _asset_feed_latency(started)
            logger.info(
                "Asset Library responsive feed not-modified request_id=%s "
                "client_request_id=%s client_attempt_id=%s workspace=%s "
                "revision=%s total=%s elapsed_ms=%.3f latency=%s phases=%s",
                request_id,
                client_request_id,
                client_attempt_id,
                workspace_id,
                page.revision,
                page.page.total,
                elapsed_ms,
                latency,
                phases,
            )
            return Response(status_code=304, headers=headers)

        phase = time.perf_counter()
        payload = responsive_page.model_dump(mode="json")
        if uploader.is_configured:
            payload = sign_oss_urls_in_data(payload, uploader)
        phases["media_signing_ms"] = round(
            (time.perf_counter() - phase) * 1000, 3
        )
        phase = time.perf_counter()
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        phases["serialization_ms"] = round(
            (time.perf_counter() - phase) * 1000, 3
        )
        headers["Server-Timing"] = _asset_feed_server_timing(phases)
        elapsed_ms, latency = _asset_feed_latency(started)
        ready_count = sum(
            item.thumbnail is not None and item.thumbnail.state == "ready"
            for item in responsive_page.items
        )
        logger.info(
            "Asset Library responsive feed success request_id=%s "
            "client_request_id=%s client_attempt_id=%s workspace=%s "
            "revision=%s count=%s total=%s ready_thumbnails=%s bytes=%s "
            "elapsed_ms=%.3f latency=%s phases=%s",
            request_id,
            client_request_id,
            client_attempt_id,
            workspace_id,
            page.revision,
            page.page.count,
            page.page.total,
            ready_count,
            len(body),
            elapsed_ms,
            latency,
            phases,
        )
        return Response(
            content=body,
            media_type="application/json",
            headers=headers,
        )
    except Exception:
        elapsed_ms, latency = _asset_feed_latency(started)
        logger.exception(
            "Asset Library responsive feed unavailable request_id=%s "
            "client_request_id=%s client_attempt_id=%s workspace=%s "
            "elapsed_ms=%.3f latency=%s phases=%s",
            request_id,
            client_request_id,
            client_attempt_id,
            workspace_id,
            elapsed_ms,
            latency,
            phases,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The Asset Library is temporarily unavailable.",
                "error": {
                    "code": "ASSET_LIBRARY_UNAVAILABLE",
                    "message": "The Asset Library is temporarily unavailable.",
                    "request_id": request_id,
                    "retryable": True,
                },
            },
            headers={
                "Cache-Control": "private, no-cache",
                "Vary": "Cookie",
                "Retry-After": "1",
            },
        )


@app.post("/library/assets")
def create_library_asset(request: CreateLibraryAssetRequest):
    """Create a new asset in the global shared pool."""
    try:
        payload = request.model_dump(exclude={"asset_type"})
        asset = pipeline.create_library_asset(request.asset_type, payload)
        return signed_response(
            pipeline.asset_response_payload(
                asset,
                source="global",
                source_id="global",
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/library/assets/upload")
def upload_library_asset_image(file: UploadFile = File(...)):
    """Upload an image to use as a global library asset's master image.

    Saves the file under output/uploads/ (served via the /files static mount)
    and returns {"image_url": <path-or-URL the frontend can load>}. When OSS
    is configured the returned URL is the (signed) OSS URL; otherwise a local
    relative path "uploads/<name>" resolvable through the frontend's
    getAssetUrl helper. The caller then passes this image_url to
    POST /library/assets (image_url=...) or PATCH /library/assets/{type}/{id}
    to attach it to a library asset. Mirrors the generic /upload endpoint but
    returns the {image_url} contract the library UI expects.
    """
    try:
        saved = save_upload_file(file, workspace_output_path("uploads"), IMAGE_UPLOAD_POLICY)
        enforce_upload_quota(saved.path)
        # Prefer OSS when configured (signed), else fall back to local path.
        oss_url = OSSImageUploader().upload_image(saved.path)
        reference = authoritative_media_reference(
            saved.path, current_output_root(), oss_url
        )
        payload = {"image_url": reference}
        return signed_response(payload) if oss_url and reference == oss_url else payload
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upload_library_asset_image failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/library/assets/{asset_type}/{asset_id}")
@app.patch("/library/assets/{asset_type}/{asset_id}")
def update_library_asset(asset_type: str, asset_id: str, request: UpdateLibraryAssetRequest):
    """Patch a global library asset (only the provided fields are applied)."""
    try:
        patch = request.model_dump(exclude_unset=True)
        asset = pipeline.update_library_asset(asset_type, asset_id, patch)
        return signed_response(
            pipeline.asset_response_payload(
                asset,
                source="global",
                source_id="global",
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}"
)
def get_source_asset(
    source_kind: str, source_id: str, asset_type: str, asset_id: str
):
    """Return one complete asset from its exact canonical owner."""

    try:
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, asset_type, asset_id
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/delete-impact")
def get_source_asset_delete_impact(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
):
    """Preview every live relationship affected by deleting one asset."""

    try:
        impact = pipeline.source_asset_delete_impact(source_kind, source_id, asset_type, asset_id)
        durable_references = _durable_source_asset_job_references(
            source_kind, source_id, asset_type, asset_id
        )
        impact["references"].extend(durable_references)
        impact["reference_count"] = len(impact["references"])
        impact["has_references"] = bool(impact["references"])
        return impact
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}")
def delete_source_asset(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    force: bool = False,
):
    """Delete an exact owner asset after a server-confirmed impact check."""

    try:
        impact = pipeline.source_asset_delete_impact(source_kind, source_id, asset_type, asset_id)
        durable_references = _durable_source_asset_job_references(
            source_kind, source_id, asset_type, asset_id
        )
        references = [*impact["references"], *durable_references]
        if references and not force:
            raise LibraryAssetInUseError(asset_type, asset_id, references)

        asset, _, _, _ = pipeline.find_source_asset(source_kind, source_id, asset_type, asset_id)
        deleted_snapshot = asset.model_dump()
        if force:
            _cancel_queued_asset_jobs(durable_references)
        pipeline.delete_source_asset(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            force=force,
        )
        reclaimed = reclaim_deleted_media(deleted_snapshot)
        return {
            "status": "deleted",
            "source_kind": source_kind,
            "source_id": source_id,
            "asset_type": asset_type,
            "id": asset_id,
            "reference_count": len(references),
            "reclaimed_media_files": len(reclaimed),
        }
    except LibraryAssetInUseError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "asset_in_use",
                "message": str(e),
                "source_kind": source_kind,
                "source_id": source_id,
                "asset_type": e.asset_type,
                "asset_id": e.asset_id,
                "references": e.references,
                "hint": "Pass ?force=true after explicit user confirmation.",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}"
)
def patch_source_asset(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    request: UpdateSourceAssetRequest,
):
    """Update metadata/prompts and safely convert an unreferenced asset type."""

    try:
        _, effective_type = pipeline.update_source_asset(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            attributes=request.attributes,
            prompts=request.prompts,
            target_asset_type=request.target_asset_type,
        )
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, effective_type, asset_id
            )
        )
    except AssetTypeChangeConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "asset_type_change_conflict",
                "message": str(e),
                "source": e.source_kind,
                "asset_type": e.asset_type,
                "target_asset_type": e.target_asset_type,
                "asset_id": e.asset_id,
                "references": e.references,
                "reason": e.reason,
                "unsupported_media": e.unsupported_media,
            },
        )
    except InvalidAssetAttributesError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/generate"
)
def generate_source_asset(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    request: GenerateAssetRequest,
    background_tasks: BackgroundTasks,
):
    """Queue image generation against the card's exact canonical owner."""

    if request.asset_id != asset_id or request.asset_type != asset_type:
        raise HTTPException(
            status_code=400,
            detail="Request asset_id/asset_type must match the route",
        )
    reservations = []
    task_id = str(uuid.uuid4())
    try:
        # Fail before reserving durable queue capacity when the route points at
        # a stale card or an invalid owner/type pair.
        target_asset, _, _, _ = pipeline.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        previous_asset_status = target_asset.status.value
        job_type = {
            "project": "project_asset",
            "series": "series_asset",
            "global": "global_asset",
        }.get(source_kind)
        if job_type is None:
            raise ValueError(f"Invalid asset source kind: {source_kind}")
        if server_mode_enabled():
            owner_key = (
                "script_id"
                if source_kind == "project"
                else "series_id"
                if source_kind == "series"
                else "source_id"
            )
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": job_type,
                        "payload": {
                            owner_key: source_id,
                            **request.model_dump(),
                            "activity_source": "library",
                            "previous_asset_status": previous_asset_status,
                        },
                        "job_id": task_id,
                    }
                ]
            )

        if source_kind == "project":
            _, task_id = pipeline.create_asset_generation_task(
                source_id,
                asset_id,
                asset_type,
                request.style_preset,
                request.reference_image_url,
                request.style_prompt,
                request.generation_type,
                request.prompt,
                request.apply_style,
                request.negative_prompt,
                request.batch_size,
                request.model_name,
                request.aspect_ratio,
                task_id=task_id,
            )
        elif source_kind == "series":
            _, task_id = pipeline.generate_series_asset(
                source_id,
                asset_id,
                asset_type,
                request.style_preset,
                request.reference_image_url,
                request.style_prompt,
                request.generation_type,
                request.prompt,
                request.apply_style,
                request.negative_prompt,
                request.batch_size,
                request.model_name,
                task_id=task_id,
                aspect_ratio=request.aspect_ratio,
            )
        else:
            _, task_id = pipeline.generate_global_asset(
                asset_id,
                asset_type,
                request.style_preset,
                request.reference_image_url,
                request.style_prompt,
                request.generation_type,
                request.prompt,
                request.apply_style,
                request.negative_prompt,
                request.batch_size,
                request.model_name,
                request.aspect_ratio,
                task_id=task_id,
            )

        if server_mode_enabled():
            publish_workspace_job_reservations(reservations)
            pipeline.forget_asset_generation_task(task_id)
        else:
            background_tasks.add_task(
                pipeline.process_asset_generation_task, task_id
            )
        response = pipeline.source_asset_response_payload(
            source_kind, source_id, asset_type, asset_id
        )
        # `_task_id` preserves the existing generation adapter contract;
        # `task_id` gives new source-aware clients an explicit public field.
        response.update(_task_id=task_id, task_id=task_id, task_status="queued")
        return signed_response(response)
    except HTTPException:
        if reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        if reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/variants/{variant_id}/select"
)
def select_source_asset_variant(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    variant_id: str,
    request: SourceVariantActionRequest,
):
    try:
        pipeline.select_source_asset_variant(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            variant_id,
            request.generation_type,
        )
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, asset_type, asset_id
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/variants/{variant_id}"
)
def delete_source_asset_variant(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    variant_id: str,
):
    try:
        asset, _, _, _ = pipeline.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        deleted_snapshot = asset.model_dump()
        pipeline.delete_source_asset_variant(
            source_kind, source_id, asset_type, asset_id, variant_id
        )
        response = pipeline.source_asset_response_payload(
            source_kind, source_id, asset_type, asset_id
        )
        reclaim_deleted_media(deleted_snapshot)
        return signed_response(response)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/variants/{variant_id}/favorite"
)
def favorite_source_asset_variant(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    variant_id: str,
    request: SourceVariantFavoriteRequest,
):
    try:
        pipeline.favorite_source_asset_variant(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            variant_id,
            request.is_favorited,
            request.generation_type,
        )
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, asset_type, asset_id
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/favorite"
)
def set_source_asset_favorite(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    request: SourceAssetFavoriteRequest,
):
    """Set an asset-level favorite using an idempotent desired-state write."""

    try:
        pipeline.set_source_asset_starred(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            request.starred,
        )
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, asset_type, asset_id
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/motion/generate"
)
def generate_source_asset_motion(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    request: GenerateSourceMotionRefRequest,
    background_tasks: BackgroundTasks,
):
    """Queue motion generation against the exact owner carried by the card."""

    reservations = []
    task_id = str(uuid.uuid4())
    try:
        _, task_id = pipeline.create_source_motion_ref_task(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            motion_type=request.motion_type,
            prompt=request.prompt,
            duration=request.duration,
            batch_size=request.batch_size,
            model_id=request.model,
            audio_url=request.audio_url,
            task_id=task_id,
        )
        if server_mode_enabled():
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "motion_reference",
                        "payload": {
                            "source_kind": source_kind,
                            "source_id": source_id,
                            "asset_type": asset_type,
                            "asset_id": asset_id,
                            **request.model_dump(),
                            "activity_source": "library",
                            "source_route": "#/library",
                        },
                        "job_id": task_id,
                    }
                ]
            )
            publish_workspace_job_reservations(reservations)
            pipeline.forget_motion_ref_task(task_id)
        else:
            background_tasks.add_task(
                pipeline.process_source_motion_ref_task, task_id
            )
        response = pipeline.source_asset_response_payload(
            source_kind, source_id, asset_type, asset_id
        )
        response.update(
            _task_id=task_id,
            task_id=task_id,
            task_status="queued",
        )
        return signed_response(response)
    except HTTPException:
        pipeline.rollback_motion_ref_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        pipeline.rollback_motion_ref_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        pipeline.rollback_motion_ref_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/motion/variants/{variant_id}/select"
)
def select_source_asset_motion_variant(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    variant_id: str,
    request: SourceMotionActionRequest,
):
    try:
        pipeline.select_source_asset_motion_variant(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            variant_id,
            request.motion_type,
        )
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, asset_type, asset_id
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/motion/variants/{variant_id}"
)
def delete_source_asset_motion_variant(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    variant_id: str,
    request: SourceMotionActionRequest,
):
    try:
        asset, _, _, _ = pipeline.find_source_asset(
            source_kind, source_id, asset_type, asset_id
        )
        deleted_snapshot = asset.model_dump()
        pipeline.delete_source_asset_motion_variant(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            variant_id,
            request.motion_type,
        )
        response = pipeline.source_asset_response_payload(
            source_kind, source_id, asset_type, asset_id
        )
        reclaim_deleted_media(deleted_snapshot)
        return signed_response(response)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put(
    "/asset-sources/{source_kind}/{source_id}/assets/{asset_type}/{asset_id}/motion/variants/{variant_id}/favorite"
)
def favorite_source_asset_motion_variant(
    source_kind: str,
    source_id: str,
    asset_type: str,
    asset_id: str,
    variant_id: str,
    request: SourceMotionFavoriteRequest,
):
    try:
        pipeline.favorite_source_asset_motion_variant(
            source_kind,
            source_id,
            asset_type,
            asset_id,
            variant_id,
            request.is_favorited,
            request.motion_type,
        )
        return signed_response(
            pipeline.source_asset_response_payload(
                source_kind, source_id, asset_type, asset_id
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/library/assets/{asset_type}/{asset_id}")
def delete_library_asset(asset_type: str, asset_id: str, force: bool = False):
    """Delete an asset from the global shared pool.

    Reference-integrity (design Q2): if any storyboard frame in any project or
    series still references this asset (via scene_id / character_ids /
    prop_ids), the delete is refused with HTTP 409 and the referrers are
    listed — unless ``force=true`` is passed, which deletes anyway and leaves
    those references dangling (the asset resolver simply drops the unknown id).
    """
    try:
        collection_name = {
            "character": "characters",
            "scene": "scenes",
            "prop": "props",
        }.get(asset_type)
        if collection_name is None:
            raise ValueError(f"Invalid asset type: {asset_type}")
        deleted_asset = next(
            (
                item
                for item in getattr(pipeline.library_store, collection_name)
                if item.id == asset_id
            ),
            None,
        )
        if deleted_asset is None:
            raise ValueError(
                f"Asset {asset_id} of type {asset_type} not found in library"
            )
        deleted_snapshot = deleted_asset.model_dump()
        pipeline.delete_library_asset(asset_type, asset_id, force=force)
        reclaimed = reclaim_deleted_media(deleted_snapshot)
        return {
            "status": "deleted",
            "asset_type": asset_type,
            "id": asset_id,
            "reclaimed_media_files": len(reclaimed),
        }
    except LibraryAssetInUseError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "library_asset_in_use",
                "message": str(e),
                "asset_type": e.asset_type,
                "asset_id": e.asset_id,
                "references": e.references,
                "hint": "Pass ?force=true to delete anyway.",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/library/assets/promote")
def promote_asset_to_library(request: PromoteAssetRequest):
    """Deep-copy an asset from a project or series into the global pool."""
    try:
        asset = pipeline.promote_asset_to_library(
            request.source_kind, request.source_id, request.asset_type, request.asset_id
        )
        return signed_response(
            pipeline.asset_response_payload(
                asset,
                source="global",
                source_id="global",
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/fork_from_library")
def fork_asset_from_library(script_id: str, request: ForkFromLibraryRequest):
    """Fork (deep-copy) a global library asset into this project as an
    independent, editable local copy with a fresh id (design Q3, 按需 fork).

    Under live-reference (D1 活引用) semantics a project references shared
    library assets directly; this endpoint materializes a project-owned copy
    so subsequent edits no longer affect the shared original. Returns the new
    project-local asset.
    """
    try:
        asset = pipeline.fork_library_asset_to_project(
            script_id, request.asset_type, request.library_asset_id
        )
        return signed_response(asset.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# File Import & Episode Splitting
# ============================================================

@app.post("/series/import/preview")
async def import_file_preview(
    file: UploadFile = File(...),
    suggested_episodes: int = 1,
):
    """Upload a txt, Markdown, or PDF file and get an LLM episode split preview."""
    if suggested_episodes < 1 or suggested_episodes > 50:
        raise HTTPException(status_code=400, detail="建议集数应在 1-50 之间")
    try:
        content_bytes = await read_upload_bytes(file, TEXT_IMPORT_UPLOAD_POLICY)
        text = await asyncio.to_thread(
            extract_import_text,
            content_bytes,
            file.filename or "",
        )
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件内容为空")

        episodes = await asyncio.to_thread(
            pipeline.import_file_and_split, text, suggested_episodes
        )
        # Store text in pipeline cache, return import_id instead of full text
        import_id = str(uuid.uuid4())
        pipeline._import_cache[import_id] = text
        return {
            "filename": file.filename,
            "text_length": len(text),
            "suggested_episodes": suggested_episodes,
            "episodes": episodes,
            "import_id": import_id,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("File import preview failed")
        raise HTTPException(status_code=500, detail=str(e))


class ConfirmImportRequest(BaseModel):
    title: str
    description: str = ""
    import_id: str = ""
    text: Optional[str] = None
    episodes: List[Dict[str, Any]]  # episode_number, title, start_marker, end_marker, ...


@app.post("/series/import/confirm")
async def import_file_confirm(request: ConfirmImportRequest):
    """Confirm the episode split and create Series + Episodes."""
    try:
        # Prefer import_id from cache, fallback to request.text
        text = None
        if request.import_id:
            text = pipeline._import_cache.pop(request.import_id, None)
        if not text:
            text = request.text
        if not text:
            raise ValueError("No text available. Provide import_id or text.")
        result = await asyncio.to_thread(
            pipeline.create_series_from_import,
            request.title,
            text,
            request.episodes,
            request.description,
        )
        return signed_response(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Import confirm failed")
        raise HTTPException(status_code=500, detail=str(e))


class EnvConfig(BaseModel):
    """Writable New API configuration.

    ``extra='forbid'`` intentionally rejects stale provider controls instead
    of accepting and silently ignoring them.
    """

    model_config = ConfigDict(extra="forbid")

    NEWAPI_BASE_URL: Optional[str] = None
    NEWAPI_CHAT_MODEL: Optional[str] = None
    NEWAPI_IMAGE_MODEL: Optional[str] = None
    NEWAPI_VIDEO_MODEL: Optional[str] = None
    NEWAPI_GPT_IMAGE_2_API_KEY: Optional[str] = None
    NEWAPI_SEEDANCE_2_API_KEY: Optional[str] = None
    NEWAPI_SEEDANCE_2_FAST_API_KEY: Optional[str] = None
    NEWAPI_SEEDANCE_2_MINI_API_KEY: Optional[str] = None
    NEWAPI_DEEPSEEK_V4_FLASH_API_KEY: Optional[str] = None
    NEWAPI_QWEN_37_MAX_API_KEY: Optional[str] = None
    NEWAPI_DEEPSEEK_V4_PRO_API_KEY: Optional[str] = None

    @field_validator("NEWAPI_BASE_URL")
    @classmethod
    def validate_newapi_base_url(cls, value):
        if value and value.strip():
            from ...models.newapi import normalize_newapi_base_url
            return normalize_newapi_base_url(value)
        return value

    @field_validator("NEWAPI_CHAT_MODEL")
    @classmethod
    def validate_chat_model(cls, value):
        if value is not None:
            get_model_spec(value, CHAT)
        return value

    @field_validator("NEWAPI_IMAGE_MODEL")
    @classmethod
    def validate_image_model(cls, value):
        if value is not None:
            get_model_spec(value, IMAGE)
        return value

    @field_validator("NEWAPI_VIDEO_MODEL")
    @classmethod
    def validate_video_model(cls, value):
        if value is not None:
            get_model_spec(value, VIDEO)
        return value


class APIKeyInspectionRequest(BaseModel):
    """Admin-only request to inspect the model credential registry."""

    model_config = ConfigDict(extra="forbid")

    reveal: bool = False


def get_user_config_path() -> str:
    """
    Returns the path to the user config file.
    - Development mode: Uses .env in project root
    - Packaged app mode: Uses the OS EnMotion application-data directory.
    """
    from ...utils import get_user_data_dir

    if server_mode_enabled():
        from ..server.runtime_config import server_runtime_config_path

        path = server_runtime_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    # Check if running in packaged mode (e.g., via environment variable or frozen check)
    is_packaged = os.getenv("ENMOTION_PACKAGED", "false").lower() == "true" or getattr(
        sys, "frozen", False
    )

    if is_packaged:
        # Use user home directory for packaged app
        config_dir = get_user_data_dir()
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "config.json")
    else:
        # Use .env in project root for development
        # Get absolute path to project root (api.py is in src/apps/comic_gen/)
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        return os.path.join(project_root, ".env")


_USER_CONFIG_LOCK = threading.RLock()


def _normalize_user_config(config: Any, config_path: str) -> Dict[str, str]:
    """Validate persisted environment data before applying any of it."""
    if not isinstance(config, dict):
        raise ValueError(f"User config at {config_path} must be a JSON object")

    normalized: Dict[str, str] = {}
    for key, value in config.items():
        if not isinstance(key, str):
            raise ValueError(f"User config at {config_path} contains a non-string key")
        if isinstance(value, str):
            normalized[key] = value
        elif isinstance(value, bool):
            # Older releases could persist booleans directly. Normalize that
            # one known legacy shape rather than making the config unusable.
            normalized[key] = "true" if value else "false"
        elif isinstance(value, (int, float)):
            normalized[key] = str(value)
        elif value is None:
            normalized[key] = ""
        else:
            raise ValueError(f"User config at {config_path} contains a non-scalar value for {key}")
    return normalized


def _read_user_config_json(config_path: str) -> Dict[str, str]:
    """Read a packaged config strictly; malformed files are never reset."""
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            return _normalize_user_config(json.load(config_file), config_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to load user configuration from {config_path}: {exc}") from exc


def _atomic_write_private_json(config_path: str, config: Dict[str, str]) -> None:
    """Atomically persist credential-bearing JSON with owner-only access."""
    directory = os.path.dirname(config_path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(config_path)}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            os.chmod(temp_path, 0o600)
            json.dump(config, temp_file, indent=2, ensure_ascii=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, config_path)
    except Exception:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        raise


def load_user_config():
    """Loads user config from file and applies to environment."""
    if server_mode_enabled():
        from ..server.runtime_config import load_server_runtime_config

        load_server_runtime_config()
        return
    config_path = get_user_config_path()

    if config_path.endswith(".json"):
        # JSON config for packaged app
        with _USER_CONFIG_LOCK:
            config = _read_user_config_json(config_path)
            # Validate the complete file before changing process state.
            for key, value in config.items():
                if value and key not in os.environ:
                    os.environ[key] = value
    # .env is already loaded at startup via dotenv


def save_user_config(config_dict: dict):
    """Saves user config to file."""
    config_path = get_user_config_path()

    with _USER_CONFIG_LOCK:
        if config_path.endswith(".json"):
            # JSON config for packaged app
            existing_config = _read_user_config_json(config_path)
            updates = _normalize_user_config(config_dict, config_path)
            candidate = {**existing_config, **updates}
            _atomic_write_private_json(config_path, candidate)
        else:
            # .env for development
            updates = _normalize_user_config(config_dict, config_path)
            for key, value in updates.items():
                if config_dict[key] is not None:
                    set_key(config_path, key, value)
        # Configuration contains credentials. Keep it private regardless of the
        # process umask or whether python-dotenv created the file.
        if os.path.exists(config_path):
            os.chmod(config_path, 0o600)


def remove_user_config_keys(keys: list):
    """Removes keys from the persisted config file."""
    if not keys:
        return
    config_path = get_user_config_path()

    with _USER_CONFIG_LOCK:
        if config_path.endswith(".json"):
            if os.path.exists(config_path):
                candidate = _read_user_config_json(config_path)
                for key in keys:
                    candidate.pop(key, None)
                _atomic_write_private_json(config_path, candidate)
        else:
            from dotenv import unset_key

            for key in keys:
                unset_key(config_path, key)
            if os.path.exists(config_path):
                os.chmod(config_path, 0o600)


# Load user config on startup
import sys

load_user_config()
_newapi_migration_updates = migrate_legacy_newapi_environment()
if _newapi_migration_updates:
    save_user_config(_newapi_migration_updates)


@app.get("/config/info")
def get_config_info(request: Request):
    """Returns information about the current config storage mode."""
    require_server_admin(request)
    config_path = get_user_config_path()
    is_packaged = os.getenv("ENMOTION_PACKAGED", "false").lower() == "true" or getattr(
        sys, "frozen", False
    )
    return {
        "mode": "packaged" if is_packaged else "development",
        "config_path": config_path,
        "config_exists": os.path.exists(config_path),
    }


@app.post("/config/env")
def update_env_config(config: EnvConfig, request: Request = None):
    """Updates environment configuration and saves to config file."""
    require_server_admin(request)
    try:
        raw_config = config.model_dump(exclude_unset=True)

        config_dict: Dict[str, str] = {
            key: value for key, value in raw_config.items() if value is not None
        }

        # Secret masking guard: GET /config/env returns secrets masked with the
        # bullet sentinel. If the frontend re-submits an unchanged secret it will
        # still contain bullets — skip it so we don't overwrite the real stored
        # key with the mask. Only genuinely edited (bullet-free) values persist.
        for field in list(config_dict.keys()):
            if field in SECRET_FIELDS and _MASK_CHAR in str(config_dict[field]):
                config_dict.pop(field, None)

        # Persist before changing process state so failed writes cannot leave
        # the running app claiming an unsaved configuration is active.
        with _USER_CONFIG_LOCK:
            save_user_config(config_dict)
            os.environ.update(config_dict)

        config_path = get_user_config_path()
        return {"status": "success", "message": f"Configuration saved to {config_path}"}
    except Exception as e:
        logger.exception("Failed to save environment configuration")
        raise HTTPException(status_code=500, detail=str(e))


def _project_scope_snapshot(script: Script) -> dict:
    """Capture every asset layer a project mutation may remove from."""

    snapshot = {
        "project": script.model_dump(),
        "library": pipeline.library_store.model_dump(),
    }
    if script.series_id:
        parent_series = pipeline.get_series(script.series_id)
        if parent_series:
            snapshot["series"] = parent_series.model_dump()
    return snapshot


@app.get("/projects/{script_id}")
def get_project(script_id: str):
    """Retrieves a project by ID. When the project belongs to a
    Series, the response merges series-shared characters / scenes /
    props on top of the episode-local lists. Each item carries a
    `source` / `source_id` fields ("episode" | "series" | "global") plus
    the current `series_id` / `episode_id` context, so the frontend can
    visually distinguish where the asset lives and route writes
    appropriately (per A2 design decision — shared writes default to
    the series side; local writes stay episode-side; the helper
    `_find_asset_with_source` in pipeline routes mutations correctly).

    Response model dropped from `Script` because the `source` field
    is a presentation-layer concern (never persisted, derived from
    container membership at read time)."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    payload = script.model_dump()
    series = pipeline.get_series(script.series_id) if script.series_id else None
    resolved = pipeline.resolve_episode_assets(script, series)
    episode_character_ids = {asset.id for asset in script.characters}
    episode_scene_ids = {asset.id for asset in script.scenes}
    episode_prop_ids = {asset.id for asset in script.props}
    series_character_ids = {asset.id for asset in series.characters} if series else set()
    series_scene_ids = {asset.id for asset in series.scenes} if series else set()
    series_prop_ids = {asset.id for asset in series.props} if series else set()

    payload["characters"] = _serialize_resolved_assets(
        resolved["characters"],
        episode_asset_ids=episode_character_ids,
        series_asset_ids=series_character_ids,
        episode_id=script.id,
        series_id=script.series_id,
    )
    payload["scenes"] = _serialize_resolved_assets(
        resolved["scenes"],
        episode_asset_ids=episode_scene_ids,
        series_asset_ids=series_scene_ids,
        episode_id=script.id,
        series_id=script.series_id,
    )
    payload["props"] = _serialize_resolved_assets(
        resolved["props"],
        episode_asset_ids=episode_prop_ids,
        series_asset_ids=series_prop_ids,
        episode_id=script.id,
        series_id=script.series_id,
    )
    return signed_response(payload)


@app.delete("/projects/{script_id}")
def delete_project(script_id: str):
    """Deletes a project by ID. WARNING: This permanently removes the project from backend storage."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    deleted_snapshot = script.model_dump()

    try:
        # If project belongs to a Series, remove from episode_ids
        if script.series_id:
            series = pipeline.get_series(script.series_id)
            if series and script_id in series.episode_ids:
                series.episode_ids.remove(script_id)
                pipeline._save_series_data()

        # Remove from pipeline scripts
        del pipeline.scripts[script_id]
        pipeline._save_data()

        reclaimed = reclaim_deleted_media(deleted_snapshot)
        return {
            "status": "deleted",
            "id": script_id,
            "title": script.title,
            "reclaimed_media_files": len(reclaimed),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────
# R2V v2 Phase 4 — Cross-episode asset reconcile
# ─────────────────────────────────────────────────────────────────────
def _name_match_confidence(local_name: str, series_name: str) -> int:
    """0-100 simple confidence score.
    Phase 4 v1: exact match = 100, substring match = 75, none = 0.
    Future: embedding-based semantic similarity on `description`."""
    a, b = (local_name or "").strip().lower(), (series_name or "").strip().lower()
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a in b or b in a:
        return 75
    return 0


@app.get("/projects/{script_id}/reconcile/suggestions")
def reconcile_suggestions(script_id: str):
    """Compute match suggestions for the current episode's just-extracted
    entities vs the parent series's shared asset library.

    Returned shape:
    {
      "characters": [
        { local_id, local_name, suggested_series_id|null, suggested_series_name|null, confidence }
      ],
      "scenes": [...],
      "props": [...],
    }
    Frontend uses this to render the ReconcileModal after script parse.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        # Standalone projects have no series library to reconcile against
        return {"characters": [], "scenes": [], "props": []}
    series = pipeline.get_series(script.series_id)
    if not series:
        return {"characters": [], "scenes": [], "props": []}

    def best_match(local_name: str, pool: list, key: str = "name"):
        best_id, best_name, best_conf = None, None, 0
        for item in pool:
            sname = getattr(item, key, "") if hasattr(item, key) else item.get(key, "")
            conf = _name_match_confidence(local_name, sname)
            if conf > best_conf:
                best_id, best_name, best_conf = item.id if hasattr(item, "id") else item.get("id"), sname, conf
        return best_id, best_name, best_conf

    def build(local_pool, series_pool):
        result = []
        for local in local_pool:
            sid, sname, conf = best_match(local.name, series_pool)
            result.append({
                "local_id": local.id,
                "local_name": local.name,
                "suggested_series_id": sid if conf > 0 else None,
                "suggested_series_name": sname if conf > 0 else None,
                "confidence": conf,
            })
        return result

    return {
        "characters": build(script.characters, series.characters),
        "scenes": build(script.scenes, series.scenes),
        "props": build(script.props, series.props),
    }


class ReconcileAction(BaseModel):
    local_id: str
    action: str  # "merge_into_series" | "create_new_in_series" | "skip"
    target_series_id: Optional[str] = None


class ApplyReconcileRequest(BaseModel):
    characters: List[ReconcileAction] = Field(default_factory=list)
    scenes: List[ReconcileAction] = Field(default_factory=list)
    props: List[ReconcileAction] = Field(default_factory=list)


@app.post("/projects/{script_id}/reconcile/apply")
def reconcile_apply(script_id: str, request: ApplyReconcileRequest):
    """Apply user-confirmed reconcile decisions.
    - merge_into_series: drop the local episode entity, replace all
      frame references with target_series_id (the series-shared asset).
    - create_new_in_series: promote the local entity to series scope.
    - skip: no-op (keep local-only).
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        raise HTTPException(status_code=400, detail="Project not in a series")
    series = pipeline.get_series(script.series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    def apply_list(local_pool_attr: str, series_pool_attr: str, actions: list, frame_ref_attr: Optional[str] = None):
        local_pool = getattr(script, local_pool_attr)
        series_pool = getattr(series, series_pool_attr)
        for act in actions:
            local_item = next((x for x in local_pool if x.id == act.local_id), None)
            if not local_item:
                continue
            if act.action == "create_new_in_series":
                # Promote: copy to series pool, drop from local
                series_pool.append(local_item)
                local_pool.remove(local_item)
            elif act.action == "merge_into_series" and act.target_series_id:
                # Rewire frame references then drop local
                if frame_ref_attr:
                    for frame in script.frames:
                        if frame_ref_attr == "scene_id":
                            if frame.scene_id == local_item.id:
                                frame.scene_id = act.target_series_id
                        elif frame_ref_attr == "character_ids":
                            frame.character_ids = [
                                act.target_series_id if cid == local_item.id else cid
                                for cid in frame.character_ids
                            ]
                        elif frame_ref_attr == "prop_ids":
                            frame.prop_ids = [
                                act.target_series_id if pid == local_item.id else pid
                                for pid in frame.prop_ids
                            ]
                local_pool.remove(local_item)
            # skip: do nothing
        setattr(script, local_pool_attr, local_pool)
        setattr(series, series_pool_attr, series_pool)

    apply_list("characters", "characters", request.characters, "character_ids")
    apply_list("scenes", "scenes", request.scenes, "scene_id")
    apply_list("props", "props", request.props, "prop_ids")

    script.updated_at = time.time()
    series.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline.series_store[series.id] = series
    pipeline._save_data()
    pipeline._save_series_data()
    return signed_response(script)


# ─────────────────────────────────────────────────────────────────────
# R2V v2 Phase 3 — "Previously on..." (上回书说到)
# ─────────────────────────────────────────────────────────────────────
def _prev_text_revision(prev_text: str) -> str:
    """Cheap revision marker for cache invalidation.
    Hash + length is sufficient — collisions don't matter, we only
    detect "did the script change since cache was built"."""
    import hashlib
    h = hashlib.md5(prev_text.encode("utf-8")).hexdigest()[:12]
    return f"{len(prev_text)}-{h}"


@app.get("/projects/{script_id}/previous_episode")
def get_previous_episode_summary(script_id: str):
    """Return previous-episode raw snippet + AI summary cache state for
    the "Previously on..." right rail in the Script step.

    Response shape:
        {
          "has_previous": bool,
          "previous_episode_id": str | null,
          "previous_episode_title": str | null,
          "raw_snippet": str  (last ~600 chars of previous original_text),
          "ai_summary": str | null  (cached summary if fresh),
          "ai_summary_stale": bool  (true when prev text changed since cache),
        }
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    series = pipeline.get_series(script.series_id)
    if not series:
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    # Find previous episode by ordered episode_ids — fall back to
    # episode_number ordering when episode_ids is malformed.
    try:
        idx = series.episode_ids.index(script_id)
    except ValueError:
        idx = -1
    if idx <= 0:
        # No previous episode (this IS the first or the id is missing)
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    prev_id = series.episode_ids[idx - 1]
    prev = pipeline.get_script(prev_id)
    if not prev or not (prev.original_text or "").strip():
        return {
            "has_previous": False,
            "previous_episode_id": prev_id,
            "previous_episode_title": prev.title if prev else None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    raw_snippet = (prev.original_text or "")[-800:]
    current_rev = _prev_text_revision(prev.original_text or "")
    is_stale = (
        script.last_episode_summary_cache is not None
        and script.last_episode_summary_revision != current_rev
    )
    # R2V v2 P2-a — last frames of previous episode for Storyboard
    # cross-step reference. We surface the *last 4 frames* with their
    # selected video / T2I image so authors composing this episode's
    # opening shots can see what the previous episode ended on.
    last_frames = []
    for f in (prev.frames or [])[-4:]:
        thumb_url = None
        # Prefer the selected video's poster/url, fall back to T2I image
        if getattr(f, "video_url", None):
            thumb_url = f.video_url
        elif getattr(f, "rendered_image_url", None):
            thumb_url = f.rendered_image_url
        elif getattr(f, "image_url", None):
            thumb_url = f.image_url
        elif getattr(f, "t2i_image_urls", None):
            urls = f.t2i_image_urls
            if urls:
                idx = getattr(f, "t2i_selected_index", 0) or 0
                idx = max(0, min(idx, len(urls) - 1))
                thumb_url = urls[idx]
        last_frames.append({
            "id": f.id,
            "action_description": (f.action_description or "")[:120],
            "thumbnail_url": thumb_url,
            "video_url": getattr(f, "video_url", None),
        })
    return {
        "has_previous": True,
        "previous_episode_id": prev_id,
        "previous_episode_title": prev.title,
        "raw_snippet": raw_snippet,
        "ai_summary": script.last_episode_summary_cache,
        "ai_summary_stale": is_stale,
        "last_frames": last_frames,
    }


@app.post("/projects/{script_id}/previous_episode/summary")
def generate_previous_episode_summary(script_id: str):
    """On-demand AI summary of the previous episode (qwen3.6-plus).
    Per Q7-followup design: this is *user-triggered*, not auto, to
    respect LLM quota and user intent. Result is cached on the current
    episode's Script record with a revision marker for invalidation.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        raise HTTPException(status_code=400, detail="Episode not in a series")
    series = pipeline.get_series(script.series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    try:
        idx = series.episode_ids.index(script_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Episode not registered in series order")
    if idx <= 0:
        raise HTTPException(status_code=400, detail="No previous episode")
    prev = pipeline.get_script(series.episode_ids[idx - 1])
    if not prev or not (prev.original_text or "").strip():
        raise HTTPException(status_code=400, detail="Previous episode has no script")

    # Call LLM — keep prompt simple and bilingual-friendly
    from .llm_adapter import LLMAdapter
    prompt = (
        "请用 150-220 字概括下面这段剧本的核心情节、关键角色出场与结尾留下的悬念/钩子。"
        "不要列点，写成一段连贯的中文叙述，便于作者快速回顾上一集走到了哪里。\n\n"
        f"剧本：\n{prev.original_text}"
    )
    try:
        adapter = LLMAdapter()
        summary = adapter.chat(
            messages=[{"role": "user", "content": prompt}],
            model=None,  # use adapter default (qwen3.6-plus with fallback chain)
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")
    summary = (summary or "").strip()
    if not summary:
        raise HTTPException(status_code=502, detail="Empty summary from LLM")

    # Persist cache + revision marker on current script
    script.last_episode_summary_cache = summary
    script.last_episode_summary_revision = _prev_text_revision(prev.original_text or "")
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return {
        "ai_summary": summary,
        "ai_summary_stale": False,
        "previous_episode_id": prev.id,
        "previous_episode_title": prev.title,
    }


@app.get("/series/{series_id}/characters/{character_id}/appearances")
def get_character_appearances(series_id: str, character_id: str):
    """R2V v2 P1-c — Cross-episode character appearance summary.
    Used by Script step's @mention helper popover. Returns chronological
    list of episodes where the character appears + per-episode frame
    count + aggregated 'last seen' info."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    char = next((c for c in series.characters if c.id == character_id), None)
    if not char:
        # Try as episode-local character — maybe id is from any episode
        for ep_id in series.episode_ids:
            ep = pipeline.get_script(ep_id)
            if ep:
                local = next((c for c in ep.characters if c.id == character_id), None)
                if local:
                    char = local
                    break
    if not char:
        raise HTTPException(status_code=404, detail="Character not found in series scope")
    appearances = []
    total_frames = 0
    for ep_id in series.episode_ids:
        ep = pipeline.get_script(ep_id)
        if not ep:
            continue
        count = sum(1 for f in ep.frames if character_id in (f.character_ids or []))
        if count > 0:
            appearances.append({
                "episode_id": ep_id,
                "episode_number": ep.episode_number,
                "episode_title": ep.title,
                "frame_count": count,
            })
            total_frames += count
    return {
        "character": {
            "id": char.id,
            "name": char.name,
            "persona": char.persona,
            "description": char.description,
        },
        "appearances": appearances,
        "total_frames": total_frames,
    }


# R2V v2 P2-b — "Next episode hook" prediction (forward-looking).
@app.get("/projects/{script_id}/next_hook")
def get_next_episode_hook(script_id: str):
    """Return cached hook prediction state for the Script step's
    'Hook for next' panel. Always returns the current revision marker
    so the frontend can decide if a refresh is warranted."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    current_text = script.original_text or ""
    current_rev = _prev_text_revision(current_text)
    is_stale = (
        script.next_hook_cache is not None
        and script.next_hook_revision != current_rev
    )
    return {
        "has_text": bool(current_text.strip()),
        "hook": script.next_hook_cache,
        "stale": is_stale,
    }


@app.post("/projects/{script_id}/next_hook")
def generate_next_episode_hook(script_id: str):
    """On-demand AI prediction of the next-episode opening hook based
    on THIS episode's ending. User-triggered (no auto-generate to
    respect LLM quota)."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    text = script.original_text or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Episode has no script text yet")

    from .llm_adapter import LLMAdapter
    # Use last ~1500 chars as context (the ending matters most for hooks)
    ending = text[-1500:]
    prompt = (
        "下面是一集剧本的结尾。请你站在编剧的视角，预测下一集开头可能的 hook，"
        "用 120-180 字给出 2-3 个具体方向（包括场景、角色出场、悬念点）。"
        "不要列点编号，写成连贯的中文段落，便于作者参考。\n\n"
        f"本集结尾：\n{ending}"
    )
    try:
        adapter = LLMAdapter()
        hook = adapter.chat(
            messages=[{"role": "user", "content": prompt}],
            model=None,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")
    hook = (hook or "").strip()
    if not hook:
        raise HTTPException(status_code=502, detail="Empty hook from LLM")

    script.next_hook_cache = hook
    script.next_hook_revision = _prev_text_revision(text)
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return {"hook": hook, "stale": False}


@app.put("/projects/{script_id}/next_hook")
def update_next_episode_hook(script_id: str, payload: dict):
    """Manually edit / clear the next-hook cache."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    hook = payload.get("hook")
    if hook is None:
        script.next_hook_cache = None
        script.next_hook_revision = None
    else:
        script.next_hook_cache = str(hook).strip()
        script.next_hook_revision = _prev_text_revision(script.original_text or "")
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return {"hook": script.next_hook_cache, "stale": False}


@app.post("/projects/{script_id}/sync_descriptions", response_model=Script)
def sync_descriptions(script_id: str):
    """
    Syncs entity descriptions from Script module to Assets module.

    This endpoint forces a refresh of the project data, ensuring that any
    description changes made in the Script module are reflected in Assets.

    Note: This only syncs descriptions; generated images/videos are preserved.
    """
    try:
        updated_script = pipeline.sync_descriptions_from_script_entities(script_id)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AddCharacterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=20_000)

@app.post("/projects/{script_id}/characters")
def add_character(script_id: str, request: AddCharacterRequest):
    """Adds a new character."""
    try:
        updated_script = pipeline.add_character(script_id, request.name, request.description)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/characters/{char_id}")
def delete_character(script_id: str, char_id: str):
    """Deletes a character."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted_snapshot = _project_scope_snapshot(script)
        updated_script = pipeline.delete_character(script_id, char_id)
        reclaim_deleted_media(deleted_snapshot)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddSceneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=20_000)

@app.post("/projects/{script_id}/scenes")
def add_scene(script_id: str, request: AddSceneRequest):
    """Adds a new scene."""
    try:
        updated_script = pipeline.add_scene(script_id, request.name, request.description)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/scenes/{scene_id}")
def delete_scene(script_id: str, scene_id: str):
    """Deletes a scene."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted_snapshot = _project_scope_snapshot(script)
        updated_script = pipeline.delete_scene(script_id, scene_id)
        reclaim_deleted_media(deleted_snapshot)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class UpdateStyleRequest(BaseModel):
    style_preset: str = Field(min_length=1, max_length=200)
    style_prompt: Optional[str] = Field(default=None, max_length=50_000)


@app.patch("/projects/{script_id}/style", response_model=Script)
def update_project_style(script_id: str, request: UpdateStyleRequest):
    """Updates the global style settings for a project."""
    try:
        updated_script = pipeline.update_project_style(
            script_id,
            request.style_preset,
            request.style_prompt
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/generate_assets", response_model=Script)
def generate_assets(script_id: str, background_tasks: BackgroundTasks):
    """Triggers asset generation."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    # Run in background to avoid blocking
    # For simplicity in this demo, we run synchronously or use background tasks
    # pipeline.generate_assets(script_id)
    # But since we want to return the updated status, we might want to run it and return.
    # Given the mock nature, it's fast.

    try:
        if server_mode_enabled():
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "project_assets_batch",
                        "payload": {"script_id": script_id},
                    }
                ]
            )
            job = publish_workspace_job_reservations(reservations)[0]
            response_data = script.model_dump()
            response_data["_task_id"] = job.id
            return signed_response(response_data)
        updated_script = pipeline.generate_assets(script_id)
        return signed_response(updated_script)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class GenerateMotionRefRequest(BaseModel):
    """Request model for generating Motion Reference videos."""
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(max_length=128)
    asset_type: str = Field(max_length=32)  # 'full_body' | 'head_shot' for characters; 'scene' | 'prop' for scenes and props
    prompt: Optional[str] = Field(default=None, max_length=50_000)
    model: Optional[str] = Field(default=None, max_length=128)
    duration: int = 5
    batch_size: int = Field(1, ge=1, le=4)

    @field_validator("model")
    @classmethod
    def validate_video_model(cls, value):
        if value is not None:
            get_model_spec(value, VIDEO)
        return value


@app.post("/projects/{script_id}/assets/generate_motion_ref")
def generate_motion_ref(script_id: str, request: GenerateMotionRefRequest, background_tasks: BackgroundTasks):
    """Generates a Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop)."""
    reservations = []
    task_id = str(uuid.uuid4())
    try:
        if server_mode_enabled():
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "motion_reference",
                        "payload": {"script_id": script_id, **request.model_dump()},
                        "job_id": task_id,
                    }
                ]
            )
        script, task_id = pipeline.create_motion_ref_task(
            script_id=script_id,
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            prompt=request.prompt,
            duration=request.duration,
            batch_size=request.batch_size,
            model_id=request.model,
            task_id=task_id,
        )

        if server_mode_enabled():
            publish_workspace_job_reservations(reservations)
            pipeline.forget_motion_ref_task(task_id)
        else:
            background_tasks.add_task(pipeline.process_motion_ref_task, script_id, task_id)

        # Return script with task_id for frontend polling
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data)

    except HTTPException:
        if server_mode_enabled() and reservations:
            pipeline.rollback_motion_ref_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_motion_ref_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_motion_ref_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


# === STORYBOARD DRAMATIZATION v2 ===

class AnalyzeToStoryboardRequest(BaseModel):
    """Request to analyze script text into storyboard frames."""
    text: str


@app.post("/projects/{script_id}/storyboard/analyze")
def analyze_to_storyboard(script_id: str, request: AnalyzeToStoryboardRequest):
    """
    Analyzes script text and generates storyboard frames using AI (Prompt B).
    Replaces existing frames with newly generated ones.
    """
    try:
        updated_script = pipeline.analyze_text_to_frames(script_id, request.text)
        return signed_response(updated_script)
    except (ValueError, MissingNewAPIKeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in analyze_to_storyboard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class RefinePromptRequest(BaseModel):
    """Request to refine a frame's prompt using AI."""
    frame_id: str
    raw_prompt: str
    assets: list = []  # List of asset references
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement


@app.post("/projects/{script_id}/storyboard/refine_prompt")
def refine_storyboard_prompt(script_id: str, request: RefinePromptRequest):
    """
    Refines a raw prompt into bilingual (CN/EN) prompts using AI (Prompt C).
    Returns the refined prompts and optionally updates the frame.

    SYNC handler — pipeline.refine_frame_prompt() does a blocking LLM call.
    Declared `def` (not `async def`) so FastAPI runs it on the threadpool
    and the event loop stays free for concurrent GETs.
    """
    from .llm import PolishError

    try:
        result = pipeline.refine_frame_prompt(
            script_id,
            request.frame_id,
            request.raw_prompt,
            request.assets,
            request.feedback,
        )
        return result
    except PolishError as e:
        logger.warning("refine_storyboard_prompt failed: %s", e)
        raise HTTPException(status_code=502, detail=_polish_error_response(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in refine_storyboard_prompt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/refine")
def refine_single_frame(script_id: str, frame_id: str):
    """Phase 2: Refine a single coarse frame into a rich frame with structured fields."""
    try:
        frame = pipeline.refine_frame(script_id, frame_id)
        if not frame:
            raise HTTPException(status_code=500, detail="Refine returned no result")
        return frame.model_dump() if hasattr(frame, 'model_dump') else frame.dict()
    except FrameRefineError as e:
        status_code = {
            "missing_config": 503,
            "provider_timeout": 504,
            "provider_error": 502,
            "malformed_response": 502,
        }.get(e.reason, 502)
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": "frame_refine_failed",
                "reason": e.reason,
                "message": e.message,
            },
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in refine_single_frame: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/storyboard/refine_batch")
def refine_storyboard_batch(script_id: str):
    """Batch-refine frames via desktop SSE or a durable server-mode job."""
    if server_mode_enabled():
        require_workspace_script(script_id)
        return enqueue_long_workspace_job(
            "refine_batch", {"script_id": script_id}
        )

    from fastapi.responses import StreamingResponse

    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    def event_stream():
        for event_type, data in pipeline.refine_batch_generator(script_id):
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/projects/{script_id}/generate_storyboard")
def generate_storyboard(script_id: str):
    """Triggers storyboard generation."""
    try:
        if server_mode_enabled():
            require_workspace_script(script_id)
            return enqueue_long_workspace_job(
                "generate_storyboard", {"script_id": script_id}
            )
        updated_script = pipeline.generate_storyboard(script_id)
        return signed_response(updated_script)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/generate_video")
def generate_video(script_id: str):
    """Triggers video generation."""
    try:
        if server_mode_enabled():
            require_workspace_script(script_id)
            return enqueue_long_workspace_job(
                "generate_video", {"script_id": script_id}
            )
        updated_script = pipeline.generate_video(script_id)
        return signed_response(updated_script)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CreateVideoTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: Optional[str] = Field(default=None, max_length=8192)
    prompt: str = Field(min_length=1, max_length=50_000)
    frame_id: Optional[str] = Field(default=None, max_length=128)
    source_image_id: Optional[str] = Field(default=None, max_length=256)
    frame_type: Optional[CameraMovementType] = None
    duration: int = Field(5, ge=4, le=15)
    seed: Optional[int] = None
    resolution: str = Field(default="720p", max_length=32)
    generate_audio: bool = False
    batch_size: int = Field(1, ge=1, le=4)
    model: Optional[str] = Field(default=None, max_length=128)
    generation_mode: str = "i2v"
    ratio: Optional[str] = Field(default=None, max_length=32)
    watermark: Optional[bool] = None
    # Source tab in the Storyboard R2V workbench. Distinct from
    # generation_mode (backend dispatcher hint) — used by the candidates
    # panel to group takes per UI tab on refresh.
    workbench_tab: Optional[str] = Field(default=None, max_length=32)  # 't2i_i2v' | 'direct_r2v'

    @field_validator("model")
    @classmethod
    def validate_video_model(cls, value):
        if value is not None:
            get_model_spec(value, VIDEO)
        return value

    @field_validator("generation_mode")
    @classmethod
    def validate_generation_mode(cls, value):
        if value not in {"t2v", "i2v"}:
            raise ValueError("New API video generation supports only t2v and i2v")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_video_prompt(cls, value):
        if not value.strip():
            raise ValueError("Clip generation requires a motion prompt")
        return value.strip()

    @field_validator("resolution")
    @classmethod
    def validate_clip_resolution(cls, value):
        normalized = value.strip().lower()
        if normalized != "720p":
            raise ValueError("Image-to-video clip generation supports only 720p")
        return normalized

    @field_validator("ratio")
    @classmethod
    def validate_clip_ratio(cls, value):
        normalized = (value or "16:9").strip()
        if normalized not in {"16:9", "9:16", "1:1"}:
            raise ValueError("Video aspect ratio must be 16:9, 9:16, or 1:1")
        return normalized


def process_video_task(script_id: str, task_id: str):
    """Background task to generate video.

    The pipeline method has its own try/except that flips status to
    "failed" on errors during generation. This outer wrapper is a
    belt-and-suspenders writeback for exceptions that escape *before*
    that inner handler armed (e.g. `get_script` raising, persistence
    layer crashing). Without it the task would stay forever-`pending`
    and the UI would show an eternal spinner.
    """
    try:
        pipeline.process_video_task(script_id, task_id)
    except Exception as e:
        logger.exception(f"Error processing video task {task_id}")
        try:
            pipeline.mark_video_task_failed(
                script_id, task_id, f"Background error: {e}"
            )
        except Exception:
            logger.exception(
                f"Could not mark video task {task_id} as failed after wrapper exception"
            )


class AnnotateVideoTaskRequest(BaseModel):
    """User-review annotations on a video task ("抽卡 review").
    Both fields optional so the same endpoint covers star-only,
    label-only, or both. `clear_label=True` explicitly removes
    the label (None on its own means "don't change")."""
    is_starred: Optional[bool] = None
    label: Optional[str] = None
    clear_label: bool = False


@app.patch("/projects/{script_id}/video_tasks/{task_id}/annotate", response_model=VideoTask)
def annotate_video_task(script_id: str, task_id: str, request: AnnotateVideoTaskRequest):
    """Set the user's star + label on a video task. Used by Storyboard's
    candidates panel for shortlist marking (multi-select) and short
    free-text notes (≤20 chars, truncated server-side)."""
    task = pipeline.annotate_video_task(
        script_id,
        task_id,
        is_starred=request.is_starred,
        label=request.label,
        clear_label=request.clear_label,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task)


class UpdateFrameWorkbenchRequest(BaseModel):
    """Storyboard R2V workbench state writeback. Every field optional;
    only what the caller passes gets updated. The server clamps lists
    and indices to safe ranges; rejects unknown enum values."""
    workbench_tab_mode: Optional[str] = None  # 't2i_i2v' | 'direct_r2v'
    t2i_image_urls: Optional[List[str]] = None  # full ordered history, server caps at 10 FIFO
    t2i_selected_index: Optional[int] = None  # active首帧 index, clamped to range
    workbench_generate_count: Optional[int] = None  # batch size, clamped to [1, 6]
    clip_start_image_id: Optional[str] = Field(default=None, max_length=256)
    clip_start_image_url: Optional[str] = Field(default=None, max_length=8192)
    video_prompt: Optional[str] = Field(default=None, max_length=50_000)


@app.patch("/projects/{script_id}/frames/{frame_id}/workbench", response_model=StoryboardFrame)
def update_frame_workbench(
    script_id: str, frame_id: str, request: UpdateFrameWorkbenchRequest
):
    """Persist Storyboard R2V workbench state onto a frame so it
    survives refresh and cross-device opens. Previously this state
    lived only in React component state and got lost on reload."""
    try:
        frame = pipeline.update_frame_workbench(
            script_id,
            frame_id,
            workbench_tab_mode=request.workbench_tab_mode,
            t2i_image_urls=request.t2i_image_urls,
            t2i_selected_index=request.t2i_selected_index,
            workbench_generate_count=request.workbench_generate_count,
            clip_start_image_id=request.clip_start_image_id,
            clip_start_image_url=request.clip_start_image_url,
            video_prompt=request.video_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if frame is None:
        raise HTTPException(status_code=404, detail="Script or frame not found")
    return signed_response(frame)


@app.delete(
    "/projects/{script_id}/frames/{frame_id}/t2i-images/{image_index}",
    response_model=StoryboardFrame,
)
def delete_frame_t2i_image(script_id: str, frame_id: str, image_index: int):
    """Delete one first-frame candidate and reclaim its unshared server file."""

    try:
        frame, removed_url = pipeline.delete_frame_t2i_image(
            script_id, frame_id, image_index
        )
        reclaim_deleted_media(removed_url)
        return signed_response(frame)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{script_id}/video_tasks/{task_id}/cancel", response_model=VideoTask)
def cancel_video_task(script_id: str, task_id: str):
    """Cancel queued work; running provider calls must finish naturally."""
    if server_mode_enabled():
        tenant = get_tenant(required=True)
        assert tenant is not None
        from ..server.database import get_database
        from ..server.jobs import (
            JobCancellationOutcome,
            cancel_workspace_job_with_record,
        )

        outcome, _record = cancel_workspace_job_with_record(
            get_database(), workspace_id=tenant.workspace_id, job_id=task_id
        )
        if outcome is JobCancellationOutcome.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Video job not found")
        if outcome is JobCancellationOutcome.RUNNING:
            raise HTTPException(
                status_code=409,
                detail="Running video jobs cannot be canceled safely; wait for completion",
            )
        if outcome is JobCancellationOutcome.FINISHED:
            raise HTTPException(status_code=409, detail="Video job is already finished")
    else:
        ok = pipeline.mark_video_task_canceled(script_id, task_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail="Video task not found or already completed",
            )
    script = pipeline.get_script(script_id)
    task = next(
        (t for t in (script.video_tasks if script else []) if t.id == task_id),
        None,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task)


@app.post("/projects/{script_id}/video_tasks/{task_id}/retry", response_model=VideoTask)
def retry_video_task(
    script_id: str, task_id: str, background_tasks: BackgroundTasks
):
    """Retry the same persisted task with its original generation parameters."""
    if server_mode_enabled():
        tenant = get_tenant(required=True)
        assert tenant is not None
        from ..server.database import get_database
        from ..server.jobs import (
            JobQueueUnavailableError,
            JobRetryOutcome,
            retry_workspace_job,
        )

        try:
            outcome, _record = retry_workspace_job(
                get_database(), workspace_id=tenant.workspace_id, job_id=task_id
            )
        except JobQueueUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if outcome is JobRetryOutcome.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Video job not found")
        if outcome is JobRetryOutcome.NOT_FAILED:
            raise HTTPException(status_code=409, detail="Only failed video jobs can be retried")
        if outcome is JobRetryOutcome.CAPACITY:
            raise HTTPException(status_code=429, detail="The generation queue is full")
    else:
        if not pipeline.prepare_video_task_retry(script_id, task_id):
            raise HTTPException(status_code=409, detail="Only failed video tasks can be retried")
        background_tasks.add_task(pipeline.process_video_task, script_id, task_id)

    script = pipeline.get_script(script_id)
    task = next(
        (item for item in (script.video_tasks if script else []) if item.id == task_id),
        None,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task)


@app.delete("/projects/{script_id}/video_tasks/{task_id}")
def delete_video_task(script_id: str, task_id: str):
    """Delete a terminal video task, its UI pointers, and unshared media."""

    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    task = next((item for item in script.video_tasks if item.id == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Video task not found")
    if task.status in {"pending", "processing"}:
        raise HTTPException(
            status_code=409,
            detail="Running video tasks cannot be deleted; cancel or wait for completion",
        )
    try:
        updated_script = pipeline.delete_video_task(script_id, task_id)
        reclaim_deleted_media(task.model_dump())
        return get_project(updated_script.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{script_id}/video_tasks", response_model=List[VideoTask])
def create_video_task(script_id: str, request: CreateVideoTaskRequest, background_tasks: BackgroundTasks):
    """Creates new video generation tasks."""
    reservations = []
    tasks: List[VideoTask] = []
    reserved_ids: List[Optional[str]] = [None] * request.batch_size
    try:
        # Motion clips are always shot-scoped. Resolve the authoritative URL
        # and canonical camera behavior before reserving queue capacity or
        # persisting any task. Test doubles used by queue unit tests predate
        # this domain method and intentionally skip the real-pipeline guard.
        canonical_image_url = request.image_url
        canonical_frame_type = request.frame_type.value if request.frame_type else None
        if hasattr(pipeline, "validate_clip_generation_request"):
            _frame, canonical_image_url, canonical_frame_type = (
                pipeline.validate_clip_generation_request(
                    script_id,
                    request.frame_id,
                    request.source_image_id,
                    request.image_url,
                    canonical_frame_type,
                )
            )
        if server_mode_enabled():
            reserved_ids = [str(uuid.uuid4()) for _ in range(request.batch_size)]
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "video",
                        "payload": {
                            "script_id": script_id,
                            "task_id": task_id,
                            "prompt": request.prompt,
                            "generation_mode": request.generation_mode,
                            "frame_id": request.frame_id,
                            "source_image_id": request.source_image_id,
                            "image_url": canonical_image_url,
                            "frame_type": canonical_frame_type,
                            "model": request.model,
                            "duration": request.duration,
                            "resolution": request.resolution,
                            "ratio": request.ratio,
                            "seed": request.seed,
                            "generate_audio": request.generate_audio,
                            "batch_size": request.batch_size,
                            "watermark": request.watermark,
                            "workbench_tab": request.workbench_tab,
                        },
                        "job_id": task_id,
                    }
                    for task_id in reserved_ids
                ]
            )

        for requested_task_id in reserved_ids:
            script, task_id = pipeline.create_video_task(
                script_id=script_id,
                image_url=canonical_image_url,
                prompt=request.prompt,
                frame_id=request.frame_id,
                source_image_id=request.source_image_id,
                source_image_url=canonical_image_url,
                frame_type=canonical_frame_type,
                duration=request.duration,
                seed=request.seed,
                resolution=request.resolution,
                generate_audio=request.generate_audio,
                model=request.model,
                generation_mode=request.generation_mode,
                ratio=request.ratio,
                watermark=request.watermark,
                workbench_tab=request.workbench_tab,
                task_id=requested_task_id,
            )
            # Find the created task object
            created_task = next((t for t in script.video_tasks if t.id == task_id), None)
            if created_task:
                tasks.append(created_task)

            if not server_mode_enabled():
                background_tasks.add_task(pipeline.process_video_task, script_id, task_id)

        if server_mode_enabled():
            publish_workspace_job_reservations(reservations)

        return signed_response(tasks)

    except HTTPException as exc:
        if (
            server_mode_enabled()
            and reservations
            and tasks
            and exc.status_code == 503
        ):
            from .video_failures import (
                VIDEO_QUEUE_UNAVAILABLE_CODE,
                VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
            )

            for task in tasks:
                pipeline.mark_video_task_failed(
                    script_id,
                    task.id,
                    VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
                    error_code=VIDEO_QUEUE_UNAVAILABLE_CODE,
                    error_diagnostic=str(exc.detail),
                    overwrite=True,
                )
            script = pipeline.get_script(script_id)
            failed_tasks = [
                item
                for item in (script.video_tasks if script else [])
                if item.id in {task.id for task in tasks}
            ]
            return signed_response(failed_tasks)
        if server_mode_enabled() and reservations:
            for task_id in reserved_ids:
                assert task_id is not None
                pipeline.rollback_video_task(script_id, task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        # Validation failures from pipeline.create_video_task (model⇄
        # mode⇄refs mismatch, missing references for R2V, etc.) are
        # the user's responsibility to fix — surface as 400 so the
        # frontend can show an inline error instead of treating it
        # as a server error.
        if server_mode_enabled() and reservations:
            for task_id in reserved_ids:
                assert task_id is not None
                pipeline.rollback_video_task(script_id, task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("An error occurred")
        if server_mode_enabled() and reservations:
            for task_id in reserved_ids:
                assert task_id is not None
                pipeline.rollback_video_task(script_id, task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/generate")
def generate_single_asset(script_id: str, request: GenerateAssetRequest, background_tasks: BackgroundTasks):
    """Generates a single asset with specific options (async).
    Returns immediately with task_id for polling progress."""
    reservations = []
    task_id = str(uuid.uuid4())
    try:
        script = require_workspace_script(script_id)
        target_asset, _ = pipeline._find_asset_with_source(
            script, request.asset_id, request.asset_type
        )
        if target_asset is None:
            raise ValueError(
                f"{request.asset_type.capitalize()} {request.asset_id} not found"
            )
        previous_asset_status = target_asset.status.value
        if server_mode_enabled():
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "project_asset",
                        "payload": {
                            "script_id": script_id,
                            **request.model_dump(),
                            "previous_asset_status": previous_asset_status,
                        },
                        "job_id": task_id,
                    }
                ]
            )
        script, task_id = pipeline.create_asset_generation_task(
            script_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt,
            request.batch_size,
            request.model_name,
            request.aspect_ratio,
            task_id=task_id,
        )

        if server_mode_enabled():
            publish_workspace_job_reservations(reservations)
            pipeline.forget_asset_generation_task(task_id)
        else:
            background_tasks.add_task(pipeline.process_asset_generation_task, task_id)

        # Return script with task_id for frontend polling
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data)

    except HTTPException:
        if server_mode_enabled() and reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_asset_generation_task(task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """Returns the status of an asset generation task for polling."""
    if server_mode_enabled():
        tenant = get_tenant(required=True)
        assert tenant is not None
        from ..server.database import get_database
        from ..server.jobs import get_workspace_job, job_to_dict

        record = get_workspace_job(
            get_database(), workspace_id=tenant.workspace_id, job_id=task_id
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        status = job_to_dict(record)
        if status["status"] == "completed" and status.get("script_id"):
            script = pipeline.get_script(status["script_id"])
            if script:
                status["script"] = signed_response(script).body.decode("utf-8")
        return status

    status = pipeline.get_asset_generation_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")

    # If completed, return the updated script as well
    if status["status"] == "completed":
        script = pipeline.get_script(status["script_id"])
        if script:
            status["script"] = signed_response(script).body.decode('utf-8')

    return status


class GenerateAssetVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Optional[str] = None
    duration: int = 5
    aspect_ratio: Optional[str] = None
    model: Optional[str] = None

    @field_validator("model")
    @classmethod
    def validate_video_model(cls, value):
        if value is not None:
            get_model_spec(value, VIDEO)
        return value


@app.post("/projects/{script_id}/assets/{asset_type}/{asset_id}/generate_video", response_model=Script)
def generate_asset_video(script_id: str, asset_type: str, asset_id: str, request: GenerateAssetVideoRequest, background_tasks: BackgroundTasks):
    """Generates a video for a specific asset (I2V)."""
    reservations = []
    task_id = str(uuid.uuid4())
    try:
        if server_mode_enabled():
            reservations = reserve_workspace_jobs(
                [
                    {
                        "job_type": "video",
                        "payload": {
                            "script_id": script_id,
                            "task_id": task_id,
                            "prompt": request.prompt,
                            "generation_mode": "i2v",
                        },
                        "job_id": task_id,
                    }
                ]
            )
        script, task_id = pipeline.create_asset_video_task(
            script_id,
            asset_id,
            asset_type,
            request.prompt,
            request.duration,
            request.aspect_ratio,
            request.model,
            task_id=task_id,
        )

        if server_mode_enabled():
            publish_workspace_job_reservations(reservations)
        else:
            background_tasks.add_task(pipeline.process_video_task, script_id, task_id)

        return signed_response(script)

    except HTTPException as exc:
        if server_mode_enabled() and reservations and exc.status_code == 503:
            from .video_failures import (
                VIDEO_QUEUE_UNAVAILABLE_CODE,
                VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
            )

            pipeline.mark_video_task_failed(
                script_id,
                task_id,
                VIDEO_QUEUE_UNAVAILABLE_MESSAGE,
                error_code=VIDEO_QUEUE_UNAVAILABLE_CODE,
                error_diagnostic=str(exc.detail),
                overwrite=True,
            )
            failed_script = pipeline.get_script(script_id)
            if failed_script is not None:
                return signed_response(failed_script)
        if server_mode_enabled() and reservations:
            pipeline.rollback_video_task(script_id, task_id)
        abandon_workspace_job_reservations(reservations)
        raise
    except (ValueError, MissingNewAPIKeyError) as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_video_task(script_id, task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if server_mode_enabled() and reservations:
            pipeline.rollback_video_task(script_id, task_id)
        abandon_workspace_job_reservations(reservations)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/projects/{script_id}/assets/{asset_type}/{asset_id}/videos/{video_id}")
def delete_asset_video(script_id: str, asset_type: str, asset_id: str, video_id: str):
    """Deletes a video from an asset."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted_snapshot = _project_scope_snapshot(script)
        updated_script = pipeline.delete_asset_video(
            script_id,
            asset_id,
            asset_type,
            video_id
        )
        reclaim_deleted_media(deleted_snapshot)
        return get_project(updated_script.id)
    except ValueError as e:
        if "Running video tasks" in str(e):
            raise HTTPException(status_code=409, detail=str(e)) from e
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/toggle_lock", response_model=Script)
def toggle_asset_lock(script_id: str, request: ToggleLockRequest):
    """Toggles the locked status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_lock(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/toggle_starred", response_model=Script)
def toggle_asset_starred(script_id: str, request: ToggleLockRequest):
    """Toggles the starred (library shortlist) status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_starred(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/update_image", response_model=Script)
def update_asset_image(script_id: str, request: UpdateAssetImageRequest):
    """Updates an asset's image URL manually."""
    try:
        updated_script = pipeline.update_asset_image(
            script_id,
            request.asset_id,
            request.asset_type,
            request.image_url
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/update_attributes", response_model=Script)
def update_asset_attributes(script_id: str, request: UpdateAssetAttributesRequest):
    """Updates allowlisted presentation attributes of an asset."""
    try:
        updated_script = pipeline.update_asset_attributes(
            script_id,
            request.asset_id,
            request.asset_type,
            request.attributes
        )
        return signed_response(updated_script)
    except InvalidAssetAttributesError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateAssetDescriptionRequest(BaseModel):
    asset_id: str = Field(max_length=128)
    asset_type: str = Field(max_length=32)
    description: str = Field(max_length=20_000)


@app.post("/projects/{script_id}/assets/update_description", response_model=Script)
def update_asset_description(script_id: str, request: UpdateAssetDescriptionRequest):
    """Updates an asset's description."""
    try:
        updated_script = pipeline.update_asset_description(
            script_id,
            request.asset_id,
            request.asset_type,
            request.description
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SelectVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str
    generation_type: str = None  # For character: "full_body", "three_view", "headshot"

@app.post("/projects/{script_id}/assets/variant/select")
def select_asset_variant(script_id: str, request: SelectVariantRequest):
    """Selects a specific variant for an asset."""
    try:
        updated_script = pipeline.select_asset_variant(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id,
            request.generation_type
        )
        # Return the same Episode > Series > Global projection as GET
        # /projects/{id}. A raw Script contains only episode-local assets and
        # would make inherited assets disappear from the editor until refresh.
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeleteVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str

@app.post("/projects/{script_id}/assets/variant/delete")
def delete_asset_variant(script_id: str, request: DeleteVariantRequest):
    """Deletes a specific variant from an asset."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted_snapshot = _project_scope_snapshot(script)
        updated_script = pipeline.delete_asset_variant(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id
        )
        reclaim_deleted_media(deleted_snapshot)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FavoriteVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str
    generation_type: Optional[str] = None  # For character: 'full_body', 'three_view', 'headshot'
    is_favorited: bool

@app.post("/projects/{script_id}/assets/variant/favorite")
def toggle_variant_favorite(script_id: str, request: FavoriteVariantRequest):
    """Toggles the favorite status of a variant. Favorited variants won't be auto-deleted when limit is reached."""
    try:
        updated_script = pipeline.toggle_variant_favorite(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id,
            request.is_favorited,
            request.generation_type
        )
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/projects/{script_id}/model_settings", response_model=Script)
def update_model_settings(script_id: str, request: UpdateModelSettingsRequest):
    """Updates project's model settings for T2I/I2I/I2V and aspect ratios."""
    try:
        updated_script = pipeline.update_model_settings(
            script_id,
            request.t2i_model,
            request.i2i_model,
            request.i2v_model,
            character_aspect_ratio=request.character_aspect_ratio,
            scene_aspect_ratio=request.scene_aspect_ratio,
            prop_aspect_ratio=request.prop_aspect_ratio,
            storyboard_aspect_ratio=request.storyboard_aspect_ratio,
            image_model=request.image_model,
            chat_model=request.chat_model,
            video_model=request.video_model,
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdatePromptConfigRequest(BaseModel):
    storyboard_polish: str = ""
    video_polish: str = ""
    entity_extraction: str = ""
    style_analysis: str = ""
    storyboard_extraction: str = ""


@app.get("/projects/{script_id}/prompt_config")
def get_prompt_config(script_id: str):
    """Returns project prompt_config and system default prompts for reference."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")
        config = script.prompt_config if hasattr(script, 'prompt_config') else PromptConfig()
        return {
            "prompt_config": config.model_dump(),
            "defaults": {
                "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
                "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
                "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/projects/{script_id}/prompt_config")
def update_prompt_config(script_id: str, request: UpdatePromptConfigRequest):
    """Updates project custom prompt configuration. Empty string = use system default."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")
        existing = getattr(script, "prompt_config", None)
        script.prompt_config = PromptConfig(
            storyboard_polish=request.storyboard_polish,
            video_polish=request.video_polish,
            entity_extraction=request.entity_extraction,
            style_analysis=request.style_analysis,
            storyboard_extraction=request.storyboard_extraction,
        )
        pipeline._save_data()
        return {"prompt_config": script.prompt_config.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/prompt_defaults")
def get_prompt_defaults():
    """Return the built-in default system prompts for all configurable prompt keys.

    The frontend uses this to pre-fill the prompt fields and to compute the
    "delta" on save (a field equal to its default is stored as "" so the
    built-in default is used instead of pinning a snapshot).
    """
    return {
        "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
        "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
        "entity_extraction": DEFAULT_ENTITY_EXTRACTION_PROMPT,
        "style_analysis": DEFAULT_STYLE_ANALYSIS_PROMPT,
        "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
    }


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# PR-3k · Audio mix endpoints (BGM presets + per-script mix settings)
# Backs the Assembly Mix phase.
# ─────────────────────────────────────────────────────────────

@app.get("/bgm/presets")
def list_bgm_presets():
    """Return the BGM preset catalog. UI populates the Mix phase picker."""
    from .audio import get_bgm_presets
    return get_bgm_presets()


class AudioMixRequest(BaseModel):
    bgm_url: Optional[str] = None  # null clears bgm
    dialogue_volume: Optional[int] = None  # 0-100
    bgm_volume: Optional[int] = None
    sfx_volume: Optional[int] = None


@app.put("/projects/{script_id}/audio_mix", response_model=Script)
def update_audio_mix(script_id: str, request: AudioMixRequest):
    """Set BGM + per-track mix levels for the final merge.

    PATCH-style: only fields explicitly present in the request body are
    applied. Using `model_fields_set` (Pydantic v2) lets us distinguish
    "omitted" from "explicit null" — clients pass `bgm_url: null` to clear
    the BGM, which the old `is not None` check silently swallowed.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    fields_set = request.model_fields_set
    if "bgm_url" in fields_set:
        script.bgm_url = request.bgm_url or None  # null and "" both clear
    mix = dict(script.mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60})
    if "dialogue_volume" in fields_set and request.dialogue_volume is not None:
        mix["dialogue"] = max(0, min(100, request.dialogue_volume))
    if "bgm_volume" in fields_set and request.bgm_volume is not None:
        mix["bgm"] = max(0, min(100, request.bgm_volume))
    if "sfx_volume" in fields_set and request.sfx_volume is not None:
        mix["sfx"] = max(0, min(100, request.sfx_volume))
    script.mix_settings = mix
    pipeline._save_data()
    return signed_response(script)


class DubPreviewRequest(BaseModel):
    video_task_id: str
    offset_ms: int = 0


@app.post("/projects/{script_id}/frames/{frame_id}/dub/preview")
def preview_dub(script_id: str, frame_id: str, request: DubPreviewRequest):
    """Generate a preview dub (cached Demucs + fast adelay+amix+mux)."""
    try:
        if server_mode_enabled():
            script = require_workspace_script(script_id)
            if not any(frame.id == frame_id for frame in script.frames):
                raise HTTPException(status_code=404, detail=f"Frame {frame_id} not found")
            return enqueue_long_workspace_job(
                "dub_preview",
                {
                    "script_id": script_id,
                    "frame_id": frame_id,
                    "video_task_id": request.video_task_id,
                    "offset_ms": request.offset_ms,
                },
            )
        updated_script = pipeline.preview_dub(
            script_id, frame_id,
            video_task_id=request.video_task_id,
            offset_ms=request.offset_ms,
        )
        return signed_response(updated_script)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/dub/apply")
def apply_dub(script_id: str, frame_id: str):
    """Promote current preview to official dubbed video."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted_snapshot = script.model_dump()
        updated_script = pipeline.apply_dub(script_id, frame_id)
        reclaim_deleted_media(deleted_snapshot)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/projects/{script_id}/frames/{frame_id}/dub")
def revert_frame_dub(script_id: str, frame_id: str):
    """Revert dubbing — remove dubbed+preview, keep bg cache."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted_snapshot = script.model_dump()
        updated_script = pipeline.revert_dub(script_id, frame_id)
        reclaim_deleted_media(deleted_snapshot)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ToggleFrameLockRequest(BaseModel):
    frame_id: str


@app.post("/projects/{script_id}/frames/toggle_lock", response_model=Script)
def toggle_frame_lock(script_id: str, request: ToggleFrameLockRequest):
    """Toggles the locked status of a frame."""
    try:
        updated_script = pipeline.toggle_frame_lock(
            script_id,
            request.frame_id
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateFrameRequest(BaseModel):
    frame_id: str = Field(max_length=128)
    image_prompt: Optional[str] = Field(default=None, max_length=50_000)
    action_description: Optional[str] = Field(default=None, max_length=20_000)
    dialogue: Optional[str] = Field(default=None, max_length=20_000)
    camera_angle: Optional[str] = Field(default=None, max_length=200)
    scene_id: Optional[str] = Field(default=None, max_length=128)
    character_ids: Optional[List[str]] = Field(default=None, max_length=100)
    prop_ids: Optional[List[str]] = Field(default=None, max_length=100)
    duration: Optional[int] = Field(default=None, ge=1, le=600)
    shot_size: Optional[str] = Field(default=None, max_length=200)
    camera_movement: Optional[CameraMovementType] = None
    camera_movement_description: Optional[str] = Field(default=None, max_length=20_000)
    transition_hint: Optional[str] = Field(default=None, max_length=2_000)

@app.post("/projects/{script_id}/frames/update", response_model=Script)
def update_frame(script_id: str, request: UpdateFrameRequest):
    """Updates frame data (prompt, scene, characters, etc.)."""
    try:
        updated_script = pipeline.update_frame(
            script_id,
            request.frame_id,
            image_prompt=request.image_prompt,
            action_description=request.action_description,
            dialogue=request.dialogue,
            camera_angle=request.camera_angle,
            scene_id=request.scene_id,
            character_ids=request.character_ids,
            prop_ids=request.prop_ids,
            duration=request.duration,
            shot_size=request.shot_size,
            camera_movement=(
                request.camera_movement.value if request.camera_movement else None
            ),
            camera_movement_description=request.camera_movement_description,
            transition_hint=request.transition_hint,
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddFrameRequest(BaseModel):
    scene_id: Optional[str] = Field(default=None, max_length=128)
    character_ids: Optional[List[str]] = Field(default=None, max_length=100)
    prop_ids: Optional[List[str]] = Field(default=None, max_length=100)
    action_description: str = Field(default="", max_length=20_000)
    camera_angle: str = Field(default="medium_shot", max_length=200)
    insert_at: Optional[int] = Field(default=None, ge=0, le=10_000)

@app.post("/projects/{script_id}/frames", response_model=Script)
def add_frame(script_id: str, request: AddFrameRequest):
    """Adds a new storyboard frame."""
    try:
        updated_script = pipeline.add_frame(
            script_id,
            request.scene_id,
            request.action_description,
            request.camera_angle,
            request.insert_at,
            character_ids=request.character_ids,
            prop_ids=request.prop_ids,
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/frames/{frame_id}")
def delete_frame(script_id: str, frame_id: str):
    """Delete a storyboard frame and every frame-owned generation record."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        deleted = next((item for item in script.frames if item.id == frame_id), None)
        if deleted is None:
            raise ValueError("Frame not found")
        deleted_snapshot = {
            "frame": deleted.model_dump(),
            "video_tasks": [
                task.model_dump() for task in script.video_tasks if task.frame_id == frame_id
            ],
        }
        removed_task_ids = [task["id"] for task in deleted_snapshot["video_tasks"]]
        updated_script = pipeline.delete_frame(script_id, frame_id)
        if server_mode_enabled():
            tenant = get_tenant(required=True)
            assert tenant is not None
            from ..server.database import get_database
            from ..server.jobs import delete_frame_generation_jobs

            deleted_snapshot["generation_jobs"] = delete_frame_generation_jobs(
                get_database(),
                workspace_id=tenant.workspace_id,
                script_id=script_id,
                frame_id=frame_id,
                task_ids=removed_task_ids,
            )
        reclaim_deleted_media(deleted_snapshot)
        return get_project(updated_script.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CopyFrameRequest(BaseModel):
    frame_id: str
    insert_at: Optional[int] = None

@app.post("/projects/{script_id}/frames/copy", response_model=Script)
def copy_frame(script_id: str, request: CopyFrameRequest):
    """Copies a storyboard frame."""
    try:
        updated_script = pipeline.copy_frame(script_id, request.frame_id, request.insert_at)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ReorderFramesRequest(BaseModel):
    frame_ids: List[str]

@app.put("/projects/{script_id}/frames/reorder", response_model=Script)
def reorder_frames(script_id: str, request: ReorderFramesRequest):
    """Reorders storyboard frames."""
    try:
        updated_script = pipeline.reorder_frames(script_id, request.frame_ids)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RenderFrameRequest(BaseModel):
    frame_id: str = Field(max_length=128)
    composition_data: Optional[Dict[str, Any]] = Field(default=None, max_length=256)
    prompt: str = Field(max_length=50_000)
    batch_size: int = Field(1, ge=1, le=4)


@app.post("/projects/{script_id}/storyboard/render")
def render_frame(script_id: str, request: RenderFrameRequest):
    """Renders a specific frame using composition data (I2I)."""
    try:
        logger.info(f"Rendering frame {request.frame_id}")
        if server_mode_enabled():
            script = require_workspace_script(script_id)
            if not any(frame.id == request.frame_id for frame in script.frames):
                raise HTTPException(
                    status_code=404,
                    detail=f"Frame {request.frame_id} not found",
                )
            return enqueue_long_workspace_job(
                "storyboard_render",
                {
                    "script_id": script_id,
                    "frame_id": request.frame_id,
                    "composition_data": request.composition_data,
                    "prompt": request.prompt,
                    "batch_size": request.batch_size,
                },
            )

        updated_script = pipeline.generate_storyboard_render(
            script_id,
            request.frame_id,
            request.composition_data,
            request.prompt,
            request.batch_size
        )
        return signed_response(updated_script)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error rendering frame {request.frame_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SelectVideoRequest(BaseModel):
    video_id: str


@app.post("/projects/{script_id}/frames/{frame_id}/select_video", response_model=Script)
def select_video(script_id: str, frame_id: str, request: SelectVideoRequest):
    """Selects a video variant for a specific frame."""
    try:
        updated_script = pipeline.select_video_for_frame(script_id, frame_id, request.video_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/auto_select_latest_video", response_model=Script)
def auto_select_latest_video(script_id: str, frame_id: str):
    """Auto-pick the latest completed video as this frame's active take.

    Idempotent; skipped when the frame is pinned (is_video_pinned=True).
    Frontend calls this on every task-completion poll so the freshly
    generated take surfaces on the hero — unless the user has explicitly
    pinned a different take.
    """
    try:
        updated_script = pipeline.auto_select_latest_video(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/unpin_video", response_model=Script)
def unpin_video(script_id: str, frame_id: str):
    """Clear the manual pin; auto_select_latest_video resumes on next poll.

    Leaves selected_video_id / video_url untouched — the user keeps seeing
    the current take until a new generation produces a newer completed
    task.
    """
    try:
        updated_script = pipeline.unpin_video(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExtractLastFrameRequest(BaseModel):
    video_task_id: str


@app.post("/projects/{script_id}/frames/{frame_id}/extract_last_frame")
def extract_last_frame(script_id: str, frame_id: str, request: ExtractLastFrameRequest):
    """Extract the last frame from a completed video and add it as a variant to the frame's rendered_image_asset."""
    try:
        updated_script = pipeline.extract_last_frame(script_id, frame_id, request.video_task_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception(f"Error extracting last frame: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/upload_image")
def upload_frame_image(script_id: str, frame_id: str, file: UploadFile = File(...)):
    """Upload an image as a variant for a frame's rendered_image_asset."""
    try:
        # Save file locally first
        saved = save_upload_file(file, workspace_output_path("uploads"), IMAGE_UPLOAD_POLICY)
        enforce_upload_quota(saved.path)

        updated_script = pipeline.upload_frame_image(script_id, frame_id, saved.path)
        return signed_response(updated_script)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error uploading frame image: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Issue 10: T2I 首帧 upload — uploaded image is appended to the frame's
# t2i_image_urls history and becomes the active首帧 immediately, unlocking
# Step 2 (I2V generation). Whitelisted formats + 8 MB cap reflect that this
# is a user-facing creator upload, not an arbitrary file store.
_T2I_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
_T2I_UPLOAD_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/projects/{script_id}/frames/{frame_id}/upload_t2i")
async def upload_t2i_frame(script_id: str, frame_id: str, file: UploadFile = File(...)):
    """Upload an external image as a T2I首帧 candidate for an I2V flow.

    Validation:
      - ≤ 8 MB (rejected with 413 to differentiate from validation 400)
      - jpg/jpeg/png/webp only (rejected with 415)

    On success, returns the updated StoryboardFrame so the client can read
    back `t2i_image_urls` and `t2i_selected_index` without an extra round-
    trip. The uploaded image becomes the active首帧 (auto-selected).
    """
    try:
        original_name = (file.filename or "").strip()
        if not original_name:
            raise HTTPException(status_code=400, detail="No filename provided")
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in _T2I_UPLOAD_ALLOWED_EXTS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type {ext!r}. Allowed: {sorted(_T2I_UPLOAD_ALLOWED_EXTS)}",
            )

        # Stream-to-disk with explicit byte cap so we never load >8 MB
        # into memory if a client lies about Content-Length.
        filename = f"t2i_{uuid.uuid4().hex}{ext}"
        rel_path = os.path.join("uploads", filename)
        abs_path = os.path.join(current_output_root(), rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        size = 0
        try:
            with open(abs_path, "wb") as buffer:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > _T2I_UPLOAD_MAX_BYTES:
                        buffer.close()
                        os.unlink(abs_path)
                        raise HTTPException(
                            status_code=413,
                            detail=f"File exceeds {_T2I_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit",
                        )
                    buffer.write(chunk)
            await asyncio.to_thread(enforce_upload_quota, abs_path)
        except HTTPException:
            raise
        except Exception as e:
            # Clean up partial file on any unexpected error
            if os.path.exists(abs_path):
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"Upload write failed: {e}")

        # `pipeline.upload_t2i_frame` synchronously serializes + writes the
        # whole projects.json (~50-500KB) — wrap in to_thread so we don't
        # block the event loop for the ~50ms write. Matches the pattern
        # used by create_project / reparse_project / analyze_script_for_styles.
        frame = await asyncio.to_thread(
            pipeline.upload_t2i_frame, script_id, frame_id, rel_path
        )
        if frame is None:
            # Roll back the file — frame/script gone, no reference will exist
            if os.path.exists(abs_path):
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise HTTPException(status_code=404, detail="Script or frame not found")
        return signed_response(frame)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upload_t2i_frame unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/merge")
def merge_videos(script_id: str):
    """Merge all selected frame videos into final output"""
    import traceback
    try:
        if server_mode_enabled():
            require_workspace_script(script_id)
            return enqueue_long_workspace_job("merge", {"script_id": script_id})
        merged_script = pipeline.merge_videos(script_id)
        return signed_response(merged_script)
    except HTTPException:
        raise
    except ValueError as e:
        # Known validation errors (no videos, etc.)
        logger.error(f"[MERGE ERROR] Validation failed: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # FFmpeg or processing errors
        logger.error(f"[MERGE ERROR] Runtime error: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[MERGE ERROR] Unexpected error: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=f"Merge failed: {str(e)}")


# ===== Export Endpoint =====

class ExportRequest(BaseModel):
    resolution: str = Field(default="1080p", max_length=32)
    format: str = Field(default="mp4", max_length=16)
    subtitles: str = Field(default="none", max_length=32)

@app.post("/projects/{script_id}/export")
def export_project(script_id: str, request: ExportRequest):
    """Export a project video using the requested media options."""
    try:
        if server_mode_enabled():
            require_workspace_script(script_id)
            return enqueue_long_workspace_job(
                "export",
                {"script_id": script_id, "options": request.model_dump()},
            )
        export_url = pipeline.export_project(script_id, request.model_dump())
        return signed_response({"url": export_url})
    except HTTPException:
        raise
    except ValueError as e:
        status_code = 404 if str(e) == "Script not found" else 400
        raise HTTPException(status_code=status_code, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[EXPORT ERROR] {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


# ===== Art Direction Endpoints =====

class AnalyzeStyleRequest(BaseModel):
    script_text: str


class SaveArtDirectionRequest(BaseModel):
    selected_style_id: str
    style_config: Dict[str, Any]
    custom_styles: List[Dict[str, Any]] = []
    ai_recommendations: List[Dict[str, Any]] = []


@app.post("/projects/{script_id}/art_direction/analyze")
async def analyze_script_for_styles(script_id: str, request: AnalyzeStyleRequest):
    """Analyze script content and recommend visual styles using LLM"""
    try:
        # Get the script to ensure it exists
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Script not found")

        # Use LLM to analyze and recommend styles without blocking the event loop.
        custom_style = getattr(getattr(script, "prompt_config", None), "style_analysis", "")
        recommendations = await asyncio.to_thread(
            pipeline.script_processor.analyze_script_for_styles,
            request.script_text,
            custom_style,
        )

        return {"recommendations": recommendations}
    except StyleAnalysisError as e:
        status_code = {
            "missing_config": 503,
            "provider_timeout": 504,
            "provider_error": 502,
            "malformed_response": 502,
        }.get(e.reason, 502)
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": "style_analysis_failed",
                "reason": e.reason,
                "message": e.message,
            },
        ) from None
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/art_direction/clear")
def clear_project_art_direction(script_id: str):
    """R2V v2 Phase 2 — clear project-level art_direction so the
    episode falls back to series baseline (inherit). Used by the
    Style step '重置为系列' button."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    script.art_direction = None
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return signed_response(script)


@app.put("/projects/{script_id}/last_episode_summary")
def update_last_episode_summary(script_id: str, payload: dict):
    """R2V v2 Phase P1-b — manually edit the cached AI summary.
    Body: {"ai_summary": "user-edited text"} or null to clear."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    summary = payload.get("ai_summary")
    if summary is None:
        script.last_episode_summary_cache = None
        script.last_episode_summary_revision = None
    else:
        script.last_episode_summary_cache = str(summary).strip()
        # When user manually edits, mark the revision as the current
        # previous-episode revision so it doesn't show stale.
        if script.series_id:
            series = pipeline.get_series(script.series_id)
            if series:
                try:
                    idx = series.episode_ids.index(script_id)
                    if idx > 0:
                        prev = pipeline.get_script(series.episode_ids[idx - 1])
                        if prev:
                            script.last_episode_summary_revision = _prev_text_revision(prev.original_text or "")
                except ValueError:
                    pass
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return signed_response(script)


@app.post("/projects/{script_id}/art_direction/save", response_model=Script)
def save_art_direction(script_id: str, request: SaveArtDirectionRequest):
    """Save Art Direction configuration to the project"""
    try:
        updated_script = pipeline.save_art_direction(
            script_id,
            request.selected_style_id,
            request.style_config,
            request.custom_styles,
            request.ai_recommendations
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/art_direction/presets")
def get_style_presets():
    """Get built-in style presets (v2: categories + presets)"""
    try:
        import json
        import os
        preset_file = os.path.join(os.path.dirname(__file__), "style_presets.json")

        if not os.path.exists(preset_file):
            return {"version": 2, "categories": [], "presets": []}

        with open(preset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and data.get("version") == 2:
                return data
            # Legacy fallback: plain array
            return {"version": 2, "categories": [], "presets": data if isinstance(data, list) else []}
    except Exception as e:
        logger.exception("Failed to load style presets")
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: /storyboard/polish_prompt removed - use /storyboard/refine_prompt instead


def _get_custom_prompt(script_id: str, field: str) -> str:
    """Read a custom prompt with 3-level fallback: Episode → Series → system default.
    Returns empty string if result equals system default (so LLM method uses its built-in)."""
    if not script_id:
        return ""
    script = pipeline.get_script(script_id)
    if not script:
        return ""
    series = pipeline.get_series(script.series_id) if script.series_id else None
    effective = pipeline.get_effective_prompt(field, script, series)
    # If it's the system default, return empty so the LLM method uses its built-in default
    from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT, DEFAULT_VIDEO_POLISH_PROMPT
    defaults = {
        "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
        "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
    }
    if effective == defaults.get(field, ""):
        return ""
    return effective


def _get_polish_model_for_project(script_id: str) -> str:
    """Resolve the project's active chat selection with series/global fallback."""
    if not script_id:
        return get_selected_model(CHAT)
    script = pipeline.get_script(script_id)
    if not script:
        return get_selected_model(CHAT)
    if getattr(script, "model_settings", None):
        return get_model_spec(script.model_settings.chat_model, CHAT).model_id
    if getattr(script, "series_id", None):
        series = pipeline.get_series(script.series_id)
        if series and getattr(series, "model_settings", None):
            return get_model_spec(series.model_settings.chat_model, CHAT).model_id
    return get_selected_model(CHAT)


class PolishVideoPromptRequest(BaseModel):
    draft_prompt: str
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config
    # 迭代时传入上一次的 CN 作为双语锚点，让模型同步修改双语；首次留空。
    prev_cn: str = ""
    # I2V 模式：首帧图 URL(s)。Vision-capable polish 模型会真正看见图像并
    # 用其指导润色（光影、构图、被摄主体等）。空列表 = 纯文本润色。
    image_urls: List[str] = Field(default_factory=list, max_length=4)
    # 显式覆盖 polish 用的 LLM 模型；空 = 用 project / series PromptConfig
    # 的 polish_model（再 fallback 到 system default）。
    polish_model: str = ""

    @field_validator("polish_model")
    @classmethod
    def validate_polish_model(cls, value):
        if value:
            get_model_spec(value, CHAT)
        return value


def _polish_error_response(err) -> Dict[str, Any]:
    """把 PolishError 转成统一的 502 响应体。
    model_echo 时附带原文双语供前端做 warning 渲染。"""
    body: Dict[str, Any] = {
        "reason": err.reason,
        "message_zh": err.message_zh,
        "message_en": err.message_en,
    }
    if err.prompt_cn:
        body["prompt_cn"] = err.prompt_cn
    if err.prompt_en:
        body["prompt_en"] = err.prompt_en
    return body


@app.post("/video/polish_prompt")
def polish_video_prompt(request: PolishVideoPromptRequest):
    """Polishes a video generation prompt using LLM. Returns bilingual prompts.

    NOTE: Defined as a SYNC handler on purpose. The body calls
    `processor.polish_video_prompt()` which makes a blocking New API HTTP call
    through the OpenAI-compatible sync client. If declared `async def`, FastAPI would
    run it on the event loop itself — blocking ALL other endpoints for the
    10-30s LLM call (e.g. concurrent `GET /prompt_config` from the modal
    looks "stuck loading"). Sync handlers get auto-dispatched to anyio's
    threadpool, freeing the event loop for other requests.

    成功：200 + {prompt_cn, prompt_en}
    失败：502 + {reason, message_zh, message_en, prompt_cn?, prompt_en?}
      其中 reason ∈ {is_configured_false, api_error, json_parse_error,
                     missing_keys, model_echo, image_unavailable}。
      model_echo 是 warning 性质（带原文双语），其余是 hard error。
    """
    from .llm import PolishError
    try:
        custom_prompt = _get_custom_prompt(request.script_id, "video_polish")
        # Polish model: request override → project/series PromptConfig → ""
        polish_model = request.polish_model or _get_polish_model_for_project(request.script_id)
        processor = ScriptProcessor()
        result = processor.polish_video_prompt(
            request.draft_prompt,
            request.feedback,
            custom_prompt,
            request.prev_cn,
            image_urls=request.image_urls or None,
            polish_model=polish_model,
        )
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except PolishError as e:
        logger.warning("polish_video_prompt failed: %s", e)
        raise HTTPException(status_code=502, detail=_polish_error_response(e))
    except Exception as e:
        logger.exception("polish_video_prompt unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


# Credential-like env fields that must never be returned in plaintext.
SECRET_FIELDS = set(MODEL_API_KEY_FIELDS)

# Bullet sentinel: never appears in a real key, so the save path can detect an
# unchanged (still-masked) field and avoid overwriting the stored secret.
_MASK_CHAR = "\u2022"


def _mask_secret(value: Optional[str]) -> str:
    """Return a masked representation (bullets + last 4 chars) for a configured
    secret, or an empty string when it is unset. Never reveals the full value."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 4:
        return _MASK_CHAR * len(v)
    return _MASK_CHAR * 8 + v[-4:]


@app.get("/config/env")
def get_env_config(request: Request = None):
    """Get current environment configuration.

    Secrets are masked (bullets + last 4 chars) and never returned in
    plaintext. `secrets_configured` reports which credential fields are set so
    the frontend can drive required-field / validation logic without the raw
    value."""
    require_server_admin(request)
    try:
        secrets_configured = {
            field: bool((os.getenv(field, "") or "").strip())
            for field in SECRET_FIELDS
        }

        return {
            # Masked secrets — never plaintext.
            **{field: _mask_secret(os.getenv(field)) for field in MODEL_API_KEY_FIELDS},
            # Non-secret config.
            "NEWAPI_BASE_URL": os.getenv("NEWAPI_BASE_URL", ""),
            "NEWAPI_CHAT_MODEL": get_selected_model(CHAT, migrate_stale=True),
            "NEWAPI_IMAGE_MODEL": get_selected_model(IMAGE, migrate_stale=True),
            "NEWAPI_VIDEO_MODEL": get_selected_model(VIDEO, migrate_stale=True),
            "secrets_configured": secrets_configured,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/config/api-keys/inspect")
def inspect_api_keys(payload: APIKeyInspectionRequest, request: Request):
    """Return the approved model keys to an authenticated Admin on demand.

    Values are masked unless the Admin explicitly requests a reveal. The
    response is never cacheable, and the endpoint exposes only credential
    fields from the fixed model registry rather than arbitrary environment
    variables.
    """

    require_server_admin_account(request)
    items = []
    for model in public_model_status():
        field = str(model["api_key_field"])
        value = (os.getenv(field, "") or "").strip()
        configured = bool(value)
        active = bool(model["active"])
        items.append(
            {
                "display_name": model["display_name"],
                "capability": model["capability"],
                "api_key_field": field,
                "configured": configured,
                "active": active,
                "in_use": configured and active,
                "value": value if payload.reveal else _mask_secret(value),
            }
        )

    return JSONResponse(
        {"revealed": payload.reveal, "items": items},
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


# ============================================
# Prop CRUD Endpoints
# ============================================

class CreatePropRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20_000)

@app.post("/projects/{script_id}/props")
def create_prop(script_id: str, request: CreatePropRequest):
    """Creates a new prop in the project."""
    try:
        updated_script = pipeline.add_prop(
            script_id, request.name, request.description
        )
        return get_project(updated_script.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/projects/{script_id}/props/{prop_id}")
def delete_prop(script_id: str, prop_id: str):
    """Deletes a prop from the project."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise ValueError("Project not found")
        deleted_snapshot = _project_scope_snapshot(script)
        updated_script = pipeline.delete_prop(script_id, prop_id)
        reclaim_deleted_media(deleted_snapshot)
        return get_project(updated_script.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
