"""Train the drilling-operations anomaly detector.

Isolation Forest over rolling telemetry features. The model learns what *normal* rig
operation looks like in this dataset and flags departures from it.

Two things this model deliberately does not do:

* It does not name a failure mode. No labelled stuck-pipe / kick / loss events exist in
  either dataset, so the output is "abnormal operational behaviour" plus the features
  that drove it. Naming an event requires historical evidence, which the risk engine
  supplies separately.
* It does not claim a probability. An isolation score is not calibrated, so it is
  reported as a normalised score with an explicit decision threshold.

Because there are no anomaly labels, evaluation is qualitative and structural:
contamination rate actually achieved, score distribution, per-well and per-rig-state
breakdown, stability across wells, and overlap with recorded operational remarks.

Usage:
    python -m ml.anomaly.train
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.common.registry import ModelRecord, ModelRegistry, new_version
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.anomaly.train")

MODEL_NAME = "anomaly"


def build_features(frame: pd.DataFrame, config) -> tuple[pd.DataFrame, list[str], dict]:
    """Rolling statistics per well, in samples at the resampled cadence."""
    channels = list(config.get("anomaly.channels"))
    windows = config.section("telemetry.rolling_windows")
    statistics = list(config.get("telemetry.statistics"))
    want_rate = bool(config.get("telemetry.rate_of_change"))

    available = [c for c in channels if c in frame.columns]
    missing = [c for c in channels if c not in frame.columns]

    # A channel with no variance carries no information; using it would only inflate the
    # feature count. These are detected, not assumed.
    constant = [
        c for c in available
        if frame[c].dropna().nunique() <= 1
    ]
    usable = [c for c in available if c not in constant]

    frame = frame.sort_values(["well_name", "timestamp"]).reset_index(drop=True)
    grouped = frame.groupby("well_name", sort=False)

    engineered: dict[str, pd.Series] = {}
    for name, size in windows.items():
        size = int(size)
        for channel in usable:
            roller = grouped[channel].rolling(size, min_periods=2)
            if "mean" in statistics:
                engineered[f"{channel}_{name}_mean"] = roller.mean().reset_index(level=0, drop=True)
            if "std" in statistics:
                engineered[f"{channel}_{name}_std"] = roller.std().reset_index(level=0, drop=True)
            if "slope" in statistics:
                # Linear trend across the window, expressed per sample.
                engineered[f"{channel}_{name}_slope"] = (
                    grouped[channel].diff(size).reset_index(level=0, drop=True) / size
                )
    if want_rate:
        for channel in usable:
            engineered[f"{channel}_rate"] = grouped[channel].diff().reset_index(level=0, drop=True)

    features = pd.concat([frame, pd.DataFrame(engineered, index=frame.index)], axis=1)
    feature_columns = usable + sorted(engineered.keys())

    spec = {
        "requested_channels": channels,
        "missing_channels": missing,
        "constant_channels_excluded": constant,
        "usable_channels": usable,
        "rolling_windows_samples": {k: int(v) for k, v in windows.items()},
        "statistics": statistics,
        "rate_of_change": want_rate,
        "feature_count": len(feature_columns),
    }
    log.info("anomaly_features_built", **{k: v for k, v in spec.items()
                                          if k != "requested_channels"})
    return features, feature_columns, spec


def robust_deviation_attribution(
    values: pd.DataFrame, feature_columns: list[str], top_n: int
) -> list[list[dict]]:
    """Rank, per row, the features furthest from their normal operating value.

    This is **not** an Isolation Forest attribution — the forest does not expose one, and
    inventing an internal explanation for it would be exactly the kind of unsupported
    claim this project refuses. What it is, and what the API says it is, is a deviation
    score: how far each measurement sits from the median of the same feature over the
    operationally-active rows the model was fitted on, in standard deviations of that
    feature.

    That is a statement about the data, verifiable from the data, and it is the answer to
    the question an engineer actually asks of a flagged sample: *which reading is odd?*

    The scale is the standard deviation rather than the interquartile range, which was
    measured to be the wrong choice here. Many engineered features — the rolling slopes
    especially — are zero for well over half the active rows, so their IQR is
    approximately zero and every non-zero value divides out to hundreds of "sigma". The
    ranking then reported the same handful of slope features for every sample, which is
    not an explanation. The standard deviation is inflated by exactly those spikes, and
    ranks the readings that are genuinely unusual for the row at hand.

    Features with no spread at all are skipped: everything is infinitely far from a
    constant.
    """
    numeric = values[feature_columns].astype("float64")
    median = numeric.median()
    scale = numeric.std().replace(0.0, np.nan)
    deviation = ((numeric - median) / scale).abs()

    usable = [c for c in feature_columns if not pd.isna(scale.get(c, np.nan))]
    if not usable:
        return [[] for _ in range(len(numeric))]

    subset = deviation[usable].to_numpy()
    names = np.asarray(usable)
    order = np.argsort(-np.nan_to_num(subset, nan=-1.0), axis=1)[:, :top_n]

    attributions: list[list[dict]] = []
    for row_index in range(subset.shape[0]):
        row = []
        for column_index in order[row_index]:
            value = subset[row_index, column_index]
            if not np.isfinite(value):
                continue
            row.append(
                {
                    "feature": str(names[column_index]),
                    "contribution": round(float(value), 3),
                    "unit": "sigma from the active-row median",
                }
            )
        attributions.append(row)
    return attributions


def main() -> int:
    config = get_config()
    processed = config.path("paths.data_processed") / "volve"
    telemetry_path = processed / "telemetry.parquet"
    if not telemetry_path.exists():
        log.error("telemetry_missing", path=str(telemetry_path),
                  hint="run python -m data_pipeline.volve.prepare")
        return 1

    frame = pd.read_parquet(telemetry_path)
    log.info("telemetry_loaded", rows=len(frame), wells=frame["well_name"].nunique())

    features, feature_columns, feature_spec = build_features(frame, config)

    # Train only on operationally active rows. Idle rows are the overwhelming majority
    # and would define "normal" as "the rig is doing nothing", making every real
    # operation look anomalous.
    active = features[features["operations_active"].fillna(False)].copy()
    min_samples = int(config.get("telemetry.min_samples_for_features"))
    if len(active) < min_samples:
        log.error("insufficient_active_samples", rows=len(active), required=min_samples)
        return 1
    log.info("training_subset", active_rows=len(active), total_rows=len(features),
             active_fraction=round(len(active) / len(features), 4))

    params = dict(config.section("anomaly.isolation_forest"))
    pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", IsolationForest(**params)),
        ]
    )

    x = active[feature_columns]
    started = time.perf_counter()
    pipeline.fit(x)
    elapsed = time.perf_counter() - started

    raw_scores = pipeline.decision_function(x)          # higher = more normal
    predictions = pipeline.predict(x)                    # -1 = anomaly
    # Normalise to [0, 1] where 1 is most anomalous, so the API reports a bounded score.
    lo, hi = float(raw_scores.min()), float(raw_scores.max())
    normalised = (hi - raw_scores) / (hi - lo) if hi > lo else np.zeros_like(raw_scores)

    active["anomaly_score"] = normalised
    active["is_anomaly"] = predictions == -1
    # Why this sample is unusual, stored per row so the API can explain a flag instead of
    # reporting a bare number. See robust_deviation_attribution for what it does and does
    # not claim.
    active["contributing_features"] = robust_deviation_attribution(
        active, feature_columns, int(config.get("anomaly.attribution_top_n"))
    )

    achieved = float((predictions == -1).mean())
    per_well = (
        active.groupby("well_name")["is_anomaly"].agg(["sum", "size", "mean"]).round(4)
    )
    state_breakdown = {}
    for state in ("circulating", "rotating", "tripping"):
        if state in active.columns:
            subset = active[active[state].fillna(False)]
            if len(subset):
                state_breakdown[state] = {
                    "rows": int(len(subset)),
                    "anomaly_rate": round(float(subset["is_anomaly"].mean()), 4),
                }

    evaluation = {
        "labels_available": False,
        "evaluation_note": (
            "No labelled drilling incidents exist in either dataset, so this model cannot "
            "be scored against ground truth. The metrics below are structural: they "
            "describe the behaviour of the detector, not its accuracy at predicting "
            "failures. No accuracy, precision or recall is claimed."
        ),
        "training_rows": int(len(active)),
        "requested_contamination": float(params.get("contamination", 0.0)),
        "achieved_anomaly_rate": round(achieved, 4),
        "score_distribution": {
            "min": round(float(normalised.min()), 4),
            "p50": round(float(np.percentile(normalised, 50)), 4),
            "p90": round(float(np.percentile(normalised, 90)), 4),
            "p99": round(float(np.percentile(normalised, 99)), 4),
            "max": round(float(normalised.max()), 4),
        },
        "per_well": {
            str(well): {
                "rows": int(row["size"]),
                "anomalies": int(row["sum"]),
                "rate": float(row["mean"]),
            }
            for well, row in per_well.iterrows()
        },
        "per_rig_state": state_breakdown,
    }

    artifacts_dir = ensure_dir(Path(config.get("paths.models")) / MODEL_NAME)
    artifact_path = artifacts_dir / "isolation_forest.joblib"
    joblib.dump(
        {
            "pipeline": pipeline,
            "feature_columns": feature_columns,
            "score_range": {"min": lo, "max": hi},
            "feature_spec": feature_spec,
        },
        artifact_path,
    )

    registry = ModelRegistry(config.get("paths.models"))
    registry.register(
        ModelRecord(
            name=MODEL_NAME,
            version=new_version(),
            task="operations_anomaly_detection",
            algorithm="isolation_forest",
            dataset="Volve WITSML normalised telemetry (operationally active rows)",
            dataset_rows=int(len(active)),
            feature_columns=feature_columns,
            target_column="",
            hyperparameters=params,
            split_strategy="unsupervised_no_split",
            split_summary={
                "total_telemetry_rows": int(len(features)),
                "operationally_active_rows": int(len(active)),
                "wells": int(active["well_name"].nunique()),
            },
            metrics=evaluation,
            artifact_path=str(artifact_path),
            training_seconds=round(elapsed, 2),
            limitations=[
                "Unsupervised and unlabelled: the model flags unusual behaviour, it does "
                "not predict a named event such as stuck pipe, kick or losses.",
                "The isolation score is not a calibrated probability and must not be "
                "presented as one.",
                "Trained on completion/workover operations from three Volve wellbores "
                "(see ASSUMPTIONS A10). It describes normal behaviour for those "
                "operations, not for drilling ahead.",
                "The contamination rate is an assumption about how much of the data is "
                "unusual, not a measurement. Changing it changes the flag count directly.",
                "Contributing features are a deviation score against the active-row "
                "median, not an attribution of the forest's decision. They say which "
                "reading is unusual, not which reading the model relied on.",
            ],
        )
    )

    scored_path = processed / "anomaly_scores.parquet"
    active[
        [
            "well_name",
            "timestamp",
            "bit_depth_m",
            "anomaly_score",
            "is_anomaly",
            "contributing_features",
        ]
    ].to_parquet(scored_path, index=False)

    log.info(
        "anomaly_trained",
        seconds=round(elapsed, 2),
        features=len(feature_columns),
        active_rows=len(active),
        achieved_anomaly_rate=evaluation["achieved_anomaly_rate"],
        per_well={k: v["rate"] for k, v in evaluation["per_well"].items()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
