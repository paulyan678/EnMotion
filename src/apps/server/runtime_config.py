"""Shared, reloadable provider configuration for API and worker processes."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


RUNTIME_CONFIG_KEYS = {
    "NEWAPI_BASE_URL",
    "NEWAPI_CHAT_MODEL",
    "NEWAPI_IMAGE_MODEL",
    "NEWAPI_VIDEO_MODEL",
    "NEWAPI_GPT_IMAGE_2_API_KEY",
    "NEWAPI_SEEDANCE_2_API_KEY",
    "NEWAPI_SEEDANCE_2_FAST_API_KEY",
    "NEWAPI_SEEDANCE_2_MINI_API_KEY",
    "NEWAPI_DEEPSEEK_V4_FLASH_API_KEY",
    "NEWAPI_QWEN_37_MAX_API_KEY",
    "NEWAPI_DEEPSEEK_V4_PRO_API_KEY",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "OSS_BUCKET_NAME",
    "OSS_ENDPOINT",
    "OSS_BASE_PATH",
    "OSS_ENABLE",
}
_LOCK = threading.RLock()


def server_runtime_config_path() -> Path:
    data_dir = Path(os.getenv("ENMOTION_DATA_DIR", "data")).expanduser()
    return (data_dir / "config.json").resolve()


def load_server_runtime_config() -> dict[str, str]:
    """Reload the admin-managed allowlisted values into this process.

    Persisted values intentionally override the initial container environment:
    otherwise blank values from ``.env.server`` would make settings saved in
    the admin UI disappear whenever the API or worker restarts.
    """

    path = server_runtime_config_path()
    if not path.exists():
        return {}
    with _LOCK:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to load server runtime configuration: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Server runtime configuration must be a JSON object")
        normalized: dict[str, str] = {}
        for key, value in raw.items():
            if key not in RUNTIME_CONFIG_KEYS:
                continue
            if isinstance(value, bool):
                normalized[key] = "true" if value else "false"
            elif isinstance(value, (str, int, float)):
                normalized[key] = str(value)
            elif value is None:
                normalized[key] = ""
            else:
                raise RuntimeError(f"Invalid server runtime configuration value for {key}")
        os.environ.update(normalized)
        return normalized
