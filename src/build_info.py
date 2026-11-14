"""Which code the running bot is.

The code is copied into the Docker image, so a change only runs after the
image is rebuilt. /health and the dashboard show this information so an old
image is obvious at a glance.
"""
from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Written by the Dockerfile at build time.
_BUILT_AT_FILE = _ROOT / ".built_at"


def build_info() -> dict[str, str]:
    """{"commit", "built_at"}: from the image (Docker) or from git (local run)."""
    commit = os.environ.get("BOT_COMMIT", "").strip()
    if not commit or commit == "unknown":
        commit = _git_commit() or commit or "unknown"
    built_at = os.environ.get("BOT_BUILT_AT", "").strip() or _read_built_at()
    return {"commit": commit, "built_at": built_at}


@functools.cache
def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _read_built_at() -> str:
    try:
        return _BUILT_AT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
