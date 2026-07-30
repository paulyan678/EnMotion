"""Deterministic regression coverage for the Asset Library read model."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.apps.comic_gen.models import (
    Character,
    GlobalAssetLibrary,
    Script,
    Series,
    StoryboardFrame,
)
from src.apps.web_runtime.asset_library_feed import (
    build_asset_library_snapshot,
    query_asset_library_snapshot,
)
from src.apps.web_runtime.file_lock import (
    bind_nonblocking_read,
    reset_nonblocking_read,
)
from src.apps.web_runtime.pipeline_registry import WorkspacePipelineRegistry
from src.apps.web_runtime.workspace_snapshot import (
    WorkspaceSnapshotUnavailable,
    publish_workspace_snapshot,
    read_asset_library_snapshot,
    snapshot_root_for,
)


def _script(script_id: str, name: str) -> Script:
    now = time.time()
    return Script(
        id=script_id,
        title=f"{name} project",
        original_text="",
        created_at=now,
        updated_at=now,
        characters=[Character(id=f"{script_id}-character", name=name, description="")],
    )


def _series(series_id: str, name: str) -> Series:
    now = time.time()
    return Series(
        id=series_id,
        title=f"{name} series",
        episode_ids=[],
        created_at=now,
        updated_at=now,
        characters=[Character(id=f"{series_id}-character", name=name, description="")],
    )


def _write_metadata(output: Path, name: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    project = _script("standalone", f"{name} project")
    series = _series("series", f"{name} series")
    library = GlobalAssetLibrary(
        characters=[Character(id="global-character", name=f"{name} global", description="")]
    )
    (output / "projects.json").write_text(
        json.dumps({project.id: project.model_dump()}),
        encoding="utf-8",
    )
    (output / "series.json").write_text(
        json.dumps({series.id: series.model_dump()}),
        encoding="utf-8",
    )
    (output / "library_assets.json").write_text(
        json.dumps(library.model_dump()),
        encoding="utf-8",
    )


def _item_names(workspace_id: str) -> set[str]:
    return {item.name for item in read_asset_library_snapshot(workspace_id).items}


def test_registry_never_caches_absent_preload_under_created_file_fingerprint(tmp_path, monkeypatch):
    loaded = threading.Event()
    release = threading.Event()

    class BarrierPipeline:
        calls = 0

        def __init__(self, config):
            type(self).calls += 1
            path = Path(config["output_root"]) / "projects.json"
            self.loaded = path.read_text(encoding="utf-8") if path.exists() else "<missing>"
            if type(self).calls == 1:
                loaded.set()
                assert release.wait(2)

    monkeypatch.setattr(
        "src.apps.web_runtime.pipeline_registry.ComicGenPipeline",
        BarrierPipeline,
    )
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(registry.get, "workspace-a")
        assert loaded.wait(2)
        path = registry.output_root_for("workspace-a") / "projects.json"
        path.write_text('{"created":true}', encoding="utf-8")
        release.set()
        pipeline = future.result(timeout=2)

    assert pipeline.loaded == '{"created":true}'
    assert BarrierPipeline.calls == 2
    assert registry.get("workspace-a") is pipeline


def test_registry_detects_same_sized_atomic_replacement_while_loading(tmp_path, monkeypatch):
    loaded = threading.Event()
    release = threading.Event()

    class BarrierPipeline:
        calls = 0

        def __init__(self, config):
            type(self).calls += 1
            path = Path(config["output_root"]) / "projects.json"
            self.loaded = path.read_text(encoding="utf-8")
            if type(self).calls == 1:
                loaded.set()
                assert release.wait(2)

    monkeypatch.setattr(
        "src.apps.web_runtime.pipeline_registry.ComicGenPipeline",
        BarrierPipeline,
    )
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    path = registry.output_root_for("workspace-a") / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text("AAAA", encoding="utf-8")
    original_mtime = path.stat().st_mtime_ns

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(registry.get, "workspace-a")
        assert loaded.wait(2)
        replacement = path.with_suffix(".next")
        replacement.write_text("BBBB", encoding="utf-8")
        os.utime(replacement, ns=(original_mtime, original_mtime))
        os.replace(replacement, path)
        release.set()
        pipeline = future.result(timeout=2)

    assert pipeline.loaded == "BBBB"
    assert BarrierPipeline.calls == 2


def test_registry_bounds_continuously_changing_writer_build(tmp_path, monkeypatch):
    class FakePipeline:
        calls = 0

        def __init__(self, _config):
            type(self).calls += 1

    monkeypatch.setattr(
        "src.apps.web_runtime.pipeline_registry.ComicGenPipeline",
        FakePipeline,
    )
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    counter = 0

    def changing_fingerprint(_workspace_id):
        nonlocal counter
        counter += 1
        return ((counter, counter, counter, counter),) * 3

    monkeypatch.setattr(registry, "_fingerprint", changing_fingerprint)

    with pytest.raises(RuntimeError, match="changed repeatedly"):
        registry.get("workspace-a")
    assert FakePipeline.calls == 3


def test_workspace_snapshot_returns_only_complete_before_or_after_revision(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    output = root / "workspace-a" / "output"
    _write_metadata(output, "before")

    before = publish_workspace_snapshot("workspace-a")
    assert before.revision == 1
    before_names = _item_names("workspace-a")
    assert before_names == {
        "before project",
        "before series",
        "before global",
    }

    # Live JSON files may be replaced one by one during a transaction. Readers
    # remain on the prior manifest until the complete revision is published.
    after_project = _script("standalone", "after project")
    (output / "projects.json").write_text(
        json.dumps({after_project.id: after_project.model_dump()}),
        encoding="utf-8",
    )
    assert _item_names("workspace-a") == before_names
    after_series = _series("series", "after series")
    (output / "series.json").write_text(
        json.dumps({after_series.id: after_series.model_dump()}),
        encoding="utf-8",
    )
    assert _item_names("workspace-a") == before_names
    after_library = GlobalAssetLibrary(
        characters=[Character(id="global-character", name="after global", description="")]
    )
    (output / "library_assets.json").write_text(
        json.dumps(after_library.model_dump()),
        encoding="utf-8",
    )
    assert _item_names("workspace-a") == before_names

    after = publish_workspace_snapshot("workspace-a")
    assert after.revision == 2
    assert _item_names("workspace-a") == {
        "after project",
        "after series",
        "after global",
    }


def test_failed_snapshot_rebuild_retains_last_valid_revision(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    output = root / "workspace-a" / "output"
    _write_metadata(output, "valid")
    published = publish_workspace_snapshot("workspace-a")
    expected = _item_names("workspace-a")

    (output / "projects.json").write_text("{", encoding="utf-8")
    with pytest.raises(WorkspaceSnapshotUnavailable):
        publish_workspace_snapshot("workspace-a", force=True)

    snapshot = read_asset_library_snapshot("workspace-a")
    assert snapshot.revision == published.revision
    assert {item.name for item in snapshot.items} == expected


def test_unchanged_snapshot_is_reused_and_corrupt_sidecar_rebuilds_automatically(
    tmp_path, monkeypatch
):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    output = root / "workspace-a" / "output"
    _write_metadata(output, "stable")

    first = publish_workspace_snapshot("workspace-a")
    warm = publish_workspace_snapshot("workspace-a")
    assert warm.revision == first.revision
    assert warm.feed_path == first.feed_path
    first_read = read_asset_library_snapshot("workspace-a")
    assert read_asset_library_snapshot("workspace-a") is first_read

    assert first.feed_path is not None
    first.feed_path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(WorkspaceSnapshotUnavailable):
        read_asset_library_snapshot("workspace-a")
    rebuilt = publish_workspace_snapshot("workspace-a")

    assert rebuilt.revision > first.revision
    snapshot = read_asset_library_snapshot("workspace-a")
    assert snapshot.revision == rebuilt.revision
    assert {item.name for item in snapshot.items} == {
        "stable project",
        "stable series",
        "stable global",
    }


def test_workspace_usage_snapshots_are_isolated_even_for_the_same_raw_asset_id(
    tmp_path, monkeypatch
):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))

    def write_workspace(workspace_id: str, frame_count: int) -> None:
        output = root / workspace_id / "output"
        output.mkdir(parents=True, exist_ok=True)
        project = _script("episode-1", workspace_id)
        project.characters = []
        project.frames = [
            StoryboardFrame(
                id=f"frame-{index}",
                scene_id="",
                character_ids=["shared-id"],
            )
            for index in range(frame_count)
        ]
        library = GlobalAssetLibrary(
            characters=[
                Character(
                    id="shared-id",
                    name=f"{workspace_id} private hero",
                    description="",
                )
            ]
        )
        (output / "projects.json").write_text(
            json.dumps({project.id: project.model_dump()}),
            encoding="utf-8",
        )
        (output / "series.json").write_text("{}", encoding="utf-8")
        (output / "library_assets.json").write_text(
            json.dumps(library.model_dump()),
            encoding="utf-8",
        )

    write_workspace("workspace-a", 2)
    write_workspace("workspace-b", 0)
    publish_workspace_snapshot("workspace-a")
    publish_workspace_snapshot("workspace-b")

    first = read_asset_library_snapshot("workspace-a")
    second = read_asset_library_snapshot("workspace-b")
    assert [(item.name, item.usage_count) for item in first.items] == [
        ("workspace-a private hero", 2)
    ]
    assert [(item.name, item.usage_count) for item in second.items] == [
        ("workspace-b private hero", 0)
    ]
    assert "workspace-b" not in first.model_dump_json()
    assert "workspace-a" not in second.model_dump_json()


def test_publish_skips_orphan_revision_left_by_interrupted_process(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    output = root / "workspace-a" / "output"
    _write_metadata(output, "before")
    assert publish_workspace_snapshot("workspace-a").revision == 1

    orphan = snapshot_root_for("workspace-a") / "revisions" / f"{2:020d}"
    orphan.mkdir(parents=True)
    (orphan / "incomplete").write_text("orphan", encoding="utf-8")
    _write_metadata(output, "after")

    published = publish_workspace_snapshot("workspace-a")

    assert published.revision == 3
    assert _item_names("workspace-a") == {
        "after project",
        "after series",
        "after global",
    }


def test_read_only_pipeline_from_snapshot_creates_or_changes_no_workspace_file(
    tmp_path, monkeypatch
):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    output = root / "workspace-a" / "output"
    _write_metadata(output, "stable")
    publish_workspace_snapshot("workspace-a")
    registry = WorkspacePipelineRegistry(str(root))

    def inventory() -> dict[str, tuple[int, int, int]]:
        return {
            str(path.relative_to(root)): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
                path.stat().st_size,
            )
            for path in root.rglob("*")
            if path.is_file()
        }

    before = inventory()
    token = bind_nonblocking_read(registry.lock_path_for("workspace-a"))
    try:
        pipeline = registry.get("workspace-a")
        assert set(pipeline.scripts) == {"standalone"}
        assert set(pipeline.series_store) == {"series"}
    finally:
        reset_nonblocking_read(token)
    assert inventory() == before


def test_writer_mutation_publishes_snapshot_for_nonblocking_hybrid_reads(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))
    registry = WorkspacePipelineRegistry(str(root))
    output = registry.output_root_for("workspace-a")

    with registry.locked("workspace-a"):
        _write_metadata(output, "committed")

    assert _item_names("workspace-a") == {
        "committed project",
        "committed series",
        "committed global",
    }
    token = bind_nonblocking_read(registry.lock_path_for("workspace-a"))
    try:
        reader = registry.get("workspace-a")
        assert set(reader.scripts) == {"standalone"}
        assert set(reader.series_store) == {"series"}
    finally:
        reset_nonblocking_read(token)


def test_cold_reload_in_one_workspace_does_not_block_another(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class PerWorkspacePipeline:
        def __init__(self, config):
            self.workspace = Path(config["output_root"]).parent.name
            if self.workspace == "workspace-a":
                started.set()
                assert release.wait(2)

    monkeypatch.setattr(
        "src.apps.web_runtime.pipeline_registry.ComicGenPipeline",
        PerWorkspacePipeline,
    )
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))

    with ThreadPoolExecutor(max_workers=1) as executor:
        slow = executor.submit(registry.get, "workspace-a")
        assert started.wait(2)
        fast_started = time.perf_counter()
        fast = registry.get("workspace-b")
        elapsed = time.perf_counter() - fast_started
        release.set()
        slow.result(timeout=2)

    assert fast.workspace == "workspace-b"
    assert elapsed < 0.25


def test_feed_is_deterministic_bounded_and_contains_only_selected_thumbnail():
    characters = [
        Character(
            id=f"character-{index:03d}",
            name=f"Character {index:03d}",
            description="card metadata",
            image_url=f"assets/{index:03d}.png",
            image_prompt="private editor prompt",
        )
        for index in range(120)
    ]
    script = _script("large", "Large")
    script.characters = characters
    snapshot = build_asset_library_snapshot(
        revision=7,
        series=[],
        projects=[script],
        library=GlobalAssetLibrary(),
        generated_at=123.0,
    )

    first = query_asset_library_snapshot(snapshot, offset=0, limit=50)
    second = query_asset_library_snapshot(snapshot, offset=50, limit=50)
    assert first.revision == second.revision == 7
    assert first.page.count == second.page.count == 50
    assert first.page.total == 120
    assert first.page.next_offset == 50
    assert second.page.next_offset == 100
    assert [item.id for item in first.items] == sorted(item.id for item in first.items)
    encoded = first.model_dump_json()
    assert "private editor prompt" not in encoded
    assert "image_prompt" not in encoded
    assert encoded.count("assets/") == 50


def test_genuine_empty_workspace_is_explicit_revision_zero(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(root))

    snapshot = read_asset_library_snapshot("new-workspace")

    assert snapshot.revision == 0
    assert snapshot.items == []
    assert not (root / "new-workspace").exists()
