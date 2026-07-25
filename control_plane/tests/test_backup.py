from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from contextlib import closing

import pytest

from app import backup


def test_encryption_failure_removes_plaintext_backup(tmp_path, monkeypatch) -> None:
    source = tmp_path / "control.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('private')")
        connection.commit()

    destination = tmp_path / "backups"
    monkeypatch.setenv("ENMOTION_DATABASE_URL", f"sqlite:///{source}")
    monkeypatch.setenv("ENMOTION_SESSION_HMAC_SECRET", "x" * 48)
    monkeypatch.setenv("ENMOTION_PROVIDER_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("ENMOTION_BACKUP_DIR", str(destination))
    monkeypatch.setenv("ENMOTION_BACKUP_AGE_RECIPIENT", "age1test-recipient")
    monkeypatch.delenv("ENMOTION_ALLOW_PLAINTEXT_BACKUPS", raising=False)
    monkeypatch.setattr(backup.shutil, "which", lambda _program: "/usr/bin/age")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def fail_encryption(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["age"])

    monkeypatch.setattr(backup.subprocess, "run", fail_encryption)

    with pytest.raises(subprocess.CalledProcessError):
        backup.main()

    assert not list(destination.glob("*.sqlite"))
    assert not list(destination.glob("*.age"))
    assert not list(tmp_path.glob("enmotion-control-*.sqlite"))
