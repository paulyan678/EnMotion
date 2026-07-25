from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.models import Character, GlobalAssetLibrary
from src.apps.web_runtime.asset_library_feed import build_asset_library_snapshot
from src.apps.web_runtime.context import bind_tenant, reset_tenant
from src.apps.web_runtime.media_derivatives import generate_image_derivatives
from src.apps.web_runtime.workspace_snapshot import WorkspaceSnapshotUnavailable


class _UnconfiguredUploader:
    is_configured = False


def test_asset_feed_has_strict_json_cache_workspace_and_request_headers(monkeypatch):
    library = GlobalAssetLibrary(
        characters=[
            Character(
                id="character-1",
                name="Visible Hero",
                description="compact metadata",
                image_url="assets/hero.png",
                image_prompt="private editor prompt",
            )
        ]
    )
    current = SimpleNamespace(
        series_store={},
        scripts={},
        library_store=library,
    )
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: False)
    monkeypatch.setattr(
        comic_api,
        "pipeline",
        SimpleNamespace(current=lambda: current),
    )
    monkeypatch.setattr(comic_api, "OSSImageUploader", _UnconfiguredUploader)

    with TestClient(comic_api.app, client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/library/feed",
            headers={
                "X-EnMotion-Client-Request-ID": "logical-1",
                "X-EnMotion-Client-Attempt-ID": "logical-1.1",
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "private, no-cache"
        assert response.headers["vary"] == "Cookie"
        assert response.headers["x-enmotion-asset-revision"] == "0"
        assert response.headers["x-request-id"]
        assert response.json()["items"][0]["name"] == "Visible Hero"
        assert "private editor prompt" not in response.text
        assert set(response.json()) == {
            "schema_version",
            "revision",
            "generated_at",
            "items",
            "facets",
            "page",
        }

        not_modified = client.get(
            "/library/feed",
            headers={"If-None-Match": response.headers["etag"]},
        )
        assert not_modified.status_code == 304
        assert not not_modified.content
        assert not_modified.headers["etag"] == response.headers["etag"]
        assert not_modified.headers["x-request-id"]


def test_asset_feed_snapshot_failure_is_retryable_and_never_empty(monkeypatch):
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)

    def unavailable(_workspace_id: str):
        raise WorkspaceSnapshotUnavailable("corrupt current manifest")

    monkeypatch.setattr(comic_api, "read_asset_library_snapshot", unavailable)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/library/feed",
            "headers": [],
            "state": {"request_id": "request-safe-503"},
        }
    )
    token = bind_tenant("user-1", "workspace-1")
    try:
        response = comic_api.get_asset_library_feed(
            request,
            asset_type=None,
            source_kind=None,
            starred=False,
            q="",
            sort="default",
            offset=0,
            limit=50,
        )
    finally:
        reset_tenant(token)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert b'"items":[]' not in response.body
    assert b'"code":"ASSET_LIBRARY_UNAVAILABLE"' in response.body
    assert b'"request_id":"request-safe-503"' in response.body


def test_asset_feed_validates_parameters_and_accepts_episode_alias(monkeypatch):
    project = SimpleNamespace(
        id="episode-1",
        title="Episode One",
        series_id=None,
        characters=[
            Character(
                id="project-character",
                name="Project Hero",
                description="",
            )
        ],
        scenes=[],
        props=[],
        frames=[],
    )
    current = SimpleNamespace(
        series_store={},
        scripts={project.id: project},
        library_store=GlobalAssetLibrary(),
    )
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: False)
    monkeypatch.setattr(
        comic_api,
        "pipeline",
        SimpleNamespace(current=lambda: current),
    )
    monkeypatch.setattr(comic_api, "OSSImageUploader", _UnconfiguredUploader)

    with TestClient(comic_api.app, client=("127.0.0.1", 50001)) as client:
        for query in (
            "sort=unsupported",
            "order=sideways",
            "asset_type=audio",
            "source_kind=unknown",
            "limit=0",
            "limit=51",
            f"q={'x' * 501}",
            "project_id=episode-1&episode_id=episode-2",
        ):
            response = client.get(f"/library/feed?{query}")
            assert response.status_code == 422, query
            assert response.json()["detail"]

        aliased = client.get(
            "/library/feed",
            params={
                "source_kind": "episode",
                "episode_id": "episode-1",
                "sort": "usage",
                "order": "desc",
            },
        )
        assert aliased.status_code == 200
        assert [
            (item["source_kind"], item["source_id"], item["usage_count"])
            for item in aliased.json()["items"]
        ] == [("project", "episode-1", 0)]


