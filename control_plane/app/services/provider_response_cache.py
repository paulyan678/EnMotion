from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("enmotion.control_plane.provider_response_cache")

_CACHE_MAGIC = b"ENMOTION-PROVIDER-RESPONSE-V1\0"
_USAGE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class ProviderResponseCacheError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CachedProviderResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


def _cache_root(database_url: str) -> Path:
    database_path = database_url.removeprefix("sqlite:///")
    if not database_path or database_path == ":memory:":
        raise ProviderResponseCacheError("provider response cache requires a file-backed database")
    return Path(database_path).expanduser().resolve().parent / "provider-response-cache"


class ProviderResponseCache:
    """Short-lived encrypted replay cache for synchronous image responses.

    New API image endpoints can take several minutes and return the complete
    image in one JSON response. If the desktop connection drops after the
    provider succeeds, an idempotent retry must be able to recover that exact
    response instead of charging for a second generation. Cache files are
    encrypted, owner-only, bounded, and automatically expire.
    """

    def __init__(
        self,
        *,
        database_url: str,
        secret: str,
        ttl_seconds: int = 24 * 60 * 60,
        max_content_bytes: int = 40 * 1024 * 1024,
    ) -> None:
        self.root = _cache_root(database_url)
        self.ttl_seconds = ttl_seconds
        self.max_content_bytes = max_content_bytes
        self._key = hashlib.sha256(
            b"enmotion-provider-response-cache-v1\0" + secret.encode("utf-8")
        ).digest()
        self._lock = threading.RLock()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    @staticmethod
    def _aad(usage_id: str) -> bytes:
        return f"enmotion-provider-response-v1:{usage_id}".encode("ascii")

    def _path(self, usage_id: str) -> Path:
        if not _USAGE_ID.fullmatch(usage_id):
            raise ProviderResponseCacheError("invalid usage id for provider response cache")
        return self.root / f"{usage_id}.bin"

    def store(
        self,
        usage_id: str,
        *,
        status_code: int,
        headers: Mapping[str, str],
        content: bytes,
    ) -> None:
        if not content or len(content) > self.max_content_bytes:
            raise ProviderResponseCacheError(
                "provider response is empty or exceeds the cache limit"
            )
        safe_headers = {
            str(key)[:120]: str(value)[:2_000]
            for key, value in headers.items()
            if str(key).lower()
            in {
                "content-type",
                "cache-control",
                "etag",
                "last-modified",
                "x-request-id",
            }
        }
        plaintext = json.dumps(
            {
                "stored_at": time.time(),
                "status_code": int(status_code),
                "headers": safe_headers,
                "content": base64.b64encode(content).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = os.urandom(12)
        encrypted = AESGCM(self._key).encrypt(nonce, plaintext, self._aad(usage_id))
        payload = _CACHE_MAGIC + nonce + encrypted
        destination = self._path(usage_id)
        temporary_name = ""
        with self._lock:
            try:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{usage_id}.",
                    suffix=".tmp",
                    dir=self.root,
                )
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, destination)
                temporary_name = ""
            except Exception as exc:
                raise ProviderResponseCacheError("provider response cache write failed") from exc
            finally:
                if temporary_name:
                    try:
                        os.unlink(temporary_name)
                    except OSError:
                        pass

    def load(self, usage_id: str) -> CachedProviderResponse | None:
        path = self._path(usage_id)
        with self._lock:
            try:
                if time.time() - path.stat().st_mtime > self.ttl_seconds:
                    path.unlink(missing_ok=True)
                    return None
                payload = path.read_bytes()
                if not payload.startswith(_CACHE_MAGIC) or len(payload) <= len(_CACHE_MAGIC) + 12:
                    raise ValueError("invalid provider response cache envelope")
                offset = len(_CACHE_MAGIC)
                nonce = payload[offset : offset + 12]
                encrypted = payload[offset + 12 :]
                plaintext = AESGCM(self._key).decrypt(
                    nonce,
                    encrypted,
                    self._aad(usage_id),
                )
                decoded = json.loads(plaintext)
                stored_at = float(decoded["stored_at"])
                if time.time() - stored_at > self.ttl_seconds:
                    path.unlink(missing_ok=True)
                    return None
                content = base64.b64decode(decoded["content"], validate=True)
                if not content or len(content) > self.max_content_bytes:
                    raise ValueError("cached provider response violates its size limit")
                headers = decoded.get("headers")
                if not isinstance(headers, dict):
                    raise ValueError("cached provider response headers are invalid")
                return CachedProviderResponse(
                    status_code=int(decoded["status_code"]),
                    headers={str(key): str(value) for key, value in headers.items()},
                    content=content,
                )
            except FileNotFoundError:
                return None
            except Exception as exc:
                logger.warning(
                    "Discarding unreadable provider response cache entry %s: %s",
                    usage_id,
                    type(exc).__name__,
                )
                path.unlink(missing_ok=True)
                return None

    def prune(self) -> int:
        removed = 0
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            for path in self.root.glob("*.bin"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
        return removed
