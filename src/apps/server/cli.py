"""Operator commands for database migration and initial account bootstrap."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import sys

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from .config import ServerSettings
from .database import Database
from .service import BootstrapAlreadyCompletedError, bootstrap_first_admin


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent / "migrations"),
    )
    # Alembic stores this in ConfigParser; percent signs must be escaped.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def migrate(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


class DeploymentCompatibilityError(RuntimeError):
    """The installed application cannot safely consume the supplied state."""


def _script_directory() -> ScriptDirectory:
    # Script discovery does not connect to this placeholder URL.
    return ScriptDirectory.from_config(alembic_config("sqlite://"))


def _bundled_revision_sets() -> tuple[set[str], set[str]]:
    scripts = _script_directory()
    heads = set(scripts.get_heads())
    supported: set[str] = set()
    pending = list(scripts.get_revisions(tuple(heads)))
    while pending:
        revision = pending.pop()
        if revision.revision in supported:
            continue
        supported.add(revision.revision)
        down = revision.down_revision
        if down is None:
            continue
        parents = (down,) if isinstance(down, str) else tuple(down)
        pending.extend(scripts.get_revisions(parents))
    return heads, supported


def _release_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", value)
    if match is None:
        raise DeploymentCompatibilityError(
            f"invalid application release version in state manifest: {value!r}"
        )
    return tuple(int(part) for part in match.groups())


def check_backup_compatibility(
    *,
    application_version: str,
    bundled_application_version: str,
    database_revision: str,
) -> None:
    """Reject state made by a newer app or an unrelated Alembic history."""

    if _release_version(application_version) > _release_version(
        bundled_application_version
    ):
        raise DeploymentCompatibilityError(
            "backup application version "
            f"{application_version} is newer than bundled version "
            f"{bundled_application_version}"
        )
    _, supported = _bundled_revision_sets()
    if database_revision not in supported:
        raise DeploymentCompatibilityError(
            f"backup Alembic revision {database_revision!r} is not in the bundled history"
        )


def check_database_compatibility(
    database_url: str,
    *,
    expected_revision: str | None = None,
    require_head: bool = False,
) -> tuple[str, ...]:
    """Validate live database revisions before and after applying migrations."""

    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            current = tuple(MigrationContext.configure(connection).get_current_heads())
    finally:
        database.dispose()

    heads, supported = _bundled_revision_sets()
    current_set = set(current)
    if expected_revision is not None and current_set != {expected_revision}:
        raise DeploymentCompatibilityError(
            "restored database revision does not match its manifest "
            f"(expected {expected_revision!r}, found {sorted(current_set)!r})"
        )
    unknown = current_set - supported
    if unknown:
        raise DeploymentCompatibilityError(
            f"database contains revisions not supported by this app: {sorted(unknown)!r}"
        )
    if require_head and current_set != heads:
        raise DeploymentCompatibilityError(
            f"database is not at the bundled Alembic head {sorted(heads)!r}; "
            f"found {sorted(current_set)!r}"
        )
    return current


def _settings_for_cli(database_url: str | None) -> ServerSettings:
    environment = dict(os.environ)
    if database_url is not None:
        environment["DATABASE_URL"] = database_url
    return ServerSettings.from_env(environment)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="enmotion-server")
    parser.add_argument("--database-url", help="Override DATABASE_URL")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_parser = commands.add_parser("migrate", help="Apply database migrations")
    migrate_parser.add_argument("--revision", default="head")

    backup_check = commands.add_parser(
        "check-backup-compatibility",
        help="Validate an application's version and Alembic revision before restore",
    )
    backup_check.add_argument("--application-version", required=True)
    backup_check.add_argument("--bundled-application-version", required=True)
    backup_check.add_argument("--database-revision", required=True)

    database_check = commands.add_parser(
        "check-database-compatibility",
        help="Validate the live database against the bundled Alembic history",
    )
    database_check.add_argument("--expected-revision")
    database_check.add_argument("--require-head", action="store_true")

    bootstrap = commands.add_parser(
        "bootstrap-admin", help="Create the first admin and personal workspace"
    )
    bootstrap.add_argument("--username")
    bootstrap.add_argument("--password")
    bootstrap.add_argument("--workspace-name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _settings_for_cli(args.database_url)
    if args.command == "migrate":
        migrate(settings.database_url, args.revision)
        print(f"Database migrated to {args.revision}")
        return 0
    if args.command == "check-backup-compatibility":
        try:
            check_backup_compatibility(
                application_version=args.application_version,
                bundled_application_version=args.bundled_application_version,
                database_revision=args.database_revision,
            )
        except DeploymentCompatibilityError as exc:
            print(f"Compatibility check failed: {exc}", file=sys.stderr)
            return 1
        print("Backup application and Alembic revisions are compatible")
        return 0
    if args.command == "check-database-compatibility":
        try:
            revisions = check_database_compatibility(
                settings.database_url,
                expected_revision=args.expected_revision,
                require_head=args.require_head,
            )
        except DeploymentCompatibilityError as exc:
            print(f"Compatibility check failed: {exc}", file=sys.stderr)
            return 1
        rendered = ",".join(revisions) if revisions else "unversioned"
        print(f"Database revision is compatible: {rendered}")
        return 0

    username = args.username or os.getenv("ENMOTION_BOOTSTRAP_ADMIN_USERNAME")
    password = args.password or os.getenv("ENMOTION_BOOTSTRAP_ADMIN_PASSWORD")
    if not username:
        username = input("Admin username: ").strip()
    if not password:
        password = getpass.getpass("Admin password: ")

    database = Database(settings.database_url)
    try:
        with database.session() as db:
            user, workspace = bootstrap_first_admin(
                db,
                username=username,
                password=password,
                workspace_name=args.workspace_name,
            )
        print(f"Created admin {user.username!r} with workspace {workspace.id}")
    except BootstrapAlreadyCompletedError as exc:
        print(str(exc))
        return 2
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
