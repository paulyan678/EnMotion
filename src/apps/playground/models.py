import os
import uuid
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...utils.newapi_models import (
    DEFAULT_MODELS,
    IMAGE,
    VIDEO,
    get_model_spec,
    validate_model_for_mode,
)


class PlaygroundMode(str, Enum):
    T2I = "t2i"
    I2I = "i2i"
    T2V = "t2v"
    I2V = "i2v"


DEFAULT_VIDEO_PARAMETERS = {
    "duration": 5,
    "resolution": "720p",
    "aspect_ratio": "16:9",
}
VIDEO_RESOLUTIONS = frozenset({"720p", "1080p"})
VIDEO_ASPECT_RATIOS = frozenset({"16:9", "9:16", "1:1"})
SEEDANCE_STANDARD_MODEL = "doubao-seedance-2-0-260128"


def normalize_playground_video_parameters(
    mode: PlaygroundMode,
    model_id: str,
    parameters: Optional[dict],
) -> dict:
    """Return canonical, provider-compatible Playground video parameters.

    The compose UI may display defaults without materializing them in its local
    parameter object. Normalizing at the API boundary keeps the persisted job
    payload authoritative and prevents an omitted resolution from silently
    becoming a provider-incompatible value later in the worker.
    """

    normalized = dict(parameters or {})

    duration = normalized.get("duration", DEFAULT_VIDEO_PARAMETERS["duration"])
    if isinstance(duration, bool) or not isinstance(duration, int):
        raise ValueError("视频时长必须是 4 至 15 秒之间的整数")
    if duration < 4 or duration > 15:
        raise ValueError("视频时长必须为 4 至 15 秒")

    resolution = normalized.get(
        "resolution", DEFAULT_VIDEO_PARAMETERS["resolution"]
    )
    if not isinstance(resolution, str):
        raise ValueError("视频分辨率必须是 720p 或 1080p")
    resolution = resolution.strip().lower()
    if resolution not in VIDEO_RESOLUTIONS:
        raise ValueError("视频分辨率必须是 720p 或 1080p")
    if resolution == "1080p" and not (
        mode == PlaygroundMode.T2V and model_id == SEEDANCE_STANDARD_MODEL
    ):
        raise ValueError(
            "仅 Seedance 2.0 文生视频支持 1080p"
        )

    aspect_ratio = normalized.get(
        "aspect_ratio", DEFAULT_VIDEO_PARAMETERS["aspect_ratio"]
    )
    if not isinstance(aspect_ratio, str):
        raise ValueError("视频画面比例必须是 16:9、9:16 或 1:1")
    aspect_ratio = aspect_ratio.strip()
    if aspect_ratio not in VIDEO_ASPECT_RATIOS:
        raise ValueError("视频画面比例必须是 16:9、9:16 或 1:1")

    normalized.update(
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
    )
    return normalized


