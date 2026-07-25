"""Regression coverage for owner-aware Asset Library usage accounting."""

from __future__ import annotations

import math
import threading
from time import perf_counter
from types import SimpleNamespace
from typing import Generic, Iterable, Iterator, TypeVar
from unittest.mock import Mock

import pytest

from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GlobalAssetLibrary,
    ImageAsset,
    ImageVariant,
    Prop,
    Scene,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.web_runtime.asset_library_feed import (
    AssetLibraryFeedItem,
    AssetLibrarySnapshot,
    build_asset_library_snapshot,
    query_asset_library_snapshot,
)
from src.apps.web_runtime.asset_library_metrics import RollingLatencyMetrics
from src.apps.web_runtime.asset_usage import build_asset_usage_index


def _character(asset_id: str, *, base_id: str | None = None) -> Character:
    return Character(
        id=asset_id,
        name=asset_id,
        description="",
        base_character_id=base_id,
    )


def _scene(asset_id: str) -> Scene:
    return Scene(id=asset_id, name=asset_id, description="")


def _prop(asset_id: str) -> Prop:
    return Prop(id=asset_id, name=asset_id, description="")


def _project(
    project_id: str,
    *,
    series_id: str | None = None,
    characters: list[Character] | None = None,
    scenes: list[Scene] | None = None,
    props: list[Prop] | None = None,
    frames: list[StoryboardFrame] | None = None,
    video_tasks: list[VideoTask] | None = None,
) -> Script:
    return Script(
        id=project_id,
        title=project_id,
        original_text="",
        series_id=series_id,
        characters=characters or [],
        scenes=scenes or [],
        props=props or [],
        frames=frames or [],
        video_tasks=video_tasks or [],
        created_at=1.0,
        updated_at=1.0,
    )


def _series(
    series_id: str,
    *,
    characters: list[Character] | None = None,
    scenes: list[Scene] | None = None,
    props: list[Prop] | None = None,
    episode_ids: list[str] | None = None,
) -> Series:
    return Series(
        id=series_id,
        title=series_id,
        characters=characters or [],
        scenes=scenes or [],
        props=props or [],
        episode_ids=episode_ids or [],
        created_at=1.0,
        updated_at=1.0,
    )


def _count(
    index,
    owner_kind: str,
    owner_id: str,
    asset_type: str,
    asset_id: str,
) -> int:
    return index.count_for((owner_kind, owner_id, asset_type, asset_id))


def test_frame_reference_transfers_from_project_to_series_to_global_when_shadow_removed():
    shared_id = "shared-character"
    project = _project(
        "episode-1",
        series_id="series-1",
        characters=[_character(shared_id)],
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="",
                character_ids=[shared_id],
            )
        ],
    )
    series = _series("series-1", characters=[_character(shared_id)])
    library = GlobalAssetLibrary(characters=[_character(shared_id)])

    project_shadow = build_asset_usage_index(
        projects=[project],
        series=[series],
        library=library,
    )
    assert _count(project_shadow, "project", project.id, "character", shared_id) == 1
    assert _count(project_shadow, "series", series.id, "character", shared_id) == 0
    assert _count(project_shadow, "global", "global", "character", shared_id) == 0

    project.characters = []
    series_fallback = build_asset_usage_index(
        projects=[project],
        series=[series],
        library=library,
    )
    assert _count(series_fallback, "project", project.id, "character", shared_id) == 0
    assert _count(series_fallback, "series", series.id, "character", shared_id) == 1
    assert _count(series_fallback, "global", "global", "character", shared_id) == 0

    series.characters = []
    global_fallback = build_asset_usage_index(
        projects=[project],
        series=[series],
        library=library,
    )
    assert _count(global_fallback, "project", project.id, "character", shared_id) == 0
    assert _count(global_fallback, "series", series.id, "character", shared_id) == 0
    assert _count(global_fallback, "global", "global", "character", shared_id) == 1
    assert global_fallback.relationship_count == 1


