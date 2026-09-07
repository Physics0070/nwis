"""Model registry.

Every trained model writes a versioned record: which data it saw, which features, which
hyperparameters, and the metrics actually measured. The API serves these records verbatim,
so nothing displayed in the UI can be a number a human typed in.
"""
from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nwis_common.paths import ensure_dir, resolve_path


def _git_revision() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


@dataclass
class ModelRecord:
    """One trained model version."""

    name: str
    version: str
    task: str
    algorithm: str
    dataset: str
    dataset_rows: int
    feature_columns: list[str]
    target_column: str
    hyperparameters: dict[str, Any]
    split_strategy: str
    split_summary: dict[str, Any]
    metrics: dict[str, Any]
    artifact_path: str
    trained_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    training_seconds: float | None = None
    git_revision: str | None = field(default_factory=_git_revision)
    python_version: str = field(default_factory=platform.python_version)
    limitations: list[str] = field(default_factory=list)
    status: str = "trained"

    @property
    def feature_count(self) -> int:
        return len(self.feature_columns)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["feature_count"] = self.feature_count
        return payload


class ModelRegistry:
    """File-backed registry under ``artifacts/models``.

    Kept deliberately simple: one JSON per model version plus a ``latest.json`` pointer.
    MLflow can be layered on later without changing callers.
    """

    def __init__(self, root: str | Path | None = None):
        self.root = ensure_dir(resolve_path(root or "artifacts/models"))

    def model_dir(self, name: str) -> Path:
        return ensure_dir(self.root / name)

    def register(self, record: ModelRecord) -> Path:
        directory = self.model_dir(record.name)
        version_path = directory / f"{record.version}.json"
        payload = json.dumps(record.to_dict(), indent=2, default=str)
        version_path.write_text(payload, encoding="utf-8")
        (directory / "latest.json").write_text(payload, encoding="utf-8")
        return version_path

    def load_latest(self, name: str) -> dict[str, Any] | None:
        path = self.root / name / "latest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_models(self) -> list[dict[str, Any]]:
        records = []
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            latest = self.load_latest(directory.name)
            if latest:
                records.append(latest)
        return records

    def list_versions(self, name: str) -> list[str]:
        directory = self.root / name
        if not directory.is_dir():
            return []
        return sorted(
            p.stem for p in directory.glob("*.json") if p.stem != "latest"
        )


def new_version() -> str:
    """Timestamp-based version identifier, sortable and collision-free in practice."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
