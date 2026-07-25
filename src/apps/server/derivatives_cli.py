"""Bounded, resumable image-derivative maintenance command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..web_runtime.media_derivatives import (
    backfill_referenced_image_derivatives,
)
from ..web_runtime.pipeline_registry import WorkspacePipelineRegistry
from ..web_runtime.workspace_snapshot import workspace_ids_with_metadata


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id")
    parser.add_argument("--limit-per-workspace", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.limit_per_workspace <= 10_000:
        parser.error("--limit-per-workspace must be between 1 and 10000")
    return args


def main() -> int:
    args = _parse_args()
    workspace_root = (
        Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser().resolve()
    )
    registry = WorkspacePipelineRegistry(str(workspace_root))
    if args.workspace_id:
        workspace_ids = [registry.validate_workspace_id(args.workspace_id)]
    else:
        workspace_ids = workspace_ids_with_metadata(workspace_root)

    totals = {
        "workspaces": 0,
        "candidates": 0,
        "processed": 0,
        "ready": 0,
        "failed": 0,
        "ready_existing": 0,
        "deferred": 0,
        "remaining": 0,
    }
    for workspace_id in workspace_ids:
        result = backfill_referenced_image_derivatives(
            registry.output_root_for(workspace_id),
            limit=args.limit_per_workspace,
        )
        totals["workspaces"] += 1
        for key in (
            "candidates",
            "processed",
            "ready",
            "failed",
            "ready_existing",
            "deferred",
            "remaining",
        ):
            totals[key] += result[key]
    print(json.dumps(totals, sort_keys=True, separators=(",", ":")))
    return 0 if totals["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
