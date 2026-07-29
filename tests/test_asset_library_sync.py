"""Canonical asset ownership and cross-library synchronization tests."""

import base64
import json
import time
import uuid
from unittest.mock import patch

import pytest

from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GenerationStatus,
    ImageAsset,
    ImageVariant,
    Prop,
    Scene,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
    VideoVariant,
)
from src.apps.comic_gen.pipeline import (
    AssetTypeChangeConflictError,
    ComicGenPipeline,
    FICTIONAL_CHARACTER_PROMPT_NOTICE,
)
from src.models.newapi import (
    INPUT_IMAGE_PRIVACY_ERROR_CODE,
    INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
    NewAPIProviderError,
)


def _script(script_id: str, **overrides) -> Script:
    now = time.time()
    values = {
        "id": script_id,
        "title": "Episode",
        "original_text": "text",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Script(**values)


def _series(series_id: str, episode_ids=None, **overrides) -> Series:
    now = time.time()
    values = {
        "id": series_id,
        "title": "Series",
        "episode_ids": episode_ids or [],
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Series(**values)


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


def test_startup_migrates_existing_episode_assets_to_series_losslessly(tmp_path):
    series = _series("series-1", ["episode-1"])
    script = _script(
        "episode-1",
        series_id="series-1",
        episode_number=1,
        characters=[
            Character(
                id="char-1",
                name="Hero",
                description="Existing generated hero",
                image_url="/files/generated/hero.png",
            )
        ],
        scenes=[Scene(id="scene-1", name="Hall", description="A hall")],
        props=[Prop(id="prop-1", name="Key", description="A key")],
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="scene-1",
                character_ids=["char-1"],
                prop_ids=["prop-1"],
            )
        ],
    )
    (tmp_path / "projects.json").write_text(
        json.dumps({script.id: script.model_dump()}), encoding="utf-8"
    )
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)

    migrated_script = pipeline.scripts[script.id]
    migrated_series = pipeline.series_store[series.id]
    assert migrated_script.characters == []
    assert migrated_script.scenes == []
    assert migrated_script.props == []
    assert [asset.id for asset in migrated_series.characters] == ["char-1"]
    assert migrated_series.characters[0].image_url == "/files/generated/hero.png"
    assert [asset.id for asset in migrated_series.scenes] == ["scene-1"]
    assert [asset.id for asset in migrated_series.props] == ["prop-1"]

    persisted_projects = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    persisted_series = json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))
    assert persisted_projects[script.id]["characters"] == []
    assert persisted_series[series.id]["characters"][0]["image_url"].endswith("hero.png")

    # A second process startup must not duplicate canonical assets.
    reloaded = _pipeline(tmp_path)
    assert [asset.id for asset in reloaded.series_store[series.id].characters] == ["char-1"]


def test_reparse_reconciles_fresh_entities_and_rewrites_references(tmp_path):
    pipeline = _pipeline(tmp_path)
    existing = Character(
        id="series-char",
        name="  HERO ",
        description="Canonical description",
        image_url="/files/generated/existing.png",
    )
    series = _series("series-1", ["episode-1"], characters=[existing])
    original = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[original.id] = original

    extracted = _script(
        "preview-id",
        original_text="updated text",
        characters=[Character(id="fresh-char", name="hero", description="Fresh description")],
        scenes=[Scene(id="fresh-scene", name="Kitchen", description="Kitchen")],
        props=[Prop(id="fresh-prop", name="Spoon", description="Spoon")],
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="fresh-scene",
                character_ids=["fresh-char", "fresh-char"],
                prop_ids=["fresh-prop"],
            )
        ],
        video_tasks=[
            VideoTask(
                id="video-1",
                project_id="episode-1",
                asset_id="fresh-char",
                image_url="/files/input.png",
                prompt="animate",
            )
        ],
    )
    revision = "reviewed-revision"
    pipeline._extraction_cache[original.id] = (time.time(), revision, extracted)

    result = pipeline.reparse_project(original.id, "updated text", revision)

    assert result.characters == []
    assert result.scenes == []
    assert result.props == []
    assert [asset.id for asset in series.characters] == ["series-char"]
    assert series.characters[0].image_url == "/files/generated/existing.png"
    assert [asset.id for asset in series.scenes] == ["fresh-scene"]
    assert [asset.id for asset in series.props] == ["fresh-prop"]
    assert result.frames[0].character_ids == ["series-char"]
    assert result.frames[0].scene_id == "fresh-scene"
    assert result.frames[0].prop_ids == ["fresh-prop"]
    assert result.video_tasks[0].asset_id == "series-char"


def test_quick_create_uses_series_as_source_of_truth(tmp_path):
    pipeline = _pipeline(tmp_path)
    series = _series("series-1", ["episode-1"])
    script = _script("episode-1", series_id=series.id, episode_number=1)
    pipeline.series_store[series.id] = series
    pipeline.scripts[script.id] = script

    pipeline.add_character(script.id, "Hero", "A hero")
    pipeline.add_scene(script.id, "Room", "A room")
    pipeline.add_prop(script.id, "Book", "A book")

    assert script.characters == []
    assert script.scenes == []
    assert script.props == []
    assert [asset.name for asset in series.characters] == ["Hero"]
    assert [asset.name for asset in series.scenes] == ["Room"]
    assert [asset.name for asset in series.props] == ["Book"]


def test_standalone_quick_create_stays_project_owned(tmp_path):
    pipeline = _pipeline(tmp_path)
    script = _script("standalone")
    pipeline.scripts[script.id] = script

    pipeline.add_character(script.id, "Hero", "A hero")
    pipeline.add_scene(script.id, "Room", "A room")
    pipeline.add_prop(script.id, "Book", "A book")

    assert [asset.name for asset in script.characters] == ["Hero"]
    assert [asset.name for asset in script.scenes] == ["Room"]
    assert [asset.name for asset in script.props] == ["Book"]


def test_asset_library_overview_is_compact_and_uses_canonical_owners(tmp_path):
    pipeline = _pipeline(tmp_path)
    series = _series(
        "series-1",
        ["episode-1"],
        characters=[Character(id="series-char", name="Series Hero", description="")],
    )
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
        original_text="large script that the library must not return",
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="scene-1",
                action_description="unused storyboard data",
            )
        ],
    )
    standalone = _script(
        "standalone-1",
        original_text="another unused script",
        scenes=[Scene(id="standalone-scene", name="Standalone Room", description="")],
    )
    empty_standalone = _script("empty-standalone")
    pipeline.series_store[series.id] = series
    pipeline.scripts = {
        episode.id: episode,
        standalone.id: standalone,
        empty_standalone.id: empty_standalone,
    }
    pipeline.library_store.characters.append(
        Character(id="global-char", name="Global Hero", description="")
    )

    overview = pipeline.get_asset_library_overview()

    assert [source["id"] for source in overview["series"]] == [series.id]
    assert [source["id"] for source in overview["projects"]] == [standalone.id]
    assert overview["series"][0]["characters"][0]["name"] == "Series Hero"
    assert overview["projects"][0]["scenes"][0]["name"] == "Standalone Room"
    assert overview["global"]["characters"][0]["name"] == "Global Hero"
    assert "original_text" not in overview["projects"][0]
    assert "frames" not in overview["projects"][0]


