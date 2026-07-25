from pathlib import Path

import pytest

from src.apps.comic_gen.pipeline import ComicGenPipeline
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


def test_playground_history_and_templates_are_workspace_private(tmp_path):
    pipeline_registry = WorkspacePipelineRegistry(str(tmp_path / "workspaces"))
    registry = WorkspacePlaygroundRegistry(pipeline_registry)
    first = registry.get("workspace-a")
    second = registry.get("workspace-b")

    assert first.storage.history_path != second.storage.history_path
    assert first.storage.templates_path != second.storage.templates_path
    assert first.service.image_output_dir != second.service.image_output_dir
