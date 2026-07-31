"""Server-neutral workspace path validation shared by every runtime."""

from __future__ import annotations

import os
import re
from pathlib import Path

_SAFE_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def workspace_output_root(workspace_id: str) -> Path:
    """Resolve one workspace output root without importing server storage code."""

    value = str(workspace_id)
    if not _SAFE_WORKSPACE_ID.fullmatch(value):
        raise ValueError("Invalid workspace id")
    root = Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser().resolve()
    output = (root / value / "output").resolve()
    if root not in output.parents:
        raise ValueError("Workspace path escapes the configured root")
    return output