def test_global_library_primary_images_are_canonical_and_persisted(tmp_path):
    pipeline = _pipeline(tmp_path)

    tester = pipeline.create_library_asset(
        "character",
        {
            "name": "Tester",
            "description": "Complete tester record",
            "persona": "QA",
            "image_url": "uploads/tester.png",
        },
    )
    room = pipeline.create_library_asset(
        "scene",
        {
            "name": "Room",
            "description": "A room",
            "image_url": "uploads/room.png",
        },
    )
    prop = pipeline.create_library_asset(
        "prop",
        {
            "name": "Book",
            "description": "A book",
            "image_url": "uploads/book.png",
        },
    )

    tester_selected = tester.reference_sheet.selected_image_id
    assert tester.image_url == "uploads/tester.png"
    assert tester_selected
    assert (
        next(
            variant.url
            for variant in tester.reference_sheet.image_variants
            if variant.id == tester_selected
        )
        == "uploads/tester.png"
    )
    for asset, expected in ((room, "uploads/room.png"), (prop, "uploads/book.png")):
        assert asset.image_url == expected
        assert asset.image_asset.selected_id
        assert (
            next(
                variant.url
                for variant in asset.image_asset.variants
                if variant.id == asset.image_asset.selected_id
            )
            == expected
        )

    pipeline.update_library_asset("character", tester.id, {"image_url": "uploads/tester-v2.png"})
    assert tester.image_url == "uploads/tester-v2.png"
    assert len(tester.reference_sheet.image_variants) == 2
    assert (
        next(
            variant.url
            for variant in tester.reference_sheet.image_variants
            if variant.id == tester.reference_sheet.selected_image_id
        )
        == "uploads/tester-v2.png"
    )

    persisted = json.loads((tmp_path / "library_assets.json").read_text(encoding="utf-8"))
    persisted_tester = persisted["characters"][0]
    assert persisted_tester["image_url"] == "uploads/tester-v2.png"
    assert persisted_tester["reference_sheet"]["selected_image_id"]

    reloaded = _pipeline(tmp_path)
    loaded_tester = reloaded.library_store.characters[0]
    assert loaded_tester.image_url == "uploads/tester-v2.png"
    assert (
        next(
            variant.url
            for variant in loaded_tester.reference_sheet.image_variants
            if variant.id == loaded_tester.reference_sheet.selected_image_id
        )
        == "uploads/tester-v2.png"
    )


def test_global_asset_full_record_resolves_in_overview_project_and_series(tmp_path, monkeypatch):
    from src.apps.comic_gen import api as comic_api

    pipeline = _pipeline(tmp_path)
    series = _series("series-1", ["episode-1"])
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode
    tester = pipeline.create_library_asset(
        "character",
        {
            "name": "Tester",
            "description": "Complete tester record",
            "persona": "QA",
            "image_url": "uploads/tester.png",
        },
    )
    monkeypatch.setattr(comic_api, "pipeline", pipeline)

    overview_tester = pipeline.get_asset_library_overview()["global"]["characters"][0]
    project_payload = json.loads(comic_api.get_project(episode.id).body)
    project_tester = next(
        asset for asset in project_payload["characters"] if asset["id"] == tester.id
    )
    series_payload = json.loads(comic_api.get_series(series.id).body)
    series_tester = next(
        asset for asset in series_payload["characters"] if asset["id"] == tester.id
    )

    for payload in (overview_tester, project_tester, series_tester):
        assert payload["id"] == tester.id
        assert payload["name"] == "Tester"
        assert payload["description"] == "Complete tester record"
        assert payload["persona"] == "QA"
        selected_id = payload["reference_sheet"]["selected_image_id"]
        assert (
            next(
                variant["url"]
                for variant in payload["reference_sheet"]["image_variants"]
                if variant["id"] == selected_id
            )
            == "uploads/tester.png"
        )

    assert overview_tester["source"] == "global"
    assert overview_tester["source_id"] == "global"
    assert overview_tester["series_id"] is None
    assert overview_tester["episode_id"] is None

    assert project_tester["source"] == "global"
    assert project_tester["source_id"] == "global"
    assert project_tester["series_id"] == series.id
    assert project_tester["episode_id"] == episode.id

    assert series_tester["source"] == "global"
    assert series_tester["source_id"] == "global"
    assert series_tester["series_id"] == series.id
    assert series_tester["episode_id"] is None

    # Resolution is read-only: the global fallback is never copied into the
    # canonical Series or Episode persistence containers.
    assert series.characters == []
    assert episode.characters == []


def test_promoted_legacy_asset_is_normalized_into_global_canonical_image(tmp_path):
    pipeline = _pipeline(tmp_path)
    project = _script("episode-1")
    project.characters = [
        Character(
            id="legacy-tester",
            name="Tester",
            description="Legacy uploaded character",
            image_url="uploads/tester.png",
        )
    ]
    pipeline.scripts[project.id] = project

    promoted = pipeline.promote_asset_to_library(
        "project", project.id, "character", "legacy-tester"
    )

    assert promoted.image_url == "uploads/tester.png"
    assert promoted.reference_sheet.selected_image_id
    assert (
        next(
            variant.url
            for variant in promoted.reference_sheet.image_variants
            if variant.id == promoted.reference_sheet.selected_image_id
        )
        == "uploads/tester.png"
    )


