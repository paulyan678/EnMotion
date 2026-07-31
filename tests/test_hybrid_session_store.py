from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.apps.hybrid.session_store import (
    CredentialStoreError,
    LocalCredentialStore,
    default_credential_path,
)


def test_default_credential_path_uses_application_data(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENMOTION_DATA_DIR", str(tmp_path / "app-data"))

    assert default_credential_path() == (
        tmp_path / "app-data" / "session" / "control-plane-refresh-token"
    )


def test_local_credential_store_round_trip_uses_owner_only_permissions(
    tmp_path,
) -> None:
    path = tmp_path / "session" / "refresh-token"
    store = LocalCredentialStore(path)

    assert store.read() is None
    store.write("refresh-token-value")

    assert store.read() == "refresh-token-value"
    if os.name != "nt":
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600

    store.delete()
    assert store.read() is None


def test_local_credential_store_replaces_existing_file_atomically(tmp_path) -> None:
    path = tmp_path / "session" / "refresh-token"
    store = LocalCredentialStore(path)

    store.write("first-token")
    store.write("second-token")

    assert store.read() == "second-token"
    assert not list(path.parent.glob(".*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW semantics are Unix-only")
def test_local_credential_store_refuses_symlink_reads(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("should-not-be-read", encoding="utf-8")
    path = tmp_path / "session" / "refresh-token"
    path.parent.mkdir()
    path.symlink_to(target)
    store = LocalCredentialStore(path)

    with pytest.raises(CredentialStoreError):
        store.read()


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are Unix-only")
def test_local_credential_store_refuses_permissive_file_mode(tmp_path) -> None:
    path = tmp_path / "session" / "refresh-token"
    store = LocalCredentialStore(path)
    store.write("refresh-token")
    path.chmod(0o644)

    with pytest.raises(CredentialStoreError, match="0600"):
        store.read()


def test_local_credential_store_rejects_oversized_tokens(tmp_path) -> None:
    store = LocalCredentialStore(tmp_path / "session" / "refresh-token")

    with pytest.raises(CredentialStoreError, match="unexpectedly large"):
        store.write("x" * (16 * 1024 + 1))
