from types import SimpleNamespace

import pytest

from src.apps.comic_gen.audio import get_bgm_presets
from src.apps.comic_gen.pipeline import ComicGenPipeline


def test_missing_bgm_tracks_are_explicitly_unavailable():
    presets = get_bgm_presets()

    assert presets
    assert all(preset["url"].startswith("presets/bgm/") for preset in presets)
    assert all(preset["available"] is False for preset in presets)


def test_configured_missing_bgm_fails_instead_of_silently_exporting(tmp_path):
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.output_root = str(tmp_path)
    script = SimpleNamespace(bgm_url="presets/bgm/missing.mp3")

    with pytest.raises(ValueError, match="configured track is unavailable"):
        pipeline._maybe_apply_bgm_mux(script, str(tmp_path / "video.mp4"), "/ffmpeg")
