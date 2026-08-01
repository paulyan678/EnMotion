"""New API-only playground generation service."""

import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional

from ...utils import get_logger
from ...utils.newapi_models import IMAGE, VIDEO, resolve_model_api_key
from ...utils.system_check import get_ffmpeg_path
from ..web_runtime.file_lock import interprocess_lock
from .models import (
    DEFAULT_IMAGE_PARAMETERS,
    DEFAULT_VIDEO_PARAMETERS,
    GenerateRequest,
    PlaygroundGeneration,
    PlaygroundMode,
    PlaygroundOutput,
    normalize_playground_image_parameters,
    normalize_playground_video_parameters,
)
from .storage import PlaygroundStorage

logger = get_logger(__name__)

GENERATION_FAILED_MESSAGE = "生成失败，请稍后重试。"
IMAGE_GENERATION_FAILED_MESSAGE = "图像生成失败，请稍后重试。"
VIDEO_GENERATION_FAILED_MESSAGE = "视频生成失败，请稍后重试。"


class _SaveTargetDisappeared(RuntimeError):
    """Internal signal used to roll back before returning a not-found result."""


class UnsupportedPlaygroundLibraryMediaError(ValueError):
    """Raised when a Playground output cannot be represented by the asset library."""


# ---------------------------------------------------------------------------
# Output directories
# ---------------------------------------------------------------------------
IMAGE_OUTPUT_DIR = os.path.join("output", "playground", "images")
VIDEO_OUTPUT_DIR = os.path.join("output", "playground", "videos")


