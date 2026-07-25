"""Persisted, content-versioned image derivatives for authenticated media.

Original media remains authoritative and immediately usable. Derivatives are a
restartable read optimization: one bounded background worker creates them,
manifests survive process restarts, and stale ``pending`` leases can be
reclaimed without changing project metadata.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Literal, Optional
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from ...utils.media_security import (
    UnsafeMediaReferenceError,
    resolve_workspace_media_path,
)
from .file_lock import interprocess_lock

logger = logging.getLogger(__name__)

DERIVATIVE_SCHEMA_VERSION = 1
DERIVATIVE_WIDTHS = (96, 384, 768)
DERIVATIVE_MIME_TYPE = "image/webp"
DERIVATIVE_ROOT = "derivatives"
DERIVATIVE_PENDING_TTL_SECONDS = 5 * 60
DERIVATIVE_FAILURE_RETRY_SECONDS = 60 * 60
SUPPORTED_IMAGE_SUFFIXES = {
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_PIXELS = 50_000_000

DerivativeState = Literal["ready", "pending", "failed", "unavailable"]


class ImageDerivativeVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=2_048)
    width: int = Field(ge=1, le=32_768)
    height: int = Field(ge=1, le=32_768)
    mime_type: Literal["image/webp"] = DERIVATIVE_MIME_TYPE
    byte_size: int = Field(ge=1)


class ImageDerivativeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = DERIVATIVE_SCHEMA_VERSION
    source_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_relative_path: str = Field(min_length=1, max_length=2_048)
    source_size: int = Field(ge=1)
    source_mtime_ns: int = Field(ge=0)
    original_width: Optional[int] = Field(default=None, ge=1, le=32_768)
    original_height: Optional[int] = Field(default=None, ge=1, le=32_768)
    original_mime_type: Optional[str] = Field(default=None, max_length=128)
    original_byte_size: int = Field(ge=1)
    state: Literal["ready", "pending", "failed"]
    generated_at: float = Field(ge=0)
    variants: tuple[ImageDerivativeVariant, ...] = ()
    failure_code: Optional[str] = Field(default=None, max_length=64)


class ImageDerivativeLookup(BaseModel):
    """Safe projection consumed by API read models."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: DerivativeState
    original_width: Optional[int] = Field(default=None, ge=1, le=32_768)
    original_height: Optional[int] = Field(default=None, ge=1, le=32_768)
    original_mime_type: Optional[str] = Field(default=None, max_length=128)
    original_byte_size: Optional[int] = Field(default=None, ge=1)
    variants: tuple[ImageDerivativeVariant, ...] = ()
    failure_code: Optional[str] = Field(default=None, max_length=64)


def _positive_env_int(name: str, default: int, *, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, value))


def _source_limit_bytes() -> int:
    return _positive_env_int(
        "ENMOTION_DERIVATIVE_MAX_SOURCE_BYTES",
        MAX_SOURCE_BYTES,
        maximum=512 * 1024 * 1024,
    )


def _source_key(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _unavailable_digest(reference: str) -> str:
    raw = str(reference or "").strip()
    parsed = urlsplit(raw)
    durable = (
        f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path}"
        if parsed.scheme or parsed.netloc
        else parsed.path or raw.split("?", 1)[0]
    )
    return hashlib.sha256(durable.encode("utf-8")).hexdigest()


def _pending_revision(source_key: str, size: int, mtime_ns: int) -> str:
    return hashlib.sha256(f"{source_key}\0{size}\0{mtime_ns}".encode("ascii")).hexdigest()


def _paths(output_root: Path, source_key: str) -> tuple[Path, Path, Path]:
    manifest = output_root / DERIVATIVE_ROOT / "manifests" / source_key[:2] / f"{source_key}.json"
    lock = manifest.with_suffix(".lock")
    images = output_root / DERIVATIVE_ROOT / "images" / source_key[:2] / source_key
    return manifest, lock, images


