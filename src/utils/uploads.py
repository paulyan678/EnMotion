"""Bounded, type-aware helpers for public file uploads."""

import os
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import FrozenSet, Tuple

from fastapi import HTTPException, UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

UPLOAD_CHUNK_BYTES = 64 * 1024

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aif",
        ".aiff",
        ".flac",
        ".m4a",
        ".mp3",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
        ".wma",
    }
)
TEXT_IMPORT_EXTENSIONS = frozenset({".md", ".pdf", ".txt"})


@dataclass(frozen=True)
class UploadPolicy:
    max_bytes: int
    extensions: FrozenSet[str]
    content_types: FrozenSet[str] = frozenset()
    content_type_prefixes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SavedUpload:
    path: str
    filename: str
    size: int


IMAGE_UPLOAD_POLICY = UploadPolicy(
    max_bytes=10 * 1024 * 1024,
    extensions=IMAGE_EXTENSIONS,
    content_type_prefixes=("image/",),
)

AUDIO_UPLOAD_POLICY = UploadPolicy(
    max_bytes=25 * 1024 * 1024,
    extensions=AUDIO_EXTENSIONS,
    content_type_prefixes=("audio/",),
)

GENERIC_MEDIA_UPLOAD_POLICY = UploadPolicy(
    max_bytes=10 * 1024 * 1024,
    extensions=IMAGE_EXTENSIONS | AUDIO_EXTENSIONS,
    content_type_prefixes=("image/", "audio/"),
)

TEXT_IMPORT_UPLOAD_POLICY = UploadPolicy(
    max_bytes=5 * 1024 * 1024,
    extensions=TEXT_IMPORT_EXTENSIONS,
    content_types=frozenset({"application/pdf", "text/markdown", "text/plain"}),
)


def extract_import_text(content: bytes, filename: str) -> str:
    """Extract importable text from an allowlisted text or PDF document."""
    extension = os.path.splitext(filename)[1].lower()
    if extension != ".pdf":
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text files must use UTF-8 encoding") from exc

    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:
                raise ValueError("Password-protected PDF files are not supported") from exc
            if not decrypted:
                raise ValueError("Password-protected PDF files are not supported")

        pages = [page.extract_text() or "" for page in reader.pages]
    except ValueError:
        raise
    except (PdfReadError, OSError, EOFError) as exc:
        raise ValueError("The PDF file is invalid or unreadable") from exc

    text = "\n\n".join(page.strip() for page in pages if page.strip())
    if not text:
        raise ValueError(
            "The PDF contains no extractable text. Use a text-based PDF or run OCR first."
        )
    return text


def validate_upload_type(file: UploadFile, policy: UploadPolicy) -> str:
    """Validate filename extension and declared MIME type, returning the extension."""
    original_name = (file.filename or "").strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="No filename provided")

    extension = os.path.splitext(original_name)[1].lower()
    if extension not in policy.extensions:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type {extension!r}. " f"Allowed: {sorted(policy.extensions)}"
            ),
        )

    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    # Browsers and API clients commonly use application/octet-stream for
    # otherwise valid .md/.webp/etc. uploads. In that case the allowlisted
    # extension remains authoritative.
    if content_type and content_type != "application/octet-stream":
        content_type_allowed = content_type in policy.content_types or any(
            content_type.startswith(prefix) for prefix in policy.content_type_prefixes
        )
        if not content_type_allowed:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported media type {content_type!r}",
            )

    return extension


def _destination(directory: str, extension: str, filename_prefix: str) -> tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    filename = f"{filename_prefix}{uuid.uuid4()}{extension}"
    return os.path.join(directory, filename), filename


def _remove_partial(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        # Preserve the original upload error; cleanup failure is secondary.
        pass


def _too_large(policy: UploadPolicy) -> HTTPException:
    limit_mb = policy.max_bytes / (1024 * 1024)
    return HTTPException(status_code=413, detail=f"File exceeds {limit_mb:g} MB limit")


def save_upload_file(
    file: UploadFile,
    directory: str,
    policy: UploadPolicy,
    *,
    filename_prefix: str = "",
) -> SavedUpload:
    """Stream a synchronous UploadFile to disk and remove partial writes on failure."""
    extension = validate_upload_type(file, policy)
    path, filename = _destination(directory, extension, filename_prefix)
    size = 0
    try:
        with open(path, "wb") as destination:
            while chunk := file.file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > policy.max_bytes:
                    raise _too_large(policy)
                destination.write(chunk)
    except Exception:
        _remove_partial(path)
        raise
    return SavedUpload(path=path, filename=filename, size=size)


async def save_upload_file_async(
    file: UploadFile,
    directory: str,
    policy: UploadPolicy,
    *,
    filename_prefix: str = "",
) -> SavedUpload:
    """Stream an asynchronous UploadFile to disk and remove partial writes on failure."""
    extension = validate_upload_type(file, policy)
    path, filename = _destination(directory, extension, filename_prefix)
    size = 0
    try:
        with open(path, "wb") as destination:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > policy.max_bytes:
                    raise _too_large(policy)
                destination.write(chunk)
    except Exception:
        _remove_partial(path)
        raise
    return SavedUpload(path=path, filename=filename, size=size)


async def read_upload_bytes(file: UploadFile, policy: UploadPolicy) -> bytes:
    """Read an upload into memory without ever exceeding the policy's byte cap."""
    validate_upload_type(file, policy)
    chunks = []
    size = 0
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > policy.max_bytes:
            raise _too_large(policy)
        chunks.append(chunk)
    return b"".join(chunks)
