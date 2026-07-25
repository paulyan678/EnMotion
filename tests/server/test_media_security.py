from __future__ import annotations

import base64
from contextlib import ExitStack

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.apps.comic_gen.llm import _resolve_image_for_vision
from src.apps.web_runtime.context import bind_tenant, reset_tenant
from src.models.newapi import _open_image_ref, _safe_output_path
from src.utils.media_security import (
    UnsafeMediaReferenceError,
    decode_image_data_url,
    resolve_workspace_media_path,
    validate_remote_media_url,
)


@pytest.fixture
def tenant_workspace(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspaces"
    output_root = workspace_root / "workspace-a" / "output"
    output_root.mkdir(parents=True)
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))
    token = bind_tenant("user-a", "workspace-a")
    try:
        yield output_root
    finally:
        reset_tenant(token)


def test_workspace_resolver_rejects_absolute_and_parent_escape(tenant_workspace, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")

    with pytest.raises(UnsafeMediaReferenceError, match="inside this workspace"):
        resolve_workspace_media_path(tenant_workspace, str(secret))
    with pytest.raises(UnsafeMediaReferenceError, match="inside this workspace"):
        resolve_workspace_media_path(tenant_workspace, "../secret.txt")


def test_image_data_url_is_type_and_size_bounded(monkeypatch):
    monkeypatch.setenv("ENMOTION_REMOTE_IMAGE_MAX_BYTES", "4")
    valid = "data:image/png;base64," + base64.b64encode(b"1234").decode("ascii")
    assert decode_image_data_url(valid) == ("image/png", b"1234")

    too_large = "data:image/png;base64," + base64.b64encode(b"12345").decode("ascii")
    with pytest.raises(UnsafeMediaReferenceError, match="larger"):
        decode_image_data_url(too_large)
    with pytest.raises(UnsafeMediaReferenceError, match="PNG, JPEG"):
        decode_image_data_url("data:text/plain;base64,c2VjcmV0")


def test_newapi_reference_and_output_are_bound_to_current_workspace(tenant_workspace, tmp_path):
    allowed = tenant_workspace / "reference.png"
    allowed.write_bytes(b"image")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")

    with ExitStack() as stack:
        name, handle, mime = _open_image_ref(str(allowed), stack)
        assert name == "reference.png"
        assert handle.read() == b"image"
        assert mime == "image/png"
    with ExitStack() as stack:
        with pytest.raises(UnsafeMediaReferenceError, match="inside this workspace"):
            _open_image_ref(str(outside), stack)

    assert _safe_output_path(str(tenant_workspace / "generated" / "out.png")).endswith(
        "generated/out.png"
    )
    with pytest.raises(UnsafeMediaReferenceError, match="inside this workspace"):
        _safe_output_path(str(tmp_path / "leak.png"))


def test_vision_resolver_cannot_inline_an_outside_file(tenant_workspace, tmp_path):
    inside = tenant_workspace / "inside.png"
    inside.write_bytes(b"inside")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")

    assert _resolve_image_for_vision(str(inside)).startswith("data:image/png;base64,")
    with pytest.raises(UnsafeMediaReferenceError, match="inside this workspace"):
        _resolve_image_for_vision(str(outside))


def test_remote_url_requires_allowlist_and_public_dns(monkeypatch):
    monkeypatch.setenv("ENMOTION_REMOTE_MEDIA_HOSTS", "media.example")
    monkeypatch.setattr(
        "src.utils.media_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("203.0.113.10", 443))],
    )
    # Documentation ranges are not globally routable and must fail closed.
    with pytest.raises(UnsafeMediaReferenceError, match="private, local, or reserved"):
        validate_remote_media_url("https://media.example/image.png")

    monkeypatch.setattr(
        "src.utils.media_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )
    assert validate_remote_media_url("https://media.example/image.png").startswith(
        "https://media.example/"
    )
    with pytest.raises(UnsafeMediaReferenceError, match="not allowed"):
        validate_remote_media_url("https://other.example/image.png")


def test_seedance_fallback_allows_only_volcengine_tos_subdomains(monkeypatch):
    monkeypatch.setenv(
        "ENMOTION_REMOTE_MEDIA_HOSTS",
        "*.tos-cn-beijing.volces.com",
    )
    monkeypatch.setattr(
        "src.utils.media_security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )

    result = validate_remote_media_url(
        "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/video.mp4"
    )
    assert result.endswith("/video.mp4")

    with pytest.raises(UnsafeMediaReferenceError, match="not allowed"):
        validate_remote_media_url("https://tos-cn-beijing.volces.com/video.mp4")
    with pytest.raises(UnsafeMediaReferenceError, match="not allowed"):
        validate_remote_media_url(
            "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com.evil.example/video.mp4"
        )


def test_generated_asset_cache_policy_is_private_and_revalidates(monkeypatch):
    from src.apps.comic_gen import api as comic_api

    monkeypatch.setattr(comic_api, "workspace_isolation_enabled", lambda: True)

    expected = "private, max-age=3600, must-revalidate"
    assert comic_api._media_cache_control("/files/assets/characters/hero.png") == expected
    assert comic_api._media_cache_control("/files/outputs/assets/scenes/room.png") == expected
    assert comic_api._media_cache_control("/files/uploads/source.png") == ("private, no-store")
    assert (
        comic_api._media_cache_control("/files/derivatives/images/ab/source/revision/w384.webp")
        == "private, max-age=31536000, immutable"
    )


def test_desktop_asset_cache_policy_remains_public(monkeypatch):
    from src.apps.comic_gen import api as comic_api

    monkeypatch.setattr(comic_api, "workspace_isolation_enabled", lambda: False)
    assert comic_api._media_cache_control("/files/assets/characters/hero.png") == (
        "public, max-age=86400"
    )


def test_private_media_supports_mime_etag_conditional_head_and_ranges(monkeypatch, tmp_path):
    from src.apps.comic_gen import api as comic_api

    # Minimal production images may not ship a WebP entry in /etc/mime.types.
    # The application contract must not depend on the host MIME database.
    monkeypatch.setattr(comic_api.mimetypes, "guess_type", lambda _name: (None, None))
    media = tmp_path / "thumbnail.webp"
    payload = b"RIFF" + bytes(range(64))
    media.write_bytes(payload)
    app = FastAPI()

    @app.api_route("/media", methods=["GET", "HEAD"])
    def serve(request: Request):
        return comic_api._private_media_response(
            request,
            media,
            cache_control="private, max-age=31536000, immutable",
        )

    with TestClient(app) as client:
        complete = client.get("/media")
        assert complete.status_code == 200
        assert complete.content == payload
        assert complete.headers["content-type"] == "image/webp"
        assert complete.headers["content-length"] == str(len(payload))
        assert complete.headers["accept-ranges"] == "bytes"
        assert complete.headers["etag"]

        head = client.head("/media")
        assert head.status_code == 200
        assert not head.content
        assert head.headers["content-length"] == str(len(payload))

        not_modified = client.get(
            "/media",
            headers={"If-None-Match": f'W/{complete.headers["etag"]}'},
        )
        assert not_modified.status_code == 304
        assert not not_modified.content
        assert not_modified.headers["etag"] == complete.headers["etag"]

        partial = client.get(
            "/media",
            headers={
                "Range": "bytes=4-11",
                "If-Range": complete.headers["etag"],
            },
        )
        assert partial.status_code == 206
        assert partial.content == payload[4:12]
        assert partial.headers["content-range"] == f"bytes 4-11/{len(payload)}"

        stale_if_range = client.get(
            "/media",
            headers={"Range": "bytes=4-11", "If-Range": '"stale"'},
        )
        assert stale_if_range.status_code == 200
        assert stale_if_range.content == payload