def test_frame_references_are_deduplicated_per_frame_and_scoped_by_asset_type():
    shared_id = "same-raw-id"
    frames = [
        StoryboardFrame(
            id=f"frame-{index}",
            scene_id=shared_id,
            character_ids=[shared_id, shared_id],
            prop_ids=[shared_id, shared_id],
        )
        for index in range(2)
    ]
    project = _project("episode-1", frames=frames)
    library = GlobalAssetLibrary(
        characters=[_character(shared_id)],
        scenes=[_scene(shared_id)],
        props=[_prop(shared_id)],
    )

    index = build_asset_usage_index(
        projects=[project],
        series=[],
        library=library,
    )

    assert _count(index, "global", "global", "character", shared_id) == 2
    assert _count(index, "global", "global", "scene", shared_id) == 2
    assert _count(index, "global", "global", "prop", shared_id) == 2
    assert index.relationship_count == 6


def test_usage_rebuild_tracks_multiple_projects_replacements_and_deletions():
    library = GlobalAssetLibrary(
        characters=[_character("hero"), _character("villain")],
        scenes=[_scene("street")],
        props=[_prop("key")],
    )
    first = _project(
        "episode-1",
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="street",
                character_ids=["hero", "hero"],
                prop_ids=["key", "key"],
            ),
            StoryboardFrame(
                id="frame-2",
                scene_id="",
                character_ids=["hero"],
            ),
        ],
    )
    second = _project(
        "episode-2",
        frames=[
            StoryboardFrame(
                # Frame ids are only unique inside their owning project.
                id="frame-1",
                scene_id="",
                character_ids=["hero", "villain"],
            )
        ],
    )

    original = build_asset_usage_index(
        projects=[first, second],
        series=[],
        library=library,
    )
    assert _count(original, "global", "global", "character", "hero") == 3
    assert _count(original, "global", "global", "character", "villain") == 1
    assert _count(original, "global", "global", "scene", "street") == 1
    assert _count(original, "global", "global", "prop", "key") == 1

    # Replacing one persisted frame moves its edges; no historical membership
    # or prior frame contents survive into the derived aggregate.
    first.frames[0] = StoryboardFrame(
        id="frame-1",
        scene_id="missing-scene",
        character_ids=["villain"],
        prop_ids=["missing-prop"],
    )
    replaced = build_asset_usage_index(
        projects=[first, second],
        series=[],
        library=library,
    )
    assert _count(replaced, "global", "global", "character", "hero") == 2
    assert _count(replaced, "global", "global", "character", "villain") == 2
    assert _count(replaced, "global", "global", "scene", "street") == 0
    assert _count(replaced, "global", "global", "prop", "key") == 0

    first.frames.pop()
    frame_deleted = build_asset_usage_index(
        projects=[first, second],
        series=[],
        library=library,
    )
    assert _count(frame_deleted, "global", "global", "character", "hero") == 1

    project_deleted = build_asset_usage_index(
        projects=[first],
        series=[],
        library=library,
    )
    assert _count(project_deleted, "global", "global", "character", "hero") == 0
    assert _count(project_deleted, "global", "global", "character", "villain") == 1


def test_frame_create_and_update_persist_character_and_prop_relationships():
    project = _project(
        "episode-1",
        scenes=[_scene("scene-1")],
    )
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {project.id: project}
    pipeline._save_data = Mock()

    pipeline.add_frame(
        project.id,
        "scene-1",
        "Action",
        character_ids=["character-1"],
        prop_ids=["prop-1"],
    )
    frame = project.frames[0]
    assert frame.character_ids == ["character-1"]
    assert frame.prop_ids == ["prop-1"]

    pipeline.update_frame(
        project.id,
        frame.id,
        character_ids=["character-2"],
        prop_ids=["prop-2"],
    )
    assert frame.character_ids == ["character-2"]
    assert frame.prop_ids == ["prop-2"]
    assert pipeline._save_data.call_count == 2


