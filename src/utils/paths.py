"""Stable, product-specific filesystem locations for EnMotion."""

from __future__ import annotations

import ctypes
import os
import re
import sys
from pathlib import Path
from uuid import UUID

_SAFE_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _expanded_env(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def app_data_dir() -> Path:
    """Return EnMotion's stable settings/cache root, never the install directory."""

    override = _expanded_env("ENMOTION_DATA_DIR")
    if override is not None:
        return override.resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "EnMotion").resolve()
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return (base / "EnMotion").resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return (base / "EnMotion").resolve()


def _windows_documents_dir() -> Path:
    """Resolve the Windows Documents Known Folder, including redirection."""

    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_uuid(cls, value: str) -> "GUID":
            parsed = UUID(value)
            data = parsed.bytes_le
            return cls.from_buffer_copy(data)

    folder_id = GUID.from_uuid("FDD39AD0-238F-46AF-ADB4-6C85480369C7")
    raw_path = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
        ctypes.byref(folder_id),
        0,
        None,
        ctypes.byref(raw_path),
    )
    if result != 0:
        raise OSError(result, "Unable to resolve the Windows Documents folder")
    try:
        return Path(raw_path.value).resolve()
    finally:
        ctypes.windll.ole32.CoTaskMemFree(raw_path)  # type: ignore[attr-defined]


def documents_dir() -> Path:
    """Return the OS-resolved Documents folder."""

    override = _expanded_env("ENMOTION_DOCUMENTS_DIR")
    if override is not None:
        return override.resolve()
    if sys.platform == "win32":
        try:
            return _windows_documents_dir()
        except (AttributeError, OSError, ValueError):
            # This fallback is for stripped-down Windows test environments.
            return (Path.home() / "Documents").resolve()
    return (Path.home() / "Documents").resolve()


def output_root() -> Path:
    """Return the user-visible root for all generated EnMotion media."""

    override = _expanded_env("ENMOTION_OUTPUT_DIR")
    return (
        override.resolve()
        if override is not None
        else (documents_dir() / "enmotion-output").resolve()
    )


def accounts_root() -> Path:
    return (output_root() / "accounts").resolve()


def account_output_root(account_id: str) -> Path:
    """Return one contained account output root using a stable server UUID."""

    normalized = str(account_id or "").strip()
    if not _SAFE_ACCOUNT_ID.fullmatch(normalized):
        raise ValueError("Invalid EnMotion account id")
    root = accounts_root()
    candidate = (root / normalized / "output").resolve()
    if root not in candidate.parents:
        raise ValueError("Account output path escapes the EnMotion output root")
    return candidate


def ensure_runtime_directories() -> None:
    app_data_dir().mkdir(parents=True, exist_ok=True)
    accounts_root().mkdir(parents=True, exist_ok=True)
