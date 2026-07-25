#!/usr/bin/env python3
"""Create or remove an isolated, synthetic Asset Library performance fixture.

The script intentionally refuses ordinary usernames and non-empty workspaces.
It never calls an AI provider and does not copy media from another workspace.
Passwords are accepted only from stdin so they do not appear in process lists.
"""

from __future__ import annotations

import argparse
import binascii
import json
import os
import shutil
import struct
import sys
import time
import uuid
import zlib
from pathlib import Path

from sqlalchemy import delete, select

from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    GlobalAssetLibrary,
    ImageAsset,
    ImageVariant,
    Prop,
    Scene,
)
from src.apps.server.database import get_database
from src.apps.server.models import LoginSession, User, Workspace
from src.apps.server.service import (
    create_user_with_personal_workspace,
    normalize_username,
    personal_workspace_for_user,
)
from src.apps.web_runtime.file_lock import interprocess_lock
from src.apps.web_runtime.pipeline_registry import WorkspacePipelineRegistry
from src.apps.web_runtime.workspace_snapshot import publish_workspace_snapshot


USERNAME_PREFIX = "enmotion-perf-"
WORKSPACE_PREFIX = "EnMotion performance fixture"
MAX_ASSETS = 2_000
MAX_UNIQUE_IMAGES = 50
IMAGE_WIDTH = 1_024
IMAGE_HEIGHT = 1_024


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _synthetic_png(seed: int) -> bytes:
    """Return deterministic, non-private RGB fixture media.

    The structured noise keeps files representative of generated art without
    requiring Pillow in the pre-optimization baseline container.
    """

    rows = bytearray()
    for y in range(IMAGE_HEIGHT):
        rows.append(0)
        for x in range(IMAGE_WIDTH):
            block = ((x // 16) * 29 + (y // 16) * 47 + seed * 61) & 0xFF
            fine = (((x // 4) * 13) ^ ((y // 4) * 7) ^ (seed * 101)) & 0x1F
            rows.extend(
                (
                    (block + fine) & 0xFF,
                    (block * 3 + x // 3 + fine) & 0xFF,
                    (block * 5 + y // 3 + fine) & 0xFF,
                )
            )
    header = struct.pack(">IIBBBBB", IMAGE_WIDTH, IMAGE_HEIGHT, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=6))
        + _png_chunk(b"IEND", b"")
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _variant(index: int, relative_path: str) -> ImageVariant:
    return ImageVariant(
        id=f"perf-image-{index:04d}",
        url=relative_path,
        created_at=1_700_000_000.0 + index,
    )


def _asset(index: int, image_count: int, *, broken: bool):
    asset_type = index % 3
    image_index = index % image_count
    relative_path = (
        "assets/perf-missing.png"
        if broken and index == image_count
        else f"assets/perf-original-{image_index:03d}.png"
    )
    variant = _variant(index, relative_path)
    common = {
        "id": f"perf-asset-{index:05d}",
        "name": f"Performance asset {index:05d}",
        "description": "Synthetic media used only for repeatable EnMotion performance verification.",
        "starred": index % 17 == 0,
    }
    if asset_type == 0:
        return (
            "character",
            Character(
                **common,
                persona=f"fixture-{index % 25:02d}",
                reference_sheet=AssetUnit(
                    selected_image_id=variant.id,
                    image_variants=[variant],
                ),
            ),
        )
    image_asset = ImageAsset(selected_id=variant.id, variants=[variant])
    if asset_type == 1:
        return "scene", Scene(**common, image_asset=image_asset)
    return "prop", Prop(**common, image_asset=image_asset)


def _require_fixture_username(username: str) -> str:
    normalized = normalize_username(username)
    if not normalized.startswith(USERNAME_PREFIX):
        raise SystemExit(f"Refusing username without required {USERNAME_PREFIX!r} prefix")
    return normalized


def _ensure_user(username: str, password: str | None) -> tuple[User, Workspace]:
    database = get_database()
    normalized = _require_fixture_username(username)
    with database.session() as session:
        user = session.scalar(select(User).where(User.username_normalized == normalized))
        if user is None:
            if not password:
                raise SystemExit("A password on stdin is required when creating the fixture user")
            user, workspace = create_user_with_personal_workspace(
                session,
                username=username,
                password=password,
                role="user",
                workspace_name=f"{WORKSPACE_PREFIX} ({username})",
            )
            session.commit()
            return user, workspace
        workspace = personal_workspace_for_user(session, user.id)
        if workspace is None or not workspace.name.startswith(WORKSPACE_PREFIX):
            raise SystemExit("Refusing an existing non-fixture workspace")
        session.expunge(user)
        session.expunge(workspace)
        return user, workspace


def _seed(
    workspace: Workspace,
    *,
    asset_count: int,
    image_count: int,
    replace: bool,
    include_broken: bool,
) -> dict[str, int]:
    registry = WorkspacePipelineRegistry()
    output_root = registry.output_root_for(workspace.id)
    with interprocess_lock(registry.lock_path_for(workspace.id)):
        pipeline = registry.get(workspace.id)
        existing_count = sum(
            len(values)
            for values in (
                pipeline.library_store.characters,
                pipeline.library_store.scenes,
                pipeline.library_store.props,
            )
        )
        if existing_count and not replace:
            raise SystemExit(
                "Fixture library is not empty; pass --replace to replace only this fixture workspace"
            )
        for path in (output_root / "assets").glob("perf-original-*.png"):
            path.unlink()
        try:
            (output_root / "assets" / "perf-missing.png").unlink()
        except FileNotFoundError:
            pass

        sizes: list[int] = []
        for index in range(image_count):
            payload = _synthetic_png(index)
            destination = output_root / "assets" / f"perf-original-{index:03d}.png"
            _atomic_write(destination, payload)
            sizes.append(len(payload))

        library = GlobalAssetLibrary()
        for index in range(asset_count):
            asset_type, value = _asset(
                index,
                image_count,
                broken=include_broken,
            )
            getattr(library, f"{asset_type}s").append(value)
        pipeline.library_store = library
        pipeline._save_library_data()
        publish_workspace_snapshot(workspace.id, force=True)

    return {
        "assets": asset_count,
        "unique_images": image_count,
        "original_bytes_total": sum(sizes),
        "original_bytes_min": min(sizes),
        "original_bytes_max": max(sizes),
    }


def _cleanup(username: str) -> dict[str, int]:
    database = get_database()
    normalized = _require_fixture_username(username)
    registry = WorkspacePipelineRegistry()
    trash: Path | None = None
    with database.session() as session:
        user = session.scalar(select(User).where(User.username_normalized == normalized))
        if user is None:
            return {"removed_users": 0, "removed_workspaces": 0}
        workspace = personal_workspace_for_user(session, user.id)
        if workspace is None or not workspace.name.startswith(WORKSPACE_PREFIX):
            raise SystemExit("Refusing cleanup of a non-fixture workspace")
        workspace_root = registry.output_root_for(workspace.id).parent
        if workspace_root.exists():
            trash = workspace_root.with_name(
                f".perf-trash-{workspace.id}-{uuid.uuid4().hex}"
            )
            os.replace(workspace_root, trash)
        try:
            # Workspace.sessions does not use delete-orphan cascading because
            # sessions are normally managed through authentication services.
            # Delete fixture sessions explicitly so SQLAlchemy never attempts
            # to null their non-nullable workspace_id during workspace cleanup.
            session.execute(
                delete(LoginSession).where(LoginSession.workspace_id == workspace.id)
            )
            session.delete(workspace)
            session.flush()
            session.delete(user)
            session.commit()
        except Exception:
            session.rollback()
            if trash is not None and trash.exists():
                os.replace(trash, workspace_root)
            raise
    if trash is not None:
        shutil.rmtree(trash)
    return {"removed_users": 1, "removed_workspaces": 1}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--assets", type=int, default=2_000)
    parser.add_argument("--unique-images", type=int, default=50)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--include-broken", action="store_true")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.assets <= MAX_ASSETS:
        parser.error(f"--assets must be between 1 and {MAX_ASSETS}")
    if not 1 <= args.unique_images <= min(MAX_UNIQUE_IMAGES, args.assets):
        parser.error(
            f"--unique-images must be between 1 and {min(MAX_UNIQUE_IMAGES, args.assets)}"
        )
    return args


def main() -> int:
    args = _parse_args()
    started = time.perf_counter()
    if args.cleanup:
        result = _cleanup(args.username)
    else:
        password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else None
        _user, workspace = _ensure_user(args.username, password)
        result = _seed(
            workspace,
            asset_count=args.assets,
            image_count=args.unique_images,
            replace=args.replace,
            include_broken=args.include_broken,
        )
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1_000)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
