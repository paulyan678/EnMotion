"""Playground API routes — generation, history, and template management."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ...utils import get_logger
from ...utils.newapi_models import MissingNewAPIKeyError, validate_model_for_mode
from ...utils.uploads import IMAGE_UPLOAD_POLICY, save_upload_file_async
from ..web_runtime.background_dispatch import DetachedTaskDispatcher
from ..web_runtime.context import get_tenant
from ..web_runtime.pipeline_registry import (
    server_mode_enabled,
    workspace_isolation_enabled,
)
from ..web_runtime.playground_registry import WorkspacePlaygroundRegistry
from .models import (
    CreateTemplateRequest,
    GenerateRequest,
    PlaygroundTemplate,
    SaveToLibraryRequest,
    UpdateTemplateRequest,
)
from .service import PlaygroundService, UnsupportedPlaygroundLibraryMediaError
from .storage import PlaygroundStorage

logger = get_logger(__name__)

router = APIRouter(tags=["playground"])

GENERATION_LIMIT_MESSAGE = "同时运行的生成任务过多，请等待现有任务完成后重试。"
GENERATION_PAYLOAD_MESSAGE = "生成请求内容过大，请减少输入后重试。"
STORAGE_QUOTA_MESSAGE = "存储空间不足，请删除部分文件后重试。"
GENERATION_QUEUE_MESSAGE = "生成队列暂时不可用，请稍后重试。"
GENERATION_REQUEST_MESSAGE = "生成参数无效，请检查后重试。"
MODEL_NOT_CONFIGURED_MESSAGE = "当前 AI 模型尚未配置，请联系管理员。"
MEDIA_REFERENCE_MESSAGE = "媒体文件无效或不可访问。"

# Module-level singletons — initialised when the router is first imported.
_storage = PlaygroundStorage()
_service = PlaygroundService(_storage)
_workspace_playgrounds = WorkspacePlaygroundRegistry()
_local_playground_dispatcher = DetachedTaskDispatcher(
    worker_count=4,
    name_prefix="enmotion-playground-local",
)


def _current_storage() -> PlaygroundStorage:
    if workspace_isolation_enabled():
        return _workspace_playgrounds.current().storage
    return _storage


def _current_service() -> PlaygroundService:
    if workspace_isolation_enabled():
        return _workspace_playgrounds.current().service
    return _service


def active_playground_generation_blockers() -> list[str]:
    """Snapshot unfinished local Playground work across loaded workspaces."""

    if workspace_isolation_enabled():
        storage_by_workspace = {
            workspace_id: runtime.storage
            for workspace_id, runtime in _workspace_playgrounds.snapshot().items()
        }
    else:
        storage_by_workspace = {"local": _storage}
    return sorted(
        f"playground:{workspace_id}:{generation_id}"
        for workspace_id, storage in storage_by_workspace.items()
        for generation_id in storage.active_generation_ids()
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    """Create a generation record and kick off processing in the background."""
    service = _current_service()
    generation_id = str(uuid.uuid4())
    reservations = []
    database = None
    tenant = None
    if server_mode_enabled():
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

        database = get_database()
        try:
            reservations = reserve_jobs(
                database,
                workspace_id=tenant.workspace_id,
                user_id=tenant.user_id,
                specs=[
                    JobSpec(
                        job_type="playground",
                        payload={
                            "generation_id": generation_id,
                            "batch_size": request.batch_size or 1,
                            "mode": request.mode.value,
                            "model_id": request.model_id,
                            "prompt": request.prompt,
                            "negative_prompt": request.negative_prompt,
                            "input_media": list(request.input_media or []),
                            "parameters": dict(request.parameters or {}),
                            "activity_source": "playground",
                        },
                        job_id=generation_id,
                    )
                ],
            )
        except JobLimitExceededError as exc:
            logger.warning("生成任务数量限制已触发：%s", exc)
            raise HTTPException(status_code=429, detail=GENERATION_LIMIT_MESSAGE) from exc
        except JobPayloadTooLargeError as exc:
            logger.warning("生成请求大小限制已触发：%s", exc)
            raise HTTPException(status_code=413, detail=GENERATION_PAYLOAD_MESSAGE) from exc
        except StorageQuotaExceededError as exc:
            logger.warning("工作区存储配额已用尽：%s", exc)
            raise HTTPException(status_code=507, detail=STORAGE_QUOTA_MESSAGE) from exc

    try:
        gen = service.create_generation(request, generation_id=generation_id)
    except MissingNewAPIKeyError as exc:
        if reservations and database is not None:
            from ..server.jobs import abandon_reserved_jobs

            abandon_reserved_jobs(database, job_ids=[record.id for record in reservations])
        logger.warning("Playground 模型未配置：%s", exc)
        raise HTTPException(status_code=400, detail=MODEL_NOT_CONFIGURED_MESSAGE) from exc
    except ValueError as exc:
        if reservations and database is not None:
            from ..server.jobs import abandon_reserved_jobs

            abandon_reserved_jobs(database, job_ids=[record.id for record in reservations])
        logger.warning("Playground 生成参数无效：%s", exc)
        raise HTTPException(status_code=400, detail=GENERATION_REQUEST_MESSAGE) from exc
    except Exception:
        if reservations and database is not None:
            from ..server.jobs import abandon_reserved_jobs

            abandon_reserved_jobs(database, job_ids=[record.id for record in reservations])
            service.storage.delete_generation(generation_id)
        raise
    if server_mode_enabled():
        from ..server.jobs import (
            JobQueueUnavailableError,
            publish_reserved_jobs,
        )

        try:
            assert database is not None
            publish_reserved_jobs(database, job_ids=[record.id for record in reservations])
        except JobQueueUnavailableError as exc:
            service.storage.delete_generation(gen.id)
            logger.warning("Playground 生成队列不可用：%s", exc)
            raise HTTPException(status_code=503, detail=GENERATION_QUEUE_MESSAGE) from exc
    else:
        if workspace_isolation_enabled():
            _workspace_playgrounds.dispatch_current(gen.id)
        else:
            _local_playground_dispatcher.submit(service.process_generation, gen.id)
    return gen


router.add_api_route("/generate", generate, methods=["POST"])

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def list_history(limit: int = 50, offset: int = 0):
    """Return paginated generation history, newest first."""
    return _current_storage().list_history(limit=limit, offset=offset)


def get_generation(generation_id: str):
    """Return full details for a single generation."""
    gen = _current_storage().get_generation(generation_id)
    if not gen:
        raise HTTPException(status_code=404, detail="未找到此生成记录。")
    return gen


def get_generation_status(generation_id: str):
    """Return lightweight status payload for polling."""
    gen = _current_storage().get_generation(generation_id)
    if not gen:
        raise HTTPException(status_code=404, detail="未找到此生成记录。")
    return {
        "id": gen.id,
        "status": gen.status,
        "outputs": gen.outputs,
        "error": gen.error,
    }


def retry_generation(generation_id: str):
    """Retry remaining batch work, resuming accepted provider jobs by id."""

    service = _current_service()
    try:
        gen = service.prepare_generation_retry(generation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="未找到此生成记录。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="只有失败的生成任务可以重试。") from exc

    if server_mode_enabled():
        tenant = get_tenant(required=True)
        assert tenant is not None
        from ..server.jobs import enqueue_job

        try:
            enqueue_job(
                workspace_id=tenant.workspace_id,
                user_id=tenant.user_id,
                job_type="playground",
                payload={
                    "generation_id": gen.id,
                    "batch_size": gen.batch_size,
                    "mode": gen.mode.value,
                    "model_id": gen.model_id,
                    "prompt": gen.prompt,
                    "negative_prompt": gen.negative_prompt,
                    "input_media": list(gen.input_media),
                    "parameters": dict(gen.parameters),
                    "activity_source": "playground",
                },
            )
        except Exception as exc:
            gen.status = "failed"
            gen.error = GENERATION_QUEUE_MESSAGE
            service.storage.update_generation(gen)
            logger.warning("Playground retry queue unavailable: %s", exc)
            raise HTTPException(status_code=503, detail=GENERATION_QUEUE_MESSAGE) from exc
    elif workspace_isolation_enabled():
        _workspace_playgrounds.dispatch_current(gen.id)
    else:
        _local_playground_dispatcher.submit(service.process_generation, gen.id)
    return gen


def delete_generation(generation_id: str):
    """Delete a generation record and its outputs."""
    storage = _current_storage()
    generation = storage.get_generation(generation_id)
    if not generation:
        raise HTTPException(status_code=404, detail="未找到此生成记录。")
    if generation.status in {"pending", "processing"}:
        raise HTTPException(
            status_code=409,
            detail="正在运行的生成任务无法删除，请等待任务完成。",
        )
    if not storage.delete_generation(generation_id):
        raise HTTPException(status_code=404, detail="未找到此生成记录。")
    return {"ok": True}


def save_to_library(
    generation_id: str,
    output_id: str,
    request: SaveToLibraryRequest,
):
    """Save a specific generation output to the project library."""
    try:
        persisted_category = _current_service().save_to_library(
            generation_id,
            output_id,
            request.category,
        )
    except UnsupportedPlaygroundLibraryMediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if persisted_category is None:
        raise HTTPException(status_code=404, detail="未找到此生成记录或输出文件。")
    return {"ok": True, "category": persisted_category}


router.add_api_route("/history", list_history, methods=["GET"])
router.add_api_route("/history/{generation_id}", get_generation, methods=["GET"])
router.add_api_route("/history/{generation_id}/status", get_generation_status, methods=["GET"])
router.add_api_route("/history/{generation_id}/retry", retry_generation, methods=["POST"])
router.add_api_route("/history/{generation_id}", delete_generation, methods=["DELETE"])
router.add_api_route(
    "/history/{generation_id}/outputs/{output_id}/save-to-library",
    save_to_library,
    methods=["POST"],
)

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def list_templates():
    """Return all saved prompt templates."""
    return _current_storage().list_templates()


def create_template(request: CreateTemplateRequest):
    """Create a new prompt template."""
    now = datetime.now(timezone.utc).isoformat()
    template = PlaygroundTemplate(
        id=str(uuid.uuid4()),
        name=request.name,
        category=request.category or "general",
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        default_mode=request.default_mode,
        default_model_id=request.default_model_id,
        default_parameters=request.default_parameters or {},
        created_at=now,
        updated_at=now,
    )
    _current_storage().add_template(template)
    return template


def update_template(template_id: str, request: UpdateTemplateRequest):
    """Update an existing prompt template (partial update)."""
    storage = _current_storage()
    template = storage.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="未找到此提示词模板。")
    update_data = request.model_dump(exclude_none=True)
    candidate_mode = update_data.get("default_mode", template.default_mode)
    candidate_model = update_data.get("default_model_id", template.default_model_id)
    if candidate_mode and candidate_model:
        mode_value = candidate_mode.value if hasattr(candidate_mode, "value") else candidate_mode
        validate_model_for_mode(candidate_model, mode_value)
    for key, value in update_data.items():
        setattr(template, key, value)
    template.updated_at = datetime.now(timezone.utc).isoformat()
    storage.update_template(template)
    return template


def delete_template(template_id: str):
    """Delete a prompt template."""
    if not _current_storage().delete_template(template_id):
        raise HTTPException(status_code=404, detail="未找到此提示词模板。")
    return {"ok": True}


router.add_api_route("/templates", list_templates, methods=["GET"])
router.add_api_route("/templates", create_template, methods=["POST"])
router.add_api_route("/templates/{template_id}", update_template, methods=["PUT"])
router.add_api_route("/templates/{template_id}", delete_template, methods=["DELETE"])

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

UPLOAD_DIR = os.path.join("output", "playground", "uploads")


class DeleteUploadRequest(BaseModel):
    path: str = Field(min_length=1, max_length=8192)


async def upload_media(file: UploadFile = File(...)):
    """Upload a media file for use as playground input (reference image, first frame, etc.)."""
    storage = _current_storage()
    upload_dir = os.path.join(storage.output_root, "playground", "uploads")
    saved = await save_upload_file_async(file, upload_dir, IMAGE_UPLOAD_POLICY)
    if server_mode_enabled():
        tenant = get_tenant(required=True)
        assert tenant is not None
        from ..server.database import get_database
        from ..server.quotas import (
            StorageQuotaExceededError,
            enforce_saved_file_quota,
        )

        try:
            await asyncio.to_thread(
                enforce_saved_file_quota,
                get_database(),
                workspace_id=tenant.workspace_id,
                created_path=saved.path,
            )
        except StorageQuotaExceededError as exc:
            logger.warning("上传文件超过工作区存储配额：%s", exc)
            raise HTTPException(status_code=507, detail=STORAGE_QUOTA_MESSAGE) from exc
    relative_path = os.path.relpath(saved.path, storage.output_root).replace(os.sep, "/")
    return {"path": relative_path}


router.add_api_route("/upload", upload_media, methods=["POST"])


def delete_upload(request: DeleteUploadRequest):
    """Delete an unreferenced upload owned by the current workspace."""

    from ...utils.media_security import UnsafeMediaReferenceError

    try:
        _current_storage().delete_upload(request.path)
    except UnsafeMediaReferenceError as exc:
        logger.warning("Playground 媒体引用无效：%s", exc)
        raise HTTPException(status_code=400, detail=MEDIA_REFERENCE_MESSAGE) from exc
    except RuntimeError as exc:
        logger.warning("Playground 素材清理暂时无法验证引用：%s", exc)
        raise HTTPException(
            status_code=503,
            detail="暂时无法安全清理素材，请稍后重试。",
        ) from exc
    return {"ok": True}


router.add_api_route("/upload", delete_upload, methods=["DELETE"])
