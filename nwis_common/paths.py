"""Repository path resolution.

Every pipeline and service resolves paths relative to the repository root so that
scripts behave identically regardless of the directory they are invoked from.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ROOT_MARKERS = ("config/default.yaml", "CHECKLIST.md")


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Locate the repository root by walking up until a marker file is found."""
    env_root = os.environ.get("NWIS_ROOT")
    if env_root:
        return Path(env_root).resolve()

    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if candidate.is_dir() and any((candidate / m).exists() for m in _ROOT_MARKERS):
            return candidate
    # Fall back to the parent of this package.
    return here.parent.parent


def resolve_path(value: str | os.PathLike[str]) -> Path:
    """Resolve a possibly-relative configured path against the repository root."""
    path = Path(value)
    return path if path.is_absolute() else (repo_root() / path)


def ensure_dir(value: str | os.PathLike[str]) -> Path:
    """Resolve a path and make sure the directory exists."""
    path = resolve_path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path
