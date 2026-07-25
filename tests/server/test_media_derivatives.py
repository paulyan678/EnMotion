from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from src.apps.web_runtime.media_derivatives import (
    backfill_referenced_image_derivatives,
    generate_image_derivatives,
    resolve_image_derivatives,
)
from src.apps.server.quotas import workspace_usage_bytes
from src.utils.media_gc import reclaim_unreferenced_workspace_media


def _rgba_fixture(path: Path, *, color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (1536, 1024), color)
    image.save(path, format="PNG")
    image.close()


def test_derivatives_are_bounded_responsive_transparent_and_idempotent(tmp_path):
    root = tmp_path / "output"
    source = root / "assets" / "hero.png"
    _rgba_fixture(source, color=(24, 80, 160, 96))

    first = generate_image_derivatives(root, "assets/hero.png")

    assert first.state == "ready"
    assert (first.original_width, first.original_height) == (1536, 1024)
    assert first.original_mime_type == "image/png"
    assert [variant.width for variant in first.variants] == [96, 384, 768]
    assert [variant.height for variant in first.variants] == [64, 256, 512]
    assert all(variant.mime_type == "image/webp" for variant in first.variants)
    assert all(variant.byte_size < 250_000 for variant in first.variants)

    derivative_paths = [root / variant.url for variant in first.variants]
    mtimes = [path.stat().st_mtime_ns for path in derivative_paths]
    with Image.open(derivative_paths[-1]) as derivative:
        assert derivative.mode == "RGBA"
        assert derivative.getextrema()[3] == (96, 96)

    second = generate_image_derivatives(root, "/files/assets/hero.png")

    assert second == first
    assert [path.stat().st_mtime_ns for path in derivative_paths] == mtimes


def test_exif_orientation_and_source_replacement_invalidate_revision(tmp_path):
    root = tmp_path / "output"
    source = root / "uploads" / "portrait.jpg"
    source.parent.mkdir(parents=True)
    original = Image.new("RGB", (1200, 800), (200, 30, 30))
    exif = Image.Exif()
    exif[274] = 6
    original.save(source, format="JPEG", quality=90, exif=exif)
    original.close()

    first = generate_image_derivatives(root, "uploads/portrait.jpg")
    assert first.state == "ready"
    assert (first.original_width, first.original_height) == (800, 1200)

    replacement = Image.new("RGB", (640, 480), (30, 200, 30))
    replacement.save(source, format="JPEG", quality=88)
    replacement.close()
    second = generate_image_derivatives(root, "uploads/portrait.jpg")

    assert second.state == "ready"
    assert second.revision != first.revision
    assert (second.original_width, second.original_height) == (640, 480)
    assert all(first.revision not in variant.url for variant in second.variants)
    old_revision_root = (
        root / "derivatives" / "images" / first.source_key[:2] / first.source_key / first.revision
    )
    assert not old_revision_root.exists()


def test_failure_and_unsupported_sources_never_remove_the_original(tmp_path):
    root = tmp_path / "output"
    broken = root / "assets" / "broken.png"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not-an-image")

    failure = generate_image_derivatives(root, "assets/broken.png")
    remote = resolve_image_derivatives(
        root,
        "https://private.example/image.png?signature=secret",
    )

    assert failure.state == "failed"
    assert failure.failure_code == "unsupported-image"
    assert broken.read_bytes() == b"not-an-image"
    assert remote.state == "unavailable"
    assert remote.variants == ()


def test_backfill_is_resumable_and_media_gc_reclaims_owned_derivatives(tmp_path):
    root = tmp_path / "output"
    source = root / "assets" / "legacy.png"
    _rgba_fixture(source, color=(40, 70, 90, 255))
    (root / "library_assets.json").write_text(
        json.dumps({"characters": [{"image_url": "assets/legacy.png"}]}),
        encoding="utf-8",
    )

    first = backfill_referenced_image_derivatives(root, limit=1)
    second = backfill_referenced_image_derivatives(root, limit=1)
    lookup = resolve_image_derivatives(root, "assets/legacy.png", schedule=False)

    assert first == {
        "candidates": 1,
        "processed": 1,
        "ready": 1,
        "failed": 0,
        "ready_existing": 0,
        "deferred": 0,
        "remaining": 0,
    }
    assert second == {
        "candidates": 1,
        "processed": 0,
        "ready": 0,
        "failed": 0,
        "ready_existing": 1,
        "deferred": 0,
        "remaining": 0,
    }
    derivative_paths = {root / variant.url for variant in lookup.variants}
    assert derivative_paths and all(path.is_file() for path in derivative_paths)

    reclaimed = {
        Path(path)
        for path in reclaim_unreferenced_workspace_media(
            deleted_value={"image_url": "assets/legacy.png"},
            remaining_values=[],
            output_root=root,
        )
    }

    assert source.resolve() in reclaimed
    assert derivative_paths.issubset(reclaimed)
    assert not source.exists()
    assert not any(path.exists() for path in derivative_paths)


def test_derivatives_are_included_in_workspace_quota_accounting(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspaces"
    root = workspace_root / "workspace-a" / "output"
    source = root / "assets" / "quota.png"
    _rgba_fixture(source, color=(20, 40, 60, 255))
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(workspace_root))

    before = workspace_usage_bytes("workspace-a")
    generated = generate_image_derivatives(root, "assets/quota.png")
    after = workspace_usage_bytes("workspace-a")
    derivative_bytes = sum(
        (root / variant.url).stat().st_size
        for variant in generated.variants
    )
    manifest_bytes = sum(
        path.stat().st_size
        for path in (root / "derivatives" / "manifests").rglob("*.json")
    )

    assert generated.state == "ready"
    assert after >= before + derivative_bytes + manifest_bytes