class PlaygroundOutput(BaseModel):
    id: str = Field(..., description="Unique identifier (UUID)")
    media_path: str = Field(..., description="Generated file path relative to output/")
    media_type: str = Field(..., description="Output media type: image or video")
    thumbnail_path: Optional[str] = Field(None, description="Thumbnail file path relative to output/")
    saved_to_library: bool = Field(False, description="Whether this output has been saved to the project library")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_media_record(cls, value: Any):
        """Normalize historical Playground output envelopes.

        Older builds persisted provider-shaped values (``url``, ``image_url``,
        ``file_path`` or a nested ``data[0]`` object) instead of the stable
        ``media_path`` contract.  Accept those records at the persistence
        boundary so every API response has one predictable shape while new
        generations continue to store server-owned paths.
        """

        if isinstance(value, str):
            data: dict[str, Any] = {"media_path": value}
        elif isinstance(value, dict):
            data = dict(value)
        else:
            return value

        candidates = [data]
        for key in ("result", "output", "media"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        provider_data = data.get("data")
        if isinstance(provider_data, list) and provider_data and isinstance(provider_data[0], dict):
            candidates.append(provider_data[0])

        selected_key = ""
        media_path = data.get("media_path")
        if not isinstance(media_path, str) or not media_path.strip():
            for candidate in candidates:
                for key in (
                    "media_path",
                    "file_path",
                    "path",
                    "image_url",
                    "video_url",
                    "output_url",
                    "download_url",
                    "url",
                ):
                    candidate_value = candidate.get(key)
                    if isinstance(candidate_value, str) and candidate_value.strip():
                        media_path = candidate_value.strip()
                        selected_key = key
                        break
                if media_path:
                    break
        if media_path:
            data["media_path"] = str(media_path).strip()

        thumbnail = data.get("thumbnail_path")
        if not isinstance(thumbnail, str) or not thumbnail.strip():
            for candidate in candidates:
                for key in ("thumbnail_path", "thumbnail_url", "thumbnail", "poster_url", "poster"):
                    candidate_value = candidate.get(key)
                    if isinstance(candidate_value, str) and candidate_value.strip():
                        data["thumbnail_path"] = candidate_value.strip()
                        break
                if data.get("thumbnail_path"):
                    break

        declared_type = str(data.get("media_type") or data.get("type") or "").lower()
        if declared_type.startswith("video/"):
            declared_type = "video"
        elif declared_type.startswith("image/"):
            declared_type = "image"
        if declared_type not in {"image", "video"}:
            extension = os.path.splitext(str(media_path or "").split("?", 1)[0])[1].lower()
            declared_type = (
                "video"
                if selected_key == "video_url"
                or extension in {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"}
                else "image"
            )
        data["media_type"] = declared_type

        if "saved_to_library" not in data and "saved" in data:
            data["saved_to_library"] = bool(data.get("saved"))

        if not data.get("id") and media_path:
            data["id"] = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"enmotion-playground-output:{media_path}",
                )
            )
        return data


