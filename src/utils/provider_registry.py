"""Strict New API-only catalog routing metadata.

The executable model/key pairing lives in :mod:`src.utils.newapi_models`.
This module remains as the catalog-family compatibility layer used by the
catalog loader and diagnostics; it deliberately exposes no provider switch.
"""

from dataclasses import dataclass, field, replace
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .model_catalog import build_provider_family_configs, load_generated_model_catalog


SUPPORTED_PROVIDER_BACKENDS = ("newapi",)


@dataclass
class ProviderFamilyConfig:
    model_family: str
    backend_default: str = "newapi"
    backend_env_key: Optional[str] = None
    credential_sources: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    supported_modalities: Tuple[str, ...] = field(default_factory=tuple)
    image_input_mode: Dict[str, str] = field(default_factory=dict)
    audio_input_mode: Dict[str, str] = field(default_factory=dict)
    reference_video_input_mode: Dict[str, str] = field(default_factory=dict)


class ProviderRegistry:
    """Catalog family registry with a fixed New API backend."""

    def __init__(self, families: Optional[Sequence[ProviderFamilyConfig]] = None):
        self._families: Dict[str, ProviderFamilyConfig] = {}
        for family in families or ():
            self.register_family(family)

    def register_family(self, config: ProviderFamilyConfig) -> None:
        family = (config.model_family or "").strip().lower()
        if not family:
            raise ValueError("model_family cannot be empty")

        backend_default = (config.backend_default or "").strip().lower()
        if backend_default != "newapi":
            raise ValueError(
                f"Unsupported provider backend '{config.backend_default}'. "
                "EnMotion supports New API only."
            )

        unsupported_sources = set(config.credential_sources) - {"newapi"}
        if unsupported_sources:
            providers = ", ".join(sorted(unsupported_sources))
            raise ValueError(
                f"Unsupported credential provider(s): {providers}. "
                "EnMotion supports New API only."
            )

        self._families[family] = replace(
            config,
            model_family=family,
            backend_default="newapi",
            backend_env_key=None,
        )

    def get_family_config(self, model_name: str) -> ProviderFamilyConfig:
        normalized = (model_name or "").strip().lower()
        if not normalized:
            raise ValueError("model_name cannot be empty")

        for family in sorted(self._families, key=len, reverse=True):
            if normalized.startswith(family):
                return self._families[family]
        raise KeyError(f"No provider family registered for model '{model_name}'")

    def resolve_backend(
        self,
        model_name: str,
        env: Optional[Mapping[str, str]] = None,
    ) -> str:
        # Resolve the family first so unsupported model IDs still fail closed.
        self.get_family_config(model_name)
        return "newapi"


def get_default_provider_registry() -> ProviderRegistry:
    """Build the registry from the validated generated catalog.

    A missing or invalid catalog is an application configuration error; there
    is intentionally no legacy provider-family fallback.
    """

    catalog = load_generated_model_catalog()
    return ProviderRegistry(build_provider_family_configs(catalog))


def resolve_provider_backend(
    model_name: str,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    return get_default_provider_registry().resolve_backend(model_name=model_name, env=env)


def get_gateway_for_model(
    model_id: str,
    backend: Optional[str] = None,
) -> Optional[str]:
    """Return catalog gateway metadata for an approved New API model."""

    from .model_catalog import get_catalog_accessor

    requested_backend = (backend or "newapi").strip().lower()
    if requested_backend != "newapi":
        raise ValueError(
            f"Unsupported provider backend '{requested_backend}'. "
            "EnMotion supports New API only."
        )

    accessor = get_catalog_accessor()
    canonical_id = accessor.resolve_legacy_to_canonical(model_id) or model_id
    gateway = accessor.get_gateway(canonical_id, "newapi")
    return gateway if isinstance(gateway, str) else None
