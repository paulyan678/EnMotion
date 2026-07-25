from pathlib import Path

import pytest

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
