"""Keep internal implementation labels out of localized product copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOCALES = [ROOT / "frontend/messages/en.json", ROOT / "frontend/messages/zh.json"]
CATALOGS = [
    ROOT / "config/model_catalog/generated/model_catalog.json",
    ROOT / "frontend/src/generated/modelCatalog.json",
]
FORBIDDEN = (
    "new api",
    "新接口",
    "backend",
    "后端",
    "polling",
    "轮询",
    "worker",
    "工作进程",
    "api calls",
    "接口调用",
)


def _strings(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _strings(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


@pytest.mark.parametrize("locale_path", LOCALES, ids=lambda path: path.stem)
def test_localized_product_copy_hides_internal_implementation_terms(locale_path):
    values = json.loads(locale_path.read_text(encoding="utf-8"))
    violations = []
    for path, value in _strings(values):
        lowered = value.casefold()
        for term in FORBIDDEN:
            if term.casefold() in lowered:
                violations.append(f"{path}: {term!r}")
    assert not violations, "Internal terms found in product copy:\n" + "\n".join(violations)


@pytest.mark.parametrize("catalog_path", CATALOGS, ids=lambda path: path.parent.name)
def test_model_catalog_display_copy_hides_provider_implementation(catalog_path):
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    violations = []
    for path, value in _strings(catalog):
        if not path.endswith(("display_name", "description")):
            continue
        lowered = value.casefold()
        for term in ("new api", "新接口", "backend", "后端"):
            if term.casefold() in lowered:
                violations.append(f"{path}: {term!r}")
    assert not violations, "Internal terms found in model display copy:\n" + "\n".join(
        violations
    )
