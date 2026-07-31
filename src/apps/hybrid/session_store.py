"""Owner-only refresh-token persistence without Keychain or browser storage."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from ...utils.paths import app_data_dir

_MAX_TOKEN_BYTES = 16 * 1024
_CREDENTIAL_DIRECTORY = "session"
_CREDENTIAL_FILENAME = "control-plane-refresh-token"


class CredentialStoreError(RuntimeError):
    """The local credential path is unsafe or cannot be read atomically."""


def default_credential_path() -> Path:
    return app_data_dir() / _CREDENTIAL_DIRECTORY / _CREDENTIAL_FILENAME


def _owner_is_current_user(status: os.stat_result) -> bool:
    return not hasattr(os, "getuid") or status.st_uid == os.getuid()


class LocalCredentialStore:
    """Store one refresh token without browser storage or OS credential APIs."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_credential_path()

    def _ensure_directory(self) -> Path:
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = os.lstat(directory)
        if (
            not stat.S_ISDIR(status.st_mode)
            or stat.S_ISLNK(status.st_mode)
            or not _owner_is_current_user(status)
        ):
            raise CredentialStoreError("credential directory is not owner-controlled")
        if os.name != "nt":
            os.chmod(directory, 0o700)
        return directory

    def read(self) -> str | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CredentialStoreError("cannot open the credential file safely") from exc
        try:
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or not _owner_is_current_user(status):
                raise CredentialStoreError("credential file is not owner-controlled")
            if os.name != "nt" and stat.S_IMODE(status.st_mode) != 0o600:
                raise CredentialStoreError("credential file permissions must be 0600")
            payload = bytearray()
            while len(payload) <= _MAX_TOKEN_BYTES:
                chunk = os.read(descriptor, min(4096, _MAX_TOKEN_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > _MAX_TOKEN_BYTES:
                raise CredentialStoreError("credential file is unexpectedly large")
        finally:
            os.close(descriptor)
        try:
            token = bytes(payload).decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise CredentialStoreError("credential file is not valid UTF-8") from exc
        return token or None

    def write(self, value: str) -> None:
        normalized = value.strip()
        payload = normalized.encode("utf-8")
        if not normalized:
            raise CredentialStoreError("refresh token cannot be empty")
        if len(payload) > _MAX_TOKEN_BYTES:
            raise CredentialStoreError("refresh token is unexpectedly large")

        directory = self._ensure_directory()
        temporary = directory / (f".{self.path.name}.{secrets.token_hex(12)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
                directory_descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def delete(self) -> None:
        try:
            status = os.lstat(self.path)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(status.st_mode):
            raise CredentialStoreError("credential path unexpectedly contains a directory")
        self.path.unlink()
