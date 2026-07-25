from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from src.apps.server.cli import (
    DeploymentCompatibilityError,
    _settings_for_cli,
    check_backup_compatibility,
    check_database_compatibility,
    migrate,
)
from src.apps.server.database import Database
from src.apps.server.models import User
from src.apps.server.service import BootstrapAlreadyCompletedError, bootstrap_first_admin


def test_alembic_migration_and_one_time_admin_bootstrap(tmp_path):
    database_path = tmp_path / "server.db"
    url = f"sqlite:///{database_path}"
    migrate(url)

    database = Database(url)
    try:
        tables = set(inspect(database.engine).get_table_names())
        assert {
            "alembic_version",
            "users",
            "workspaces",
            "workspace_memberships",
            "login_sessions",
            "generation_jobs",
        } <= tables
        generation_job_columns = {
            column["name"] for column in inspect(database.engine).get_columns("generation_jobs")
        }
        assert "retry_context" in generation_job_columns
        assert {
            "progress_stage",
            "progress_is_estimated",
            "progress_steps",
            "provider_progress",
        } <= generation_job_columns

        with database.session() as db:
            user, workspace = bootstrap_first_admin(
                db,
                username="owner",
                password="a long bootstrap password",
            )
            assert user.role == "admin"
            assert workspace.owner_user_id == user.id

        with database.session() as db:
            assert db.scalar(select(User.username)) == "owner"
            try:
                bootstrap_first_admin(
                    db,
                    username="second",
                    password="another bootstrap password",
                )
            except BootstrapAlreadyCompletedError:
                pass
            else:
                raise AssertionError("bootstrap must be one-time")
    finally:
        database.dispose()


def test_cli_database_flag_can_supply_url_when_environment_omits_it(monkeypatch, tmp_path):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    monkeypatch.setenv("ENMOTION_SESSION_SECRET", "x" * 32)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    explicit_url = f"sqlite:///{tmp_path / 'explicit.db'}"
    assert _settings_for_cli(explicit_url).database_url == explicit_url


def test_deployment_compatibility_checks_versions_and_alembic_history(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'compatibility.db'}"

    assert check_database_compatibility(database_url) == ()
    with pytest.raises(DeploymentCompatibilityError, match="not at the bundled"):
        check_database_compatibility(database_url, require_head=True)

    migrate(database_url)
    assert check_database_compatibility(
        database_url,
        expected_revision="0004_job_activity",
        require_head=True,
    ) == ("0004_job_activity",)

    check_backup_compatibility(
        application_version="0.1.0",
        bundled_application_version="0.1.0",
        database_revision="0001_server_identity",
    )
    with pytest.raises(DeploymentCompatibilityError, match="newer than bundled"):
        check_backup_compatibility(
            application_version="0.2.0",
            bundled_application_version="0.1.0",
            database_revision="0002_generation_jobs",
        )
    with pytest.raises(DeploymentCompatibilityError, match="not in the bundled history"):
        check_backup_compatibility(
            application_version="0.1.0",
            bundled_application_version="0.1.0",
            database_revision="future_revision",
        )
