from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from ..audit import record_audit
from ..config import (
    MODEL_CAPABILITIES,
    ConfigurationError,
    Settings,
    decode_provider_config_master_key,
    validate_provider_base_url,
)
from ..database import Database, begin_immediate
from ..models import ProviderConfiguration


class ProviderConfigUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderConfigSnapshot:
    version: int
    source: str
    base_url: str
    credentials: Mapping[str, str]
    updated_at: datetime | None = None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


class ProviderConfigService:
    """Own the single organization-wide provider configuration."""

    def __init__(self, settings: Settings, db: Database):
        self._settings = settings
        self._db = db
        self._lock = threading.RLock()
        self._cache: dict[int, ProviderConfigSnapshot] = {}
        self._master_key = (
            decode_provider_config_master_key(settings.provider_config_master_key)
            if settings.provider_config_master_key
            else None
        )
        self._environment_snapshot = ProviderConfigSnapshot(
            version=0,
            source="environment",
            base_url=settings.provider_base_url.rstrip("/"),
            credentials=dict(settings.provider_credentials),
        )

    @property
    def writable(self) -> bool:
        return self._master_key is not None

    @staticmethod
    def _aad(version: int, base_url: str) -> bytes:
        return f"enmotion-provider-config-v1:{version}:{base_url}".encode("utf-8")

    def _decrypt(self, row: ProviderConfiguration) -> ProviderConfigSnapshot:
        if self._master_key is None:
            raise ProviderConfigUnavailable(
                "managed provider configuration exists but its master key is unavailable"
            )
        cached = self._cache.get(row.version)
        if cached is not None:
            return cached
        try:
            plaintext = AESGCM(self._master_key).decrypt(
                _b64decode(row.credentials_nonce),
                _b64decode(row.credentials_ciphertext),
                self._aad(row.version, row.provider_base_url),
            )
            credentials = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise ProviderConfigUnavailable(
                "managed provider configuration could not be decrypted"
            ) from exc
        if not isinstance(credentials, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in credentials.items()
        ):
            raise ProviderConfigUnavailable("managed provider configuration is invalid")
        unknown = set(credentials) - set(MODEL_CAPABILITIES)
        if unknown:
            raise ProviderConfigUnavailable("managed provider configuration has unknown models")
        snapshot = ProviderConfigSnapshot(
            version=row.version,
            source="managed",
            base_url=row.provider_base_url,
            credentials=dict(credentials),
            updated_at=row.created_at,
        )
        self._cache[row.version] = snapshot
        return snapshot

    def current(self) -> ProviderConfigSnapshot:
        with self._lock, self._db.session() as session:
            row = session.scalar(
                select(ProviderConfiguration).order_by(ProviderConfiguration.version.desc())
            )
            return self._decrypt(row) if row is not None else self._environment_snapshot

    def get_version(self, version: int | None) -> ProviderConfigSnapshot:
        effective = int(version or 0)
        if effective == 0:
            return self._environment_snapshot
        with self._lock:
            cached = self._cache.get(effective)
            if cached is not None:
                return cached
            with self._db.session() as session:
                row = session.scalar(
                    select(ProviderConfiguration).where(ProviderConfiguration.version == effective)
                )
                if row is None:
                    raise ProviderConfigUnavailable("provider configuration version is unavailable")
                return self._decrypt(row)

    def public_status(self) -> dict[str, object]:
        snapshot = self.current()
        return {
            "version": snapshot.version,
            "source": snapshot.source,
            "base_url": snapshot.base_url,
            "writable": self.writable,
            "updated_at": snapshot.updated_at,
            "models": [
                {
                    "model": model,
                    "capability": capability,
                    "configured": bool(snapshot.credentials.get(model)),
                }
                for model, capability in MODEL_CAPABILITIES.items()
            ],
        }

    def update(
        self,
        *,
        base_url: str | None,
        credential_changes: Mapping[str, str | None],
        actor_user_id: str,
        ip_address: str | None,
    ) -> dict[str, object]:
        if self._master_key is None:
            raise ProviderConfigUnavailable(
                "set ENMOTION_PROVIDER_CONFIG_MASTER_KEY before saving provider credentials"
            )
        unknown = set(credential_changes) - set(MODEL_CAPABILITIES)
        if unknown:
            raise ValueError("unsupported provider model")
        for value in credential_changes.values():
            if value is not None and (
                not value.strip() or len(value) > 16_384 or "\r" in value or "\n" in value
            ):
                raise ValueError("provider credential is empty or invalid")

        with self._lock:
            with self._db.session() as session:
                begin_immediate(session)
                latest = session.scalar(
                    select(ProviderConfiguration).order_by(ProviderConfiguration.version.desc())
                )
                current = self._decrypt(latest) if latest else self._environment_snapshot
                effective_base_url = validate_provider_base_url(
                    base_url if base_url is not None else current.base_url,
                    allow_insecure=self._settings.allow_insecure_upstreams,
                )
                credentials = dict(current.credentials)
                changed_models: list[str] = []
                for model, value in credential_changes.items():
                    previous = credentials.get(model)
                    if value is None:
                        credentials.pop(model, None)
                    else:
                        credentials[model] = value.strip()
                    if credentials.get(model) != previous:
                        changed_models.append(model)
                changed_fields = []
                if effective_base_url != current.base_url:
                    changed_fields.append("base_url")
                if changed_models:
                    changed_fields.append("credentials")
                if not changed_fields:
                    raise ValueError("provider configuration did not change")

                version = (latest.version if latest else 0) + 1
                nonce = os.urandom(12)
                ciphertext = AESGCM(self._master_key).encrypt(
                    nonce,
                    json.dumps(
                        credentials,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    self._aad(version, effective_base_url),
                )
                row = ProviderConfiguration(
                    version=version,
                    provider_base_url=effective_base_url,
                    credentials_nonce=_b64encode(nonce),
                    credentials_ciphertext=_b64encode(ciphertext),
                    created_by_user_id=actor_user_id,
                )
                session.add(row)
                session.flush()
                record_audit(
                    session,
                    actor_user_id=actor_user_id,
                    action="admin.provider_config_updated",
                    target_type="provider_configuration",
                    target_id=row.id,
                    detail={
                        "version": version,
                        "changed_fields": changed_fields,
                        "changed_models": sorted(changed_models),
                    },
                    ip_address=ip_address,
                )
                snapshot = ProviderConfigSnapshot(
                    version=version,
                    source="managed",
                    base_url=effective_base_url,
                    credentials=credentials,
                    updated_at=row.created_at,
                )
            self._cache[version] = snapshot
        return self.public_status()
