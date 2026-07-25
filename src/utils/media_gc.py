"""Conservative reclamation of workspace-local media after record deletion."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .media_security import UnsafeMediaReferenceError, resolve_workspace_media_path

logger = logging.getLogger(__name__)

MEDIA_SUFFIXES = {
    ".aac",
    ".aif",
    ".aiff",
    ".avi",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".png",
    ".wav",
    ".webm",
    ".webp",
    ".wma",
}
PROTECTED_MEDIA_PREFIXES = {"presets"}


def _plain_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def collect_workspace_media_paths(value: Any, output_root: str | Path) -> set[Path]:
    """Collect existing local media paths referenced by nested application data."""

    root = Path(output_root).expanduser().resolve()
    found: set[Path] = set()

    def visit(candidate: Any) -> None:
        candidate = _plain_value(candidate)
        if isinstance(candidate, Mapping):
            for nested in candidate.values():
                visit(nested)
            return
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
            for nested in candidate:
                visit(nested)
            return
        if not isinstance(candidate, str):
            return
        raw = candidate.strip()
        if not raw or raw.startswith(("http://", "https://", "data:")):
            return
        path_part = urlparse(raw).path
        if Path(path_part).suffix.lower() not in MEDIA_SUFFIXES:
            return
        try:
            resolved = Path(resolve_workspace_media_path(root, path_part))
        except UnsafeMediaReferenceError:
            return
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            return
        if relative.parts and relative.parts[0] in PROTECTED_MEDIA_PREFIXES:
            return
        if resolved.is_file():
            found.add(resolved)

    visit(value)
    return found


def reclaim_unreferenced_workspace_media(
    *,
    deleted_value: Any,
    remaining_values: Sequence[Any],
    output_root: str | Path,
    delete_callback: Callable[[set[Path]], None] | None = None,
) -> list[str]:
    """Delete media referenced by deleted data and nowhere in remaining data.

    The collector considers only known media suffixes that resolve beneath the
    current workspace. It never follows a reference outside that root and
    deletion failures are logged without corrupting the already-committed JSON
    mutation.
    """

    candidates = collect_workspace_media_paths(deleted_value, output_root)
    retained: set[Path] = set()
    for value in remaining_values:
        retained.update(collect_workspace_media_paths(value, output_root))

    deletable = candidates - retained
    # Derivatives are restartable cache files keyed to one original path. They
    # are not embedded in authoritative project JSON, so explicitly reclaim
    # them with an original that became unreachable instead of leaking cache
    # storage or making quota accounting drift upward.
    try:
        from ..apps.web_runtime.media_derivatives import (
            derivative_files_for_sources,
        )

        deletable.update(derivative_files_for_sources(output_root, deletable))
    except Exception as exc:
        # Original-media reclamation must stay conservative if a cache
        # manifest is malformed. Leaving regenerable bytes behind is safer
        # than turning a successful metadata mutation into data loss.
        logger.warning("Could not enumerate image derivatives for reclamation: %s", exc)
    if delete_callback is not None:
        delete_callback(deletable)
        return [str(path) for path in sorted(deletable)]

    reclaimed: list[str] = []
    for path in sorted(deletable):
        try:
            path.unlink()
            reclaimed.append(str(path))
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not reclaim workspace media %s: %s", path, exc)
    return reclaimed


def load_workspace_reference_values(output_root: str | Path) -> list[Any]:
    """Read every authoritative workspace JSON record used by media GC.

    Reading from disk after a persisted mutation avoids stale registry state
    and, importantly, always includes both Playground history and templates.
    A malformed authoritative file is treated conservatively: reclamation is
    skipped rather than risking deletion of media that may still be referenced.
    """

    root = Path(output_root).expanduser().resolve()
    names = (
        "projects.json",
        "series.json",
        "library_assets.json",
        "playground_history.json",
        "playground_templates.json",
    )
    values: list[Any] = []
    for name in names:
        path = root / name
        if not path.exists():
            continue
        try:
            values.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot safely scan workspace references in {name}: {exc}") from exc
    return values
