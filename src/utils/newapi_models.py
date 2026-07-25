"""Strict New API model registry and credential routing.

This module is the runtime source of truth for the seven models supported by
EnMotion.  Model identifiers and API keys are deliberately resolved together so
that a credential can never be reused for a different model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, MutableMapping, Optional, Tuple


CHAT = "chat"
IMAGE = "image"
VIDEO = "video"
CAPABILITIES = (CHAT, IMAGE, VIDEO)


@dataclass(frozen=True)
class NewAPIModelSpec:
    model_id: str
    display_name: str
    capability: str
    api_key_env: str
    supported_modes: Tuple[str, ...]


MODEL_SPECS = {
    "gpt-image-2": NewAPIModelSpec(
        model_id="gpt-image-2",
        display_name="GPT Image 2",
        capability=IMAGE,
        api_key_env="NEWAPI_GPT_IMAGE_2_API_KEY",
        supported_modes=("t2i", "i2i"),
    ),
    "doubao-seedance-2-0-260128": NewAPIModelSpec(
        model_id="doubao-seedance-2-0-260128",
        display_name="Seedance 2.0",
        capability=VIDEO,
        api_key_env="NEWAPI_SEEDANCE_2_API_KEY",
        supported_modes=("t2v", "i2v"),
    ),
    "doubao-seedance-2-0-fast-260128": NewAPIModelSpec(
        model_id="doubao-seedance-2-0-fast-260128",
        display_name="Seedance 2.0 Fast",
        capability=VIDEO,
        api_key_env="NEWAPI_SEEDANCE_2_FAST_API_KEY",
        supported_modes=("t2v", "i2v"),
    ),
    "doubao-seedance-2-0-mini-260615": NewAPIModelSpec(
        model_id="doubao-seedance-2-0-mini-260615",
        display_name="Seedance 2.0 Mini",
        capability=VIDEO,
        api_key_env="NEWAPI_SEEDANCE_2_MINI_API_KEY",
        supported_modes=("t2v", "i2v"),
    ),
    "deepseek-v4-flash": NewAPIModelSpec(
        model_id="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        capability=CHAT,
        api_key_env="NEWAPI_DEEPSEEK_V4_FLASH_API_KEY",
        supported_modes=("chat",),
    ),
    "qwen3.7-max": NewAPIModelSpec(
        model_id="qwen3.7-max",
        display_name="Qwen 3.7 Max",
        capability=CHAT,
        api_key_env="NEWAPI_QWEN_37_MAX_API_KEY",
        supported_modes=("chat",),
    ),
    "deepseek-v4-pro": NewAPIModelSpec(
        model_id="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        capability=CHAT,
        api_key_env="NEWAPI_DEEPSEEK_V4_PRO_API_KEY",
        supported_modes=("chat",),
    ),
}


DEFAULT_MODELS = {
    CHAT: "deepseek-v4-flash",
    IMAGE: "gpt-image-2",
    VIDEO: "doubao-seedance-2-0-fast-260128",
}

ACTIVE_MODEL_ENV = {
    CHAT: "NEWAPI_CHAT_MODEL",
    IMAGE: "NEWAPI_IMAGE_MODEL",
    VIDEO: "NEWAPI_VIDEO_MODEL",
}

# Read only during migration. Runtime request routing never consults these
# capability-scoped legacy fields and never consults NEWAPI_API_KEY.
LEGACY_CAPABILITY_KEY_ENV = {
    CHAT: "NEWAPI_CHAT_API_KEY",
    IMAGE: "NEWAPI_IMAGE_API_KEY",
    VIDEO: "NEWAPI_VIDEO_API_KEY",
}

MODEL_API_KEY_FIELDS = tuple(spec.api_key_env for spec in MODEL_SPECS.values())


class UnsupportedNewAPIModelError(ValueError):
    """Raised when a caller supplies a model outside the approved registry."""


class MissingNewAPIKeyError(RuntimeError):
    """Raised when the selected model's dedicated credential is missing."""


def models_for_capability(capability: str) -> Tuple[NewAPIModelSpec, ...]:
    normalized = (capability or "").strip().lower()
    if normalized not in CAPABILITIES:
        raise ValueError(f"Unsupported New API capability: {capability!r}")
    return tuple(spec for spec in MODEL_SPECS.values() if spec.capability == normalized)


def get_model_spec(model_id: str, capability: Optional[str] = None) -> NewAPIModelSpec:
    normalized = (model_id or "").strip()
    spec = MODEL_SPECS.get(normalized)
    if spec is None:
        raise UnsupportedNewAPIModelError(f"Unsupported New API model ID: {normalized or '<empty>'}")
    if capability is not None and spec.capability != capability:
        raise UnsupportedNewAPIModelError(
            f"Model '{normalized}' is a {spec.capability} model, not a {capability} model"
        )
    return spec


