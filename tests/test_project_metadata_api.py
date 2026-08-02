"""Project metadata and resolved-overview regression coverage."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.models import Character, Scene, Script, Series
from src.apps.comic_gen.pipeline import ComicGenPipeline


def _pipeline(output_root) -> ComicGenPipeline:
    with (
        patch("src.apps.comic_gen.pipeline.ScriptProcessor"),
        patch("src.apps.comic_gen.pipeline.AssetGenerator"),
        patch("src.apps.comic_gen.pipeline.StoryboardGenerator"),
        patch("src.apps.comic_gen.pipeline.VideoGenerator"),
        patch("src.apps.comic_gen.pipeline.ExportManager"),
    ):
        return ComicGenPipeline(
            {
                "output_root": str(output_root),
                "recover_orphan_tasks": False,
            }
        )


def _seed_series_episode(pipeline: ComicGenPipeline) -> tuple[Series, Script]:
    now = time.time()
    series = Series(
        id="series-metadata",
        title="Original series",
        description="Original description",
        episode_ids=["episode-metadata"],
        characters=[
            Character(
                id="shared-character",
                name="Shared hero",
                description="Available to every episode",
            )
        ],
        scenes=[
            Scene(
                id="shared-scene",
                name="Shared city",
                description="Available to every episode",
            )
        ],
        created_at=now,
        updated_at=now,
    )
    episode = Script(
        id="episode-metadata",
        title="Original episode",
        original_text="Full script remains unchanged.",
        series_id=series.id,
        episode_number=1,
        created_at=now,
        updated_at=now,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode
    pipeline._save_data()
    pipeline._save_series_data()
    return series, episode


def test_update_project_metadata_persists_without_rewriting_script(tmp_path):
    pipeline = _pipeline(tmp_path)
    _, episode = _seed_series_episode(pipeline)
    previous_updated_at = episode.updated_at

    updated = pipeline.update_project_metadata(
        episode.id,
        title="  Renamed episode  ",
        description="  Episode description  ",
        script_summary="  Editable summary  ",
    )

    assert updated.title == "Renamed episode"
    assert updated.description == "Episode description"
    assert updated.script_summary == "Editable summary"
    assert updated.original_text == "Full script remains unchanged."
    assert updated.updated_at > previous_updated_at
    persisted = json.loads(Path(pipeline.data_file).read_text(encoding="utf-8"))
    assert persisted[episode.id]["description"] == "Episode description"
    assert persisted[episode.id]["script_summary"] == "Editable summary"


def test_metadata_api_and_project_lists_return_resolved_series_assets(
    tmp_path,
    monkeypatch,
):
    pipeline = _pipeline(tmp_path)
    series, episode = _seed_series_episode(pipeline)
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    client = TestClient(comic_api.app)

    response = client.put(
        f"/projects/{episode.id}/metadata",
        json={
            "title": "Renamed through API",
            "description": "Visible episode description",
            "script_summary": "Visible script summary",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed through API"
    assert body["description"] == "Visible episode description"
    assert body["script_summary"] == "Visible script summary"
    assert [item["id"] for item in body["characters"]] == ["shared-character"]
    assert [item["id"] for item in body["scenes"]] == ["shared-scene"]

    listed = client.get("/projects/").json()
    assert listed[0]["characters"][0]["source"] == "series"
    assert listed[0]["scenes"][0]["series_id"] == series.id

    episodes = client.get(f"/series/{series.id}/episodes").json()
    assert episodes[0]["title"] == "Renamed through API"
    assert len(episodes[0]["characters"]) == 1
    assert len(episodes[0]["scenes"]) == 1


def test_project_metadata_rejects_blank_title_without_changing_data(
    tmp_path,
    monkeypatch,
):
    pipeline = _pipeline(tmp_path)
    _, episode = _seed_series_episode(pipeline)
    monkeypatch.setattr(comic_api, "pipeline", pipeline)
    client = TestClient(comic_api.app)

    response = client.put(
        f"/projects/{episode.id}/metadata",
        json={"title": "   ", "description": "should not be saved"},
    )

    assert response.status_code == 400
    assert pipeline.scripts[episode.id].title == "Original episode"
    assert pipeline.scripts[episode.id].description == ""