def test_episode_image_update_mutates_global_canonical_record(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1")
    pipeline.scripts[episode.id] = episode
    tester = pipeline.create_library_asset(
        "character",
        {
            "name": "Tester",
            "description": "Global character",
            "image_url": "uploads/tester-v1.png",
        },
    )

    pipeline.update_asset_image(
        episode.id,
        tester.id,
        "character",
        "uploads/tester-v2.png",
    )

    assert tester.image_url == "uploads/tester-v2.png"
    assert pipeline._selected_asset_unit_image_url(tester.reference_sheet) == (
        "uploads/tester-v2.png"
    )
    reloaded = _pipeline(tmp_path)
    persisted = reloaded.library_store.characters[0]
    assert persisted.image_url == "uploads/tester-v2.png"
    assert reloaded._selected_asset_unit_image_url(persisted.reference_sheet) == (
        "uploads/tester-v2.png"
    )


def test_clearing_global_character_image_clears_legacy_fallbacks(tmp_path):
    pipeline = _pipeline(tmp_path)
    tester = pipeline.create_library_asset(
        "character",
        {
            "name": "Tester",
            "description": "Legacy and canonical images",
            "image_url": "uploads/canonical.png",
        },
    )
    legacy = ImageVariant(id="legacy-image", url="uploads/legacy.png")
    tester.full_body.image_variants = [legacy.model_copy(deep=True)]
    tester.full_body.selected_image_id = legacy.id
    tester.full_body_asset.variants = [legacy.model_copy(deep=True)]
    tester.full_body_asset.selected_id = legacy.id
    tester.full_body_image_url = legacy.url
    tester.avatar_url = legacy.url

    pipeline.update_library_asset("character", tester.id, {"image_url": None})

    assert tester.image_url is None
    assert tester.avatar_url is None
    assert tester.full_body_image_url is None
    assert tester.reference_sheet.image_variants == []
    assert tester.full_body.image_variants == []
    assert tester.full_body_asset.variants == []
    assert pipeline._library_primary_image_url("character", tester) is None


def test_episode_actions_resolve_and_persist_global_asset_owner(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1")
    pipeline.scripts[episode.id] = episode
    tester = pipeline.create_library_asset(
        "character",
        {
            "name": "Tester",
            "description": "Global character",
            "image_url": "uploads/tester-v1.png",
        },
    )
    first_variant_id = tester.reference_sheet.selected_image_id
    pipeline.update_library_asset("character", tester.id, {"image_url": "uploads/tester-v2.png"})

    pipeline.select_asset_variant(
        episode.id,
        tester.id,
        "character",
        first_variant_id,
        "reference_sheet",
    )
    assert tester.reference_sheet.selected_image_id == first_variant_id
    assert tester.image_url == "uploads/tester-v1.png"

    pipeline.add_uploaded_asset_variant(
        episode.id,
        "character",
        tester.id,
        "full_body",
        "uploads/tester-upload.png",
    )
    assert pipeline._selected_asset_unit_image_url(tester.reference_sheet) == (
        "uploads/tester-upload.png"
    )
    assert _pipeline(tmp_path).library_store.characters[0].image_url == (
        "uploads/tester-upload.png"
    )


def test_global_asset_generation_reservation_tracks_and_restores_owner(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1")
    pipeline.scripts[episode.id] = episode
    tester = pipeline.create_library_asset(
        "character",
        {"name": "Tester", "description": "Global character"},
    )

    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec"),
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        _, task_id = pipeline.create_asset_generation_task(
            episode.id,
            tester.id,
            "character",
            generation_type="reference_sheet",
            task_id="global-generation-task",
        )

    task = pipeline.asset_generation_tasks[task_id]
    assert task["asset_source"] == "global"
    assert task["asset_is_global_level"] is True
    assert tester.status is GenerationStatus.PROCESSING
    assert pipeline.rollback_asset_generation_task(task_id)
    assert tester.status is GenerationStatus.PENDING
    assert _pipeline(tmp_path).library_store.characters[0].status is GenerationStatus.PENDING

    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec"),
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        pipeline.generate_asset(
            episode.id,
            tester.id,
            "character",
            generation_type="reference_sheet",
        )
    pipeline.asset_generator.generate_character.assert_called_once()
    assert tester.status is GenerationStatus.COMPLETED
    assert _pipeline(tmp_path).library_store.characters[0].status is GenerationStatus.COMPLETED


def test_series_generation_persists_real_primary_image_across_every_payload(
    tmp_path,
):
    pipeline = _pipeline(tmp_path)
    character = Character(
        id="series-character",
        name="Generated hero",
        description="A generated series character",
    )
    series = _series(
        "series-1",
        ["episode-1"],
        characters=[character],
    )
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    generated_url = "assets/characters/series-character-generated.png"
    generated_path = tmp_path / generated_url

    def generate_character(target, **_kwargs):
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_bytes(png_bytes)
        variant = ImageVariant(
            id="generated-primary",
            url=generated_url,
            prompt_used="Generated hero prompt",
        )
        target.full_body_asset.variants = [variant]
        target.full_body_asset.selected_id = variant.id
        target.full_body_image_url = variant.url

    pipeline.asset_generator.generate_character.side_effect = generate_character

    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec"),
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        pipeline.generate_asset(
            episode.id,
            character.id,
            "character",
            generation_type="full_body",
        )

    assert generated_path.read_bytes() == png_bytes
    assert generated_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert character.image_url == generated_url
    assert character.reference_sheet.selected_image_id == "generated-primary"
    assert pipeline._selected_asset_unit_image_url(character.reference_sheet) == (
        generated_url
    )

    overview_asset = pipeline.get_asset_library_overview()["series"][0][
        "characters"
    ][0]
    resolved_project_asset = next(
        asset
        for asset in pipeline.resolve_episode_assets(episode, series)["characters"]
        if asset.id == character.id
    )
    resolved_series_asset = next(
        asset
        for asset in pipeline.resolve_series_assets(series)["characters"]
        if asset.id == character.id
    )
    assert overview_asset["image_url"] == generated_url
    assert resolved_project_asset.image_url == generated_url
    assert resolved_series_asset.image_url == generated_url

    reloaded = _pipeline(tmp_path)
    saved = reloaded.series_store[series.id].characters[0]
    assert saved.image_url == generated_url
    assert saved.reference_sheet.selected_image_id == "generated-primary"
    assert reloaded._selected_asset_unit_image_url(saved.reference_sheet) == (
        generated_url
    )


def test_series_variant_selection_and_deletion_share_one_canonical_record(tmp_path):
    full_body = ImageVariant(id="full-body", url="assets/full-body.png")
    headshot = ImageVariant(id="headshot", url="assets/headshot.png")
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        full_body_asset=ImageAsset(
            selected_id=full_body.id,
            variants=[full_body],
        ),
        headshot_asset=ImageAsset(
            selected_id=headshot.id,
            variants=[headshot],
        ),
        image_url=full_body.url,
    )
    series = _series("series-1", ["episode-1"], characters=[character])
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )
    (tmp_path / "projects.json").write_text(
        json.dumps({episode.id: episode.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    migrated = pipeline.series_store[series.id].characters[0]
    assert pipeline._selected_asset_unit_image_url(migrated.reference_sheet) == (
        full_body.url
    )

    pipeline.select_asset_variant(
        episode.id,
        migrated.id,
        "character",
        headshot.id,
        "headshot",
    )
    assert migrated.image_url == full_body.url
    assert migrated.headshot_image_url == headshot.url
    assert migrated.reference_sheet.selected_image_id == full_body.id
    assert pipeline._selected_asset_unit_image_url(migrated.reference_sheet) == (
        full_body.url
    )

    pipeline.video_generator.generate_i2v.return_value = {
        "video_url": "videos/full-body-motion.mp4"
    }
    pipeline.generate_motion_ref(
        episode.id,
        migrated.id,
        "full_body",
        prompt="Animate the full-body master",
    )
    assert pipeline.video_generator.generate_i2v.call_args.kwargs["image_url"] == (
        full_body.url
    )

    pipeline.delete_asset_variant(
        episode.id,
        migrated.id,
        "character",
        headshot.id,
    )
    assert migrated.image_url == full_body.url
    assert all(
        variant.url != headshot.url
        for variant in migrated.reference_sheet.image_variants
    )

    reloaded = _pipeline(tmp_path)
    saved = reloaded.series_store[series.id].characters[0]
    assert saved.image_url == full_body.url
    assert reloaded._selected_asset_unit_image_url(saved.reference_sheet) == (
        full_body.url
    )


def test_derived_character_upload_and_generation_preserve_existing_master(tmp_path):
    master = ImageVariant(id="master", url="assets/master-full-body.png")
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            selected_image_id=master.id,
            image_variants=[master],
        ),
        image_url=master.url,
        full_body_image_url=master.url,
    )
    series = _series("series-1", ["episode-1"], characters=[character])
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    pipeline = _pipeline(tmp_path)
    pipeline.series_store[series.id] = series
    pipeline.scripts[episode.id] = episode

    pipeline.add_uploaded_asset_variant(
        episode.id,
        "character",
        character.id,
        "head_shot",
        "assets/uploaded-headshot.png",
    )
    assert character.headshot_image_url == "assets/uploaded-headshot.png"
    assert character.image_url == master.url
    assert pipeline._selected_asset_unit_image_url(character.reference_sheet) == (
        master.url
    )

    generated = ImageVariant(
        id="generated-three-view",
        url="assets/generated-three-view.png",
    )

    def generate_character(target, **_kwargs):
        target.three_view_asset.variants = [generated]
        target.three_view_asset.selected_id = generated.id
        target.three_view_image_url = generated.url
        target.image_url = generated.url

    pipeline.asset_generator.generate_character.side_effect = generate_character
    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec"),
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        pipeline.generate_asset(
            episode.id,
            character.id,
            "character",
            generation_type="three_view",
        )

    assert character.three_view_image_url == generated.url
    assert character.image_url == master.url
    assert pipeline._selected_asset_unit_image_url(character.reference_sheet) == (
        master.url
    )
    reloaded = _pipeline(tmp_path)
    persisted = reloaded.series_store[series.id].characters[0]
    assert persisted.image_url == master.url
    assert reloaded._selected_asset_unit_image_url(persisted.reference_sheet) == (
        master.url
    )


def test_startup_normalization_keeps_derived_character_views_out_of_master_list(
    tmp_path,
):
    master = ImageVariant(id="master", url="assets/master.png")
    headshot = ImageVariant(id="headshot", url="assets/headshot.png")
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            selected_image_id=master.id,
            image_variants=[master],
        ),
        headshot_asset=ImageAsset(
            selected_id=headshot.id,
            variants=[headshot],
        ),
        image_url=master.url,
        headshot_image_url=headshot.url,
    )
    series = _series("series-1", ["episode-1"], characters=[character])
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )
    (tmp_path / "projects.json").write_text(
        json.dumps({episode.id: episode.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    migrated = pipeline.series_store[series.id].characters[0]
    assert [
        (variant.id, variant.url)
        for variant in migrated.reference_sheet.image_variants
    ] == [(master.id, master.url)]
    assert [
        (variant.id, variant.url) for variant in migrated.headshot_asset.variants
    ] == [(headshot.id, headshot.url)]

    with pytest.raises(ValueError, match="Variant headshot not found"):
        pipeline.select_asset_variant(
            episode.id,
            migrated.id,
            "character",
            headshot.id,
            "reference_sheet",
        )
    assert migrated.image_url == master.url


def test_startup_normalization_does_not_promote_legacy_three_view_alias(
    tmp_path,
):
    master = ImageVariant(id="master", url="assets/master.png")
    three_view = ImageVariant(id="three-view", url="assets/three-view.png")
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        full_body_asset=ImageAsset(
            selected_id=master.id,
            variants=[master],
        ),
        three_view_asset=ImageAsset(
            selected_id=three_view.id,
            variants=[three_view],
        ),
        # Legacy generators wrote the derived selection into both aliases.
        image_url=three_view.url,
        three_view_image_url=three_view.url,
    )
    series = _series("series-1", characters=[character])
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    migrated = pipeline.series_store[series.id].characters[0]
    assert migrated.image_url == master.url
    assert migrated.reference_sheet.selected_image_id == master.id
    assert [
        (variant.id, variant.url)
        for variant in migrated.reference_sheet.image_variants
    ] == [(master.id, master.url)]
    assert [
        (variant.id, variant.url)
        for variant in migrated.three_view_asset.variants
    ] == [(three_view.id, three_view.url)]

    reloaded = _pipeline(tmp_path).series_store[series.id].characters[0]
    assert reloaded.image_url == master.url
    assert reloaded.reference_sheet.selected_image_id == master.id


def test_startup_normalization_preserves_legacy_master_mirrored_to_avatar(
    tmp_path,
):
    master_url = "uploads/legacy-master.png"
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        # Generic legacy uploads mirrored the master into avatar_url without
        # creating any canonical full-body container.
        image_url=master_url,
        avatar_url=master_url,
    )
    series = _series("series-1", characters=[character])
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    migrated = pipeline.series_store[series.id].characters[0]
    assert migrated.image_url == master_url
    assert pipeline._selected_asset_unit_image_url(migrated.reference_sheet) == (
        master_url
    )
    assert [
        variant.url for variant in migrated.reference_sheet.image_variants
    ] == [master_url]

    reloaded = _pipeline(tmp_path).series_store[series.id].characters[0]
    assert reloaded.image_url == master_url
    assert pipeline._selected_asset_unit_image_url(reloaded.reference_sheet) == (
        master_url
    )


def test_startup_normalization_preserves_authoritative_same_url_metadata(tmp_path):
    shared_url = "assets/uploaded-master.png"
    plain_alias = ImageVariant(
        id="plain",
        url=shared_url,
        created_at=1,
    )
    authoritative = ImageVariant(
        id="selected-upload",
        url=shared_url,
        created_at=2,
        prompt_used="Authoritative uploaded prompt",
        is_favorited=True,
        is_uploaded_source=True,
        upload_type="full_body",
    )
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            image_variants=[plain_alias],
        ),
        full_body_asset=ImageAsset(
            selected_id=authoritative.id,
            variants=[authoritative],
        ),
        image_url=shared_url,
    )
    series = _series("series-1", characters=[character])
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    saved = pipeline.series_store[series.id].characters[0]
    assert len(saved.reference_sheet.image_variants) == 1
    retained = saved.reference_sheet.image_variants[0]
    assert retained.id == authoritative.id
    assert retained.prompt_used == authoritative.prompt_used
    assert retained.is_favorited is True
    assert retained.is_uploaded_source is True
    assert retained.upload_type == "full_body"
    assert saved.reference_sheet.selected_image_id == authoritative.id


