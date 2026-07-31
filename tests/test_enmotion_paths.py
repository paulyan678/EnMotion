from pathlib import Path

import pytest

from src.apps.web_runtime.workspace_paths import workspace_output_root
from src.utils.paths import (
    account_output_root,
    accounts_root,
    app_data_dir,
    output_root,
)


def test_output_defaults_to_named_documents_folder(monkeypatch, tmp_path: Path) -> None:
    documents = tmp_path / "Documents Redirected"
    monkeypatch.setenv("ENMOTION_DOCUMENTS_DIR", str(documents))
    monkeypatch.delenv("ENMOTION_OUTPUT_DIR", raising=False)

    assert output_root() == documents / "enmotion-output"
    assert accounts_root() == documents / "enmotion-output" / "accounts"


def test_explicit_data_and_output_roots_are_independent(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "private-app-data"
    output = tmp_path / "visible-output"
    monkeypatch.setenv("ENMOTION_DATA_DIR", str(data))
    monkeypatch.setenv("ENMOTION_OUTPUT_DIR", str(output))

    assert app_data_dir() == data
    assert output_root() == output
    assert data not in output.parents


def test_account_output_is_contained(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENMOTION_OUTPUT_DIR", str(tmp_path / "enmotion-output"))

    assert account_output_root("user_123") == (
        tmp_path / "enmotion-output" / "accounts" / "user_123" / "output"
    )
    with pytest.raises(ValueError):
        account_output_root("../escape")


def test_workspace_output_is_contained_without_server_dependencies(
    monkeypatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))

    assert workspace_output_root("workspace_123") == workspace_root / "workspace_123" / "output"
    for invalid in ("", "../escape", "workspace/child", "a" * 129):
        with pytest.raises(ValueError):
            workspace_output_root(invalid)
