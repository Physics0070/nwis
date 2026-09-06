"""Dataset profiling primitives.

The profiler makes no assumptions about which columns exist. It reports what is
actually in the file so that downstream pipelines can be driven by observed schema
rather than by hardcoded column lists.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    non_null: int
    null_count: int
    null_fraction: float
    unique_count: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    std: float | None = None
    example_values: list[Any] = field(default_factory=list)


@dataclass
class DatasetProfile:
    dataset: str
    source_file: str
    generated_at: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    notes: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
            return None
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return str(value)


def profile_column(series: pd.Series, *, max_examples: int) -> ColumnProfile:
    non_null = int(series.notna().sum())
    total = int(len(series))
    null_count = total - non_null
    profile = ColumnProfile(
        name=str(series.name),
        dtype=str(series.dtype),
        non_null=non_null,
        null_count=null_count,
        null_fraction=round(null_count / total, 6) if total else 1.0,
    )

    values = series.dropna()
    if pd.api.types.is_numeric_dtype(series):
        if non_null:
            profile.minimum = _json_safe(values.min())
            profile.maximum = _json_safe(values.max())
            profile.mean = _json_safe(values.mean())
            profile.std = _json_safe(values.std())
        profile.example_values = [_json_safe(v) for v in values.head(max_examples).tolist()]
    else:
        profile.unique_count = int(values.nunique())
        top = values.value_counts().head(max_examples)
        profile.example_values = [_json_safe(v) for v in top.index.tolist()]
    return profile


def profile_dataframe(
    frame: pd.DataFrame,
    *,
    dataset: str,
    source_file: str,
    max_examples: int,
    notes: Sequence[str] = (),
    extras: dict[str, Any] | None = None,
) -> DatasetProfile:
    return DatasetProfile(
        dataset=dataset,
        source_file=source_file,
        generated_at=datetime.now(timezone.utc).isoformat(),
        row_count=int(len(frame)),
        column_count=int(frame.shape[1]),
        columns=[profile_column(frame[c], max_examples=max_examples) for c in frame.columns],
        notes=list(notes),
        extras=extras or {},
    )


def write_profile(profile: DatasetProfile, json_path: Path) -> Path:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(profile.to_dict(), indent=2, default=_json_safe), encoding="utf-8"
    )
    return json_path


def profile_to_markdown(profile: DatasetProfile) -> str:
    lines = [
        f"# Data profile — {profile.dataset}",
        "",
        f"- Source: `{profile.source_file}`",
        f"- Generated: {profile.generated_at}",
        f"- Rows: {profile.row_count:,}",
        f"- Columns: {profile.column_count}",
        "",
    ]
    if profile.notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in profile.notes] + [""]

    lines += [
        "## Columns",
        "",
        "| Column | dtype | non-null | null % | min | max | mean |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for col in profile.columns:
        def fmt(v):
            if v is None:
                return "—"
            return f"{v:,.4g}" if isinstance(v, float) else f"{v}"

        lines.append(
            f"| `{col.name}` | {col.dtype} | {col.non_null:,} | "
            f"{col.null_fraction * 100:.1f}% | {fmt(col.minimum)} | "
            f"{fmt(col.maximum)} | {fmt(col.mean)} |"
        )
    lines.append("")
    return "\n".join(lines)
