from __future__ import annotations

from src.utils.media_gc import (
    collect_workspace_media_paths,
    load_workspace_reference_values,
    reclaim_unreferenced_workspace_media,
)


def test_media_gc_reclaims_only_unreferenced_workspace_media(tmp_path):
    root = tmp_path / "output"
    storyboard = root / "storyboard"
    storyboard.mkdir(parents=True)
    orphan = storyboard / "orphan.png"
    shared = storyboard / "shared.png"
    orphan.write_bytes(b"orphan")
    shared.write_bytes(b"shared")

    deleted = {
        "frames": [
            {"image_url": "storyboard/orphan.png"},
            {"image_url": "storyboard/shared.png"},
        ]
    }
    remaining = [{"image_url": "storyboard/shared.png"}]

    reclaimed = reclaim_unreferenced_workspace_media(
        deleted_value=deleted,
        remaining_values=remaining,
        output_root=root,
    )

    assert reclaimed == [str(orphan.resolve())]
    assert not orphan.exists()
    assert shared.exists()


def test_media_gc_ignores_remote_text_and_paths_outside_workspace(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    inside = root / "inside.png"
    inside.write_bytes(b"inside")

    paths = collect_workspace_media_paths(
        {
            "prompt": "mention inside.png without treating prose as a path",
            "remote": "https://cdn.example/image.png",
            "outside": str(outside),
            "inside": "inside.png",
        },
        root,
    )

    assert paths == {inside.resolve()}
    assert outside.exists()


def test_media_gc_scans_projects_library_and_playground_before_deleting(tmp_path):
    import json

    root = tmp_path / "output"
    video = root / "video"
    video.mkdir(parents=True)
    shared = video / "shared.mp4"
    orphan = video / "orphan.mp4"
    shared.write_bytes(b"shared")
    orphan.write_bytes(b"orphan")
    (root / "projects.json").write_text(
        json.dumps([{"merged_video_url": "videos/shared.mp4"}]), encoding="utf-8"
    )
    (root / "playground_history.json").write_text("[]", encoding="utf-8")
    (root / "playground_templates.json").write_text(
        json.dumps([{"reference": "video/shared.mp4"}]), encoding="utf-8"
    )

    remaining = load_workspace_reference_values(root)
    reclaimed = reclaim_unreferenced_workspace_media(
        deleted_value={"old": ["video/shared.mp4", "video/orphan.mp4"]},
        remaining_values=remaining,
        output_root=root,
    )

    assert reclaimed == [str(orphan.resolve())]
    assert shared.exists()
    assert not orphan.exists()


def test_media_gc_callback_defers_physical_deletion(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    media = root / "image.png"
    media.write_bytes(b"image")
    queued = set()

    reclaimed = reclaim_unreferenced_workspace_media(
        deleted_value={"image_url": "image.png"},
        remaining_values=[],
        output_root=root,
        delete_callback=queued.update,
    )

    assert reclaimed == [str(media.resolve())]
    assert queued == {media.resolve()}
    assert media.exists()


def test_frame_media_cleanup_deletes_generated_files_but_preserves_shared_media(tmp_path):
    root = tmp_path / "output"
    storyboard = root / "storyboard"
    video = root / "video"
    audio = root / "audio"
    storyboard.mkdir(parents=True)
    video.mkdir(parents=True)
    audio.mkdir(parents=True)
    deleted_image = storyboard / "deleted.png"
    shared_image = storyboard / "shared.png"
    deleted_video = video / "deleted.mp4"
    deleted_audio = audio / "deleted.wav"
    for path in (deleted_image, shared_image, deleted_video, deleted_audio):
        path.write_bytes(path.name.encode("utf-8"))

    deleted_snapshot = {
        "frame": {
            "image_url": "storyboard/deleted.png",
            "t2i_image_urls": ["storyboard/shared.png"],
            "audio_url": "audio/deleted.wav",
        },
        "video_tasks": [{"video_url": "video/deleted.mp4"}],
        "generation_jobs": [
            {"result": {"image_url": "storyboard/deleted.png"}}
        ],
    }
    remaining_values = [{"frames": [{"image_url": "storyboard/shared.png"}]}]

    reclaimed = reclaim_unreferenced_workspace_media(
        deleted_value=deleted_snapshot,
        remaining_values=remaining_values,
        output_root=root,
    )

    assert set(reclaimed) == {
        str(deleted_image.resolve()),
        str(deleted_video.resolve()),
        str(deleted_audio.resolve()),
    }
    assert not deleted_image.exists()
    assert not deleted_video.exists()
    assert not deleted_audio.exists()
    assert shared_image.exists()


def test_media_gc_supports_all_stored_media_and_protects_presets(tmp_path):
    root = tmp_path / "output"
    generated = root / "video" / "generated.mkv"
    preset = root / "presets" / "bgm" / "operator.aiff"
    generated.parent.mkdir(parents=True)
    preset.parent.mkdir(parents=True)
    generated.write_bytes(b"video")
    preset.write_bytes(b"preset")

    reclaimed = reclaim_unreferenced_workspace_media(
        deleted_value={"video": "video/generated.mkv", "bgm": "presets/bgm/operator.aiff"},
        remaining_values=[],
        output_root=root,
    )

    assert reclaimed == [str(generated.resolve())]
    assert not generated.exists()
    assert preset.exists()