class PlaygroundService:
    """High-level service that creates generation records and delegates to
    the correct model adapter for execution."""

    def __init__(self, storage: PlaygroundStorage):
        self.storage = storage
        self.output_root = getattr(storage, "output_root", "output")
        self.image_output_dir = os.path.join(self.output_root, "playground", "images")
        self.video_output_dir = os.path.join(self.output_root, "playground", "videos")
        self.video_thumbnail_dir = os.path.join(self.output_root, "playground", "thumbnails")
        self._newapi_video_model = None
        self._newapi_image_model = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_generation(
        self, request: GenerateRequest, *, generation_id: Optional[str] = None
    ) -> PlaygroundGeneration:
        """Create a :class:`PlaygroundGeneration` record with *status=pending*,
        persist it via storage, and return it."""
        capability = IMAGE if request.mode in {PlaygroundMode.T2I, PlaygroundMode.I2I} else VIDEO
        resolve_model_api_key(request.model_id, capability)
        now = datetime.now(timezone.utc).isoformat()
        gen = PlaygroundGeneration(
            id=generation_id or str(uuid.uuid4()),
            mode=request.mode,
            model_id=request.model_id,
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            input_media=request.input_media or [],
            parameters=request.parameters or {},
            batch_size=request.batch_size or 1,
            outputs=[],
            status="pending",
            error=None,
            error_code=None,
            error_diagnostic=None,
            created_at=now,
            updated_at=now,
        )
        self.storage.add_generation(gen)
        return gen

    def process_generation(self, generation_id: str) -> None:
        """Execute the actual generation.  Intended to run in a background
        thread -- all calls are synchronous (blocking)."""
        gen = self.storage.get_generation(generation_id)
        if gen is None:
            logger.error("Generation %s not found", generation_id)
            return

        # Mark processing
        gen.status = "processing"
        gen.error = None
        gen.error_code = None
        gen.error_diagnostic = None
        self.storage.update_generation(gen)

        try:
            mode = gen.mode
            if mode in (PlaygroundMode.T2I, PlaygroundMode.I2I):
                self._process_image_generation(gen)
            elif mode in (PlaygroundMode.T2V, PlaygroundMode.I2V):
                self._process_video_generation(gen)
            else:
                raise ValueError(f"不支持的 Playground 生成模式：{mode}")

            gen.status = "completed"
        except Exception as exc:
            logger.exception("Generation %s failed", generation_id)
            gen.status = "failed"
            from ...models.newapi import NewAPIProviderError

            if isinstance(exc, NewAPIProviderError):
                gen.error = str(exc)
                gen.error_code = exc.error_code
                gen.error_diagnostic = exc.diagnostic
                # An ambiguous accepted task can safely be resumed by its
                # provider id. A terminal rejection must allow a later retry
                # to submit a fresh request instead of polling a known failure.
                if exc.error_code != "provider_outcome_ambiguous":
                    gen.provider_name = None
                    gen.provider_task_id = None
                    gen.provider_request_id = None
            elif gen.outputs and len(gen.outputs) < gen.batch_size:
                gen.error = (
                    f"批量生成已完成 {len(gen.outputs)}/{gen.batch_size} 个输出。"
                    "请点击重试继续生成剩余输出。"
                )
                gen.error_code = "partial_batch_failed"
            else:
                gen.error = GENERATION_FAILED_MESSAGE

        self.storage.update_generation(gen)

    def save_to_library(
        self,
        generation_id: str,
        output_id: str,
        category: str,
    ) -> Optional[str]:
        """Copy a generated output to ``output/assets/{category}/`` and flag
        :pyattr:`PlaygroundOutput.saved_to_library` = True.

        The workspace lock covers the complete read/copy/register/persist
        operation. It is re-entrant with server middleware/storage locking and
        also serializes desktop retries that otherwise could create duplicate
        assets.
        """
        asset_type = self._category_to_asset_type(category)
        with interprocess_lock(self.storage.workspace_lock_path):
            return self._save_to_library_locked(
                generation_id,
                output_id,
                asset_type,
            )

    def _save_to_library_locked(
        self,
        generation_id: str,
        output_id: str,
        asset_type: str,
    ) -> Optional[str]:
        gen = self.storage.get_generation(generation_id)
        if gen is None:
            logger.warning("save_to_library: generation %s not found", generation_id)
            return None

        target_output: Optional[PlaygroundOutput] = None
        for out in gen.outputs:
            if out.id == output_id:
                target_output = out
                break
        if target_output is None:
            logger.warning(
                "save_to_library: output %s not found in generation %s", output_id, generation_id
            )
            return None
        if target_output.media_type != "image":
            raise UnsupportedPlaygroundLibraryMediaError(
                "当前资产库仅支持保存图像输出，请先下载视频文件。"
            )
        if target_output.saved_to_library:
            # The operation is idempotent. Replaying a successful request must
            # not create another full copy of the same potentially large file.
            return target_output.library_category or "prop"

        # media_path is stored as e.g. "output/playground/images/t2i_xxx_0.png"
        # Normalise: try as-is first, then strip leading "output/" and re-join
        src_path = target_output.media_path
        if not os.path.isfile(src_path):
            alt = os.path.join(self.output_root, target_output.media_path)
            if os.path.isfile(alt):
                src_path = alt
        if not os.path.isfile(src_path):
            logger.error("save_to_library: source file not found: %s", target_output.media_path)
            return None

        # Journal stable identities before the first side effect. If the
        # process exits after copy/registration, a retry reconciles the same
        # path and asset rather than allocating duplicates. A conflicting
        # retry also honors the category recorded by the original attempt.
        intended_category = target_output.library_category or asset_type
        deterministic_asset_id, deterministic_media_path = self._library_save_identifiers(
            generation_id,
            output_id,
            intended_category,
            os.path.basename(src_path),
        )
        intent = self.storage.prepare_output_library_save(
            generation_id,
            output_id,
            intended_category,
            deterministic_asset_id,
            deterministic_media_path,
        )
        if intent is None:
            return None
        asset_type, asset_id, relative_image_url = intent
        expected_asset_id, expected_media_path = self._library_save_identifiers(
            generation_id,
            output_id,
            asset_type,
            os.path.basename(src_path),
        )
        if asset_id != expected_asset_id or relative_image_url != expected_media_path:
            raise RuntimeError("资产库保存记录包含无效的目标标识")
        dest_path = os.path.join(self.output_root, *relative_image_url.split("/"))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        comic_pipeline = None
        asset_registered = False
        try:
            source_size = os.path.getsize(src_path)
            existing_size = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
            self._ensure_server_capacity(max(0, source_size - existing_size))
            shutil.copy2(src_path, dest_path)
            self._enforce_server_file_quota(dest_path)
            logger.info("Saved output %s to library: %s", output_id, dest_path)

            # Wave A (shared asset pool): besides copying the file, register a
            # real global library asset record so the output is immediately
            # curatable under the user's explicit classification.
            prompt_text = (gen.prompt or "").strip()
            asset_name = prompt_text[:40] or os.path.splitext(os.path.basename(dest_path))[0]
            # Deferred import: comic_gen.api owns the live ComicGenPipeline
            # singleton -- the same instance that backs the /library/assets
            # CRUD endpoints, so the new asset is immediately visible there.
            # A top-level import would create a cycle (comic_gen.api imports the
            # playground router at module load), so we import lazily at call
            # time when both modules are fully initialised.
            from ..comic_gen.api import pipeline as comic_pipeline

            asset = comic_pipeline.create_library_asset(
                asset_type,
                {
                    "name": asset_name,
                    "description": prompt_text,
                    # Point the library record at the freshly-copied file.
                    "image_url": relative_image_url,
                },
                asset_id=asset_id,
            )
            registered_asset_id = str(getattr(asset, "id", "") or "").strip()
            if registered_asset_id != asset_id:
                raise RuntimeError("资产库未返回已保存资产的标识")
            asset_registered = True
            logger.info(
                "save_to_library: created global %s asset %s from output %s",
                asset_type,
                asset_id,
                output_id,
            )
            persisted_category = self.storage.mark_output_saved(
                generation_id,
                output_id,
                asset_type,
            )
            if persisted_category is None:
                raise _SaveTargetDisappeared(
                    "Playground generation or output disappeared during save"
                )
        except Exception as exc:
            logger.exception(
                "save_to_library: rolling back failed library save for output %s",
                output_id,
            )
            asset_rolled_back = not asset_registered
            if comic_pipeline is not None and asset_registered:
                try:
                    comic_pipeline.delete_library_asset(
                        asset_type,
                        asset_id,
                        force=True,
                    )
                    asset_rolled_back = True
                except Exception:
                    logger.exception(
                        "save_to_library: failed to roll back global %s asset %s",
                        asset_type,
                        asset_id,
                    )
            if asset_rolled_back:
                try:
                    os.unlink(dest_path)
                except FileNotFoundError:
                    pass
            else:
                logger.error(
                    "save_to_library: preserving %s because asset %s still references it",
                    dest_path,
                    asset_id,
                )
            if isinstance(exc, _SaveTargetDisappeared):
                return None
            raise
        return persisted_category

    # ------------------------------------------------------------------
    # Image generation (t2i / i2i)
    # ------------------------------------------------------------------

    def _process_image_generation(self, gen: PlaygroundGeneration) -> None:
        os.makedirs(self.image_output_dir, exist_ok=True)

        failures: list[Exception] = []

        for idx in range(len(gen.outputs), gen.batch_size):
            ext = "png"
            out_filename = f"{gen.mode.value}_{gen.id}_{idx}.{ext}"
            out_path = os.path.join(self.image_output_dir, out_filename)

            try:
                self._generate_image_newapi(gen, out_path, idx)
                self._assert_generated_media(out_path, "image")
                self._enforce_server_file_quota(out_path)

                output_entry = PlaygroundOutput(
                    id=str(uuid.uuid4()),
                    media_path=self._persisted_media_path(out_path),
                    media_type="image",
                )
                gen.outputs.append(output_entry)
                self.storage.update_generation(gen)
            except Exception as exc:
                logger.error("Image generation %s batch %d failed: %s", gen.id, idx, exc)
                failures.append(exc)
                # Keep successful outputs contiguous from index zero. Retry
                # resumes at len(outputs), so continuing after a hole would
                # later overwrite or duplicate a higher-index file.
                break

        if failures:
            from ...models.newapi import NewAPIProviderError

            provider_failure = next(
                (failure for failure in failures if isinstance(failure, NewAPIProviderError)),
                None,
            )
            if provider_failure is not None:
                raise provider_failure
            raise RuntimeError(
                f"{IMAGE_GENERATION_FAILED_MESSAGE} "
                f"已完成 {len(gen.outputs)}/{gen.batch_size} 个输出。"
            )

    def _generate_image_newapi(self, gen: PlaygroundGeneration, out_path: str, _idx: int) -> None:
        from ...models.newapi import NewAPIImageModel

        if self._newapi_image_model is None:
            self._newapi_image_model = NewAPIImageModel({})

        params = normalize_playground_image_parameters(gen.parameters)
        gen.parameters = params
        kwargs = {
            "model_id": gen.model_id,
            "size": params.get("size", DEFAULT_IMAGE_PARAMETERS["size"]),
            "quality": params.get("quality", DEFAULT_IMAGE_PARAMETERS["quality"]),
            "n": 1,
        }

        # i2i: attach reference images
        if gen.mode == PlaygroundMode.I2I and gen.input_media:
            kwargs["ref_image_paths"] = [
                self._resolve_image_input_reference(reference) for reference in gen.input_media
            ]

        self._newapi_image_model.generate(
            prompt=gen.prompt,
            output_path=out_path,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Video generation (t2v / i2v)
    # ------------------------------------------------------------------

    def _process_video_generation(self, gen: PlaygroundGeneration) -> None:
        os.makedirs(self.video_output_dir, exist_ok=True)

        failures: list[Exception] = []

        for idx in range(len(gen.outputs), gen.batch_size):
            out_filename = f"{gen.mode.value}_{gen.id}_{idx}.mp4"
            out_path = os.path.join(self.video_output_dir, out_filename)

            try:
                self._generate_video_newapi(gen, out_path)
                self._assert_generated_media(out_path, "video")
                self._enforce_server_file_quota(out_path)
                thumbnail_path = self._create_video_thumbnail(out_path)

                output_entry = PlaygroundOutput(
                    id=str(uuid.uuid4()),
                    media_path=self._persisted_media_path(out_path),
                    media_type="video",
                    thumbnail_path=(
                        self._persisted_media_path(thumbnail_path) if thumbnail_path else None
                    ),
                )
                gen.outputs.append(output_entry)
                gen.provider_name = None
                gen.provider_task_id = None
                gen.provider_request_id = None
                self.storage.update_generation(gen)
            except Exception as exc:
                logger.error("Video generation %s batch %d failed: %s", gen.id, idx, exc)
                failures.append(exc)
                # Provider task ids and output indices belong to the current
                # batch item. Stop at the first failure so a retry can resume
                # exactly that item before advancing to the next one.
                break

        if failures:
            from ...models.newapi import NewAPIProviderError

            provider_failure = next(
                (failure for failure in failures if isinstance(failure, NewAPIProviderError)),
                None,
            )
            if provider_failure is not None:
                raise provider_failure
            raise RuntimeError(
                f"{VIDEO_GENERATION_FAILED_MESSAGE} "
                f"已完成 {len(gen.outputs)}/{gen.batch_size} 个输出。"
            )

    # -- adapter delegates ------------------------------------------------

    def _generate_video_newapi(self, gen: PlaygroundGeneration, out_path: str) -> None:
        from ...models.newapi import NewAPIVideoModel

        if self._newapi_video_model is None:
            self._newapi_video_model = NewAPIVideoModel({})

        params = normalize_playground_video_parameters(
            gen.mode,
            gen.model_id,
            gen.parameters,
        )
        gen.parameters = params
        img_path, img_url = self._resolve_first_input_media(gen)

        kwargs = {
            "model_id": gen.model_id,
            "duration": params["duration"],
            "resolution": params.get("resolution", DEFAULT_VIDEO_PARAMETERS["resolution"]),
            "aspect_ratio": params["aspect_ratio"],
            "seed": params.get("seed"),
            "watermark": params.get("watermark", False),
            "generate_audio": params.get("generate_audio", True),
            "generation_mode": gen.mode.value,
        }

        if gen.provider_task_id:
            kwargs["provider_task_id"] = gen.provider_task_id

        def persist_provider_ids(
            provider_name: str,
            provider_task_id: Optional[str],
            provider_request_id: Optional[str],
        ) -> None:
            gen.provider_name = provider_name or None
            gen.provider_task_id = provider_task_id or None
            gen.provider_request_id = provider_request_id or None
            self.storage.update_generation(gen)

        kwargs["on_provider_ids"] = persist_provider_ids

        self._newapi_video_model.generate(
            prompt=gen.prompt,
            output_path=out_path,
            img_url=img_url,
            img_path=img_path,
            **kwargs,
        )

    def prepare_generation_retry(self, generation_id: str) -> PlaygroundGeneration:
        """Requeue failed work without duplicating accepted or completed outputs."""

        gen = self.storage.get_generation(generation_id)
        if gen is None:
            raise LookupError("generation not found")
        if gen.status != "failed":
            raise ValueError("only failed generations can be retried")
        gen.status = "pending"
        gen.error = None
        gen.error_code = None
        gen.error_diagnostic = None
        self.storage.update_generation(gen)
        return gen

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_video_thumbnail(self, video_path: str) -> Optional[str]:
        """Extract a compact poster frame without risking the video result.

        A thumbnail is presentation metadata, so a missing FFmpeg binary,
        malformed frame, timeout, or quota failure must not turn an otherwise
        valid and playable provider result into a failed generation. The web UI
        falls back to rendering the video itself for older or exceptional
        records that do not have a persisted poster.
        """

        try:
            ffmpeg_path = get_ffmpeg_path()
        except Exception as exc:
            logger.warning("Could not locate FFmpeg for Playground thumbnail: %s", exc)
            return None
        if not ffmpeg_path:
            logger.warning("Skipping Playground video thumbnail: FFmpeg was not found")
            return None

        try:
            os.makedirs(self.video_thumbnail_dir, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create Playground thumbnail directory: %s", exc)
            return None
        video_stem = os.path.splitext(os.path.basename(video_path))[0]
        thumbnail_path = os.path.join(self.video_thumbnail_dir, f"{video_stem}_thumbnail.jpg")
        temporary_path = os.path.join(
            self.video_thumbnail_dir,
            f".{video_stem}_{uuid.uuid4().hex}.tmp.jpg",
        )
        promoted = False
        completed = False

        command = [
            ffmpeg_path,
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-vf",
            "scale=-2:min(540\\,ih)",
            "-q:v",
            "3",
            "-y",
            temporary_path,
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                detail = " ".join((result.stderr or "").split())[-500:]
                raise RuntimeError(detail or f"FFmpeg exited with {result.returncode}")
            self._assert_generated_media(temporary_path, "video thumbnail")
            os.replace(temporary_path, thumbnail_path)
            promoted = True
            self._enforce_server_file_quota(thumbnail_path)
            completed = True
            return thumbnail_path
        except Exception as exc:
            logger.warning("Could not create Playground video thumbnail: %s", exc)
            return None
        finally:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            if promoted and not completed:
                try:
                    os.remove(thumbnail_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _assert_generated_media(path: str, media_type: str) -> None:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            try:
                os.remove(path)
            except OSError:
                pass
            raise RuntimeError(f"{media_type} 服务没有生成可用的输出文件")

    def _persisted_media_path(self, path: str) -> str:
        """Return a browser-safe path without exposing a tenant's server root.

        Unmanaged web development keeps its established ``output/...`` form.
        Managed desktop and server workspaces store only paths relative to the
        authenticated workspace output root, which maps to ``/files/<path>``.
        """

        from ..web_runtime.pipeline_registry import workspace_isolation_enabled

        if not workspace_isolation_enabled():
            return path
        relative = os.path.relpath(path, self.output_root)
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            raise RuntimeError("生成文件超出了工作区输出目录")
        return relative.replace(os.sep, "/")

    @staticmethod
    def _category_to_asset_type(category: Optional[str]) -> str:
        """Validate an explicit Playground library classification."""
        normalized = (category or "").strip().lower()
        if normalized in ("character", "scene", "prop"):
            return normalized
        raise ValueError("保存到资产库前必须选择角色、场景或道具分类")

    @staticmethod
    def _library_save_identifiers(
        generation_id: str,
        output_id: str,
        asset_type: str,
        source_filename: str,
    ) -> tuple[str, str]:
        """Return stable asset and media identities for crash-safe retries."""

        token = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"enmotion-playground-library:{generation_id}:{output_id}",
        ).hex
        prefix = {
            "character": "char",
            "scene": "scene",
            "prop": "prop",
        }[asset_type]
        asset_id = f"{prefix}_{token[:12]}"
        media_path = f"assets/{asset_type}/{token}_{os.path.basename(source_filename)}"
        return asset_id, media_path

    @staticmethod
    def _ensure_server_capacity(reserve_bytes: int) -> None:
        from ..server.config import server_mode_enabled

        if not server_mode_enabled():
            return
        from ..server.database import get_database
        from ..server.quotas import ensure_storage_capacity
        from ..web_runtime.context import get_tenant

        tenant = get_tenant(required=True)
        assert tenant is not None
        ensure_storage_capacity(
            get_database(),
            workspace_id=tenant.workspace_id,
            reserve_bytes=reserve_bytes,
        )

    def _enforce_server_file_quota(self, created_path: str) -> None:
        from ..server.config import server_mode_enabled

        if not server_mode_enabled():
            return
        from ..server.database import get_database
        from ..server.quotas import enforce_saved_file_quota
        from ..web_runtime.context import get_tenant
        from ..web_runtime.file_lock import interprocess_lock

        tenant = get_tenant(required=True)
        assert tenant is not None
        with interprocess_lock(self.storage.workspace_lock_path):
            enforce_saved_file_quota(
                get_database(),
                workspace_id=tenant.workspace_id,
                created_path=created_path,
            )

    def _resolve_first_input_media(self, gen: PlaygroundGeneration):
        """Return ``(img_path, img_url)`` for the first entry in
        :pyattr:`input_media`.  Local files are returned as *img_path*;
        remote URLs as *img_url*."""
        if not gen.input_media:
            return None, None

        first = gen.input_media[0]
        if first.startswith(("http://", "https://")):
            from ..server.config import server_mode_enabled

            if server_mode_enabled():
                from ...utils.media_security import validate_remote_media_url

                first = validate_remote_media_url(first)
            return None, first

        from ..web_runtime.pipeline_registry import workspace_isolation_enabled

        if workspace_isolation_enabled():
            from ...utils.media_security import resolve_workspace_media_path

            return resolve_workspace_media_path(self.output_root, first), None

        # Try as-is, then relative to output/
        if os.path.exists(first):
            return first, None
        candidate = os.path.join(self.output_root, first)
        if os.path.exists(candidate):
            return candidate, None

        raise ValueError("未找到输入媒体文件")

    def _resolve_image_input_reference(self, reference: str) -> str:
        """Normalize an I2I reference against this workspace output root."""

        if reference.startswith(("http://", "https://", "data:")):
            return reference

        from ..web_runtime.pipeline_registry import workspace_isolation_enabled

        if workspace_isolation_enabled():
            from ...utils.media_security import resolve_workspace_media_path

            return resolve_workspace_media_path(self.output_root, reference)
        if os.path.isfile(reference):
            return reference
        candidate = os.path.join(self.output_root, reference)
        if os.path.isfile(candidate):
            return candidate
        raise ValueError(f"未找到参考图片：{reference}")