def test_startup_image_normalization_is_idempotent_for_reused_variant_ids(
    tmp_path,
):
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            selected_image_id="reused-id",
            image_variants=[
                ImageVariant(id="reused-id", url="assets/master.png")
            ],
        ),
        full_body_asset=ImageAsset(
            selected_id="reused-id",
            variants=[
                ImageVariant(id="reused-id", url="assets/legacy-full-body.png")
            ],
        ),
        image_url="assets/master.png",
    )
    series = _series("series-1", characters=[character])
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )

    first = _pipeline(tmp_path)
    first_variants = first.series_store[series.id].characters[0].reference_sheet.image_variants
    first_snapshot = [(variant.id, variant.url) for variant in first_variants]
    assert {variant.url for variant in first_variants} == {
        "assets/master.png",
        "assets/legacy-full-body.png",
    }

    second = _pipeline(tmp_path)
    second_variants = second.series_store[series.id].characters[0].reference_sheet.image_variants
    second_snapshot = [(variant.id, variant.url) for variant in second_variants]
    third = _pipeline(tmp_path)
    third_variants = third.series_store[series.id].characters[0].reference_sheet.image_variants
    third_snapshot = [(variant.id, variant.url) for variant in third_variants]

    assert second_snapshot == first_snapshot
    assert third_snapshot == first_snapshot


