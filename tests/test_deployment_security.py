import re

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.utils.deployment_security import (
    LOOPBACK_CORS_ORIGIN_REGEX,
    configured_cors_origins,
    cors_middleware_options,
    diagnostic_access_allowed,
    is_diagnostic_path,
)


def _cors_probe(env: dict[str, str]) -> TestClient:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **cors_middleware_options(env))

    @app.get("/probe")
    def probe():
        return {"ok": True}

    return TestClient(app)


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/probe",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_cors_defaults_allow_loopback_origins_only():
    options = cors_middleware_options({})

    assert options["allow_origins"] == []
    loopback_pattern = re.compile(LOOPBACK_CORS_ORIGIN_REGEX)
    assert loopback_pattern.fullmatch("http://localhost:3008")
    assert loopback_pattern.fullmatch("https://127.0.0.1:17177")
    assert loopback_pattern.fullmatch("http://[::1]:3008")
    assert not loopback_pattern.fullmatch("https://studio.example")
    assert set(options["expose_headers"]) >= {
        "ETag",
        "Retry-After",
        "X-EnMotion-Asset-Revision",
        "X-EnMotion-Workspace-ID",
        "X-Request-ID",
    }

    client = _cors_probe({})
    local = _preflight(client, "http://localhost:3008")
    remote = _preflight(client, "https://studio.example")
    assert local.status_code == 200
    assert local.headers["access-control-allow-origin"] == "http://localhost:3008"
    assert remote.status_code == 400
    assert "access-control-allow-origin" not in remote.headers


def test_cors_remote_origin_requires_an_exact_opt_in():
    env = {"ENMOTION_CORS_ORIGINS": "https://studio.example, https://admin.example:8443"}
    assert configured_cors_origins(env) == [
        "https://studio.example",
        "https://admin.example:8443",
    ]

    client = _cors_probe(env)
    allowed = _preflight(client, "https://studio.example")
    sibling = _preflight(client, "https://evil.studio.example")
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://studio.example"
    assert sibling.status_code == 400


def test_fastapi_application_installs_the_safe_cors_policy():
    middleware = next(item for item in comic_api.app.user_middleware if item.cls is CORSMiddleware)

    assert "*" not in middleware.kwargs["allow_origins"]
    assert middleware.kwargs["allow_origin_regex"] == LOOPBACK_CORS_ORIGIN_REGEX
    assert middleware.kwargs["allow_credentials"] is True


def test_openapi_operation_ids_are_unique():
    operation_ids = [
        operation["operationId"]
        for path_item in comic_api.app.openapi()["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "put", "post", "delete", "patch", "options", "head"}
    ]

    assert len(operation_ids) == len(set(operation_ids))


@pytest.mark.parametrize(
    "origin",
    ["*", "studio.example", "https://studio.example/path", "file:///tmp/studio.html"],
)
def test_cors_rejects_unsafe_or_malformed_opt_ins(origin: str):
    with pytest.raises(ValueError):
        configured_cors_origins({"ENMOTION_CORS_ORIGINS": origin})


def test_diagnostics_are_local_by_default_and_remote_by_explicit_opt_in():
    assert diagnostic_access_allowed("127.0.0.1", {})
    assert diagnostic_access_allowed("::1", {})
    assert not diagnostic_access_allowed("203.0.113.10", {})
    assert diagnostic_access_allowed(
        "203.0.113.10",
        {"ENMOTION_ENABLE_REMOTE_DIAGNOSTICS": "true"},
    )

    for path in (
        "/debug/config",
        "/diagnose/log_tail",
        "/config/info",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    ):
        assert is_diagnostic_path(path)
    assert not is_diagnostic_path("/health")
    assert not is_diagnostic_path("/projects")


def test_removed_settings_system_check_route_is_not_registered():
    assert "/system/check" not in {getattr(route, "path", None) for route in comic_api.app.routes}


def test_remote_health_is_sanitized_and_diagnostics_are_hidden(monkeypatch):
    monkeypatch.delenv("ENMOTION_ENABLE_REMOTE_DIAGNOSTICS", raising=False)
    client = TestClient(comic_api.app, client=("203.0.113.10", 50000))

    health = client.get("/health")
    assert health.status_code == 200
    assert set(health.json()) == {"ok", "time"}

    for path in ("/debug/config", "/diagnose/log_tail", "/config/info", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}


def test_loopback_and_opt_in_remote_requests_retain_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr("src.utils.get_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(comic_api, "get_user_config_path", lambda: str(tmp_path / "config.json"))
    monkeypatch.delenv("ENMOTION_ENABLE_REMOTE_DIAGNOSTICS", raising=False)

    local = TestClient(comic_api.app, client=("127.0.0.1", 50000))
    local_health = local.get("/health")
    assert local_health.status_code == 200
    assert local_health.json()["log_dir"] == str(tmp_path)
    assert local.get("/docs").status_code == 200
    assert local.get("/config/info").status_code == 200

    monkeypatch.setenv("ENMOTION_ENABLE_REMOTE_DIAGNOSTICS", "true")
    remote = TestClient(comic_api.app, client=("203.0.113.10", 50000))
    assert remote.get("/openapi.json").status_code == 200
    assert remote.get("/config/info").status_code == 200
    assert remote.get("/diagnose/log_tail").status_code == 200


def test_static_media_allowlist_does_not_expose_persisted_project_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "output"
    uploads = output / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "preview.txt").write_text("allowed-media", encoding="utf-8")
    (output / "projects.json").write_text('{"private": true}', encoding="utf-8")
    (output / "series.json").write_text('{"private": true}', encoding="utf-8")
    (output / "library_assets.json").write_text('{"private": true}', encoding="utf-8")
    (output / "merge_list_project.txt").write_text("private paths", encoding="utf-8")

    client = TestClient(comic_api.app, client=("127.0.0.1", 50000))
    allowed = client.get("/files/uploads/preview.txt")
    assert allowed.status_code == 200
    assert allowed.text == "allowed-media"

    for path in (
        "/files/projects.json",
        "/files/series.json",
        "/files/library_assets.json",
        "/files/merge_list_project.txt",
        "/files/outputs/projects.json",
    ):
        assert client.get(path).status_code == 404


def test_oss_initialization_does_not_print_configuration(monkeypatch, capsys):
    from src.utils.oss_utils import OSSImageUploader

    for name in (
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "OSS_ENDPOINT",
        "OSS_BUCKET_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    OSSImageUploader.reset_instance()
    try:
        OSSImageUploader()
        assert capsys.readouterr().out == ""
    finally:
        OSSImageUploader.reset_instance()