def _resolved_source(
    output_root: str | Path,
    reference: str,
) -> tuple[Path, str, str] | None:
    raw = str(reference or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or raw.startswith("//"):
        return None
    path_reference = parsed.path
    if Path(path_reference).suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
        return None
    root = Path(output_root).expanduser().resolve()
    try:
        candidate = Path(resolve_workspace_media_path(root, path_reference, require_file=True))
    except UnsafeMediaReferenceError:
        return None
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        return None
    if relative.startswith(f"{DERIVATIVE_ROOT}/") or candidate.is_symlink():
        return None
    key = _source_key(relative)
    return candidate, relative, key


def _read_manifest(path: Path) -> ImageDerivativeManifest | None:
    try:
        if path.stat().st_size > 64 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ImageDerivativeManifest.model_validate(payload)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _matches_source(
    manifest: ImageDerivativeManifest,
    *,
    source_key: str,
    size: int,
    mtime_ns: int,
) -> bool:
    return (
        manifest.source_key == source_key
        and manifest.source_size == size
        and manifest.source_mtime_ns == mtime_ns
    )


def _manifest_current(
    manifest: ImageDerivativeManifest | None,
    *,
    output_root: Path,
    source_key: str,
    size: int,
    mtime_ns: int,
) -> bool:
    if manifest is None or not _matches_source(
        manifest,
        source_key=source_key,
        size=size,
        mtime_ns=mtime_ns,
    ):
        return False
    if manifest.state != "ready" or not manifest.variants:
        return True
    root = output_root.resolve()
    for variant in manifest.variants:
        candidate = (root / variant.url).resolve()
        if (
            root not in candidate.parents
            or not candidate.is_file()
            or candidate.stat().st_size != variant.byte_size
        ):
            return False
    return True


def _lookup_from_manifest(
    manifest: ImageDerivativeManifest,
) -> ImageDerivativeLookup:
    return ImageDerivativeLookup(
        source_key=manifest.source_key,
        media_id=manifest.media_id,
        revision=manifest.revision,
        state=manifest.state,
        original_width=manifest.original_width,
        original_height=manifest.original_height,
        original_mime_type=manifest.original_mime_type,
        original_byte_size=manifest.original_byte_size,
        variants=manifest.variants,
        failure_code=manifest.failure_code,
    )


def _temporary_lookup(
    *,
    source_key: str,
    size: int,
    mtime_ns: int,
    state: DerivativeState,
) -> ImageDerivativeLookup:
    revision = _pending_revision(source_key, size, mtime_ns)
    return ImageDerivativeLookup(
        source_key=source_key,
        media_id=revision,
        revision=revision,
        state=state,
        original_byte_size=size,
    )


def _pending_manifest(
    *,
    source_key: str,
    relative_path: str,
    size: int,
    mtime_ns: int,
) -> ImageDerivativeManifest:
    revision = _pending_revision(source_key, size, mtime_ns)
    return ImageDerivativeManifest(
        source_key=source_key,
        media_id=revision,
        revision=revision,
        source_relative_path=relative_path,
        source_size=size,
        source_mtime_ns=mtime_ns,
        original_byte_size=size,
        state="pending",
        generated_at=time.time(),
    )


def _failed_manifest(
    pending: ImageDerivativeManifest,
    failure_code: str,
) -> ImageDerivativeManifest:
    return pending.model_copy(
        update={
            "state": "failed",
            "generated_at": time.time(),
            "failure_code": failure_code,
            "variants": (),
        }
    )


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _image_mode(image: Image.Image) -> str:
    if "A" in image.getbands() or "transparency" in image.info:
        return "RGBA"
    return "RGB"


def _write_webp(
    image: Image.Image,
    destination: Path,
    *,
    icc_profile: bytes | None,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        save_options: dict[str, object] = {
            "format": "WEBP",
            "quality": _positive_env_int("ENMOTION_DERIVATIVE_WEBP_QUALITY", 78, maximum=95),
            "method": 4,
        }
        if icc_profile:
            save_options["icc_profile"] = icc_profile
        image.save(temporary, **save_options)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        size = temporary.stat().st_size
        os.replace(temporary, destination)
        return size
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _generate_locked(
    *,
    output_root: Path,
    source: Path,
    relative_path: str,
    source_key: str,
    expected_size: int,
    expected_mtime_ns: int,
) -> ImageDerivativeManifest:
    manifest_path, _lock_path, source_derivatives = _paths(output_root, source_key)
    pending = _pending_manifest(
        source_key=source_key,
        relative_path=relative_path,
        size=expected_size,
        mtime_ns=expected_mtime_ns,
    )
    try:
        if expected_size > _source_limit_bytes():
            raise ValueError("source-too-large")
        content_hash = _source_digest(source)
        before_open = source.stat()
        if before_open.st_size != expected_size or before_open.st_mtime_ns != expected_mtime_ns:
            raise RuntimeError("source-changed")
        with Image.open(source) as opened:
            if opened.width * opened.height > MAX_SOURCE_PIXELS:
                raise ValueError("source-too-large")
            original_mime = Image.MIME.get(opened.format or "") or None
            image = ImageOps.exif_transpose(opened)
            image.load()
            icc_profile = image.info.get("icc_profile")
            if not isinstance(icc_profile, bytes):
                icc_profile = None
            converted = image.convert(_image_mode(image))
            original_width, original_height = converted.size

        revision_root = source_derivatives / content_hash
        variants: list[ImageDerivativeVariant] = []
        emitted_widths: set[int] = set()
        for requested_width in DERIVATIVE_WIDTHS:
            target_width = min(requested_width, original_width)
            if target_width in emitted_widths:
                continue
            emitted_widths.add(target_width)
            target_height = max(
                1,
                round(original_height * target_width / original_width),
            )
            resized = (
                converted.copy()
                if (target_width, target_height) == converted.size
                else converted.resize(
                    (target_width, target_height),
                    Image.Resampling.LANCZOS,
                    reducing_gap=3.0,
                )
            )
            destination = revision_root / f"w{target_width}.webp"
            byte_size = _write_webp(
                resized,
                destination,
                icc_profile=icc_profile,
            )
            variants.append(
                ImageDerivativeVariant(
                    url=destination.relative_to(output_root).as_posix(),
                    width=target_width,
                    height=target_height,
                    byte_size=byte_size,
                )
            )
            resized.close()
        converted.close()
        after = source.stat()
        if after.st_size != expected_size or after.st_mtime_ns != expected_mtime_ns:
            shutil.rmtree(revision_root, ignore_errors=True)
            raise RuntimeError("source-changed")
        ready = ImageDerivativeManifest(
            source_key=source_key,
            media_id=content_hash,
            revision=content_hash,
            source_relative_path=relative_path,
            source_size=expected_size,
            source_mtime_ns=expected_mtime_ns,
            original_width=original_width,
            original_height=original_height,
            original_mime_type=original_mime,
            original_byte_size=expected_size,
            state="ready",
            generated_at=time.time(),
            variants=tuple(sorted(variants, key=lambda item: item.width)),
        )
        _atomic_json(manifest_path, ready.model_dump(mode="json"))
        for child in source_derivatives.iterdir():
            if child.is_dir() and child.name != content_hash:
                shutil.rmtree(child, ignore_errors=True)
        return ready
    except UnidentifiedImageError:
        failure = _failed_manifest(pending, "unsupported-image")
    except (Image.DecompressionBombError, ValueError) as exc:
        failure = _failed_manifest(
            pending,
            "source-too-large" if str(exc) == "source-too-large" else "decode-failed",
        )
    except RuntimeError as exc:
        failure = _failed_manifest(
            pending,
            "source-changed" if str(exc) == "source-changed" else "generation-failed",
        )
    except (OSError, SyntaxError):
        failure = _failed_manifest(pending, "generation-failed")
    _atomic_json(manifest_path, failure.model_dump(mode="json"))
    return failure


def generate_image_derivatives(
    output_root: str | Path,
    reference: str,
    *,
    force: bool = False,
) -> ImageDerivativeLookup:
    """Synchronously ensure one derivative set, for CLI/backfill use."""

    resolved = _resolved_source(output_root, reference)
    if resolved is None:
        digest = _unavailable_digest(reference)
        return ImageDerivativeLookup(
            source_key=digest,
            media_id=digest,
            revision=digest,
            state="unavailable",
        )
    source, relative_path, source_key = resolved
    output = Path(output_root).expanduser().resolve()
    stat = source.stat()
    manifest_path, lock_path, _images = _paths(output, source_key)
    with interprocess_lock(lock_path):
        existing = _read_manifest(manifest_path)
        if (
            not force
            and _manifest_current(
                existing,
                output_root=output,
                source_key=source_key,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
            and existing is not None
            and existing.state == "ready"
        ):
            return _lookup_from_manifest(existing)
        pending = _pending_manifest(
            source_key=source_key,
            relative_path=relative_path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        _atomic_json(manifest_path, pending.model_dump(mode="json"))
        generated = _generate_locked(
            output_root=output,
            source=source,
            relative_path=relative_path,
            source_key=source_key,
            expected_size=stat.st_size,
            expected_mtime_ns=stat.st_mtime_ns,
        )
        return _lookup_from_manifest(generated)


class _DerivativeScheduler:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=_positive_env_int("ENMOTION_DERIVATIVE_WORKERS", 1, maximum=2),
            thread_name_prefix="enmotion-derivative",
        )
        self._maximum_pending = _positive_env_int("ENMOTION_DERIVATIVE_QUEUE_SIZE", 64, maximum=256)
        self._lock = threading.Lock()
        self._pending: set[tuple[str, str]] = set()

    def submit(self, output_root: Path, reference: str, source_key: str) -> bool:
        key = (str(output_root), source_key)
        with self._lock:
            if key in self._pending:
                return True
            if len(self._pending) >= self._maximum_pending:
                return False
            self._pending.add(key)

        def work() -> None:
            try:
                generate_image_derivatives(output_root, reference)
            except Exception:
                logger.exception(
                    "Image derivative generation failed source_key=%s",
                    source_key[:12],
                )
            finally:
                with self._lock:
                    self._pending.discard(key)

        self._executor.submit(work)
        return True


_SCHEDULER: _DerivativeScheduler | None = None
_SCHEDULER_LOCK = threading.Lock()


def _scheduler() -> _DerivativeScheduler:
    global _SCHEDULER
    with _SCHEDULER_LOCK:
        if _SCHEDULER is None:
            _SCHEDULER = _DerivativeScheduler()
        return _SCHEDULER


def resolve_image_derivatives(
    output_root: str | Path,
    reference: str,
    *,
    schedule: bool = True,
) -> ImageDerivativeLookup:
    """Return a current manifest or schedule one bounded background build."""

    resolved = _resolved_source(output_root, reference)
    if resolved is None:
        digest = _unavailable_digest(reference)
        return ImageDerivativeLookup(
            source_key=digest,
            media_id=digest,
            revision=digest,
            state="unavailable",
        )
    source, relative_path, source_key = resolved
    output = Path(output_root).expanduser().resolve()
    stat = source.stat()
    manifest_path, lock_path, _images = _paths(output, source_key)
    manifest = _read_manifest(manifest_path)
    if (
        _manifest_current(
            manifest,
            output_root=output,
            source_key=source_key,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        and manifest is not None
    ):
        age = max(0.0, time.time() - manifest.generated_at)
        if manifest.state == "ready":
            return _lookup_from_manifest(manifest)
        if manifest.state == "pending" and age < DERIVATIVE_PENDING_TTL_SECONDS:
            return _lookup_from_manifest(manifest)
        if manifest.state == "failed" and age < DERIVATIVE_FAILURE_RETRY_SECONDS:
            return _lookup_from_manifest(manifest)

    pending = _pending_manifest(
        source_key=source_key,
        relative_path=relative_path,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    if schedule:
        with interprocess_lock(lock_path):
            current = _read_manifest(manifest_path)
            current_age = (
                max(0.0, time.time() - current.generated_at) if current is not None else None
            )
            if (
                _manifest_current(
                    current,
                    output_root=output,
                    source_key=source_key,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
                and current is not None
            ):
                if current.state == "ready":
                    return _lookup_from_manifest(current)
                if (
                    current.state == "pending"
                    and current_age is not None
                    and current_age < DERIVATIVE_PENDING_TTL_SECONDS
                ):
                    return _lookup_from_manifest(current)
                if (
                    current.state == "failed"
                    and current_age is not None
                    and current_age < DERIVATIVE_FAILURE_RETRY_SECONDS
                ):
                    return _lookup_from_manifest(current)
            if _scheduler().submit(output, relative_path, source_key):
                _atomic_json(manifest_path, pending.model_dump(mode="json"))
    return _lookup_from_manifest(pending)


def backfill_referenced_image_derivatives(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> dict[str, int]:
    """Synchronously backfill a bounded set of referenced local images."""

    from ...utils.media_gc import (
        collect_workspace_media_paths,
        load_workspace_reference_values,
    )

    root = Path(output_root).expanduser().resolve()
    referenced: set[Path] = set()
    for value in load_workspace_reference_values(root):
        referenced.update(collect_workspace_media_paths(value, root))
    candidates = [
        path
        for path in sorted(referenced)
        if path.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES
        and DERIVATIVE_ROOT not in path.relative_to(root).parts
    ]
    unresolved: list[Path] = []
    ready_existing = 0
    deferred = 0
    for source in candidates:
        lookup = resolve_image_derivatives(
            root,
            source.relative_to(root).as_posix(),
            schedule=False,
        )
        if lookup.state == "ready":
            ready_existing += 1
        elif lookup.state == "failed":
            deferred += 1
        else:
            unresolved.append(source)

    ready = 0
    failed = 0
    selected = unresolved[: max(0, limit)]
    for source in selected:
        lookup = generate_image_derivatives(
            root,
            source.relative_to(root).as_posix(),
        )
        if lookup.state == "ready":
            ready += 1
        else:
            failed += 1
    return {
        "candidates": len(candidates),
        "processed": len(selected),
        "ready": ready,
        "failed": failed,
        "ready_existing": ready_existing,
        "deferred": deferred,
        "remaining": max(0, len(unresolved) - len(selected)),
    }


def derivative_files_for_sources(
    output_root: str | Path,
    sources: Iterable[Path],
) -> set[Path]:
    """Return derivative files owned by original files being reclaimed."""

    root = Path(output_root).expanduser().resolve()
    result: set[Path] = set()
    for source in sources:
        try:
            relative = source.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        source_key = _source_key(relative)
        manifest, lock, images = _paths(root, source_key)
        for candidate in (manifest, lock):
            if candidate.is_file() and not candidate.is_symlink():
                result.add(candidate.resolve())
        if images.is_dir() and not images.is_symlink():
            for candidate in images.rglob("*"):
                if candidate.is_file() and not candidate.is_symlink():
                    result.add(candidate.resolve())
    return result