def test_startup_image_normalization_resolves_deterministic_id_collisions(
    tmp_path,
):
    second_url = "assets/second.png"
    colliding_id = f"img_{uuid.uuid5(uuid.NAMESPACE_URL, second_url).hex[:12]}"
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            selected_image_id=colliding_id,
            image_variants=[
                ImageVariant(id=colliding_id, url="assets/first.png")
            ],
        ),
        full_body_asset=ImageAsset(
            selected_id=colliding_id,
            variants=[ImageVariant(id=colliding_id, url=second_url)],
        ),
        image_url="assets/first.png",
    )
    series = _series("series-1", characters=[character])
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    variants = pipeline.series_store[series.id].characters[0].reference_sheet.image_variants
    ids = [variant.id for variant in variants]
    assert len(ids) == len(set(ids)) == 2
    assert {variant.url for variant in variants} == {
        "assets/first.png",
        second_url,
    }


def test_variant_deletion_removes_same_url_aliases_from_all_image_containers(
    tmp_path,
):
    shared_url = "assets/duplicate-headshot.png"
    fallback = ImageVariant(id="fallback", url="assets/fallback.png")
    canonical_alias = ImageVariant(id="canonical-alias", url=shared_url)
    legacy_alias = ImageVariant(id="legacy-alias", url=shared_url)
    character = Character(
        id="series-character",
        name="Hero",
        description="Hero",
        reference_sheet=AssetUnit(
            selected_image_id=fallback.id,
            image_variants=[fallback, canonical_alias],
        ),
        headshot_asset=ImageAsset(
            selected_id=legacy_alias.id,
            variants=[legacy_alias],
        ),
        image_url=fallback.url,
    )
    series = _series("series-1", ["episode-1"], characters=[character])
    episode = _script(
        "episode-1",
        series_id=series.id,
        episode_number=1,
    )
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )
    (tmp_path / "projects.json").write_text(
        json.dumps({episode.id: episode.model_dump()}), encoding="utf-8"
    )

    pipeline = _pipeline(tmp_path)
    migrated = pipeline.series_store[series.id].characters[0]
    assert {
        variant.id
        for variant in migrated.reference_sheet.image_variants
        if variant.url == shared_url
    } == {canonical_alias.id}
    assert [variant.id for variant in migrated.headshot_asset.variants] == [
        legacy_alias.id
    ]

    pipeline.delete_asset_variant(
        episode.id,
        migrated.id,
        "character",
        legacy_alias.id,
    )

    for container in (
        migrated.reference_sheet,
        migrated.full_body,
        migrated.three_views,
        migrated.head_shot,
        migrated.full_body_asset,
        migrated.three_view_asset,
        migrated.headshot_asset,
    ):
        variants = getattr(container, "image_variants", None)
        if variants is None:
            variants = getattr(container, "variants", [])
        assert all(variant.url != shared_url for variant in variants)
    assert migrated.image_url == fallback.url

    reloaded = _pipeline(tmp_path)
    saved = reloaded.series_store[series.id].characters[0]
    assert all(
        variant.url != shared_url
        for variant in saved.reference_sheet.image_variants
    )
    assert saved.image_url == fallback.url


@pytest.mark.parametrize(
    ("asset_type", "collection_name", "asset"),
    [
        (
            "character",
            "characters",
            Character(
                id="character-1",
                name="Hero",
                description="Hero",
                image_url="assets/old-character.png",
            ),
        ),
        (
            "scene",
            "scenes",
            Scene(
                id="scene-1",
                name="Square",
                description="Square",
                image_url="assets/old-scene.png",
            ),
        ),
        (
            "prop",
            "props",
            Prop(
                id="prop-1",
                name="Key",
                description="Key",
                image_url="assets/old-prop.png",
            ),
        ),
    ],
)
def test_update_series_asset_image_updates_canonical_container_and_survives_reload(
    tmp_path,
    asset_type,
    collection_name,
    asset,
):
    series = _series("series-1", **{collection_name: [asset]})
    (tmp_path / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}), encoding="utf-8"
    )
    pipeline = _pipeline(tmp_path)
    new_url = f"assets/new-{asset_type}.png"

    pipeline.update_series_asset_image(
        series.id,
        asset.id,
        asset_type,
        new_url,
    )

    updated = getattr(pipeline.series_store[series.id], collection_name)[0]
    canonical = (
        updated.reference_sheet
        if asset_type == "character"
        else updated.image_asset
    )
    assert updated.image_url == new_url
    assert pipeline._selected_variant_url(canonical) == new_url

    reloaded = _pipeline(tmp_path)
    saved = getattr(reloaded.series_store[series.id], collection_name)[0]
    canonical = (
        saved.reference_sheet if asset_type == "character" else saved.image_asset
    )
    assert saved.image_url == new_url
    assert reloaded._selected_variant_url(canonical) == new_url


