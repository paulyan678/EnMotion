from __future__ import annotations

import argparse
import base64
import os
import secrets
import stat
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
ENV_FILE = LOCAL / "control.env"
PASSWORD_FILE = LOCAL / "admin-password"
DATABASE_FILE = LOCAL / "control.db"


def _private_write(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _ensure_provider_config_master_key(path: Path) -> None:
    current = path.read_text(encoding="utf-8")
    if any(
        line.startswith("ENMOTION_PROVIDER_CONFIG_MASTER_KEY=")
        for line in current.splitlines()
    ):
        return

    provider_config_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    prefix = "" if not current or current.endswith("\n") else "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{prefix}ENMOTION_PROVIDER_CONFIG_MASTER_KEY={provider_config_key}\n")


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Create an isolated localhost control plane")
    parser.add_argument("--port", type=int, default=18787)
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    os.chmod(LOCAL, 0o700)
    if not PASSWORD_FILE.exists():
        _private_write(PASSWORD_FILE, secrets.token_urlsafe(24) + "\n")
    if not ENV_FILE.exists():
        secret = secrets.token_urlsafe(48)
        provider_config_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        database_url = f"sqlite:///{DATABASE_FILE}"
        env = "\n".join(
            [
                "ENMOTION_ENV=development",
                f"ENMOTION_DATABASE_URL={database_url}",
                f"ENMOTION_SESSION_HMAC_SECRET={secret}",
                "ENMOTION_COOKIE_SECURE=false",
                "ENMOTION_PROVIDER_BASE_URL=https://api.example.invalid/v1",
                "ENMOTION_PROVIDER_CREDENTIALS_JSON={}",
                f"ENMOTION_PROVIDER_CONFIG_MASTER_KEY={provider_config_key}",
                f"ENMOTION_BOOTSTRAP_PASSWORD_FILE={PASSWORD_FILE}",
                "",
            ]
        )
        _private_write(ENV_FILE, env)
    if stat.S_IMODE(ENV_FILE.stat().st_mode) != 0o600:
        raise RuntimeError("local environment file permissions must be 0600")
    if stat.S_IMODE(PASSWORD_FILE.stat().st_mode) != 0o600:
        raise RuntimeError("local password file permissions must be 0600")
    _ensure_provider_config_master_key(ENV_FILE)

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value

    alembic = Config(str(ROOT / "alembic.ini"))
    alembic.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(alembic, "head")

    from app.config import Settings
    from app.database import Database, begin_immediate
    from app.models import User
    from app.security import hash_password, normalize_username

    settings = Settings.from_env()
    db = Database(settings.database_url)
    with db.session() as session:
        begin_immediate(session)
        existing = session.scalar(select(User).where(User.role == "admin"))
        if existing is None:
            password = PASSWORD_FILE.read_text(encoding="utf-8").rstrip("\r\n")
            admin = User(
                username=args.username,
                normalized_username=normalize_username(args.username),
                password_hash=hash_password(password),
                role="admin",
                available_credits=0,
            )
            session.add(admin)
    db.engine.dispose()
    for database_artifact in (
        DATABASE_FILE,
        DATABASE_FILE.with_name(DATABASE_FILE.name + "-wal"),
        DATABASE_FILE.with_name(DATABASE_FILE.name + "-shm"),
    ):
        if database_artifact.exists():
            os.chmod(database_artifact, 0o600)
    print(f"Local control plane prepared at http://127.0.0.1:{args.port}/admin/")
    print(f"Administrator username: {args.username}")
    print(f"Administrator password file: {PASSWORD_FILE}")
    print(
        f"Run with: .venv/bin/uvicorn app.main:app --env-file {ENV_FILE} "
        f"--host 127.0.0.1 --port {args.port} --loop asyncio"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Local setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