def validate_model_for_mode(model_id: str, mode: str) -> NewAPIModelSpec:
    normalized_mode = (mode or "").strip().lower()
    capability = IMAGE if normalized_mode in {"t2i", "i2i"} else VIDEO
    if normalized_mode not in {"t2i", "i2i", "t2v", "i2v"}:
        raise UnsupportedNewAPIModelError(
            f"New API does not support generation mode: {normalized_mode or '<empty>'}"
        )
    spec = get_model_spec(model_id, capability)
    if normalized_mode not in spec.supported_modes:
        raise UnsupportedNewAPIModelError(
            f"Model '{model_id}' does not support generation mode '{normalized_mode}'"
        )
    return spec


def normalize_selected_model(capability: str, model_id: Optional[str]) -> str:
    """Return an approved selection, migrating missing/stale values to default."""

    normalized = (model_id or "").strip()
    try:
        return get_model_spec(normalized, capability).model_id
    except UnsupportedNewAPIModelError:
        return DEFAULT_MODELS[capability]


def get_selected_model(
    capability: str,
    env: Optional[Mapping[str, str]] = None,
    *,
    migrate_stale: bool = False,
) -> str:
    source = env if env is not None else os.environ
    raw = source.get(ACTIVE_MODEL_ENV[capability])
    if migrate_stale:
        return normalize_selected_model(capability, raw)
    model_id = (raw or DEFAULT_MODELS[capability]).strip()
    return get_model_spec(model_id, capability).model_id


def resolve_model_api_key(
    model_id: str,
    capability: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve only the dedicated credential belonging to ``model_id``.

    There is intentionally no shared-key or capability-key fallback.
    """

    spec = get_model_spec(model_id, capability)
    from ..apps.hybrid.provider import hybrid_mode_enabled, provider_gateway_token

    if hybrid_mode_enabled():
        # Managed desktops use the employee's opaque control-plane token.
        # The gateway injects the model's company credential server-side.
        gateway_token: str = provider_gateway_token()
        return gateway_token
    source = env if env is not None else os.environ
    value = (source.get(spec.api_key_env) or "").strip()
    if not value:
        raise MissingNewAPIKeyError(
            f"API key for model '{spec.model_id}' is not configured (set {spec.api_key_env})"
        )
    return value


def is_model_configured(model_id: str, env: Optional[Mapping[str, str]] = None) -> bool:
    try:
        resolve_model_api_key(model_id, env=env)
        return True
    except MissingNewAPIKeyError:
        return False


def redact_newapi_secrets(
    value: object,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Remove every configured model credential from diagnostic text."""

    source = env if env is not None else os.environ
    redacted = str(value)
    for field in MODEL_API_KEY_FIELDS:
        secret = (source.get(field) or "").strip()
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def migrate_legacy_newapi_environment(
    env: Optional[MutableMapping[str, str]] = None,
) -> dict[str, str]:
    """Migrate capability-scoped keys and stale selections in-place.

    A legacy capability key is copied only to the model that was selected for
    that capability, and only when that model-specific destination is empty.
    Existing model-specific keys are never overwritten. Legacy keys are left
    untouched so migration cannot destroy a credential.
    """

    target = env if env is not None else os.environ
    updates: dict[str, str] = {}
    for capability in CAPABILITIES:
        active_env = ACTIVE_MODEL_ENV[capability]
        selected = normalize_selected_model(capability, target.get(active_env))
        if target.get(active_env) != selected:
            target[active_env] = selected
            updates[active_env] = selected

        spec = get_model_spec(selected, capability)
        legacy_value = (target.get(LEGACY_CAPABILITY_KEY_ENV[capability]) or "").strip()
        existing_value = (target.get(spec.api_key_env) or "").strip()
        if legacy_value and not existing_value:
            target[spec.api_key_env] = legacy_value
            updates[spec.api_key_env] = legacy_value
    return updates


def public_model_status(env: Optional[Mapping[str, str]] = None) -> list[dict[str, object]]:
    source = env if env is not None else os.environ
    active_by_capability = {
        capability: get_selected_model(capability, source, migrate_stale=True)
        for capability in CAPABILITIES
    }
    return [
        {
            "model_id": spec.model_id,
            "display_name": spec.display_name,
            "capability": spec.capability,
            "api_key_field": spec.api_key_env,
            "enabled": True,
            "configured": bool((source.get(spec.api_key_env) or "").strip()),
            "active": active_by_capability[spec.capability] == spec.model_id,
            "supported_modes": list(spec.supported_modes),
        }
        for spec in MODEL_SPECS.values()
    ]
