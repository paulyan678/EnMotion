from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.apps.server import include_server_mode
from src.apps.server.cli import migrate
from src.apps.server.config import clear_server_settings_cache
from src.apps.server.context import RequestContext, get_request_context
from src.apps.server.database import clear_database_cache, get_database
from src.apps.server.rate_limit import InMemoryRateLimiter, login_rate_limiter
from src.apps.server.service import bootstrap_first_admin

from .conftest import ADMIN_PASSWORD, TEST_SECRET


def test_include_server_mode_wires_real_database_router_and_tenant_context(monkeypatch, tmp_path):
    database_url = f"sqlite:///{tmp_path / 'integrated.db'}"
    monkeypatch.delenv("ENMOTION_SERVER_MODE", raising=False)
    monkeypatch.setenv("ENMOTION_DEPLOYMENT_MODE", "server")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ENMOTION_SESSION_SECRET", TEST_SECRET)
    monkeypatch.setenv("ENMOTION_ALLOWED_ORIGINS", "http://testserver")
    clear_database_cache()
    clear_server_settings_cache()
    if isinstance(login_rate_limiter, InMemoryRateLimiter):
        login_rate_limiter.clear()
    migrate(database_url)

    app = FastAPI()
    assert include_server_mode(app) is True

    @app.get("/workspace-context")
    def workspace_context(context: RequestContext = Depends(get_request_context)):
        return {"workspace_id": context.workspace_id}

    database = get_database()
    with database.session() as db:
        _, workspace = bootstrap_first_admin(
            db,
            username="admin",
            password=ADMIN_PASSWORD,
        )

    try:
        with TestClient(app, base_url="http://testserver") as client:
            assert client.get("/workspace-context").status_code == 401
            login = client.post(
                "/auth/login",
                json={"username": "admin", "password": ADMIN_PASSWORD},
            )
            assert login.status_code == 200
            assert client.get("/workspace-context").json() == {"workspace_id": workspace.id}
    finally:
        clear_database_cache()
        clear_server_settings_cache()