def test_asset_feed_uses_only_the_authenticated_workspace_snapshot(monkeypatch):
    snapshots = {
        "workspace-a": build_asset_library_snapshot(
            revision=1,
            series=[],
            projects=[],
            library=GlobalAssetLibrary(
                characters=[Character(id="same-id", name="Workspace A", description="")]
            ),
            generated_at=1.0,
        ),
        "workspace-b": build_asset_library_snapshot(
            revision=2,
            series=[],
            projects=[],
            library=GlobalAssetLibrary(
                characters=[Character(id="same-id", name="Workspace B", description="")]
            ),
            generated_at=2.0,
        ),
    }
    requested: list[str] = []

    def read_snapshot(workspace_id: str):
        requested.append(workspace_id)
        return snapshots[workspace_id]

    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setattr(comic_api, "read_asset_library_snapshot", read_snapshot)
    monkeypatch.setattr(comic_api, "OSSImageUploader", _UnconfiguredUploader)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/library/feed",
            "headers": [],
            "state": {"request_id": "request-workspace-a"},
        }
    )
    token = bind_tenant("user-a", "workspace-a")
    try:
        response = comic_api.get_asset_library_feed(
            request,
            asset_type=None,
            source_kind=None,
            project_id=None,
            episode_id=None,
            series_id=None,
            starred=False,
            q="",
            sort="usage",
            order="desc",
            offset=0,
            limit=50,
        )
    finally:
        reset_tenant(token)

    payload = json.loads(response.body)
    assert requested == ["workspace-a"]
    assert [item["name"] for item in payload["items"]] == ["Workspace A"]
    assert "Workspace B" not in response.body.decode("utf-8")


def test_responsive_feed_returns_page_local_derivatives_and_is_conditionally_cached(
    monkeypatch,
    tmp_path,
):
    output_root = tmp_path / "output"
    source = output_root / "assets" / "hero.png"
    source.parent.mkdir(parents=True)
    image = Image.new("RGBA", (1024, 768), (40, 90, 180, 128))
    image.save(source, format="PNG")
    image.close()
    generated = generate_image_derivatives(output_root, "assets/hero.png")
    assert generated.state == "ready"

    current = SimpleNamespace(
        series_store={},
        scripts={},
        library_store=GlobalAssetLibrary(
            characters=[
                Character(
                    id="character-local",
                    name="Local Hero",
                    description="",
                    image_url="/files/assets/hero.png",
                ),
                Character(
                    id="character-remote",
                    name="Remote Hero",
                    description="",
                    image_url="https://private.example/hero.png?signature=redacted",
                ),
            ]
        ),
    )
    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: False)
    monkeypatch.setattr(
        comic_api,
        "pipeline",
        SimpleNamespace(current=lambda: current),
    )
    monkeypatch.setattr(comic_api, "current_output_root", lambda: str(output_root))
    monkeypatch.setattr(comic_api, "OSSImageUploader", _UnconfiguredUploader)

    with TestClient(comic_api.app, client=("127.0.0.1", 50002)) as client:
        response = client.get("/library/feed/v3")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-cache"
        assert response.headers["vary"] == "Cookie"
        assert "asset-derivative-lookup" in response.headers["server-timing"]
        payload = response.json()
        assert payload["schema_version"] == 3
        assert len(payload["items"]) == 2
        local = next(item for item in payload["items"] if item["id"] == "character-local")
        remote = next(item for item in payload["items"] if item["id"] == "character-remote")
        assert local["thumbnail"]["url"] == "/files/assets/hero.png"
        assert local["thumbnail"]["state"] == "ready"
        assert [derivative["width"] for derivative in local["thumbnail"]["derivatives"]] == [
            96,
            384,
            768,
        ]
        assert all(
            derivative["url"].startswith("derivatives/images/")
            for derivative in local["thumbnail"]["derivatives"]
        )
        assert remote["thumbnail"]["state"] == "unavailable"
        assert remote["thumbnail"]["derivatives"] == []

        not_modified = client.get(
            "/library/feed/v3",
            headers={"If-None-Match": response.headers["etag"]},
        )
        assert not_modified.status_code == 304
        assert not not_modified.content