def test_lineage_counts_valid_edges_but_excludes_self_cycles_and_orphans():
    library = GlobalAssetLibrary(
        characters=[
            _character("base"),
            _character("child", base_id="base"),
            _character("grandchild", base_id="child"),
            _character("self", base_id="self"),
            _character("cycle-a", base_id="cycle-b"),
            _character("cycle-b", base_id="cycle-a"),
            _character("orphan", base_id="missing"),
        ]
    )

    index = build_asset_usage_index(
        projects=[],
        series=[],
        library=library,
    )

    assert _count(index, "global", "global", "character", "base") == 1
    assert _count(index, "global", "global", "character", "child") == 1
    for asset_id in ("grandchild", "self", "cycle-a", "cycle-b", "orphan"):
        assert _count(index, "global", "global", "character", asset_id) == 0
    assert index.relationship_count == 2


def test_lineage_uses_exact_owner_precedence_and_rejects_ambiguous_targets():
    project = _project(
        "episode-1",
        series_id="series-1",
        characters=[
            _character("base"),
            _character("project-child", base_id="base"),
        ],
    )
    series = _series(
        "series-1",
        characters=[
            _character("base"),
            _character("series-child", base_id="base"),
        ],
    )
    library = GlobalAssetLibrary(
        characters=[
            _character("base"),
            _character("global-child", base_id="base"),
        ]
    )

    index = build_asset_usage_index(
        projects=[project],
        series=[series],
        library=library,
    )

    assert _count(index, "project", project.id, "character", "base") == 1
    assert _count(index, "series", series.id, "character", "base") == 1
    assert _count(index, "global", "global", "character", "base") == 1

    series.characters.insert(1, _character("base"))
    ambiguous = build_asset_usage_index(
        projects=[project],
        series=[series],
        library=library,
    )
    assert ambiguous.ambiguous_asset_count == 1
    assert _count(ambiguous, "series", series.id, "character", "base") == 0
    # A project-local base remains unambiguous and still wins for its child.
    assert _count(ambiguous, "project", project.id, "character", "base") == 1


def test_membership_media_variants_and_generation_jobs_do_not_count_as_usage():
    image = ImageVariant(
        id="selected-image",
        url="assets/selected.png",
        is_favorited=True,
    )
    character = _character("character-1")
    character.reference_sheet = AssetUnit(
        selected_image_id=image.id,
        image_variants=[image],
    )
    character.image_url = image.url
    character.video_assets = [
        VideoTask(
            id="asset-video",
            project_id="episode-1",
            asset_id=character.id,
            image_url=image.url,
            prompt="not a relationship",
        )
    ]
    scene = _scene("scene-1")
    scene.image_asset = ImageAsset(selected_id=image.id, variants=[image])
    prop = _prop("prop-1")
    prop.image_asset = ImageAsset(selected_id=image.id, variants=[image])
    series = _series(
        "series-1",
        characters=[character],
        scenes=[scene],
        props=[prop],
        episode_ids=["episode-1"],
    )
    project = _project(
        "episode-1",
        series_id=series.id,
        video_tasks=[
            VideoTask(
                id=f"job-{asset_id}",
                project_id="episode-1",
                asset_id=asset_id,
                image_url=image.url,
                prompt="generation output does not establish usage",
            )
            for asset_id in (character.id, scene.id, prop.id)
        ],
    )

    index = build_asset_usage_index(
        projects=[project],
        series=[series],
        library=GlobalAssetLibrary(),
    )

    assert index.asset_count == 3
    assert index.relationship_count == 0
    assert all(count == 0 for count in index.counts.values())


