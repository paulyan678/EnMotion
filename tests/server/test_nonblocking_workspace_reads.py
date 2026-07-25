from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from src.apps.server.context import RequestContext, get_request_context
from src.apps.server.middleware import ServerAuthMiddleware
from src.apps.web_runtime.file_lock import (
    acquire_lock_file,
    interprocess_lock,
    release_lock_file,
)

from .conftest import ADMIN_PASSWORD


def test_authenticated_read_does_not_wait_for_busy_workspace_writer(
    app, admin, monkeypatch, tmp_path
):
    """A provider job may hold the writer lock for many minutes.

    GET routes must still be able to load a complete atomic snapshot, including
    when their storage layer enters the normal inter-process lock helper.
    """

    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))

    @app.get("/protected/atomic-snapshot")
    def atomic_snapshot(
        context: RequestContext = Depends(get_request_context),
    ):
        lock_path = ServerAuthMiddleware._workspace_lock_path(context.workspace_id)
        with interprocess_lock(lock_path):
            return {"workspace_id": context.workspace_id}

    workspace_id = admin[1].id
    lock_path = workspace_root / workspace_id / ".workspace.lock"
    descriptor, _canonical = acquire_lock_file(lock_path)
    try:
        with TestClient(app, base_url="http://testserver") as client:
            login = client.post(
                "/auth/login",
                json={"username": "admin", "password": ADMIN_PASSWORD},
            )
            assert login.status_code == 200

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(client.get, "/protected/atomic-snapshot")
                try:
                    response = future.result(timeout=1.0)
                except TimeoutError:  # pragma: no cover - regression guard
                    pytest.fail("authenticated GET blocked on the workspace writer")
    finally:
        release_lock_file(descriptor)

    assert response.status_code == 200
    assert response.json() == {"workspace_id": workspace_id}
    assert response.headers["x-enmotion-workspace-id"] == workspace_id