class PlaygroundGeneration(BaseModel):
    id: str = Field(..., description="Unique identifier (UUID)")
    mode: PlaygroundMode = Field(..., description="Generation mode")
    model_id: str = Field(..., description="Model identifier from model catalog")
    prompt: str = Field(..., description="Text prompt for generation")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt to exclude undesired elements")
    input_media: List[str] = Field(default_factory=list, description="Input file paths for image/video-conditioned modes")
    parameters: dict = Field(default_factory=dict, description="Generation parameters (resolution, duration, aspect_ratio, etc.)")
    batch_size: int = Field(1, ge=1, le=4, description="Number of outputs to generate per request (1-4)")
    outputs: List[PlaygroundOutput] = Field(default_factory=list, description="Generated outputs")
    status: str = Field("pending", description="Generation status: pending/processing/completed/failed")
    error: Optional[str] = Field(None, description="Error message if generation failed")
    created_at: str = Field(..., description="Creation timestamp in ISO 8601 format")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_outputs(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        outputs = data.get("outputs")

        if isinstance(outputs, dict):
            nested_data = outputs.get("data")
            outputs = nested_data if isinstance(nested_data, list) else [outputs]
        elif not isinstance(outputs, list):
            outputs = None

        legacy_kind = ""
        if not outputs:
            for key in ("results", "images", "videos", "output_paths", "output", "result"):
                candidate = data.get(key)
                if candidate is None:
                    continue
                legacy_kind = key
                if isinstance(candidate, dict) and isinstance(candidate.get("data"), list):
                    outputs = candidate["data"]
                elif isinstance(candidate, list):
                    outputs = candidate
                else:
                    outputs = [candidate]
                break

        if not outputs:
            for key in ("image_url", "video_url", "output_url", "media_path"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    legacy_kind = key
                    outputs = [{key: candidate}]
                    break

        if outputs:
            mode = str(data.get("mode") or "").lower()
            default_type = (
                "video"
                if mode in {"t2v", "i2v"} or legacy_kind in {"videos", "video_url"}
                else "image"
            )
            normalized_outputs = []
            for output in outputs:
                if isinstance(output, str):
                    normalized_outputs.append({"media_path": output, "media_type": default_type})
                elif isinstance(output, dict):
                    normalized = dict(output)
                    normalized.setdefault("media_type", default_type)
                    normalized_outputs.append(normalized)
                else:
                    normalized_outputs.append(output)
            data["outputs"] = normalized_outputs
        return data


class PlaygroundTemplate(BaseModel):
    id: str = Field(..., description="Unique identifier (UUID)")
    name: str = Field(..., description="Template display name")
    category: str = Field("general", description="Template category: image/video/general")
    prompt: str = Field(..., description="Template prompt text")
    negative_prompt: Optional[str] = Field(None, description="Default negative prompt")
    default_mode: Optional[PlaygroundMode] = Field(None, description="Default generation mode for this template")
    default_model_id: Optional[str] = Field(None, description="Default model identifier")
    default_parameters: dict = Field(default_factory=dict, description="Default generation parameters")
    created_at: str = Field(..., description="Creation timestamp in ISO 8601 format")
    updated_at: str = Field(..., description="Last update timestamp in ISO 8601 format")

    @model_validator(mode="before")
    @classmethod
    def migrate_stale_default_model(cls, value):
        data = dict(value or {})
        mode = data.get("default_mode")
        mode = mode.value if isinstance(mode, PlaygroundMode) else mode
        if mode not in {"t2i", "i2i", "t2v", "i2v", None}:
            data["default_mode"] = None
            data["default_model_id"] = None
            return data
        model_id = data.get("default_model_id")
        if model_id and mode:
            try:
                validate_model_for_mode(model_id, mode)
            except ValueError:
                data["default_model_id"] = DEFAULT_MODELS[
                    IMAGE if mode in {"t2i", "i2i"} else VIDEO
                ]
        elif model_id:
            try:
                get_model_spec(model_id)
            except ValueError:
                data["default_model_id"] = None
        return data


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PlaygroundMode = Field(..., description="Generation mode")
    model_id: str = Field(..., max_length=128, description="Model identifier from model catalog")
    prompt: str = Field(..., max_length=50_000, description="Text prompt for generation")
    negative_prompt: Optional[str] = Field(None, max_length=50_000, description="Negative prompt to exclude undesired elements")
    input_media: Optional[List[str]] = Field(None, max_length=4, description="Input file paths for image/video-conditioned modes")
    parameters: Optional[dict] = Field(None, max_length=64, description="Generation parameters (resolution, duration, aspect_ratio, etc.)")
    batch_size: Optional[int] = Field(1, ge=1, le=4, description="Number of outputs to generate (1-4)")

    @model_validator(mode="after")
    def validate_newapi_selection(self):
        validate_model_for_mode(self.model_id, self.mode.value)
        if self.mode in {PlaygroundMode.I2I, PlaygroundMode.I2V} and not self.input_media:
            raise ValueError(f"{self.mode.value} 生成需要一张来源图片")
        if self.mode in {PlaygroundMode.T2V, PlaygroundMode.I2V}:
            self.parameters = normalize_playground_video_parameters(
                self.mode,
                self.model_id,
                self.parameters,
            )
        return self


class SaveToLibraryRequest(BaseModel):
    category: str = Field("general", description="Library category for the saved output")


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Template display name")
    category: Optional[str] = Field("general", description="Template category: image/video/general")
    prompt: str = Field(..., description="Template prompt text")
    negative_prompt: Optional[str] = Field(None, description="Default negative prompt")
    default_mode: Optional[PlaygroundMode] = Field(None, description="Default generation mode")
    default_model_id: Optional[str] = Field(None, description="Default model identifier")
    default_parameters: Optional[dict] = Field(None, description="Default generation parameters")

    @model_validator(mode="after")
    def validate_newapi_default(self):
        if self.default_model_id and self.default_mode:
            validate_model_for_mode(self.default_model_id, self.default_mode.value)
        elif self.default_model_id:
            get_model_spec(self.default_model_id)
        return self


class UpdateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, description="Template display name")
    category: Optional[str] = Field(None, description="Template category: image/video/general")
    prompt: Optional[str] = Field(None, description="Template prompt text")
    negative_prompt: Optional[str] = Field(None, description="Default negative prompt")
    default_mode: Optional[PlaygroundMode] = Field(None, description="Default generation mode")
    default_model_id: Optional[str] = Field(None, description="Default model identifier")
    default_parameters: Optional[dict] = Field(None, description="Default generation parameters")

    @model_validator(mode="after")
    def validate_newapi_default(self):
        if self.default_model_id and self.default_mode:
            validate_model_for_mode(self.default_model_id, self.default_mode.value)
        elif self.default_model_id:
            get_model_spec(self.default_model_id)
        return self
