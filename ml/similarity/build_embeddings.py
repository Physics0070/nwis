"""Build depth-segment embeddings for the contextual analogue engine.

Similarity in NWIS is asked at a depth interval, not for a whole well: "which wells have
seen rock like the rock my bit is in now". So a well is represented as a series of
overlapping depth segments, each with its own vector.

The vector is deliberately interpretable rather than learned end-to-end. Each dimension is
a named petrophysical statistic, so an analogue match can be explained to an engineer in
terms of the curves that drove it:

    for each of N curves : mean, standard deviation, median   (3N dimensions)
    plus                 : normalised segment thickness, normalised mid-depth

Features are z-scored using statistics computed once over the whole corpus and stored
alongside the model, so a query segment is scaled exactly like the indexed ones.

Usage:
    python -m ml.similarity.build_embeddings
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from ml.common.registry import ModelRecord, ModelRegistry, new_version
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.similarity.embeddings")

MODEL_NAME = "analogue_embedding"
STATISTICS = ("mean", "std", "median")


def build_feature_names(curves: list[str]) -> list[str]:
    names = [f"{curve}_{statistic}" for curve in curves for statistic in STATISTICS]
    names += ["segment_thickness_norm", "segment_middepth_norm"]
    return names


def segment_features(
    block: pd.DataFrame,
    curves: list[str],
    depth_column: str,
    depth_scale: float,
) -> np.ndarray:
    """Compute the raw (pre-scaling) feature vector for one depth segment."""
    values: list[float] = []
    for curve in curves:
        series = block[curve].dropna() if curve in block.columns else pd.Series(dtype=float)
        if series.empty:
            values.extend([np.nan, np.nan, np.nan])
        else:
            values.extend([
                float(series.mean()),
                float(series.std()) if len(series) > 1 else 0.0,
                float(series.median()),
            ])
    top = float(block[depth_column].min())
    base = float(block[depth_column].max())
    values.append((base - top) / depth_scale)
    values.append(((top + base) / 2.0) / depth_scale)
    return np.asarray(values, dtype="float64")


def main() -> int:
    config = get_config()
    processed = config.path("paths.data_processed") / "force"
    features_path = processed / "features.parquet"
    if not features_path.exists():
        log.error("force_features_missing", path=str(features_path))
        return 1

    well_column = config.get("force_pipeline.well_column")
    depth_column = config.get("force_pipeline.depth_column")
    group_column = config.get("force_pipeline.group_column")
    formation_column = config.get("force_pipeline.formation_column")

    curves = list(config.get("analogue.embedding_curves"))
    segment_length = float(config.get("analogue.segment_length_m"))
    segment_stride = float(config.get("analogue.segment_stride_m"))
    depth_scale = float(config.get("analogue.depth_scale_m"))
    min_samples = int(config.get("analogue.min_samples_per_segment"))
    expected_dimensions = int(config.get("analogue.embedding_dim"))

    feature_names = build_feature_names(curves)
    if len(feature_names) != expected_dimensions:
        log.error(
            "embedding_dimension_mismatch",
            computed=len(feature_names),
            configured=expected_dimensions,
            hint="set analogue.embedding_dim to the computed value",
        )
        return 1

    columns = [well_column, depth_column, group_column, formation_column, *curves]
    frame = pd.read_parquet(features_path, columns=columns)
    log.info("segments_source_loaded", rows=len(frame), curves=len(curves))

    records: list[dict] = []
    raw_vectors: list[np.ndarray] = []

    for well_name, well_block in frame.groupby(well_column, sort=True):
        well_block = well_block.sort_values(depth_column)
        depths = well_block[depth_column].to_numpy()
        start, end = float(depths.min()), float(depths.max())

        top = start
        while top < end:
            base = top + segment_length
            block = well_block[
                (well_block[depth_column] >= top) & (well_block[depth_column] < base)
            ]
            if len(block) >= min_samples:
                vector = segment_features(block, curves, depth_column, depth_scale)
                raw_vectors.append(vector)

                groups = block[group_column].dropna().value_counts()
                formations = block[formation_column].dropna().value_counts()
                records.append(
                    {
                        "well_name": str(well_name),
                        "segment_top_m": float(block[depth_column].min()),
                        "segment_base_m": float(block[depth_column].max()),
                        "sample_count": int(len(block)),
                        "groups": [str(g) for g in groups.index.tolist()],
                        "formations": [str(f) for f in formations.index.tolist()],
                        "dominant_group": str(groups.index[0]) if len(groups) else None,
                        "dominant_formation": str(formations.index[0])
                        if len(formations) else None,
                    }
                )
            top += segment_stride

    if not records:
        log.error("no_segments_built")
        return 1

    matrix = np.vstack(raw_vectors)

    # Column statistics computed once over the corpus; NaN-safe so a curve missing from
    # some wells does not poison the scaler.
    centre = np.nanmean(matrix, axis=0)
    spread = np.nanstd(matrix, axis=0)
    spread[spread == 0] = 1.0

    filled = np.where(np.isnan(matrix), centre, matrix)
    scaled = (filled - centre) / spread
    # Bound extreme outliers so one absurd reading cannot dominate cosine similarity.
    clip = float(config.get("analogue.feature_clip_sigma"))
    scaled = np.clip(scaled, -clip, clip)

    out_dir = ensure_dir(Path(config.get("paths.models")) / MODEL_NAME)
    np.save(out_dir / "embeddings.npy", scaled.astype("float32"))
    (out_dir / "segments.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    scaler = {
        "feature_names": feature_names,
        "centre": centre.tolist(),
        "spread": spread.tolist(),
        "clip_sigma": clip,
        "depth_scale_m": depth_scale,
        "curves": curves,
        "statistics": list(STATISTICS),
    }
    (out_dir / "scaler.json").write_text(json.dumps(scaler, indent=2), encoding="utf-8")

    coverage = {
        "segments": len(records),
        "wells": len({r["well_name"] for r in records}),
        "dimensions": scaled.shape[1],
        "segment_length_m": segment_length,
        "segment_stride_m": segment_stride,
        "nan_fraction_before_fill": round(float(np.isnan(matrix).mean()), 6),
    }

    registry = ModelRegistry(config.get("paths.models"))
    registry.register(
        ModelRecord(
            name=MODEL_NAME,
            version=new_version(),
            task="analogue_segment_embedding",
            algorithm="standardised_petrophysical_statistics",
            dataset="FORCE 2020 processed features",
            dataset_rows=int(len(frame)),
            feature_columns=feature_names,
            target_column="",
            hyperparameters={
                "segment_length_m": segment_length,
                "segment_stride_m": segment_stride,
                "min_samples_per_segment": min_samples,
                "feature_clip_sigma": clip,
            },
            split_strategy="not_applicable_unsupervised",
            split_summary=coverage,
            metrics={"coverage": coverage},
            artifact_path=str(out_dir / "embeddings.npy"),
            limitations=[
                "Segments are built from FORCE wireline logs only; wells without logs "
                "(the Volve wellbores) have no geology embedding and are matched on the "
                "remaining similarity dimensions.",
                "Dimensions are hand-chosen petrophysical statistics, not learned. This "
                "keeps matches explainable but will underperform a trained ranking model "
                "once engineer feedback provides relevance labels.",
            ],
        )
    )

    log.info("embeddings_built", **coverage, output=str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
