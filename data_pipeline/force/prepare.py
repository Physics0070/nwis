"""FORCE 2020 preprocessing: raw -> cleaned -> features -> well-level splits.

Design rules enforced here:

* Curve selection is driven by *measured* coverage in the profiling step, never by a
  hardcoded curve list.
* Splits are well-level. A well contributes rows to exactly one split, so the test
  metrics answer "how does this behave on a well it has never seen".
* Nothing is silently dropped. Every cleaning decision is counted and logged.

Outputs:
    data/processed/force/features.parquet   feature matrix + label + well + depth
    data/processed/force/split_manifest.json which well went to which split, and why
    data/processed/force/feature_spec.json   exact feature list + provenance

Usage:
    python -m data_pipeline.force.prepare
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.force.prepare")


# --------------------------------------------------------------------------- cleaning


def apply_quality_rules(frame: pd.DataFrame, config) -> tuple[pd.DataFrame, dict]:
    """Replace null sentinels and record every quality observation. Drops nothing."""
    report: dict = {"input_rows": int(len(frame))}
    sentinels = [float(v) for v in config.get("data_quality.null_sentinels")]

    numeric_cols = [c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]
    sentinel_hits: dict[str, int] = {}
    for col in numeric_cols:
        mask = frame[col].isin(sentinels)
        hits = int(mask.sum())
        if hits:
            sentinel_hits[col] = hits
            frame.loc[mask, col] = np.nan
    report["sentinel_values_nulled"] = sentinel_hits

    well_col = config.get("force_pipeline.well_column")
    depth_col = config.get("force_pipeline.depth_column")

    report["duplicate_well_depth_rows"] = int(
        frame.duplicated(subset=[well_col, depth_col]).sum()
    )
    report["null_depth_rows"] = int(frame[depth_col].isna().sum())

    # Wells too short to build depth-window features from.
    min_rows = int(config.get("force_pipeline.min_rows_per_well"))
    counts = frame.groupby(well_col).size()
    short_wells = sorted(str(w) for w in counts[counts < min_rows].index)
    report["wells_below_min_rows"] = {"threshold": min_rows, "wells": short_wells}

    log.info(
        "quality_rules_applied",
        rows=report["input_rows"],
        sentinel_columns=len(sentinel_hits),
        duplicate_rows=report["duplicate_well_depth_rows"],
        short_wells=len(short_wells),
    )
    return frame, report


def select_feature_curves(frame: pd.DataFrame, config) -> tuple[list[str], dict]:
    """Choose feature curves from observed coverage, minus configured exclusions."""
    threshold = float(config.get("force_pipeline.min_curve_coverage"))
    excluded = set(config.get("force_pipeline.excluded_features"))
    well_col = config.get("force_pipeline.well_column")

    numeric_cols = [
        c
        for c in frame.columns
        if pd.api.types.is_numeric_dtype(frame[c]) and c not in excluded and c != well_col
    ]
    coverage = {c: float(frame[c].notna().mean()) for c in numeric_cols}
    selected = sorted([c for c, v in coverage.items() if v >= threshold])

    rejected = {c: round(v, 4) for c, v in coverage.items() if v < threshold}
    provenance = {
        "coverage_threshold": threshold,
        "excluded_by_config": sorted(excluded),
        "selected": selected,
        "rejected_for_low_coverage": rejected,
        "selected_coverage": {c: round(coverage[c], 4) for c in selected},
    }
    log.info(
        "curves_selected",
        selected=len(selected),
        rejected=len(rejected),
        curves=selected,
    )
    return selected, provenance


# ------------------------------------------------------------------- feature building


def _median_sample_spacing(depths: pd.Series) -> float:
    diffs = depths.diff().dropna()
    diffs = diffs[diffs > 0]
    return float(diffs.median()) if len(diffs) else float("nan")


def build_features(
    frame: pd.DataFrame, base_curves: list[str], config
) -> tuple[pd.DataFrame, list[str], dict]:
    """Add depth-window rolling statistics and gradients, computed per well.

    Windows are configured in *metres* and converted to a sample count using the
    measured depth sampling interval, so the same configuration behaves correctly
    if sampling density differs between wells.
    """
    well_col = config.get("force_pipeline.well_column")
    depth_col = config.get("force_pipeline.depth_column")

    windows_m = [float(w) for w in config.get("force_pipeline.features.rolling_windows_m")]
    statistics = list(config.get("force_pipeline.features.rolling_statistics"))
    want_gradient = bool(config.get("force_pipeline.features.gradient"))

    frame = frame.sort_values([well_col, depth_col]).reset_index(drop=True)

    spacings = frame.groupby(well_col)[depth_col].apply(_median_sample_spacing)
    spacing = float(np.nanmedian(spacings.to_numpy()))
    log.info(
        "depth_sampling_measured",
        median_spacing_m=round(spacing, 5),
        per_well_min=round(float(np.nanmin(spacings.to_numpy())), 5),
        per_well_max=round(float(np.nanmax(spacings.to_numpy())), 5),
    )

    grouped = frame.groupby(well_col, sort=False)
    new_columns: dict[str, pd.Series] = {}
    window_samples: dict[str, int] = {}

    for window_m in windows_m:
        n_samples = max(3, int(round(window_m / spacing)))
        window_samples[f"{window_m:g}m"] = n_samples
        for curve in base_curves:
            roller = grouped[curve].rolling(n_samples, min_periods=2, center=True)
            for stat in statistics:
                series = getattr(roller, stat)().reset_index(level=0, drop=True)
                new_columns[f"{curve}_roll{window_m:g}m_{stat}"] = series

    if want_gradient:
        for curve in base_curves:
            delta = grouped[curve].diff()
            ddepth = grouped[depth_col].diff()
            new_columns[f"{curve}_grad"] = delta / ddepth.replace(0.0, np.nan)

    engineered = pd.DataFrame(new_columns, index=frame.index)
    frame = pd.concat([frame, engineered], axis=1)

    feature_columns = base_curves + sorted(engineered.columns)
    spec = {
        "base_curves": base_curves,
        "rolling_windows_m": windows_m,
        "rolling_window_samples": window_samples,
        "rolling_statistics": statistics,
        "gradient": want_gradient,
        "measured_depth_spacing_m": round(spacing, 5),
        "feature_count": len(feature_columns),
    }
    log.info("features_built", base=len(base_curves), engineered=len(engineered.columns),
             total=len(feature_columns))
    return frame, feature_columns, spec


# ------------------------------------------------------------------------ well splits


def make_well_level_splits(frame: pd.DataFrame, config) -> tuple[dict[str, str], dict]:
    """Assign whole wells to train/val/test.

    Rare lithologies live in very few wells. A naive shuffle can leave a class absent
    from a split entirely, which silently makes its metrics meaningless. Wells are
    therefore assigned rarest-class-first: the wells carrying the scarcest lithology
    are distributed across splits before common wells are dealt out.
    """
    well_col = config.get("force_pipeline.well_column")
    target_col = config.get("force_pipeline.target_column")
    split_cfg = config.section("force_pipeline.split")

    fractions = {
        "train": float(split_cfg["train_fraction"]),
        "validation": float(split_cfg["validation_fraction"]),
        "test": float(split_cfg["test_fraction"]),
    }
    total_fraction = sum(fractions.values())
    if not np.isclose(total_fraction, 1.0):
        raise ValueError(f"Split fractions must sum to 1.0, got {total_fraction}")

    rng = np.random.default_rng(int(split_cfg["random_seed"]))
    wells = sorted(frame[well_col].astype(str).unique())

    class_wells: dict[int, list[str]] = defaultdict(list)
    for well, group in frame.groupby(well_col):
        for cls in group[target_col].dropna().unique():
            class_wells[int(cls)].append(str(well))

    target_counts = {
        name: int(round(len(wells) * frac)) for name, frac in fractions.items()
    }
    # Absorb rounding drift into the training split.
    target_counts["train"] += len(wells) - sum(target_counts.values())

    assignment: dict[str, str] = {}
    order = ["train", "validation", "test"]
    assigned_per_split = {s: 0 for s in order}
    # How many wells carrying each class have landed in each split so far.
    class_split_counts: dict[int, dict[str, int]] = defaultdict(
        lambda: {s: 0 for s in order}
    )

    def _remaining(split: str) -> int:
        return target_counts[split] - assigned_per_split[split]

    def _commit(well: str, split: str, well_classes: dict[str, set[int]]) -> None:
        assignment[well] = split
        assigned_per_split[split] += 1
        for cls in well_classes.get(well, ()):
            class_split_counts[cls][split] += 1

    well_classes: dict[str, set[int]] = defaultdict(set)
    for cls, members in class_wells.items():
        for well in members:
            well_classes[well].add(cls)

    if bool(split_cfg.get("stratify_by_class_presence", True)):
        # Only genuinely scarce classes need help. A class present in nearly every well
        # (Shale, Sandstone) reaches every split on its own, and round-robining those
        # would override the configured split sizes entirely.
        rare_max_wells = int(split_cfg.get("rare_class_max_wells"))
        rare_classes = [c for c in class_wells if len(class_wells[c]) <= rare_max_wells]

        # Rarest first: each well goes to whichever split holds the fewest wells carrying
        # that class, among splits that still have quota left.
        for cls in sorted(rare_classes, key=lambda c: len(class_wells[c])):
            members = sorted(w for w in class_wells[cls] if w not in assignment)
            rng.shuffle(members)
            for well in members:
                open_splits = [s for s in order if _remaining(s) > 0] or order
                split = min(
                    open_splits,
                    key=lambda s: (class_split_counts[cls][s], -_remaining(s)),
                )
                _commit(well, split, well_classes)

    # Remaining wells fill the splits furthest from their quota.
    leftovers = [w for w in wells if w not in assignment]
    rng.shuffle(leftovers)
    for well in leftovers:
        split = max(order, key=lambda s: (_remaining(s), s == "train"))
        _commit(well, split, well_classes)

    # Report which classes actually reached each split.
    coverage: dict[str, dict] = {}
    for split_name in order:
        split_wells = [w for w, s in assignment.items() if s == split_name]
        subset = frame[frame[well_col].astype(str).isin(split_wells)]
        counts = subset[target_col].value_counts()
        coverage[split_name] = {
            "wells": sorted(split_wells),
            "well_count": len(split_wells),
            "rows": int(len(subset)),
            "classes_present": int(counts.size),
            "class_rows": {str(int(k)): int(v) for k, v in counts.items()},
        }

    all_classes = {int(c) for c in frame[target_col].dropna().unique()}
    missing = {
        s: sorted(all_classes - {int(k) for k in coverage[s]["class_rows"]}) for s in order
    }
    manifest = {
        "strategy": split_cfg["strategy"],
        "random_seed": int(split_cfg["random_seed"]),
        "fractions": fractions,
        "assignment": assignment,
        "coverage": coverage,
        "classes_absent_from_split": missing,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        "splits_created",
        train_wells=coverage["train"]["well_count"],
        validation_wells=coverage["validation"]["well_count"],
        test_wells=coverage["test"]["well_count"],
        classes_absent=({k: v for k, v in missing.items() if v} or None),
    )
    return assignment, manifest


# ----------------------------------------------------------------------------- driver


def main() -> int:
    config = get_config()
    raw_dir = config.path("datasets.force.raw_dir")
    separator = config.get("datasets.force.csv_separator")
    out_dir = ensure_dir(Path(config.get("paths.data_processed")) / "force")

    well_col = config.get("force_pipeline.well_column")
    depth_col = config.get("force_pipeline.depth_column")
    target_col = config.get("force_pipeline.target_column")

    train_path = raw_dir / "train.csv"
    if not train_path.exists():
        log.error("force_train_missing", path=str(train_path))
        return 1

    log.info("prepare_start", source=str(train_path))
    frame = pd.read_csv(train_path, sep=separator, low_memory=False)

    frame, quality = apply_quality_rules(frame, config)
    base_curves, curve_provenance = select_feature_curves(frame, config)
    frame, feature_columns, feature_spec = build_features(frame, base_curves, config)
    assignment, split_manifest = make_well_level_splits(frame, config)

    frame["split"] = frame[well_col].astype(str).map(assignment)

    keep = [well_col, depth_col, target_col, "split"]
    for extra in ("GROUP", "FORMATION", "X_LOC", "Y_LOC", "Z_LOC"):
        if extra in frame.columns and extra not in keep:
            keep.append(extra)
    keep += [c for c in feature_columns if c not in keep]

    output = frame[keep].copy()
    float_cols = output.select_dtypes(include=["float64"]).columns
    output[float_cols] = output[float_cols].astype("float32")

    features_path = out_dir / "features.parquet"
    output.to_parquet(features_path, index=False)

    spec = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(train_path),
        "row_count": int(len(output)),
        "well_column": well_col,
        "depth_column": depth_col,
        "target_column": target_col,
        "feature_columns": feature_columns,
        "curve_selection": curve_provenance,
        "feature_engineering": feature_spec,
        "quality_report": quality,
    }
    (out_dir / "feature_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (out_dir / "split_manifest.json").write_text(
        json.dumps(split_manifest, indent=2), encoding="utf-8"
    )

    log.info(
        "prepare_complete",
        features_parquet=str(features_path),
        rows=len(output),
        feature_count=len(feature_columns),
        size_mb=round(features_path.stat().st_size / 1e6, 1),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
