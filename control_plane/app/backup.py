from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings


def _sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///") or database_url in {
        "sqlite://",
        "sqlite:///:memory:",
    }:
        raise RuntimeError("online backup currently supports file-backed SQLite only")
    return Path(database_url.removeprefix("sqlite:///")).expanduser().resolve()


def main() -> int:
    settings = Settings.from_env()
    source = _sqlite_path(settings.database_url)
    if not source.is_file():
        raise RuntimeError(f"database does not exist: {source}")
    destination_root = Path(
        os.getenv("ENMOTION_BACKUP_DIR", "/var/backups/enmotion-control")
    ).expanduser()
    destination_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination_root, 0o700)
    recipient = os.getenv("ENMOTION_BACKUP_AGE_RECIPIENT", "").strip()
    allow_plaintext = os.getenv("ENMOTION_ALLOW_PLAINTEXT_BACKUPS", "").lower() == "true"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    encrypted: Path | None = None
    if recipient:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="enmotion-control-",
            suffix=".sqlite",
        )
        os.close(descriptor)
        plaintext = Path(temporary_name)
    else:
        plaintext = destination_root / f"control-{stamp}.sqlite"

    try:
        with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as source_db:
            with closing(sqlite3.connect(plaintext)) as backup_db:
                source_db.backup(backup_db, pages=256)
                result = backup_db.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RuntimeError("backup integrity check failed")
        os.chmod(plaintext, 0o600)

        if recipient:
            age = shutil.which("age")
            if not age:
                raise RuntimeError(
                    "age is required when an encrypted backup recipient is configured"
                )
            encrypted = destination_root / f"control-{stamp}.sqlite.age"
            subprocess.run(
                [age, "--recipient", recipient, "--output", str(encrypted), str(plaintext)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            os.chmod(encrypted, 0o600)
            plaintext.unlink()
            final_path = encrypted
        elif allow_plaintext:
            final_path = plaintext
        else:
            raise RuntimeError(
                "ENMOTION_BACKUP_AGE_RECIPIENT is required unless plaintext backups are "
                "explicitly allowed"
            )
    except Exception:
        plaintext.unlink(missing_ok=True)
        if encrypted is not None:
            encrypted.unlink(missing_ok=True)
        raise

    retention = max(1, int(os.getenv("ENMOTION_BACKUP_RETENTION", "14")))
    backups = sorted(
        destination_root.glob("control-*.sqlite*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in backups[retention:]:
        old.unlink()
    print(final_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
