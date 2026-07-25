from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.apps.server.config import ServerSettings, get_server_settings
from src.apps.server.context import RequestContext, get_request_context
from src.apps.server.database import Database, get_db
from src.apps.server.middleware import ServerAuthMiddleware
from src.apps.server.rate_limit import InMemoryRateLimiter, login_rate_limiter
from src.apps.server.router import router
from src.apps.server.service import bootstrap_first_admin

TEST_SECRET = "test-only-session-secret-that-is-long-enough"
ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def database():
    value = Database("sqlite://")
    value.create_schema_for_tests()
    try:
        yield value
    finally:
        value.dispose()


@pytest.fixture
def settings():
    return ServerSettings(
        enabled=True,
        database_url="sqlite://",
        session_secret=TEST_SECRET,
        allowed_origins=("http://testserver", "http://localhost:3008"),
        login_attempts=50,
        login_account_attempts=100,
    )


@pytest.fixture
def admin(database):
    with database.session() as db:
        return bootstrap_first_admin(
            db,
            username="admin",
            password=ADMIN_PASSWORD,
            workspace_name="Admin workspace",
        )


@pytest.fixture
def app(database, settings, admin):
    application = FastAPI()

    @application.get("/health")
    def health():
        return {"ok": True}

    @application.get("/protected")
    def protected(context: RequestContext = Depends(get_request_context)):
        return {
            "user_id": context.actor.user_id,
            "workspace_id": context.workspace_id,
        }

    @application.post("/protected")
    def mutate(context: RequestContext = Depends(get_request_context)):
        return {"workspace_id": context.workspace_id}

    application.include_router(router)
    application.add_middleware(
        ServerAuthMiddleware,
        database=database,
        settings=settings,
    )

    def provide_db():
        with database.session() as db:
            yield db

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    return application


@pytest.fixture
def client(app):
    if isinstance(login_rate_limiter, InMemoryRateLimiter):
        login_rate_limiter.clear()
    with TestClient(app, base_url="http://testserver") as value:
        yield value


@pytest.fixture
def logged_in_admin(client):
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()
