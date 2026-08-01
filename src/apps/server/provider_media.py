"""Short-lived, signed image URLs for server-side generation providers.

Provider APIs cannot read an authenticated ``/files`` URL and large inline
data URLs are commonly rejected by gateways.  These helpers expose exactly
one workspace image for a short period without exposing the user's session or
making the workspace media tree public.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from .config import ServerSettings
from ..web_runtime.context import get_tenant
from ..web_runtime.workspace_paths import workspace_output_root
from ...utils.media_security import image_limit_bytes, resolve_workspace_media_path


PROVIDER_MEDIA_PATH_PREFIX = "/provider-media/"
DEFAULT_PROVIDER_MEDIA_TTL_SECONDS = 30 * 60
MAX_PROVIDER_MEDIA_TTL_SECONDS = 60 * 60
PROVIDER_MEDIA_SIGNING_PURPOSE = "enmotion/provider-media/v1"
PROVIDER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class ProviderMediaTokenError(ValueError):
    """Raised when a provider-media token or target is invalid."""


class _ProviderMediaAccessLogFilter(logging.Filter):
    """Keep short-lived bearer URLs out of application access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        return PROVIDER_MEDIA_PATH_PREFIX not in record.getMessage()


def install_provider_media_access_log_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(
        isinstance(existing, _ProviderMediaAccessLogFilter)
        for existing in access_logger.filters
    ):
        access_logger.addFilter(_ProviderMediaAccessLogFilter())


def _urlsafe_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _urlsafe_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    try:
        return base64.b64decode(payload + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ProviderMediaTokenError("Invalid provider media token") from exc


def _configured_ttl_seconds() -> int:
    raw = os.getenv(
        "ENMOTION_PROVIDER_MEDIA_TTL_SECONDS",
        str(DEFAULT_PROVIDER_MEDIA_TTL_SECONDS),
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderMediaTokenError(
            "ENMOTION_PROVIDER_MEDIA_TTL_SECONDS must be an integer"
        ) from exc
    if value <= 0 or value > MAX_PROVIDER_MEDIA_TTL_SECONDS:
        raise ProviderMediaTokenError(
            "ENMOTION_PROVIDER_MEDIA_TTL_SECONDS must be between 1 and 3600"
        )
    return value


def _public_origin(settings: ServerSettings) -> str:
    configured = settings.public_base_url
    if configured:
        parsed = urlparse(configured)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return configured.rstrip("/")
    raise ProviderMediaTokenError(
        "ENMOTION_PUBLIC_BASE_URL is required for server image-to-video generation"
    )


def _validate_image_path(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    media_type = mimetypes.guess_type(candidate.name)[0] or ""
    if (
        not candidate.is_file()
        or candidate.suffix.lower() not in PROVIDER_IMAGE_SUFFIXES
        or not media_type.startswith("image/")
    ):
        raise ProviderMediaTokenError("Provider media must be an existing image")
    if candidate.stat().st_size > image_limit_bytes():
        raise ProviderMediaTokenError("Provider media image exceeds the configured limit")
    return candidate


def _signature(secret: str, encoded_payload: str) -> str:
    message = f"{PROVIDER_MEDIA_SIGNING_PURPOSE}\n{encoded_payload}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def create_provider_media_url(
    reference: str,
    *,
    now: int | None = None,
    ttl_seconds: int | None = None,
    settings: ServerSettings | None = None,
) -> str:
    """Create a bearer URL for one image in the current tenant workspace."""

    active_settings = settings or ServerSettings.from_env()
    tenant = get_tenant(required=True)
    assert tenant is not None
    output_root = workspace_output_root(tenant.workspace_id)
    candidate = _validate_image_path(
        resolve_workspace_media_path(output_root, reference, require_file=True)
    )
    relative_path = candidate.relative_to(output_root.resolve()).as_posix()
    issued_at = int(time.time() if now is None else now)
    ttl = _configured_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0 or ttl > MAX_PROVIDER_MEDIA_TTL_SECONDS:
        raise ProviderMediaTokenError("Provider media URL lifetime is invalid")

    token_payload = {
        "v": 1,
        "workspace_id": tenant.workspace_id,
        "path": relative_path,
        "expires_at": issued_at + ttl,
    }
    encoded = _urlsafe_encode(
        json.dumps(token_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _signature(active_settings.session_secret, encoded)
    filename = quote(candidate.name, safe="-._~")
    return (
        f"{_public_origin(active_settings)}{PROVIDER_MEDIA_PATH_PREFIX}"
        f"{encoded}.{signature}/{filename}"
    )


def resolve_provider_media_token(
    token: str,
    *,
    now: int | None = None,
    settings: ServerSettings | None = None,
) -> Path:
    """Verify a signed token and resolve its workspace-confined image path."""

    active_settings = settings or ServerSettings.from_env()
    if not token or len(token) > 4096 or token.count(".") != 1:
        raise ProviderMediaTokenError("Invalid provider media token")
    encoded, supplied_signature = token.split(".", 1)
    if len(supplied_signature) != 64 or any(
        char not in "0123456789abcdef" for char in supplied_signature
    ):
        raise ProviderMediaTokenError("Invalid provider media token")
    try:
        expected_signature = _signature(active_settings.session_secret, encoded)
    except UnicodeEncodeError as exc:
        raise ProviderMediaTokenError("Invalid provider media token") from exc
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ProviderMediaTokenError("Invalid provider media token")

    try:
        payload = json.loads(_urlsafe_decode(encoded))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderMediaTokenError("Invalid provider media token") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ProviderMediaTokenError("Invalid provider media token")
    workspace_id = payload.get("workspace_id")
    relative_path = payload.get("path")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(workspace_id, str)
        or not isinstance(relative_path, str)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
    ):
        raise ProviderMediaTokenError("Invalid provider media token")
    current_time = int(time.time() if now is None else now)
    if expires_at <= current_time:
        raise ProviderMediaTokenError("Provider media token has expired")
    if expires_at > current_time + MAX_PROVIDER_MEDIA_TTL_SECONDS:
        raise ProviderMediaTokenError("Provider media token lifetime is invalid")

    try:
        output_root = workspace_output_root(workspace_id)
        resolved = resolve_workspace_media_path(
            output_root,
            relative_path,
            require_file=True,
        )
        return _validate_image_path(resolved)
    except (OSError, ValueError) as exc:
        raise ProviderMediaTokenError("Provider media is unavailable") from exc