def test_exact_source_asset_patch_persists_metadata_and_prompts(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script(
        "episode-1",
        characters=[
            Character(
                id="project-character",
                name="Old project name",
                description="Old project description",
            )
        ],
    )
    pipeline.scripts[episode.id] = episode
    pipeline._save_data()
    global_scene = pipeline.create_library_asset(
        "scene",
        {"name": "Old global scene", "description": "Old global description"},
    )

    project_asset, project_type = pipeline.update_source_asset(
        "project",
        episode.id,
        "character",
        "project-character",
        attributes={
            "name": "Project hero",
            "description": "Updated locally",
            "age": "28",
        },
        prompts={"image_prompt": "Project hero reference sheet"},
    )
    global_asset, global_type = pipeline.update_source_asset(
        "global",
        "global",
        "scene",
        global_scene.id,
        attributes={
            "name": "Global plaza",
            "description": "A shared plaza",
            "time_of_day": "Dawn",
        },
        prompts={"image_prompt": "Wide dawn plaza establishing shot"},
    )

    assert project_type == "character"
    assert project_asset.reference_sheet.image_prompt == "Project hero reference sheet"
    assert project_asset.full_body_prompt == "Project hero reference sheet"
    assert global_type == "scene"
    assert global_asset.image_prompt == "Wide dawn plaza establishing shot"

    project_payload = pipeline.source_asset_response_payload(
        "project", episode.id, "character", project_asset.id
    )
    assert project_payload["asset_type"] == "character"
    assert project_payload["source"] == "project"
    assert project_payload["source_id"] == episode.id
    assert project_payload["episode_id"] == episode.id

    reloaded = _pipeline(tmp_path)
    reloaded_project = reloaded.scripts[episode.id].characters[0]
    reloaded_global = reloaded.library_store.scenes[0]
    assert reloaded_project.name == "Project hero"
    assert reloaded_project.age == "28"
    assert reloaded_project.reference_sheet.image_prompt == "Project hero reference sheet"
    assert reloaded_global.name == "Global plaza"
    assert reloaded_global.time_of_day == "Dawn"
    assert reloaded_global.image_prompt == "Wide dawn plaza establishing shot"


def test_global_asset_impact_counts_only_series_episodes(tmp_path):
    pipeline = _pipeline(tmp_path)
    shared_scene = pipeline.create_library_asset(
        "scene",
        {"name": "Shared plaza", "description": "A reusable location"},
    )
    pipeline.scripts["episode-1"] = _script(
        "episode-1",
        series_id="series-1",
        episode_number=1,
    )
    pipeline.scripts["standalone"] = _script("standalone")

    payload = pipeline.source_asset_response_payload(
        "global",
        "global",
        "scene",
        shared_scene.id,
    )

    assert payload["_editor_context"]["affectedEpisodeCount"] == 1


def test_source_asset_variant_actions_persist_to_global_owner(tmp_path):
    pipeline = _pipeline(tmp_path)
    character = pipeline.create_library_asset(
        "character",
        {
            "name": "Shared hero",
            "description": "Global reference",
            "image_url": "uploads/first.png",
        },
    )
    first_id = character.reference_sheet.selected_image_id
    second = ImageVariant(id="second", url="uploads/second.png")
    character.reference_sheet.image_variants.append(second)
    pipeline._save_library_data()

    pipeline.favorite_source_asset_variant(
        "global",
        "global",
        "character",
        character.id,
        second.id,
        True,
        "reference_sheet",
    )
    pipeline.select_source_asset_variant(
        "global",
        "global",
        "character",
        character.id,
        second.id,
        "reference_sheet",
    )
    pipeline.delete_source_asset_variant(
        "global", "global", "character", character.id, first_id
    )

    reloaded = _pipeline(tmp_path)
    saved = reloaded.library_store.characters[0]
    assert saved.reference_sheet.selected_image_id == second.id
    assert [variant.id for variant in saved.reference_sheet.image_variants] == [
        second.id
    ]
    assert saved.reference_sheet.image_variants[0].is_favorited is True
    assert saved.image_url == second.url


def test_asset_type_change_is_atomic_for_unreferenced_exact_owners(tmp_path):
    pipeline = _pipeline(tmp_path)
    convertible = pipeline.create_library_asset(
        "character",
        {
            "name": "Convertible",
            "description": "Unreferenced global asset",
            "image_url": "uploads/convertible.png",
        },
    )
    converted, effective_type = pipeline.update_source_asset(
        "global",
        "global",
        "character",
        convertible.id,
        target_asset_type="scene",
        attributes={"time_of_day": "Night"},
        prompts={"image_prompt": "Moonlit city square"},
    )
    assert effective_type == "scene"
    assert converted.id == convertible.id
    assert converted.image_url == "uploads/convertible.png"
    assert converted.time_of_day == "Night"
    assert pipeline.library_store.characters == []
    assert pipeline.library_store.scenes == [converted]

    project_character = Character(
        id="project-character", name="Local", description="Project owned"
    )
    episode = _script("episode-1", characters=[project_character])
    pipeline.scripts[episode.id] = episode
    project_prop, project_type = pipeline.update_source_asset(
        "project",
        episode.id,
        "character",
        project_character.id,
        target_asset_type="prop",
    )
    assert project_type == "prop"
    assert episode.characters == []
    assert episode.props == [project_prop]

    shared = Character(id="shared-character", name="Shared", description="Series owned")
    series = _series("series-1", ["series-episode"], characters=[shared])
    series_episode = _script(
        "series-episode", series_id=series.id, episode_number=1
    )
    pipeline.series_store[series.id] = series
    pipeline.scripts[series_episode.id] = series_episode
    series_scene, series_type = pipeline.update_source_asset(
        "series",
        series.id,
        "character",
        shared.id,
        target_asset_type="scene",
    )
    assert series_type == "scene"
    assert series.characters == []
    assert series.scenes == [series_scene]

    episode.frames.append(StoryboardFrame(id="frame-1", scene_id=converted.id))
    with pytest.raises(AssetTypeChangeConflictError):
        pipeline.update_source_asset(
            "global",
            "global",
            "scene",
            converted.id,
            target_asset_type="prop",
        )

    reloaded = _pipeline(tmp_path)
    assert reloaded.library_store.characters == []
    assert reloaded.library_store.scenes[0].id == converted.id
    assert reloaded.scripts[episode.id].props[0].id == project_character.id
    assert reloaded.series_store[series.id].scenes[0].id == shared.id


@pytest.mark.parametrize("source_kind", ["project", "series"])
def test_asset_type_change_rejects_referenced_project_and_series_assets(
    tmp_path, source_kind
):
    pipeline = _pipeline(tmp_path)
    character = Character(id="character-1", name="Hero", description="Referenced")
    if source_kind == "project":
        episode = _script(
            "episode-1",
            characters=[character],
            frames=[
                StoryboardFrame(
                    id="frame-1", scene_id="", character_ids=[character.id]
                )
            ],
        )
        pipeline.scripts[episode.id] = episode
        source_id = episode.id
    else:
        series = _series("series-1", ["episode-1"], characters=[character])
        episode = _script(
            "episode-1",
            series_id=series.id,
            episode_number=1,
            frames=[
                StoryboardFrame(
                    id="frame-1", scene_id="", character_ids=[character.id]
                )
            ],
        )
        pipeline.series_store[series.id] = series
        pipeline.scripts[episode.id] = episode
        source_id = series.id

    with pytest.raises(AssetTypeChangeConflictError) as exc_info:
        pipeline.update_source_asset(
            source_kind,
            source_id,
            "character",
            character.id,
            target_asset_type="prop",
        )

    assert exc_info.value.reason == "referenced"
    assert exc_info.value.references[0]["frame_id"] == "frame-1"


def test_asset_type_change_aggregates_all_character_image_media(tmp_path):
    pipeline = _pipeline(tmp_path)
    character = Character(
        id="character-1",
        name="Hero",
        description="Many legacy images",
        image_url="uploads/direct-primary.png",
        full_body_image_url="uploads/direct-body.png",
        three_view_image_url="uploads/direct-three.png",
        headshot_image_url="uploads/direct-head.png",
        avatar_url="uploads/direct-avatar.png",
        reference_sheet=AssetUnit(
            selected_image_id="reference",
            image_variants=[ImageVariant(id="reference", url="uploads/reference.png")],
        ),
        full_body=AssetUnit(
            selected_image_id="body",
            image_variants=[ImageVariant(id="body", url="uploads/body.png")],
        ),
        three_views=AssetUnit(
            selected_image_id="three",
            image_variants=[ImageVariant(id="three", url="uploads/three.png")],
        ),
        head_shot=AssetUnit(
            selected_image_id="head",
            image_variants=[ImageVariant(id="head", url="uploads/head.png")],
        ),
        full_body_asset=ImageAsset(
            selected_id="legacy-body",
            variants=[ImageVariant(id="legacy-body", url="uploads/legacy-body.png")],
        ),
        three_view_asset=ImageAsset(
            selected_id="legacy-three",
            variants=[ImageVariant(id="legacy-three", url="uploads/legacy-three.png")],
        ),
        headshot_asset=ImageAsset(
            selected_id="legacy-head",
            variants=[ImageVariant(id="legacy-head", url="uploads/legacy-head.png")],
        ),
    )
    pipeline.library_store.characters.append(character)

    scene, effective_type = pipeline.update_source_asset(
        "global",
        "global",
        "character",
        character.id,
        target_asset_type="scene",
    )

    assert effective_type == "scene"
    expected_urls = {
        "uploads/reference.png",
        "uploads/body.png",
        "uploads/three.png",
        "uploads/head.png",
        "uploads/legacy-body.png",
        "uploads/legacy-three.png",
        "uploads/legacy-head.png",
        "uploads/direct-primary.png",
        "uploads/direct-body.png",
        "uploads/direct-three.png",
        "uploads/direct-head.png",
        "uploads/direct-avatar.png",
    }
    assert {variant.url for variant in scene.image_asset.variants} == expected_urls
    assert scene.image_url == "uploads/direct-primary.png"
    assert pipeline._selected_variant_url(scene.image_asset) == scene.image_url
    reloaded = _pipeline(tmp_path)
    assert {
        variant.url for variant in reloaded.library_store.scenes[0].image_asset.variants
    } == expected_urls


@pytest.mark.parametrize(
    ("asset", "asset_type", "target_type", "unsupported_field"),
    [
        (
            Character(
                id="character-1",
                name="Hero",
                description="Has motion",
                reference_sheet=AssetUnit(
                    video_variants=[VideoVariant(id="motion-1", url="uploads/motion.mp4")]
                ),
            ),
            "character",
            "scene",
            "reference_sheet.video_variants",
        ),
        (
            Prop(
                id="prop-1",
                name="Radio",
                description="Has audio",
                audio_url="uploads/radio.mp3",
            ),
            "prop",
            "character",
            "audio_url",
        ),
    ],
)
def test_asset_type_change_rejects_unsupported_motion_or_audio_media(
    tmp_path, asset, asset_type, target_type, unsupported_field
):
    pipeline = _pipeline(tmp_path)
    pipeline._asset_list_for_owner(pipeline.library_store, asset_type).append(asset)

    with pytest.raises(AssetTypeChangeConflictError) as exc_info:
        pipeline.update_source_asset(
            "global",
            "global",
            asset_type,
            asset.id,
            target_asset_type=target_type,
        )

    assert exc_info.value.reason == "unsupported_media"
    assert unsupported_field in exc_info.value.unsupported_media
    assert pipeline._asset_list_for_owner(pipeline.library_store, asset_type) == [asset]


@pytest.mark.parametrize("source_kind", ["project", "series", "global"])
def test_restore_asset_reservation_persists_exact_previous_status(
    tmp_path, source_kind
):
    pipeline = _pipeline(tmp_path)
    character = Character(
        id="character-1",
        name="Hero",
        description="Reserved asset",
        status=GenerationStatus.PROCESSING,
    )
    if source_kind == "project":
        owner = _script("episode-1", characters=[character])
        pipeline.scripts[owner.id] = owner
        restored = pipeline.restore_asset_reservation(
            owner.id, character.id, "character", "completed"
        )
    elif source_kind == "series":
        owner = _series("series-1", characters=[character])
        pipeline.series_store[owner.id] = owner
        restored = pipeline.restore_source_asset_reservation(
            "series", owner.id, character.id, "character", "completed"
        )
    else:
        pipeline.library_store.characters.append(character)
        restored = pipeline.restore_source_asset_reservation(
            "global", "global", character.id, "character", "completed"
        )

    assert restored is True
    assert character.status is GenerationStatus.COMPLETED
    reloaded = _pipeline(tmp_path)
    if source_kind == "project":
        saved = reloaded.scripts[owner.id].characters[0]
    elif source_kind == "series":
        saved = reloaded.series_store[owner.id].characters[0]
    else:
        saved = reloaded.library_store.characters[0]
    assert saved.status is GenerationStatus.COMPLETED

    # A late cleanup must not overwrite a result that has already changed.
    if source_kind == "project":
        late_restore = pipeline.restore_asset_reservation(
            owner.id, character.id, "character", "pending"
        )
    elif source_kind == "series":
        late_restore = pipeline.restore_source_asset_reservation(
            "series", owner.id, character.id, "character", "pending"
        )
    else:
        late_restore = pipeline.restore_source_asset_reservation(
            "global", "global", character.id, "character", "pending"
        )
    assert late_restore is False
    assert character.status is GenerationStatus.COMPLETED


def test_global_generation_task_persists_prompt_variants_and_status(tmp_path):
    pipeline = _pipeline(tmp_path)
    scene = pipeline.create_library_asset(
        "scene", {"name": "Square", "description": "Open plaza"}
    )

    def generate_scene(target, **kwargs):
        target.image_prompt = kwargs["prompt"]
        target.image_asset.variants.insert(
            0,
            ImageVariant(
                id="generated",
                url="uploads/generated.png",
                prompt_used=kwargs["prompt"],
            ),
        )
        target.image_asset.selected_id = "generated"
        target.image_url = "uploads/generated.png"
        target.status = GenerationStatus.COMPLETED

    pipeline.asset_generator.generate_scene.side_effect = generate_scene
    with (
        patch("src.apps.comic_gen.pipeline.get_selected_model", return_value="image-model"),
        patch("src.apps.comic_gen.pipeline.get_model_spec") as get_model_spec,
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        get_model_spec.return_value.model_id = "image-model"
        _, task_id = pipeline.generate_global_asset(
            scene.id,
            "scene",
            prompt="A lively square at sunrise",
            aspect_ratio="16:9",
            task_id="global-source-task",
        )
        pipeline.process_asset_generation_task(task_id)

    status = pipeline.get_asset_generation_task_status(task_id)
    assert status["status"] == "completed"
    assert status["asset_source"] == "global"
    reloaded = _pipeline(tmp_path)
    saved = reloaded.library_store.scenes[0]
    assert saved.status is GenerationStatus.COMPLETED
    assert saved.image_prompt == "A lively square at sunrise"
    assert saved.image_asset.selected_id == "generated"
    assert saved.image_url == "uploads/generated.png"


def test_reference_sheet_favorites_persist_to_series_and_global_owners(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1", series_id="series-1", episode_number=1)
    series_character = Character(
        id="series-character",
        name="Series Character",
        description="Shared by the series",
    )
    series_variant = ImageVariant(
        id="series-reference",
        url="uploads/series-reference.png",
    )
    series_character.reference_sheet.image_variants = [series_variant]
    series_character.reference_sheet.selected_image_id = series_variant.id
    series = _series(
        "series-1",
        [episode.id],
        characters=[series_character],
    )
    pipeline.scripts[episode.id] = episode
    pipeline.series_store[series.id] = series

    global_character = pipeline.create_library_asset(
        "character",
        {
            "name": "Global Character",
            "description": "Shared globally",
            "image_url": "uploads/global-reference.png",
        },
    )
    global_variant_id = global_character.reference_sheet.selected_image_id

    pipeline.toggle_variant_favorite(
        episode.id,
        series_character.id,
        "character",
        series_variant.id,
        True,
        "reference_sheet",
    )
    pipeline.toggle_variant_favorite(
        episode.id,
        global_character.id,
        "character",
        global_variant_id,
        True,
        "reference_sheet",
    )

    assert series_variant.is_favorited is True
    assert global_character.reference_sheet.image_variants[0].is_favorited is True

    reloaded = _pipeline(tmp_path)
    persisted_series_variant = (
        reloaded.series_store[series.id]
        .characters[0]
        .reference_sheet.image_variants[0]
    )
    persisted_global_variant = (
        reloaded.library_store.characters[0]
        .reference_sheet.image_variants[0]
    )
    assert persisted_series_variant.is_favorited is True
    assert persisted_global_variant.is_favorited is True


def test_motion_reference_uses_canonical_image_and_persists_shared_owners(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1", series_id="series-1", episode_number=1)
    series_character = Character(
        id="series-character",
        name="Series Character",
        description="Shared by the series",
        image_url="uploads/series-legacy.png",
        full_body_image_url="uploads/series-legacy.png",
    )
    series_variant = ImageVariant(
        id="series-reference",
        url="uploads/series-canonical.png",
    )
    series_character.reference_sheet.image_variants = [series_variant]
    series_character.reference_sheet.selected_image_id = series_variant.id
    series_headshot_variant = ImageVariant(
        id="series-headshot",
        url="uploads/series-headshot.png",
    )
    series_character.head_shot.image_variants = [series_headshot_variant]
    series_character.head_shot.selected_image_id = series_headshot_variant.id
    series = _series(
        "series-1",
        [episode.id],
        characters=[series_character],
    )
    pipeline.scripts[episode.id] = episode
    pipeline.series_store[series.id] = series

    global_character = pipeline.create_library_asset(
        "character",
        {
            "name": "Global Character",
            "description": "Shared globally",
            "image_url": "uploads/global-canonical.png",
        },
    )
    global_character.full_body_image_url = "uploads/global-legacy.png"
    pipeline.video_generator.generate_i2v.side_effect = [
        {"video_url": "videos/series-motion.mp4"},
        {"video_url": "videos/series-headshot-motion.mp4"},
        {"video_url": "videos/global-motion.mp4"},
    ]

    pipeline.generate_motion_ref(
        episode.id,
        series_character.id,
        "full_body",
        prompt="Animate the series character",
    )
    pipeline.generate_motion_ref(
        episode.id,
        series_character.id,
        "head_shot",
        prompt="Animate the series portrait",
    )
    pipeline.generate_motion_ref(
        episode.id,
        global_character.id,
        "full_body",
        prompt="Animate the global character",
    )

    calls = pipeline.video_generator.generate_i2v.call_args_list
    assert calls[0].kwargs["image_url"] == "uploads/series-canonical.png"
    assert calls[1].kwargs["image_url"] == "uploads/series-headshot.png"
    assert calls[2].kwargs["image_url"] == "uploads/global-canonical.png"
    assert series_character.full_body.video_variants[0].url == (
        "videos/series-motion.mp4"
    )
    assert series_character.head_shot.video_variants[0].url == (
        "videos/series-headshot-motion.mp4"
    )
    assert global_character.full_body.video_variants[0].url == (
        "videos/global-motion.mp4"
    )

    reloaded = _pipeline(tmp_path)
    persisted_series_character = reloaded.series_store[series.id].characters[0]
    persisted_global_character = reloaded.library_store.characters[0]
    assert persisted_series_character.full_body.video_variants[0].url == (
        "videos/series-motion.mp4"
    )
    assert persisted_series_character.head_shot.video_variants[0].url == (
        "videos/series-headshot-motion.mp4"
    )
    assert persisted_global_character.full_body.video_variants[0].url == (
        "videos/global-motion.mp4"
    )


def test_motion_reference_task_preflights_shared_asset_and_source_image(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1", series_id="series-1", episode_number=1)
    shared_character = Character(
        id="shared-character",
        name="Shared Character",
        description="Owned by the parent series",
        headshot_image_url="uploads/shared-headshot.png",
    )
    series = _series(
        "series-1",
        [episode.id],
        characters=[shared_character],
    )
    pipeline.scripts[episode.id] = episode
    pipeline.series_store[series.id] = series

    with (
        patch("src.apps.comic_gen.pipeline.get_model_spec") as get_model_spec,
        patch("src.apps.comic_gen.pipeline.resolve_model_api_key"),
    ):
        get_model_spec.return_value.model_id = "video-model"
        _, task_id = pipeline.create_motion_ref_task(
            episode.id,
            shared_character.id,
            "head_shot",
        )

        assert pipeline.video_generation_tasks[task_id]["asset_id"] == (
            shared_character.id
        )

        with pytest.raises(ValueError, match="Character missing-character not found"):
            pipeline.create_motion_ref_task(
                episode.id,
                "missing-character",
                "head_shot",
            )

    shared_character.headshot_image_url = None
    with pytest.raises(ValueError, match="No source image available for head_shot"):
        pipeline.create_motion_ref_task(
            episode.id,
            shared_character.id,
            "head_shot",
        )


def test_motion_reference_replacement_is_used_after_provider_safety_rejection(tmp_path):
    pipeline = _pipeline(tmp_path)
    episode = _script("episode-1")
    original = ImageVariant(id="original", url="uploads/original.png")
    replacement = ImageVariant(id="replacement", url="uploads/fictional.png")
    character = Character(
        id="character-1",
        name="Fictional Hero",
        description="An illustrated fantasy character",
    )
    character.reference_sheet.image_variants = [original, replacement]
    character.reference_sheet.selected_image_id = original.id
    episode.characters = [character]
    pipeline.scripts[episode.id] = episode
    pipeline.video_generator.generate_i2v.side_effect = [
        NewAPIProviderError(
            INPUT_IMAGE_PRIVACY_PUBLIC_MESSAGE,
            error_code=INPUT_IMAGE_PRIVACY_ERROR_CODE,
            provider_code="InputImageSensitiveContentDetected.PrivacyInformation",
            provider_message="The input image may contain a real person",
            http_status=400,
            phase="video submission",
        ),
        {"video_url": "videos/retry-succeeded.mp4"},
    ]

    with pytest.raises(NewAPIProviderError):
        pipeline.generate_motion_ref(episode.id, character.id, "full_body")

    character.reference_sheet.selected_image_id = replacement.id
    pipeline.generate_motion_ref(episode.id, character.id, "full_body")

    calls = pipeline.video_generator.generate_i2v.call_args_list
    assert calls[0].kwargs["image_url"] == original.url
    assert calls[1].kwargs["image_url"] == replacement.url
    assert FICTIONAL_CHARACTER_PROMPT_NOTICE in calls[1].kwargs["prompt"]
    assert character.full_body.video_variants[0].url == "videos/retry-succeeded.mp4"
