from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.apps.comic_gen.models import Script, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.server.config import ServerSettings
from src.apps.server.middleware import ServerAuthMiddleware
from src.apps.server.models import Workspace

from .conftest import ADMIN_PASSWORD


def _csrf_login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_declared_request_body_limit_rejects_before_route(client, settings):
    object.__setattr__(settings, "max_request_body_bytes", 32)
    response = client.post(
        "/auth/login",
        content=b"x" * 33,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "请求内容过大，请减少输入后重试。"


def test_chunked_request_body_limit_counts_every_asgi_message(database, settings, admin):
    object.__setattr__(settings, "max_request_body_bytes", 5)
    reached = False

    async def downstream(scope, receive, send):
        nonlocal reached
        reached = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = ServerAuthMiddleware(downstream, database=database, settings=settings)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    assert reached is False
    assert sent[0]["status"] == 413


def test_successful_over_quota_mutation_restores_metadata_and_new_files(
    database, settings, admin, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    with database.session() as session:
        session.scalar(select(Workspace).where(Workspace.id == workspace_id)).storage_quota_bytes = 64
        session.commit()

    application = FastAPI()

    @application.post("/mutate")
    def mutate():
        root = tmp_path / "workspaces" / workspace_id / "output"
        root.mkdir(parents=True, exist_ok=True)
        (root / "projects.json").write_text(json.dumps([{"title": "x" * 100}]))
        (root / "video").mkdir()
        (root / "video" / "new.mp4").write_bytes(b"v" * 100)
        return {"ok": True}

    from src.apps.server.router import router
    from src.apps.server.database import get_db
    from src.apps.server.config import get_server_settings

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(application, base_url="http://testserver") as test_client:
        response = test_client.post("/mutate", headers=_csrf_login(test_client))

    assert response.status_code == 507
    root = tmp_path / "workspaces" / workspace_id / "output"
    assert not (root / "projects.json").exists()
    assert not (root / "video" / "new.mp4").exists()
    assert response.json()["rolled_back_files"] == 1


def test_failed_mutation_does_not_leave_workspace_files(
    database, settings, admin, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    application = FastAPI()

    @application.post("/mutate")
    def mutate():
        root = tmp_path / "workspaces" / workspace_id / "output"
        root.mkdir(parents=True, exist_ok=True)
        (root / "projects.json").write_text("changed")
        (root / "new.png").write_bytes(b"new")
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "no"}, status_code=409)

    from src.apps.server.router import router
    from src.apps.server.database import get_db
    from src.apps.server.config import get_server_settings

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(application, base_url="http://testserver") as test_client:
        response = test_client.post("/mutate", headers=_csrf_login(test_client))

    assert response.status_code == 409
    root = tmp_path / "workspaces" / workspace_id / "output"
    assert not (root / "projects.json").exists()
    assert not (root / "new.png").exists()


def test_successful_metadata_delete_physically_removes_unreferenced_media(
    database, settings, admin, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    root = tmp_path / "workspaces" / workspace_id / "output"
    media_path = root / "storyboard" / "deleted.png"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"generated image")
    projects_path = root / "projects.json"
    projects_path.write_text(
        json.dumps(
            [
                {
                    "id": "project-1",
                    "frames": [{"id": "frame-1", "image_url": "storyboard/deleted.png"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    application = FastAPI()

    @application.delete("/remove-media-reference")
    def remove_media_reference():
        projects_path.write_text("[]", encoding="utf-8")
        return {"ok": True}

    from src.apps.server.config import get_server_settings
    from src.apps.server.database import get_db
    from src.apps.server.router import router

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(application, base_url="http://testserver") as test_client:
        response = test_client.delete(
            "/remove-media-reference", headers=_csrf_login(test_client)
        )

    assert response.status_code == 200
    assert json.loads(projects_path.read_text(encoding="utf-8")) == []
    assert not media_path.exists()
    assert not (root.parent / ".trash").exists()


def test_replacement_stages_old_media_before_quota_validation(
    database, settings, admin, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    root = tmp_path / "workspaces" / workspace_id / "output"
    old_media = root / "storyboard" / "old.png"
    new_media = root / "storyboard" / "new.png"
    old_media.parent.mkdir(parents=True)
    old_media.write_bytes(b"o" * 128)
    projects_path = root / "projects.json"
    original_projects = [{"image_url": "storyboard/old.png"}]
    projects_path.write_text(json.dumps(original_projects), encoding="utf-8")
    starting_usage = sum(
        path.stat().st_size for path in root.rglob("*") if path.is_file()
    )
    with database.session() as session:
        workspace = session.scalar(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace.storage_quota_bytes = starting_usage + 16
        session.commit()

    application = FastAPI()

    @application.post("/replace-media")
    def replace_media():
        new_media.write_bytes(b"n" * 128)
        projects_path.write_text(
            json.dumps([{"image_url": "storyboard/new.png"}]), encoding="utf-8"
        )
        return {"ok": True}

    from src.apps.server.config import get_server_settings
    from src.apps.server.database import get_db
    from src.apps.server.router import router

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(application, base_url="http://testserver") as test_client:
        response = test_client.post(
            "/replace-media", headers=_csrf_login(test_client)
        )

    assert response.status_code == 200
    assert not old_media.exists()
    assert new_media.read_bytes() == b"n" * 128
    assert json.loads(projects_path.read_text(encoding="utf-8")) == [
        {"image_url": "storyboard/new.png"}
    ]
    assert not (root.parent / ".trash").exists()


def test_over_quota_replacement_restores_staged_media_and_metadata(
    database, settings, admin, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    root = tmp_path / "workspaces" / workspace_id / "output"
    old_media = root / "storyboard" / "old.png"
    new_media = root / "storyboard" / "new.png"
    old_media.parent.mkdir(parents=True)
    old_media.write_bytes(b"old")
    projects_path = root / "projects.json"
    original_projects = [{"image_url": "storyboard/old.png"}]
    projects_path.write_text(json.dumps(original_projects), encoding="utf-8")
    starting_usage = sum(
        path.stat().st_size for path in root.rglob("*") if path.is_file()
    )
    with database.session() as session:
        workspace = session.scalar(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace.storage_quota_bytes = starting_usage + 1
        session.commit()

    application = FastAPI()

    @application.post("/replace-media")
    def replace_media():
        new_media.write_bytes(b"new file over quota")
        projects_path.write_text(
            json.dumps([{"image_url": "storyboard/new.png"}]), encoding="utf-8"
        )
        return {"ok": True}

    from src.apps.server.config import get_server_settings
    from src.apps.server.database import get_db
    from src.apps.server.router import router

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(application, base_url="http://testserver") as test_client:
        response = test_client.post(
            "/replace-media", headers=_csrf_login(test_client)
        )

    assert response.status_code == 507
    assert old_media.read_bytes() == b"old"
    assert not new_media.exists()
    assert json.loads(projects_path.read_text(encoding="utf-8")) == original_projects
    assert not (root.parent / ".trash").exists()


def test_staging_failure_rolls_back_metadata_and_keeps_original_media(
    database, settings, admin, monkeypatch, tmp_path
):
    from src.apps.server import workspace_storage

    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    root = tmp_path / "workspaces" / workspace_id / "output"
    media_path = root / "storyboard" / "preserved.png"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"generated image")
    projects_path = root / "projects.json"
    original_projects = [{"image_url": "storyboard/preserved.png"}]
    projects_path.write_text(json.dumps(original_projects), encoding="utf-8")

    real_replace = workspace_storage.os.replace

    def fail_media_staging(source, destination):
        if Path(source) == media_path and ".trash" in Path(destination).parts:
            raise PermissionError("read-only workspace")
        return real_replace(source, destination)

    monkeypatch.setattr(workspace_storage.os, "replace", fail_media_staging)
    application = FastAPI()

    @application.delete("/remove-media-reference")
    def remove_media_reference():
        projects_path.write_text("[]", encoding="utf-8")
        return {"ok": True}

    from src.apps.server.config import get_server_settings
    from src.apps.server.database import get_db
    from src.apps.server.router import router

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(
        application,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as test_client:
        response = test_client.delete(
            "/remove-media-reference", headers=_csrf_login(test_client)
        )

    assert response.status_code == 500
    assert media_path.read_bytes() == b"generated image"
    assert json.loads(projects_path.read_text(encoding="utf-8")) == original_projects
    assert not (root.parent / ".trash").exists()


def test_failed_metadata_delete_restores_reference_and_preserves_media(
    database, settings, admin, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    workspace_id = admin[1].id
    root = tmp_path / "workspaces" / workspace_id / "output"
    media_path = root / "storyboard" / "preserved.png"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"generated image")
    projects_path = root / "projects.json"
    original_projects = [
        {
            "id": "project-1",
            "frames": [{"id": "frame-1", "image_url": "storyboard/preserved.png"}],
        }
    ]
    projects_path.write_text(json.dumps(original_projects), encoding="utf-8")

    application = FastAPI()

    @application.delete("/reject-media-delete")
    def reject_media_delete():
        from fastapi.responses import JSONResponse

        projects_path.write_text("[]", encoding="utf-8")
        return JSONResponse({"detail": "conflict"}, status_code=409)

    from src.apps.server.config import get_server_settings
    from src.apps.server.database import get_db
    from src.apps.server.router import router

    application.include_router(router)
    application.add_middleware(ServerAuthMiddleware, database=database, settings=settings)

    def provide_db():
        with database.session() as session:
            yield session

    application.dependency_overrides[get_db] = provide_db
    application.dependency_overrides[get_server_settings] = lambda: settings
    with TestClient(application, base_url="http://testserver") as test_client:
        response = test_client.delete(
            "/reject-media-delete", headers=_csrf_login(test_client)
        )

    assert response.status_code == 409
    assert json.loads(projects_path.read_text(encoding="utf-8")) == original_projects
    assert media_path.read_bytes() == b"generated image"


def test_delete_frame_t2i_image_persists_removal_and_repairs_selection(
    tmp_path, monkeypatch
):
    pipeline = ComicGenPipeline(
        {"output_root": str(tmp_path / "output"), "recover_orphan_tasks": False}
    )
    frame = StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        t2i_image_urls=["storyboard/first.png", "storyboard/second.png"],
        t2i_selected_index=1,
    )
    now = time.time()
    script = Script(
        id="project-1",
        title="Project",
        original_text="text",
        frames=[frame],
        created_at=now,
        updated_at=now,
    )
    pipeline.scripts = {script.id: script}
    saves = []
    monkeypatch.setattr(pipeline, "_save_data", lambda: saves.append(True))

    updated_frame, removed_url = pipeline.delete_frame_t2i_image(
        script.id, frame.id, 0
    )

    assert removed_url == "storyboard/first.png"
    assert updated_frame.t2i_image_urls == ["storyboard/second.png"]
    assert updated_frame.t2i_selected_index == 0
    assert saves == [True]

    with pytest.raises(ValueError, match="T2I image not found"):
        pipeline.delete_frame_t2i_image(script.id, frame.id, 1)
