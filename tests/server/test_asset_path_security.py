from __future__ import annotations

from pathlib import Path

import pytest

from src.apps.comic_gen.assets import AssetGenerator
from src.apps.comic_gen.models import Character, Prop, Scene, Script
from src.apps.comic_gen.pipeline import ComicGenPipeline, InvalidAssetAttributesError


def _asset(asset_type: str, asset_id: str):
    model = {"character": Character, "scene": Scene, "prop": Prop}[asset_type]
    return model(id=asset_id, name="Asset", description="Description")


@pytest.mark.parametrize("asset_type", ["character", "scene", "prop"])
@pytest.mark.parametrize("malicious_id", ["../../escape", "/tmp/enmotion-absolute-escape"])
def test_generic_asset_attributes_cannot_rewrite_identity(
    tmp_path, asset_type, malicious_id
):
    pipeline = ComicGenPipeline(
        {"output_root": str(tmp_path / "output"), "recover_orphan_tasks": False}
    )
    asset = _asset(asset_type, f"safe-{asset_type}")
    script = Script(
        id="project", title="Project", original_text="", created_at=1.0, updated_at=1.0
    )
    getattr(script, f"{asset_type}s").append(asset)
    pipeline.scripts[script.id] = script
    pipeline._save_data()

    with pytest.raises(InvalidAssetAttributesError, match="immutable"):
        pipeline.update_asset_attributes(
            script.id, asset.id, asset_type, {"id": malicious_id}
        )

    assert asset.id == f"safe-{asset_type}"
    assert malicious_id not in (tmp_path / "output" / "projects.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("asset_type", ["character", "scene", "prop"])
@pytest.mark.parametrize("malicious_id", ["../../escape", "/tmp/enmotion-absolute-escape"])
def test_asset_generation_rejects_unsafe_ids_before_mkdir(
    tmp_path, monkeypatch, asset_type, malicious_id
):
    output_root = tmp_path / "workspace" / "output"
    generator = AssetGenerator({"output_root": str(output_root)})

    class FailIfCalled:
        def generate(self, *_args, **_kwargs):
            raise AssertionError("provider must not run for an unsafe asset id")

    monkeypatch.setattr(generator, "_get_model_for", lambda _name: FailIfCalled())
    asset = _asset(asset_type, malicious_id)

    with pytest.raises(ValueError, match="unsafe path"):
        if asset_type == "character":
            generator.generate_character(asset, generation_type="reference_sheet")
        elif asset_type == "scene":
            generator.generate_scene(asset)
        else:
            generator.generate_prop(asset)

    assert not (tmp_path / "workspace" / "escape").exists()
    assert not Path("/tmp/enmotion-absolute-escape").exists()


def test_asset_generator_rejects_output_directory_outside_workspace(tmp_path):
    output_root = tmp_path / "workspace" / "output"
    outside = tmp_path / "outside-assets"
    generator = AssetGenerator(
        {"output_root": str(output_root), "output_dir": str(outside)}
    )

    with pytest.raises(ValueError, match="escapes"):
        generator._asset_output_path("props", "safe.png")

    assert not outside.exists()


def test_allowlisted_asset_attributes_still_update(tmp_path):
    pipeline = ComicGenPipeline(
        {"output_root": str(tmp_path / "output"), "recover_orphan_tasks": False}
    )
    character = Character(id="safe-character", name="Before", description="Before")
    script = Script(
        id="project",
        title="Project",
        original_text="",
        characters=[character],
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline.scripts[script.id] = script

    pipeline.update_asset_attributes(
        script.id,
        character.id,
        "character",
        {"name": "After", "visual_weight": 5},
    )

    assert character.name == "After"
    assert character.visual_weight == 5
