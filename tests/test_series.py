"""Tests for Series functionality (Phase 3).

Covers: Models, Pipeline CRUD, Episode association, asset resolution,
PromptConfig three-level fallback, text splitting, and cross-series import.
"""

import json
import time
import uuid
import pytest
from unittest.mock import patch, MagicMock

from src.apps.comic_gen.models import (
    Series,
    Script,
    Character,
    Scene,
    Prop,
    PromptConfig,
    ModelSettings,
    StoryboardFrame,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline(tmp_path):
    """Create a pipeline with temp data files, bypassing real IO."""
    with (
        patch("src.apps.comic_gen.pipeline.ScriptProcessor"),
        patch("src.apps.comic_gen.pipeline.AssetGenerator"),
        patch("src.apps.comic_gen.pipeline.StoryboardGenerator"),
        patch("src.apps.comic_gen.pipeline.VideoGenerator"),
        patch("src.apps.comic_gen.pipeline.ExportManager"),
    ):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.scripts = {}
    p.series_store = {}
    return p


def _make_script(title="Episode 1", text="Some text", **overrides) -> Script:
    """Helper to create a Script with sensible defaults."""
    now = time.time()
    defaults = dict(
        id=str(uuid.uuid4()),
        title=title,
        original_text=text,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Script(**defaults)


def _make_character(name="Hero", **kw) -> Character:
    return Character(
        id=kw.pop("id", str(uuid.uuid4())),
        name=name,
        description=kw.pop("description", "A brave hero"),
        **kw,
    )


def _make_scene(name="Forest", **kw) -> Scene:
    return Scene(
        id=kw.pop("id", str(uuid.uuid4())),
        name=name,
        description=kw.pop("description", "A dark forest"),
        **kw,
    )


def _make_prop(name="Sword", **kw) -> Prop:
    return Prop(
        id=kw.pop("id", str(uuid.uuid4())),
        name=name,
        description=kw.pop("description", "A magic sword"),
        **kw,
    )


# ===================================================================
# 1. Models tests
# ===================================================================


class TestModels:
    def test_series_defaults(self):
        now = time.time()
        s = Series(id="s1", title="My Series", created_at=now, updated_at=now)
        assert s.id == "s1"
        assert s.title == "My Series"
        assert s.description == ""
        assert s.characters == []
        assert s.scenes == []
        assert s.props == []
        assert s.art_direction is None
        assert isinstance(s.prompt_config, PromptConfig)
        assert isinstance(s.model_settings, ModelSettings)
        assert s.episode_ids == []
        assert s.created_at > 0
        assert s.updated_at > 0

    def test_script_series_fields_default_none(self):
        sc = _make_script()
        assert sc.series_id is None
        assert sc.episode_number is None


# ===================================================================
# 2. Pipeline CRUD tests
# ===================================================================


class TestSeriesCRUD:
    def test_create_series(self, pipeline):
        s = pipeline.create_series("Title A", "desc A")
        assert s.id in pipeline.series_store
        assert s.title == "Title A"
        assert s.description == "desc A"
        assert s.created_at > 0
        assert s.updated_at > 0

    def test_create_series_persists_creation_defaults_with_exact_provenance(self, pipeline):
        settings = ModelSettings.model_validate(
            {
                "chat_model": "deepseek-v4-pro",
                "storyboard_aspect_ratio": "9:16",
            }
        )
        prompt_config = PromptConfig(entity_extraction="series entity extraction")

        series = pipeline.create_series(
            "Configured Series",
            model_settings=settings,
            prompt_config=prompt_config,
        )

        assert series.model_settings.chat_model == "deepseek-v4-pro"
        assert series.model_settings_overrides == [
            "chat_model",
            "storyboard_aspect_ratio",
        ]
        assert series.prompt_config.entity_extraction == "series entity extraction"

    def test_get_series_exists(self, pipeline):
        s = pipeline.create_series("X")
        assert pipeline.get_series(s.id) is s

    def test_get_series_not_found(self, pipeline):
        assert pipeline.get_series("nonexistent") is None

    def test_list_series_empty(self, pipeline):
        assert pipeline.list_series() == []

    def test_list_series_multiple(self, pipeline):
        pipeline.create_series("A")
        pipeline.create_series("B")
        assert len(pipeline.list_series()) == 2

    def test_update_series_title_and_description(self, pipeline):
        s = pipeline.create_series("Old")
        old_updated = s.updated_at
        updated = pipeline.update_series(s.id, {"title": "New", "description": "new desc"})
        assert updated.title == "New"
        assert updated.description == "new desc"
        assert updated.updated_at >= old_updated

    def test_update_series_episode_ids_not_overwritten(self, pipeline):
        s = pipeline.create_series("X")
        s.episode_ids = ["ep1"]
        pipeline.update_series(s.id, {"episode_ids": ["should_not_change"]})
        assert s.episode_ids == ["ep1"]

    def test_update_series_not_found(self, pipeline):
        with pytest.raises(ValueError, match="Series not found"):
            pipeline.update_series("missing", {"title": "X"})

    def test_delete_series_clears_episodes(self, pipeline):
        s = pipeline.create_series("ToDelete")
        ep = _make_script(title="Ep1")
        pipeline.scripts[ep.id] = ep
        pipeline.add_episode_to_series(s.id, ep.id)
        assert ep.series_id == s.id

        pipeline.delete_series(s.id)
        assert s.id not in pipeline.series_store
        assert ep.series_id is None
        assert ep.episode_number is None

    def test_delete_entire_series_removes_all_owned_episodes(self, pipeline):
        series = pipeline.create_series("Delete Everything")
        listed_episode = _make_script(title="Listed episode")
        recovered_episode = _make_script(
            title="Recovered association",
            series_id=series.id,
            episode_number=2,
        )
        unrelated = _make_script(title="Keep me")
        pipeline.scripts = {
            listed_episode.id: listed_episode,
            recovered_episode.id: recovered_episode,
            unrelated.id: unrelated,
        }
        pipeline.add_episode_to_series(series.id, listed_episode.id, episode_number=1)

        deleted = pipeline.delete_series(series.id, delete_episodes=True)

        assert {episode.id for episode in deleted} == {
            listed_episode.id,
            recovered_episode.id,
        }
        assert series.id not in pipeline.series_store
        assert listed_episode.id not in pipeline.scripts
        assert recovered_episode.id not in pipeline.scripts
        assert pipeline.scripts[unrelated.id] is unrelated

        with open(pipeline.data_file, encoding="utf-8") as project_file:
            persisted_projects = json.load(project_file)
        with open(pipeline.series_data_file, encoding="utf-8") as series_file:
            persisted_series = json.load(series_file)

        assert listed_episode.id not in persisted_projects
        assert recovered_episode.id not in persisted_projects
        assert unrelated.id in persisted_projects
        assert series.id not in persisted_series

    def test_delete_entire_series_api_reclaims_series_and_episode_media(
        self, pipeline, monkeypatch
    ):
        from src.apps.comic_gen import api as comic_api

        series = pipeline.create_series("Delete through API")
        episode = _make_script(title="Episode")
        pipeline.scripts[episode.id] = episode
        pipeline.add_episode_to_series(series.id, episode.id)
        reclaimed_snapshots = []

        monkeypatch.setattr(comic_api, "pipeline", pipeline)
        monkeypatch.setattr(
            comic_api,
            "reclaim_deleted_media",
            lambda snapshot: reclaimed_snapshots.append(snapshot) or ["one", "two"],
        )

        response = comic_api.delete_series(series.id, delete_episodes=True)

        assert response["status"] == "deleted"
        assert response["deleted_episode_count"] == 1
        assert response["deleted_episode_ids"] == [episode.id]
        assert response["reclaimed_media_files"] == 2
        assert reclaimed_snapshots[0]["series"]["id"] == series.id
        assert reclaimed_snapshots[0]["episodes"][0]["id"] == episode.id
        assert series.id not in pipeline.series_store
        assert episode.id not in pipeline.scripts

    def test_delete_series_not_found(self, pipeline):
        with pytest.raises(ValueError, match="Series not found"):
            pipeline.delete_series("missing")

    def test_update_series_model_settings_api_returns_settings_only(self, pipeline, monkeypatch):
        from src.apps.comic_gen import api as comic_api

        series = pipeline.create_series("Settings response")
        monkeypatch.setattr(comic_api, "pipeline", pipeline)

        response = comic_api.update_series_model_settings(
            series.id,
            comic_api.UpdateModelSettingsRequest(chat_model="qwen3.7-max"),
        )

        assert response["chat_model"] == "qwen3.7-max"
        assert response["image_model"] == series.model_settings.image_model
        assert response["video_model"] == series.model_settings.video_model
        assert response["model_settings_overrides"] == ["chat_model"]
        assert "id" not in response
        assert "characters" not in response
        assert series.model_settings_overrides == ["chat_model"]

    def test_series_model_setting_null_clears_only_that_override(self, pipeline, monkeypatch):
        from src.apps.comic_gen import api as comic_api

        series = pipeline.create_series(
            "Settings inheritance",
            model_settings=ModelSettings(
                chat_model="qwen3.7-max",
                storyboard_aspect_ratio="9:16",
            ),
        )
        monkeypatch.setattr(comic_api, "pipeline", pipeline)

        response = comic_api.update_series_model_settings(
            series.id,
            comic_api.UpdateModelSettingsRequest(chat_model=None),
        )

        assert series.model_settings_overrides == ["storyboard_aspect_ratio"]
        assert response["model_settings_overrides"] == ["storyboard_aspect_ratio"]
        assert response["storyboard_aspect_ratio"] == "9:16"


# ===================================================================
# 3. Episode association tests
# ===================================================================


class TestEpisodeAssociation:
    def test_add_episode_to_series(self, pipeline):
        s = pipeline.create_series("S")
        ep = _make_script()
        pipeline.scripts[ep.id] = ep

        result = pipeline.add_episode_to_series(s.id, ep.id)
        assert ep.id in result.episode_ids
        assert ep.series_id == s.id
        assert ep.episode_number == 1

    def test_add_episode_reassign_from_old_series(self, pipeline):
        s1 = pipeline.create_series("S1")
        s2 = pipeline.create_series("S2")
        ep = _make_script()
        pipeline.scripts[ep.id] = ep

        pipeline.add_episode_to_series(s1.id, ep.id)
        assert ep.id in s1.episode_ids

        pipeline.add_episode_to_series(s2.id, ep.id)
        assert ep.id not in s1.episode_ids
        assert ep.id in s2.episode_ids
        assert ep.series_id == s2.id

    def test_remove_episode_from_series(self, pipeline):
        s = pipeline.create_series("S")
        ep = _make_script()
        pipeline.scripts[ep.id] = ep
        pipeline.add_episode_to_series(s.id, ep.id)

        pipeline.remove_episode_from_series(s.id, ep.id)
        assert ep.id not in s.episode_ids
        assert ep.series_id is None
        assert ep.episode_number is None

    def test_get_series_episodes_order(self, pipeline):
        s = pipeline.create_series("S")
        ep1 = _make_script(title="Ep1")
        ep2 = _make_script(title="Ep2")
        pipeline.scripts[ep1.id] = ep1
        pipeline.scripts[ep2.id] = ep2

        pipeline.add_episode_to_series(s.id, ep1.id, episode_number=1)
        pipeline.add_episode_to_series(s.id, ep2.id, episode_number=2)

        episodes = pipeline.get_series_episodes(s.id)
        assert len(episodes) == 2
        assert episodes[0].title == "Ep1"
        assert episodes[1].title == "Ep2"


# ===================================================================
# 4. Asset resolution tests
# ===================================================================


class TestResolveEpisodeAssets:
    def test_no_series_returns_local(self, pipeline):
        ep = _make_script()
        char = _make_character()
        ep.characters = [char]
        result = pipeline.resolve_episode_assets(ep)
        assert result["characters"] == [char]
        assert result["scenes"] == []
        assert result["props"] == []

    def test_merge_series_and_episode_local_priority(self, pipeline):
        shared_id = "shared-char"
        series_char = _make_character(name="Series Hero", id=shared_id)
        series_scene = _make_scene(name="Series Forest", id="series-scene")
        now = time.time()
        series = Series(
            id="s1",
            title="S",
            characters=[series_char],
            scenes=[series_scene],
            created_at=now,
            updated_at=now,
        )

        ep_char = _make_character(name="Episode Hero", id=shared_id)  # same ID → local wins
        ep = _make_script(characters=[ep_char])

        result = pipeline.resolve_episode_assets(ep, series=series)
        # Episode char with same ID should take priority
        assert len(result["characters"]) == 1
        assert result["characters"][0].name == "Episode Hero"
        # Series scene should be included
        assert len(result["scenes"]) == 1
        assert result["scenes"][0].name == "Series Forest"

    def test_auto_lookup_series_via_episode_series_id(self, pipeline):
        s = pipeline.create_series("S")
        series_prop = _make_prop(name="Series Sword")
        s.props = [series_prop]

        ep = _make_script(series_id=s.id)
        pipeline.scripts[ep.id] = ep

        result = pipeline.resolve_episode_assets(ep)
        assert len(result["props"]) == 1
        assert result["props"][0].name == "Series Sword"


# ===================================================================
# 5. PromptConfig three-level fallback tests
# ===================================================================


class TestGetEffectivePrompt:
    def test_episode_custom_takes_priority(self, pipeline):
        ep = _make_script()
        ep.prompt_config = PromptConfig(storyboard_polish="EP custom prompt")
        now = time.time()
        series = Series(
            id="s1",
            title="S",
            prompt_config=PromptConfig(storyboard_polish="Series prompt"),
            created_at=now,
            updated_at=now,
        )

        result = pipeline.get_effective_prompt("storyboard_polish", ep, series)
        assert result == "EP custom prompt"

    def test_fallback_to_series(self, pipeline):
        ep = _make_script()
        ep.prompt_config = PromptConfig(storyboard_polish="")
        now = time.time()
        series = Series(
            id="s1",
            title="S",
            prompt_config=PromptConfig(storyboard_polish="Series prompt"),
            created_at=now,
            updated_at=now,
        )

        result = pipeline.get_effective_prompt("storyboard_polish", ep, series)
        assert result == "Series prompt"

    def test_fallback_to_system_default(self, pipeline):
        ep = _make_script()
        ep.prompt_config = PromptConfig()
        now = time.time()
        series = Series(
            id="s1", title="S", prompt_config=PromptConfig(), created_at=now, updated_at=now
        )

        result = pipeline.get_effective_prompt("storyboard_polish", ep, series)
        # Should return the DEFAULT_STORYBOARD_POLISH_PROMPT (non-empty string)
        assert len(result.strip()) > 0

    def test_invalid_prompt_type_raises_error(self, pipeline):
        """Invalid prompt_type raises ValueError."""
        ep = _make_script()
        with pytest.raises(ValueError, match="Invalid prompt_type"):
            pipeline.get_effective_prompt("nonexistent_type", ep)

    def test_polish_model_round_trips_with_prompt_config(self):
        config = PromptConfig(polish_model="qwen3.7-max")
        assert PromptConfig.model_validate(config.model_dump()).polish_model == "qwen3.7-max"


class TestEpisodeChatModelRouting:
    def test_default_project_settings_inherit_series_chat_model(self, pipeline):
        series = Series(
            id="series-model",
            title="Series",
            model_settings=ModelSettings(chat_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        script = _make_script(series_id=series.id)
        pipeline.series_store[series.id] = series

        assert pipeline._effective_chat_model(script) == "qwen3.7-max"

    def test_project_chat_override_beats_series_chat_model(self, pipeline):
        series = Series(
            id="series-model",
            title="Series",
            model_settings=ModelSettings(chat_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        script = _make_script(
            series_id=series.id,
            model_settings=ModelSettings(chat_model="deepseek-v4-pro"),
        )
        pipeline.series_store[series.id] = series

        assert pipeline._effective_chat_model(script) == "deepseek-v4-pro"

    def test_explicit_default_project_choice_persists_and_beats_series(self, pipeline):
        series = Series(
            id="series-model",
            title="Series",
            model_settings=ModelSettings(chat_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        script = _make_script(series_id=series.id)
        pipeline.series_store[series.id] = series
        pipeline.scripts[script.id] = script

        pipeline.update_model_settings(
            script.id,
            chat_model="deepseek-v4-flash",
        )
        reloaded = Script.model_validate(script.model_dump())

        assert reloaded.model_settings_overrides == ["chat_model"]
        assert pipeline._effective_chat_model(reloaded) == "deepseek-v4-flash"

    def test_project_model_setting_null_reverts_to_series_inheritance(self, pipeline, monkeypatch):
        from src.apps.comic_gen import api as comic_api

        series = pipeline.create_series(
            "Series",
            model_settings=ModelSettings(chat_model="qwen3.7-max"),
        )
        script = _make_script(
            series_id=series.id,
            model_settings=ModelSettings(
                chat_model="deepseek-v4-pro",
                storyboard_aspect_ratio="9:16",
            ),
        )
        pipeline.scripts[script.id] = script
        monkeypatch.setattr(comic_api, "pipeline", pipeline)

        response = comic_api.update_model_settings(
            script.id,
            comic_api.UpdateModelSettingsRequest(chat_model=None),
        )
        payload = json.loads(response.body)

        assert script.model_settings_overrides == ["storyboard_aspect_ratio"]
        assert pipeline._effective_chat_model(script) == "qwen3.7-max"
        assert payload["model_settings"]["chat_model"] == "qwen3.7-max"
        assert payload["model_settings_overrides"] == ["storyboard_aspect_ratio"]

    def test_untouched_project_and_series_settings_use_global_model(self, pipeline, monkeypatch):
        monkeypatch.setenv("NEWAPI_CHAT_MODEL", "qwen3.7-max")
        series = Series(
            id="series-default",
            title="Series",
            created_at=time.time(),
            updated_at=time.time(),
        )
        script = _make_script(series_id=series.id)
        pipeline.series_store[series.id] = series

        assert pipeline._effective_chat_model(script) == "qwen3.7-max"

    def test_existing_episode_uses_live_series_models_and_aspect_ratios(
        self, pipeline, monkeypatch
    ):
        from src.apps.comic_gen import api as comic_api

        series = pipeline.create_series("Series")
        script = _make_script(series_id=series.id)
        pipeline.scripts[script.id] = script
        pipeline.add_episode_to_series(series.id, script.id, episode_number=1)
        monkeypatch.setattr(comic_api, "pipeline", pipeline)

        comic_api.update_series_model_settings(
            series.id,
            comic_api.UpdateModelSettingsRequest(
                video_model="doubao-seedance-2-0-mini-260615",
                storyboard_aspect_ratio="9:16",
            ),
        )
        effective = pipeline._effective_model_settings(script)

        # The existing episode still carries its old snapshot, proving this is
        # live inheritance rather than one-time copying.
        assert script.model_settings.video_model == "doubao-seedance-2-0-fast-260128"
        assert script.model_settings.storyboard_aspect_ratio == "16:9"
        assert effective.video_model == "doubao-seedance-2-0-mini-260615"
        assert effective.storyboard_aspect_ratio == "9:16"

        pipeline.video_generator.generate_video.return_value = script
        pipeline.generate_video(script.id)
        pipeline.video_generator.generate_video.assert_called_once_with(
            script,
            model_id="doubao-seedance-2-0-mini-260615",
        )

        # An explicit project choice of the catalog default remains an
        # override even though its value looks identical to an untouched one.
        pipeline.update_model_settings(
            script.id,
            video_model="doubao-seedance-2-0-fast-260128",
            storyboard_aspect_ratio="16:9",
        )
        project_effective = pipeline._effective_model_settings(script)
        assert project_effective.video_model == "doubao-seedance-2-0-fast-260128"
        assert project_effective.storyboard_aspect_ratio == "16:9"

    def test_project_creation_binds_series_before_initial_extraction(self, pipeline):
        series = Series(
            id="series-create",
            title="Series",
            prompt_config=PromptConfig(entity_extraction="series extraction"),
            model_settings=ModelSettings(
                chat_model="qwen3.7-max",
                video_model="doubao-seedance-2-0-mini-260615",
            ),
            created_at=time.time(),
            updated_at=time.time(),
        )
        draft = _make_script(title="Draft", text="source")
        parsed = _make_script(title="Parsed", text="source")
        pipeline.series_store[series.id] = series
        pipeline.script_processor.create_draft_script.return_value = draft
        pipeline.script_processor.parse_novel.return_value = parsed

        created = pipeline.create_project(
            "Episode",
            "source",
            series_id=series.id,
            # Workspace defaults must not become episode overrides when the
            # episode belongs to a Series.
            model_settings=ModelSettings(chat_model="deepseek-v4-pro"),
            prompt_config=PromptConfig(entity_extraction="standalone extraction"),
        )

        assert draft.series_id == series.id
        assert draft.episode_number == 1
        assert created.id == draft.id
        assert created.series_id == series.id
        assert created.episode_number == 1
        assert created.model_settings.video_model == "doubao-seedance-2-0-mini-260615"
        assert created.model_settings is not series.model_settings
        assert created.model_settings_overrides == []
        assert series.episode_ids == [created.id]
        pipeline.script_processor.parse_novel.assert_called_once_with(
            "Episode",
            "source",
            "series extraction",
            model="qwen3.7-max",
        )

    def test_standalone_creation_applies_defaults_before_initial_extraction(self, pipeline):
        draft = _make_script(title="Draft", text="source")
        parsed = _make_script(title="Parsed", text="source")
        pipeline.script_processor.create_draft_script.return_value = draft
        pipeline.script_processor.parse_novel.return_value = parsed
        settings = ModelSettings.model_validate(
            {
                "chat_model": "deepseek-v4-pro",
                "storyboard_aspect_ratio": "9:16",
            }
        )
        prompt_config = PromptConfig(
            entity_extraction="workspace entity extraction",
            storyboard_polish="workspace storyboard polish",
        )

        created = pipeline.create_project(
            "Standalone",
            "source",
            model_settings=settings,
            prompt_config=prompt_config,
        )

        pipeline.script_processor.parse_novel.assert_called_once_with(
            "Standalone",
            "source",
            "workspace entity extraction",
            model="deepseek-v4-pro",
        )
        assert created.id == draft.id
        assert created.model_settings.chat_model == "deepseek-v4-pro"
        assert created.model_settings_overrides == [
            "chat_model",
            "storyboard_aspect_ratio",
        ]
        assert created.prompt_config.storyboard_polish == "workspace storyboard polish"

    def test_entity_preview_uses_the_episode_chat_model(self, pipeline):
        script = _make_script(model_settings=ModelSettings(chat_model="deepseek-v4-pro"))
        pipeline.scripts[script.id] = script
        preview = _make_script(title=script.title)
        pipeline.script_processor.parse_novel.return_value = preview

        result, revision = pipeline.extract_preview(script.id, "fresh text")

        assert result is preview
        assert revision
        assert pipeline.script_processor.parse_novel.call_args.kwargs["model"] == "deepseek-v4-pro"

    def test_entity_preview_and_style_prompt_inherit_from_series(self, pipeline):
        series = pipeline.create_series(
            "Prompt Series",
            prompt_config=PromptConfig(
                entity_extraction="series entity extraction",
                style_analysis="series style analysis",
            ),
        )
        script = _make_script(
            series_id=series.id,
            prompt_config=PromptConfig(),
        )
        pipeline.scripts[script.id] = script
        preview = _make_script(title=script.title)
        pipeline.script_processor.parse_novel.return_value = preview

        pipeline.extract_preview(script.id, "fresh text")

        pipeline.script_processor.parse_novel.assert_called_once_with(
            script.title,
            "fresh text",
            "series entity extraction",
            model=pipeline._effective_chat_model(script),
        )
        assert (
            pipeline._effective_prompt_override("style_analysis", script) == "series style analysis"
        )

    def test_storyboard_analysis_uses_the_episode_chat_model(self, pipeline):
        script = _make_script(model_settings=ModelSettings(chat_model="qwen3.7-max"))
        pipeline.scripts[script.id] = script
        pipeline.script_processor.analyze_to_storyboard.return_value = [
            {
                "scene_ref_name": "",
                "character_ref_names": [],
                "prop_ref_names": [],
                "action_summary": "Hero enters",
            }
        ]

        pipeline.analyze_text_to_frames(script.id, "A scene")

        assert (
            pipeline.script_processor.analyze_to_storyboard.call_args.kwargs["model"]
            == "qwen3.7-max"
        )

    def test_previous_episode_summary_revision_ignores_outer_whitespace(
        self, pipeline, monkeypatch
    ):
        from src.apps.comic_gen import api as comic_api
        from src.apps.comic_gen.llm_adapter import LLMAdapter

        series = pipeline.create_series("Continuity")
        previous = _make_script(title="Episode 1", text="\n  Previous plot  \n")
        current = _make_script(title="Episode 2")
        pipeline.scripts[previous.id] = previous
        pipeline.scripts[current.id] = current
        pipeline.add_episode_to_series(series.id, previous.id, episode_number=1)
        pipeline.add_episode_to_series(series.id, current.id, episode_number=2)
        monkeypatch.setattr(comic_api, "pipeline", pipeline)
        monkeypatch.setattr(LLMAdapter, "chat", lambda *_args, **_kwargs: "Episode recap")

        generated = comic_api.generate_previous_episode_summary(current.id)
        status = comic_api.get_previous_episode_summary(current.id)

        assert generated["ai_summary_stale"] is False
        assert status["ai_summary"] == "Episode recap"
        assert status["ai_summary_stale"] is False

    def test_storyboard_polish_uses_project_prompt_model_before_series(self, pipeline):
        series = Series(
            id="series-polish",
            title="Series",
            prompt_config=PromptConfig(polish_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        frame = StoryboardFrame(id="frame-1", scene_id="scene-1")
        script = _make_script(
            series_id=series.id,
            frames=[frame],
            prompt_config=PromptConfig(polish_model="deepseek-v4-pro"),
        )
        pipeline.series_store[series.id] = series
        pipeline.scripts[script.id] = script
        pipeline.script_processor.polish_storyboard_prompt.return_value = {
            "prompt_cn": "中文",
            "prompt_en": "English",
        }

        pipeline.refine_frame_prompt(script.id, frame.id, "draft", [])

        assert (
            pipeline.script_processor.polish_storyboard_prompt.call_args.kwargs["polish_model"]
            == "deepseek-v4-pro"
        )

    def test_storyboard_and_video_polish_share_series_model_fallback(self, pipeline, monkeypatch):
        from src.apps.comic_gen import api as comic_api

        series = Series(
            id="series-polish",
            title="Series",
            prompt_config=PromptConfig(polish_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        frame = StoryboardFrame(id="frame-1", scene_id="scene-1")
        script = _make_script(series_id=series.id, frames=[frame])
        pipeline.series_store[series.id] = series
        pipeline.scripts[script.id] = script
        pipeline.script_processor.polish_storyboard_prompt.return_value = {
            "prompt_cn": "中文",
            "prompt_en": "English",
        }
        monkeypatch.setattr(comic_api, "pipeline", pipeline)

        pipeline.refine_frame_prompt(script.id, frame.id, "draft", [])

        assert (
            pipeline.script_processor.polish_storyboard_prompt.call_args.kwargs["polish_model"]
            == "qwen3.7-max"
        )
        assert comic_api._get_polish_model_for_project(script.id) == "qwen3.7-max"

    def test_rich_frame_refinement_uses_effective_series_chat_model(self, pipeline):
        series = Series(
            id="series-refine",
            title="Series",
            model_settings=ModelSettings(chat_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        frame = StoryboardFrame(id="frame-1", scene_id="scene-1")
        script = _make_script(series_id=series.id, frames=[frame])
        pipeline.series_store[series.id] = series
        pipeline.scripts[script.id] = script
        pipeline.script_processor.refine_frame_to_rich.return_value = {
            "visual_description": "A refined frame",
        }

        pipeline.refine_frame(script.id, frame.id)

        assert pipeline.script_processor.refine_frame_to_rich.call_args.args[-1] == "qwen3.7-max"

    def test_next_episode_hook_uses_effective_series_chat_model(self, pipeline, monkeypatch):
        from src.apps.comic_gen import api as comic_api
        from src.apps.comic_gen import llm_adapter

        series = Series(
            id="series-hook",
            title="Series",
            model_settings=ModelSettings(chat_model="qwen3.7-max"),
            created_at=time.time(),
            updated_at=time.time(),
        )
        script = _make_script(series_id=series.id, text="Episode ending")
        pipeline.series_store[series.id] = series
        pipeline.scripts[script.id] = script
        adapter = MagicMock()
        adapter.chat.return_value = "Next hook"
        monkeypatch.setattr(comic_api, "pipeline", pipeline)
        monkeypatch.setattr(llm_adapter, "LLMAdapter", lambda: adapter)

        response = comic_api.generate_next_episode_hook(script.id)

        assert response == {"hook": "Next hook", "stale": False}
        assert adapter.chat.call_args.kwargs["model"] == "qwen3.7-max"

    def test_entity_apply_rejects_wrong_preview_revision(self, pipeline):
        script = _make_script()
        pipeline.scripts[script.id] = script
        preview = _make_script(title=script.title)
        pipeline._extraction_cache[script.id] = (time.time(), "opaque-revision", preview)

        with pytest.raises(ValueError, match="Entity preview changed"):
            pipeline.reparse_project(script.id, "new text", "old-revision")

    def test_entity_apply_without_cached_preview_never_reparses(self, pipeline):
        script = _make_script()
        pipeline.scripts[script.id] = script

        with pytest.raises(ValueError, match="Entity preview expired"):
            pipeline.reparse_project(script.id, "new text", "lost-revision")

        pipeline.script_processor.parse_novel.assert_not_called()

    def test_entity_apply_retry_with_consumed_revision_is_idempotent(self, pipeline):
        script = _make_script(text="old text")
        preview = _make_script(title=script.title, text="new text")
        pipeline.scripts[script.id] = script
        pipeline._extraction_cache[script.id] = (
            time.time(),
            "reviewed-revision",
            preview,
        )

        first = pipeline.reparse_project(
            script.id,
            "new text",
            "reviewed-revision",
        )
        second = pipeline.reparse_project(
            script.id,
            "new text",
            "reviewed-revision",
        )

        assert second is first
        assert pipeline.scripts[script.id] is first
        pipeline.script_processor.parse_novel.assert_not_called()

    def test_entity_apply_rejects_text_changed_after_preview(self, pipeline):
        script = _make_script(text="old text")
        preview = _make_script(title=script.title, text="reviewed text")
        pipeline.scripts[script.id] = script
        pipeline._extraction_cache[script.id] = (
            time.time(),
            "reviewed-revision",
            preview,
        )

        with pytest.raises(ValueError, match="Entity preview text changed"):
            pipeline.reparse_project(
                script.id,
                "different text",
                "reviewed-revision",
            )


# ===================================================================
# 6. Text splitting tests
# ===================================================================


class TestSplitTextByMarkers:
    def test_imported_series_persists_workspace_model_defaults(self, pipeline):
        pipeline.script_processor.create_draft_script.side_effect = (
            lambda title, text: _make_script(title=title, text=text)
        )
        result = pipeline.create_series_from_import(
            "Imported Series",
            "Episode text",
            [
                {
                    "episode_number": 1,
                    "title": "Episode 1",
                    "start_marker": "Episode",
                    "end_marker": "text",
                }
            ],
            model_settings=ModelSettings(
                video_model="doubao-seedance-2-0-mini-260615",
                storyboard_aspect_ratio="9:16",
            ),
        )
        series = pipeline.series_store[result["series"]["id"]]

        assert series.model_settings.video_model == "doubao-seedance-2-0-mini-260615"
        assert series.model_settings.storyboard_aspect_ratio == "9:16"
        assert "video_model" in series.model_settings_overrides
        assert "storyboard_aspect_ratio" in series.model_settings_overrides

    def test_normal_marker_split(self, pipeline):
        text = "AAAA第一章开始BBBB内容CCCC第二章开始DDDD内容EEEE"
        episodes_data = [
            {"start_marker": "第一章开始", "end_marker": "CCCC"},
            {"start_marker": "第二章开始", "end_marker": "EEEE"},
        ]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 2
        assert "第一章开始" in chunks[0]
        assert "第二章开始" in chunks[1]

    def test_markers_not_found_fallback_equal_split(self, pipeline):
        text = "ABCDEFGHIJ"
        episodes_data = [
            {"start_marker": "XXX", "end_marker": "YYY"},
            {"start_marker": "ZZZ", "end_marker": "WWW"},
        ]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 2
        # Equal split: each chunk ~5 chars
        combined = "".join(chunks)
        assert combined == text

    def test_sequential_search_no_overlap(self, pipeline):
        text = "AAABBBCCC"
        episodes_data = [
            {"start_marker": "AAA", "end_marker": "BBB"},
            {"start_marker": "CCC", "end_marker": ""},
        ]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 2
        # First chunk should contain AAA through BBB
        assert "AAA" in chunks[0]
        assert "BBB" in chunks[0]
        # Second chunk should start from CCC onwards
        assert "CCC" in chunks[1]


# ===================================================================
# 7. Cross-series import tests
# ===================================================================


class TestImportAssetsFromSeries:
    def test_deep_copy_with_new_id(self, pipeline):
        source = pipeline.create_series("Source")
        char = _make_character(name="Hero")
        source.characters = [char]
        original_id = char.id

        target = pipeline.create_series("Target")

        result, imported_ids, skipped_ids = pipeline.import_assets_from_series(
            target.id, source.id, [original_id]
        )
        assert len(result.characters) == 1
        imported = result.characters[0]
        # New ID, same name
        assert imported.id != original_id
        assert imported.name == "Hero"
        assert original_id in imported_ids
        assert len(skipped_ids) == 0

    def test_skip_nonexistent_asset_id(self, pipeline):
        source = pipeline.create_series("Source")
        source.characters = [_make_character(name="Hero")]
        target = pipeline.create_series("Target")

        result, imported_ids, skipped_ids = pipeline.import_assets_from_series(
            target.id, source.id, ["nonexistent-id"]
        )
        assert len(result.characters) == 0
        assert len(result.scenes) == 0
        assert len(result.props) == 0
        assert "nonexistent-id" in skipped_ids

    def test_import_mixed_asset_types(self, pipeline):
        source = pipeline.create_series("Source")
        char = _make_character(name="C")
        scene = _make_scene(name="S")
        prop = _make_prop(name="P")
        source.characters = [char]
        source.scenes = [scene]
        source.props = [prop]

        target = pipeline.create_series("Target")
        result, imported_ids, skipped_ids = pipeline.import_assets_from_series(
            target.id, source.id, [char.id, scene.id, prop.id]
        )
        assert len(result.characters) == 1
        assert len(result.scenes) == 1
        assert len(result.props) == 1
        assert len(imported_ids) == 3
