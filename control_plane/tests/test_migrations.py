from __future__ import annotations

import base64
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]


def test_fresh_database_migrates_to_head_and_can_reapply_provider_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migrations.db"
    monkeypatch.setenv("ENMOTION_ENV", "test")
    monkeypatch.setenv("ENMOTION_DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("ENMOTION_SESSION_HMAC_SECRET", "migration-test-" + "x" * 40)
    monkeypatch.setenv("ENMOTION_PROVIDER_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv(
        "ENMOTION_PROVIDER_CONFIG_MASTER_KEY",
        base64.urlsafe_b64encode(b"m" * 32).decode("ascii"),
    )

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    command.downgrade(config, "0003_one_time_release_grants")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        database = inspect(engine)
        assert "provider_configurations" in database.get_table_names()
        provider_task_columns = {
            column["name"] for column in database.get_columns("provider_tasks")
        }
        assert "provider_config_version" in provider_task_columns
    finally:
        engine.dispose()