def test_copy_import_promotion_and_fork_only_count_retained_frame_edges():
    original = _character("original")
    promoted_copy = _character("promoted-copy")
    project_fork = _character("project-fork")
    frame = StoryboardFrame(
        id="frame-1",
        scene_id="",
        character_ids=[original.id],
    )
    copied_frame = frame.model_copy(deep=True)
    copied_frame.id = "frame-2"
    project = _project(
        "episode-1",
        characters=[project_fork],
        frames=[frame, copied_frame],
    )
    library = GlobalAssetLibrary(characters=[original, promoted_copy])

    index = build_asset_usage_index(
        projects=[project],
        series=[],
        library=library,
    )

    assert _count(index, "global", "global", "character", original.id) == 2
    # Deep-copied import/promotion/fork records have new ids and no persisted
    # source edge in the current schema, so membership alone never counts.
    assert _count(index, "global", "global", "character", promoted_copy.id) == 0
    assert _count(index, "project", project.id, "character", project_fork.id) == 0


def test_usage_sort_is_stable_and_paginates_after_server_side_ordering():
    assets = [
        _character("zulu"),
        _character("gamma"),
        _character("alpha"),
        _character("delta"),
        _character("beta"),
    ]
    for asset in assets:
        asset.name = asset.id.title()
    project = _project(
        "episode-1",
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="",
                character_ids=["delta", "alpha", "zulu", "beta"],
            ),
            StoryboardFrame(
                id="frame-2",
                scene_id="",
                character_ids=["delta", "alpha", "zulu"],
            ),
            StoryboardFrame(
                id="frame-3",
                scene_id="",
                character_ids=["delta"],
            ),
        ],
    )
    snapshot = build_asset_library_snapshot(
        revision=7,
        series=[],
        projects=[project],
        library=GlobalAssetLibrary(characters=assets),
        generated_at=123.0,
    )

    first = query_asset_library_snapshot(
        snapshot,
        sort="usage",
        order="desc",
        offset=0,
        limit=2,
    )
    second = query_asset_library_snapshot(
        snapshot,
        sort="usage",
        order="desc",
        offset=2,
        limit=2,
    )
    last = query_asset_library_snapshot(
        snapshot,
        sort="usage",
        order="desc",
        offset=4,
        limit=2,
    )
    ascending = query_asset_library_snapshot(
        snapshot,
        sort="usage",
        order="asc",
        offset=0,
        limit=5,
    )

    assert [(item.id, item.usage_count) for item in first.items] == [
        ("delta", 3),
        ("alpha", 2),
    ]
    assert [(item.id, item.usage_count) for item in second.items] == [
        ("zulu", 2),
        ("beta", 1),
    ]
    assert [(item.id, item.usage_count) for item in last.items] == [("gamma", 0)]
    assert [item.id for item in ascending.items] == [
        "gamma",
        "beta",
        "alpha",
        "zulu",
        "delta",
    ]
    assert first.page.model_dump() == {
        "offset": 0,
        "limit": 2,
        "count": 2,
        "total": 5,
        "has_more": True,
        "next_offset": 2,
    }
    assert second.page.next_offset == 4
    assert last.page.next_offset is None
    assert first.facets.all == 5


def test_usage_ties_follow_the_complete_canonical_identity_tuple():
    for asset in (
        project_asset := _character("same"),
        series_asset := _character("same"),
        global_asset := _character("same"),
    ):
        asset.name = "SAME"
    project = _project("episode-1", characters=[project_asset])
    series = _series("series-1", characters=[series_asset])
    snapshot = build_asset_library_snapshot(
        revision=1,
        projects=[project],
        series=[series],
        library=GlobalAssetLibrary(characters=[global_asset]),
        generated_at=1.0,
    )

    response = query_asset_library_snapshot(
        snapshot,
        sort="usage",
        order="desc",
    )

    assert [
        (item.source_kind, item.source_id, item.asset_type, item.id) for item in response.items
    ] == [
        ("global", "global", "character", "same"),
        ("project", "episode-1", "character", "same"),
        ("series", "series-1", "character", "same"),
    ]


