"""Profile the FORCE 2020 dataset as it actually is on disk.

Answers the questions the ML pipeline depends on:
  wells present, curves present, missingness per curve, depth ranges,
  label vocabulary and class balance, duplicate depth records, coordinate ranges.

Nothing here assumes a column exists; every lookup is guarded and reported.

Usage:
    python -m data_pipeline.force.profile
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from data_pipeline.common.profiling import (
    profile_dataframe,
    profile_to_markdown,
    write_profile,
)
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.force.profile")

# FORCE publishes numeric lithology codes. The human-readable names come from the
# published competition key and are kept here as reference data, resolved at load
# time, never inlined into model or API code.
LITHOLOGY_CODE_NAMES: dict[int, str] = {
    30000: "Sandstone",
    65030: "Sandstone/Shale",
    65000: "Shale",
    80000: "Marl",
    74000: "Dolomite",
    70000: "Limestone",
    70032: "Chalk",
    88000: "Halite",
    86000: "Anhydrite",
    99000: "Tuff",
    90000: "Coal",
    93000: "Basement",
}


def load_force_frame(path: Path, separator: str) -> pd.DataFrame:
    log.info("force_load_start", file=str(path), size_mb=round(path.stat().st_size / 1e6, 1))
    frame = pd.read_csv(path, sep=separator, low_memory=False)
    log.info("force_load_complete", rows=len(frame), columns=frame.shape[1])
    return frame


def _column_or_none(frame: pd.DataFrame, name: str) -> str | None:
    return name if name in frame.columns else None


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _safe_code(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def build_extras(frame: pd.DataFrame, config) -> tuple[dict, list[str]]:
    """Compute the dataset facts the downstream pipeline needs, defensively."""
    notes: list[str] = []
    extras: dict = {}

    well_col = _column_or_none(frame, config.get("force_pipeline.well_column"))
    depth_col = _column_or_none(frame, config.get("force_pipeline.depth_column"))
    target_col = _column_or_none(frame, config.get("force_pipeline.target_column"))

    for label, col, cfg_key in (
        ("well", well_col, "force_pipeline.well_column"),
        ("depth", depth_col, "force_pipeline.depth_column"),
        ("target", target_col, "force_pipeline.target_column"),
    ):
        if col is None:
            notes.append(
                f"Configured {label} column {config.get(cfg_key)!r} is NOT present in this file."
            )

    # ---- wells --------------------------------------------------------------
    if well_col:
        per_well = frame.groupby(well_col).size().sort_values(ascending=False)
        extras["wells"] = {
            "count": int(per_well.size),
            "rows_per_well": {
                "min": int(per_well.min()),
                "median": int(per_well.median()),
                "max": int(per_well.max()),
            },
            "names": [str(w) for w in per_well.index.tolist()],
        }
        if depth_col:
            depth_stats = frame.groupby(well_col)[depth_col].agg(["min", "max", "count"])
            extras["well_depth_ranges"] = {
                str(w): {
                    "depth_min_m": round(float(r["min"]), 3),
                    "depth_max_m": round(float(r["max"]), 3),
                    "rows": int(r["count"]),
                }
                for w, r in depth_stats.iterrows()
            }

    # ---- curve availability -------------------------------------------------
    numeric_cols = [c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]
    coverage = {c: round(float(frame[c].notna().mean()), 6) for c in numeric_cols}
    extras["curve_coverage"] = dict(sorted(coverage.items(), key=lambda kv: -kv[1]))

    threshold = float(config.get("force_pipeline.min_curve_coverage"))
    extras["curves_meeting_coverage_threshold"] = {
        "threshold": threshold,
        "curves": [c for c, v in coverage.items() if v >= threshold],
    }

    if well_col:
        # A curve can be well covered overall yet absent from many individual wells.
        presence = {
            c: int(frame.groupby(well_col)[c].apply(lambda s: bool(s.notna().any())).sum())
            for c in numeric_cols
        }
        extras["curve_well_presence"] = dict(sorted(presence.items(), key=lambda kv: -kv[1]))

    # ---- labels -------------------------------------------------------------
    if target_col:
        counts = frame[target_col].value_counts(dropna=False)
        total = int(counts.sum())
        extras["target"] = {
            "column": target_col,
            "class_count": int(counts.size),
            "distribution": [
                {
                    "code": _safe_code(code),
                    "name": LITHOLOGY_CODE_NAMES.get(_safe_int(code), "UNKNOWN_CODE"),
                    "rows": int(n),
                    "fraction": round(int(n) / total, 6),
                }
                for code, n in counts.items()
            ],
            "imbalance_ratio": (
                round(float(counts.max() / counts.min()), 2) if counts.min() > 0 else None
            ),
        }
        unmapped = [
            _safe_code(c) for c in counts.index if _safe_int(c) not in LITHOLOGY_CODE_NAMES
        ]
        if unmapped:
            notes.append(f"Lithology codes without a known name: {unmapped}")

    # ---- duplicate and out-of-order depth records ---------------------------
    if well_col and depth_col:
        dupes = int(frame.duplicated(subset=[well_col, depth_col]).sum())
        extras["duplicate_well_depth_rows"] = {
            "count": dupes,
            "fraction": round(dupes / len(frame), 8) if len(frame) else 0.0,
        }
        non_monotonic = [
            str(w)
            for w, g in frame.groupby(well_col)[depth_col]
            if not g.is_monotonic_increasing
        ]
        extras["wells_with_non_monotonic_depth"] = non_monotonic

    # ---- coordinates --------------------------------------------------------
    coord_cols = [c for c in ("X_LOC", "Y_LOC", "Z_LOC") if c in frame.columns]
    if coord_cols:
        extras["coordinates"] = {
            c: {
                "min": round(float(frame[c].min()), 3),
                "max": round(float(frame[c].max()), 3),
                "null_fraction": round(float(frame[c].isna().mean()), 6),
            }
            for c in coord_cols
        }
        notes.append(
            "X_LOC/Y_LOC magnitudes are projected metres (North Sea UTM), not degrees. "
            "A CRS transform is required before these can be mapped."
        )

    # ---- categorical geology ------------------------------------------------
    for col in ("GROUP", "FORMATION"):
        if col in frame.columns:
            vc = frame[col].value_counts(dropna=True)
            extras[f"{col.lower()}_values"] = {
                "distinct": int(vc.size),
                "null_fraction": round(float(frame[col].isna().mean()), 6),
                "top": [{"value": str(k), "rows": int(v)} for k, v in vc.head(30).items()],
            }

    return extras, notes


def main() -> int:
    config = get_config()
    raw_dir = config.path("datasets.force.raw_dir")
    separator = config.get("datasets.force.csv_separator")
    max_examples = int(config.get("profiling.max_example_values"))
    out_dir = ensure_dir("artifacts/profiling")

    train_path = raw_dir / "train.csv"
    if not train_path.exists():
        log.error(
            "force_train_missing",
            path=str(train_path),
            hint="run scripts/download_datasets.py --only force",
        )
        return 1

    frame = load_force_frame(train_path, separator)
    extras, notes = build_extras(frame, config)

    profile = profile_dataframe(
        frame,
        dataset="FORCE 2020 - train",
        source_file=str(train_path),
        max_examples=max_examples,
        notes=notes,
        extras=extras,
    )

    json_path = write_profile(profile, out_dir / "force_train_profile.json")
    md_path = out_dir / "force_train_profile.md"
    md_path.write_text(profile_to_markdown(profile), encoding="utf-8")

    log.info(
        "force_profile_written",
        json=str(json_path),
        markdown=str(md_path),
        wells=extras.get("wells", {}).get("count"),
        classes=extras.get("target", {}).get("class_count"),
        usable_curves=len(
            extras.get("curves_meeting_coverage_threshold", {}).get("curves", [])
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
