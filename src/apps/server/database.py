"""SQLAlchemy engine/session management for server mode."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import ServerSettings
from .models import Base


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        engine_kwargs: dict[str, object] = {
            "pool_pre_ping": True,
            "echo": echo,
        }
        if url in {"sqlite://", "sqlite:///:memory:"}:
            engine_kwargs.update(
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        elif url.startswith("sqlite:"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}

        self.engine: Engine = create_engine(url, **engine_kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

        if url.startswith("sqlite:"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)

    def session(self) -> Session:
        return self.session_factory()

    def create_schema_for_tests(self) -> None:
        """Create tables without Alembic; intended only for isolated test databases."""

        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache(maxsize=1)
def get_database() -> Database:
    settings = ServerSettings.from_env()
    return Database(settings.database_url)


def get_db() -> Generator[Session, None, None]:
    database = get_database()
    with database.session() as db:
        try:
            yield db
        finally:
            db.close()


def clear_database_cache() -> None:
    """Dispose/reset the lazy process database (primarily for tests)."""

    if get_database.cache_info().currsize:
        get_database().dispose()
    get_database.cache_clear()