def test_search_owner_type_and_favorite_filters_run_before_pagination():
    project_hero = _character("project-hero")
    project_hero.name = "Project Hero"
    project_hero.starred = True
    project_scene = _scene("project-scene")
    project_scene.name = "Hero Street"
    series_hero = _character("series-hero")
    series_hero.name = "Series Hero"
    series_hero.starred = True
    global_hero = _character("global-hero")
    global_hero.name = "Global Hero"
    global_hero.starred = True
    global_prop = _prop("global-prop")
    global_prop.name = "Unrelated"
    project = _project(
        "episode-1",
        series_id="series-1",
        characters=[project_hero],
        scenes=[project_scene],
    )
    snapshot = build_asset_library_snapshot(
        revision=4,
        projects=[project],
        series=[_series("series-1", characters=[series_hero])],
        library=GlobalAssetLibrary(
            characters=[global_hero],
            props=[global_prop],
        ),
        generated_at=1.0,
    )

    filtered = query_asset_library_snapshot(
        snapshot,
        search="  HERO ",
        starred=True,
        asset_type="character",
        sort="name",
        offset=1,
        limit=1,
    )
    assert filtered.page.total == 3
    assert filtered.page.count == 1
    assert filtered.items[0].name == "Project Hero"
    assert filtered.facets.model_dump() == {
        "all": 3,
        "characters": 3,
        "scenes": 0,
        "props": 0,
        "starred": 3,
    }

    assert {
        (item.source_kind, item.source_id)
        for item in query_asset_library_snapshot(
            snapshot,
            source_kind="global",
            limit=50,
        ).items
    } == {("global", "global")}
    assert {
        item.id
        for item in query_asset_library_snapshot(
            snapshot,
            project_id="episode-1",
            limit=50,
        ).items
    } == {"project-hero", "project-scene"}
    assert {
        item.id
        for item in query_asset_library_snapshot(
            snapshot,
            series_id="series-1",
            limit=50,
        ).items
    } == {"project-hero", "project-scene", "series-hero"}


def test_ambiguous_duplicate_canonical_assets_are_zero_and_not_duplicated_in_feed():
    duplicate_a = _character("duplicate")
    duplicate_b = _character("duplicate")
    project = _project(
        "episode-1",
        frames=[
            StoryboardFrame(
                id="frame-1",
                scene_id="",
                character_ids=["duplicate"],
            )
        ],
    )
    snapshot = build_asset_library_snapshot(
        revision=1,
        projects=[project],
        series=[],
        library=GlobalAssetLibrary(characters=[duplicate_a, duplicate_b]),
        generated_at=1.0,
    )

    assert len(snapshot.items) == 1
    assert snapshot.items[0].usage_count == 0


def test_desktop_snapshot_cache_reuses_unchanged_index_and_advances_on_mutation():
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline._save_lock = threading.RLock()
    pipeline._asset_library_data_revision = 0
    pipeline._asset_library_snapshot_cache = None
    pipeline.scripts = {}
    pipeline.series_store = {}
    pipeline.library_store = GlobalAssetLibrary(characters=[_character("character-1")])

    first = pipeline.asset_library_snapshot()
    warm = pipeline.asset_library_snapshot()
    assert warm is first
    assert warm.revision == 0

    pipeline._mark_asset_library_changed()
    rebuilt = pipeline.asset_library_snapshot()
    assert rebuilt is not first
    assert rebuilt.revision == 1


def test_latency_metrics_publish_bounded_p50_p95_and_p99_without_labels():
    metrics = RollingLatencyMetrics(capacity=3)
    metrics.observe(30)
    metrics.observe(10)
    sample = metrics.observe(20)

    assert sample == {
        "samples": 3,
        "p50_ms": 20.0,
        "p95_ms": 30.0,
        "p99_ms": 30.0,
    }
    replaced = metrics.observe(5)
    assert replaced["samples"] == 3
    assert replaced["p50_ms"] == 10.0


T = TypeVar("T")


class _CountingIterable(Generic[T]):
    def __init__(self, values: Iterable[T]):
        self.values = list(values)
        self.iterations = 0

    def __iter__(self) -> Iterator[T]:
        self.iterations += 1
        return iter(self.values)


