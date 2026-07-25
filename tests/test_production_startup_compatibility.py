"""Regression coverage for production FastAPI startup registration."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api


def test_workspace_read_model_startup_uses_supported_router_api(monkeypatch):
    calls: list[str] = []
    application = FastAPI()
    monkeypatch.setattr(
        comic_api,
        "_initialize_workspace_read_models",
        lambda: calls.append("initialized"),
    )

    comic_api._register_workspace_read_model_startup(application)

    with TestClient(application):
        pass

    assert calls == ["initialized"]
