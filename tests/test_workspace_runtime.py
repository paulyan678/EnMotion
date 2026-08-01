from pathlib import Path

import pytest

from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.models import StoryboardFrame
from src.apps.web_runtime.context import bind_tenant, get_tenant, reset_tenant
from src.apps.web_runtime.pipeline_registry import PipelineProxy, WorkspacePipelineRegistry
from src.apps.web_runtime.playground_registry import WorkspacePlaygroundRegistry


def test_pipeline_output_root_is_configurable_and_component_scoped(tmp_path):
    root = tmp_path / "private-output"
    pipeline = ComicGenPipeline({"output_root": str(root)})

    assert Path(pipeline.data_file) == root / "projects.json"
    assert Path(pipeline.series_data_file) == root / "series.json"
    assert Path(pipeline.library_data_file) == root / "library_assets.json"
    assert Path(pipeline.asset_generator.output_dir) == root / "assets"
    assert Path(pipeline.storyboard_generator.output_dir) == root / "storyboard"
    assert Path(pipeline.video_generator.output_dir) == root / "video"
    assert Path(pipeline.export_manager.output_dir) == root / "export"


def test_tenant_context_restores_previous_value():
    outer = bind_tenant("user-a", "workspace-a")
    try:
        assert get_tenant().workspace_id == "workspace-a"
        inner = bind_tenant("user-b", "workspace-b", "admin")
        try:
            assert get_tenant().workspace_id == "workspace-b"
            assert get_tenant().role == "admin"
        finally:
            reset_tenant(inner)
        assert get_tenant().workspace_id == "workspace-a"
    finally:
        reset_tenant(outer)
    assert get_tenant(required=False) is None


def test_registry_separates_pipeline_state_and_paths(tmp_path):
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    first = registry.get("workspace-a")
    second = registry.get("workspace-b")

    first.create_project("A", "private A", skip_analysis=True)
    second.create_project("B", "private B", skip_analysis=True)

    assert {item.title for item in first.scripts.values()} == {"A"}
    assert {item.title for item in second.scripts.values()} == {"B"}
    assert first.output_root != second.output_root
    assert Path(first.data_file).is_file()
    assert Path(second.data_file).is_file()


def test_hybrid_registry_recovers_non_durable_orphan_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")

    pipeline = WorkspacePipelineRegistry(str(tmp_path / "workspaces")).get("workspace-a")

    assert pipeline.config["recover_orphan_tasks"] is True


def test_hybrid_registry_only_recovers_orphans_once_per_process(tmp_path, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")
    workspace_root = tmp_path / "workspaces"
    registry = WorkspacePipelineRegistry(str(workspace_root))

    pipeline = registry.get("workspace-a")
    project = pipeline.create_project("A", "private A", skip_analysis=True)
    project.frames = [StoryboardFrame(id="frame-live", scene_id="scene-1", status="processing")]
    pipeline._save_data()

    registry.discard("workspace-a")
    rebuilt = registry.get("workspace-a")
    assert rebuilt.config["recover_orphan_tasks"] is False
    assert rebuilt.scripts[project.id].frames[0].status == "processing"

    restarted = WorkspacePipelineRegistry(str(workspace_root)).get("workspace-a")
    assert restarted.scripts[project.id].frames[0].status == "failed"


def test_server_registry_leaves_orphans_to_durable_job_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "false")

    pipeline = WorkspacePipelineRegistry(str(tmp_path / "workspaces")).get("workspace-a")

    assert pipeline.config["recover_orphan_tasks"] is False


def test_registry_rejects_workspace_path_traversal(tmp_path):
    registry = WorkspacePipelineRegistry(str(tmp_path))
    with pytest.raises(ValueError, match="Invalid workspace id"):
        registry.get("../../escape")


def test_proxy_fails_closed_without_tenant_in_server_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
    proxy = PipelineProxy(
        ComicGenPipeline({"output_root": str(tmp_path / "local")}),
        WorkspacePipelineRegistry(str(tmp_path / "workspaces")),
    )

    with pytest.raises(RuntimeError, match="No authenticated workspace"):
        proxy.current()


def test_hybrid_proxy_does_not_build_unused_global_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")
    constructed = []

    def local_factory():
        constructed.append(True)
        return ComicGenPipeline({"output_root": str(tmp_path / "local")})

    proxy = PipelineProxy(
        local_factory,
        WorkspacePipelineRegistry(str(tmp_path / "workspaces")),
    )
    tenant = bind_tenant("user-a", "workspace-a")
    try:
        assert proxy.current().output_root.endswith("workspace-a/output")
    finally:
        reset_tenant(tenant)
    assert constructed == []


def test_hybrid_proxy_routes_methods_without_building_global_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")
    constructed = []
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))

    def local_factory():
        constructed.append(True)
        return ComicGenPipeline({"output_root": str(tmp_path / "local")})

    proxy = PipelineProxy(local_factory, registry)
    tenant = bind_tenant("user-a", "workspace-a")
    try:
        project = proxy.create_project("A", "private A", skip_analysis=True)
    finally:
        reset_tenant(tenant)

    assert project.title == "A"
    assert registry.get("workspace-a").scripts[project.id].title == "A"
    assert constructed == []


def test_locked_writer_refresh_keeps_transient_task_state_after_metadata_save(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENMOTION_SERVER_MODE", "false")
    monkeypatch.setenv("ENMOTION_HYBRID_MODE", "true")
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))

    with registry.locked("workspace-a") as pipeline:
        pipeline.asset_generation_tasks["task-visible"] = {
            "status": "processing",
            "progress": 25,
        }
        pipeline._save_data()

    refreshed = registry.get("workspace-a")
    assert refreshed is pipeline
    assert refreshed.asset_generation_tasks["task-visible"]["status"] == "processing"


def test_background_writer_lease_prevents_rebuild_during_provider_work(
    tmp_path,
    monkeypatch,
):
    from src.apps.web_runtime import pipeline_registry as registry_module

    monkeypatch.setattr(registry_module, "publish_workspace_snapshot", lambda _workspace_id: None)
    registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    pipeline = registry.get("workspace-a")
    project = pipeline.create_project("A", "private A", skip_analysis=True)

    registry.retain_background_writer("workspace-a", pipeline)
    project.title = "provider mutation"
    pipeline._save_data()

    # The changed file fingerprint must not construct a second mutable writer
    # while a detached provider task still owns the original object.
    assert registry.get("workspace-a") is pipeline
    registry.discard("workspace-a")
    assert registry.get("workspace-a") is pipeline

    registry.release_background_writer("workspace-a", pipeline)
    assert registry.get("workspace-a") is pipeline
    assert registry.get("workspace-a").scripts[project.id].title == "provider mutation"


def test_playground_history_and_templates_are_workspace_private(tmp_path):
    pipeline_registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    registry = WorkspacePlaygroundRegistry(pipeline_registry)
    first = registry.get("workspace-a")
    second = registry.get("workspace-b")

    assert first.storage.history_path != second.storage.history_path
    assert first.storage.templates_path != second.storage.templates_path
    assert first.service.image_output_dir != second.service.image_output_dir
