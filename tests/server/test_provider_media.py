from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.server.middleware import ServerAuthMiddleware
from src.apps.server.provider_media import (
    ProviderMediaTokenError,
    create_provider_media_url,
    resolve_provider_media_token,
)
from src.apps.web_runtime.context import bind_tenant, reset_tenant


def test_signed_provider_url_resolves_only_until_expiry(monkeypatch, tmp_path, settings):
    workspace_id = "workspace-1"
    image = tmp_path / workspace_id / "output" / "playground" / "frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path))
    active_settings = replace(
        settings,
        public_base_url="https://studio.example.test",
        allowed_origins=("https://studio.example.test",),
    )

    tenant_token = bind_tenant("user-1", workspace_id)
    try:
        url = create_provider_media_url(
            "playground/frame.png",
            now=1_000,
            ttl_seconds=60,
            settings=active_settings,
        )
    finally:
        reset_tenant(tenant_token)

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "studio.example.test"
    token, filename = parsed.path.removeprefix("/provider-media/").split("/", 1)
    assert filename == "frame.png"
    assert resolve_provider_media_token(token, now=1_059, settings=active_settings) == image
    with pytest.raises(ProviderMediaTokenError, match="expired"):
        resolve_provider_media_token(token, now=1_061, settings=active_settings)


def test_signed_provider_url_rejects_tampering(monkeypatch, tmp_path, settings):
    workspace_id = "workspace-1"
    image = tmp_path / workspace_id / "output" / "playground" / "frame.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpeg")
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path))
    active_settings = replace(settings, public_base_url="http://testserver")

    tenant_token = bind_tenant("user-1", workspace_id)
    try:
        url = create_provider_media_url(
            str(image),
            now=1_000,
            ttl_seconds=60,
            settings=active_settings,
        )
    finally:
        reset_tenant(tenant_token)

    token = urlparse(url).path.removeprefix("/provider-media/").split("/", 1)[0]
    encoded, signature = token.split(".", 1)
    replacement = "0" if signature[-1] != "0" else "1"
    with pytest.raises(ProviderMediaTokenError, match="Invalid"):
        resolve_provider_media_token(
            f"{encoded}.{signature[:-1]}{replacement}",
            now=1_001,
            settings=active_settings,
        )


def test_provider_media_path_is_public_but_other_paths_remain_protected(
    database,
    settings,
):
    application = FastAPI()

    @application.api_route(
        "/provider-media/{token}/{filename}",
        methods=["GET", "HEAD"],
    )
    def provider_media(token: str, filename: str):
        return {"token": token, "filename": filename}

    @application.get("/protected")
    def protected():
        return {"ok": True}

    application.add_middleware(
        ServerAuthMiddleware,
        database=database,
        settings=settings,
    )

    with TestClient(application, base_url="http://testserver") as client:
        path = "/provider-media/signed-token/frame.png"
        assert client.get(path).status_code == 200
        assert client.head(path).status_code == 200
        assert client.post(path).status_code == 401
        assert client.get("/protected").status_code == 401