def test_usage_index_consumes_each_source_once_for_a_representative_large_snapshot():
    asset_count = 2_500
    characters = _CountingIterable(
        SimpleNamespace(id=f"character-{index}", base_character_id=None)
        for index in range(asset_count)
    )
    frames = _CountingIterable(
        SimpleNamespace(
            id=f"frame-{index}",
            character_ids=[f"character-{index}"],
            scene_id="",
            prop_ids=[],
        )
        for index in range(asset_count)
    )
    project = SimpleNamespace(
        id="episode-1",
        series_id=None,
        characters=_CountingIterable([]),
        scenes=_CountingIterable([]),
        props=_CountingIterable([]),
        frames=frames,
    )
    library = SimpleNamespace(
        characters=characters,
        scenes=_CountingIterable([]),
        props=_CountingIterable([]),
    )
    projects = _CountingIterable([project])
    series = _CountingIterable([])

    index = build_asset_usage_index(
        projects=projects,
        series=series,
        library=library,
    )

    assert index.asset_count == asset_count
    assert index.relationship_count == asset_count
    assert all(count == 1 for count in index.counts.values())
    assert projects.iterations == 1
    assert series.iterations == 1
    assert characters.iterations == 1
    assert frames.iterations == 1
    assert project.characters.iterations == 1
    assert project.scenes.iterations == 1
    assert project.props.iterations == 1
    assert library.scenes.iterations == 1
    assert library.props.iterations == 1
    assert index.build_ms < 2_000


def test_usage_counts_are_immutable_after_publication():
    index = build_asset_usage_index(
        projects=[],
        series=[],
        library=GlobalAssetLibrary(characters=[_character("character-1")]),
    )

    with pytest.raises(TypeError):
        index.counts[("global", "global", "character", "character-1")] = 1


def test_large_usage_fixture_is_linear_and_warm_page_p95_stays_under_budget():
    asset_count = 10_000
    frame_count = 10_000
    references_per_frame = 10
    characters = [
        SimpleNamespace(id=f"character-{index}", base_character_id=None)
        for index in range(asset_count)
    ]
    frames = [
        SimpleNamespace(
            id=f"frame-{frame_index}",
            scene_id="",
            character_ids=[
                f"character-{(frame_index + offset) % asset_count}"
                for offset in range(references_per_frame)
            ],
            prop_ids=[],
        )
        for frame_index in range(frame_count)
    ]
    project = SimpleNamespace(
        id="episode-large",
        series_id=None,
        characters=[],
        scenes=[],
        props=[],
        frames=frames,
    )

    usage = build_asset_usage_index(
        projects=[project],
        series=[],
        library=SimpleNamespace(
            characters=characters,
            scenes=[],
            props=[],
        ),
    )

    assert usage.asset_count == asset_count
    assert usage.processed_reference_count == frame_count * references_per_frame
    assert usage.relationship_count == frame_count * references_per_frame
    assert usage.build_ms < 5_000

    snapshot = AssetLibrarySnapshot(
        revision=9,
        generated_at=1.0,
        items=[
            AssetLibraryFeedItem(
                id=f"character-{index}",
                name=f"Character {index:05d}",
                description="",
                asset_type="character",
                source_kind="global",
                source_id="global",
                source_name="Global",
                usage_count=index % 31,
            )
            for index in range(asset_count)
        ],
    )
    timings_ms: list[float] = []
    response = None
    for offset in range(0, 600, 50):
        started = perf_counter()
        response = query_asset_library_snapshot(
            snapshot,
            sort="usage",
            order="desc",
            offset=offset,
            limit=50,
        )
        timings_ms.append((perf_counter() - started) * 1000)

    assert response is not None
    p95_index = math.ceil(0.95 * len(timings_ms)) - 1
    p95_ms = sorted(timings_ms)[p95_index]
    assert p95_ms < 500
    assert response.page.count == 50
    assert len(response.model_dump_json().encode("utf-8")) < 100_000
