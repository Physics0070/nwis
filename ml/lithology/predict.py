"""Run the trained lithology model over wells and store the predictions.

Training produces a model; this is what makes it usable. Without it
``GET /api/wells/{id}/lithology`` correctly reports that no predictions exist, which is
honest but not useful.

The model actually served is the one chosen by ``selected.json`` — the validation-set
winner — not simply the most recently trained artifact.

Predictions are decimated to the configured depth step, matching the stored log samples,
because a prediction every 0.152 m is far finer than any display or depth lookup needs and
would add millions of rows for no benefit.

Where a well carries a true label the actual class is stored alongside the prediction, so
the UI can show agreement and disagreement rather than only the model's opinion.

Usage:
    python -m ml.lithology.predict
    python -m ml.lithology.predict --wells 15/9-13 --wells 15/9-17
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from backend.app.core.database import session_scope
from backend.app.models import LithologyPrediction, ModelVersion, Well
from nwis_common import get_config, get_logger

log = get_logger("nwis.lithology.predict")

MODEL_NAME = "lithology"


def load_selected_model(config) -> tuple[dict, str]:
    """Load the artifact for the model chosen by the selection step."""
    models_dir = config.path("paths.models") / MODEL_NAME
    selection_path = models_dir / "selected.json"

    if selection_path.exists():
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        algorithm = selection["selected_model"]
        log.info("using_selected_model", algorithm=algorithm,
                 metric=selection.get("selection_metric"))
        if selection.get("disagreement_warning"):
            log.warning("selection_disagreement_carried_forward",
                        detail=selection["disagreement_warning"][:160])
    else:
        candidates = sorted(models_dir.glob("*.joblib"))
        if not candidates:
            raise FileNotFoundError(
                f"No trained lithology model in {models_dir}. Run ml.lithology.train."
            )
        algorithm = candidates[0].stem
        log.warning("no_selection_file", note="falling back to the first artifact found",
                    algorithm=algorithm)

    artifact_path = models_dir / f"{algorithm}.joblib"
    if not artifact_path.exists():
        raise FileNotFoundError(f"Selected model artifact missing: {artifact_path}")
    return joblib.load(artifact_path), algorithm


def main() -> int:
    parser = argparse.ArgumentParser(description="Store lithology predictions")
    parser.add_argument("--wells", action="append", default=None,
                        help="restrict to specific well names (repeatable)")
    parser.add_argument("--batch-size", type=int, default=50_000)
    args = parser.parse_args()

    config = get_config()
    processed = config.path("paths.data_processed") / "force"
    features_path = processed / "features.parquet"
    if not features_path.exists():
        log.error("features_missing", path=str(features_path))
        return 1

    bundle, algorithm = load_selected_model(config)
    pipeline = bundle["pipeline"]
    feature_columns = bundle["feature_columns"]
    inverse = {int(k): int(v) for k, v in bundle["inverse_index"].items()}
    class_names = {int(k): v for k, v in bundle["class_names"].items()}

    spec = json.loads((processed / "feature_spec.json").read_text(encoding="utf-8"))
    well_column = spec["well_column"]
    depth_column = spec["depth_column"]
    target_column = spec["target_column"]
    depth_step = float(config.get("ingestion.log_depth_step_m"))

    # DEPTH_MD is both the depth column and a model feature, so the list must be
    # de-duplicated while preserving order; pandas rejects duplicate column labels.
    requested = [well_column, depth_column, target_column, *feature_columns]
    columns = list(dict.fromkeys(requested))
    frame = pd.read_parquet(features_path, columns=columns)

    # Match the decimation used for stored log samples so predictions align with them.
    frame = frame[(frame[depth_column] % depth_step).abs() < 0.08]
    if args.wells:
        wanted = {w.strip() for w in args.wells}
        frame = frame[frame[well_column].astype(str).isin(wanted)]
    if frame.empty:
        log.error("no_rows_to_predict")
        return 1

    log.info("predicting", rows=len(frame), wells=int(frame[well_column].nunique()),
             algorithm=algorithm)

    encoded = pipeline.predict(frame[feature_columns])
    predictions = np.array([inverse[int(v)] for v in encoded])

    # Class probability, where the estimator exposes one. A model without predict_proba
    # stores null rather than a fabricated confidence.
    probabilities = None
    if hasattr(pipeline, "predict_proba"):
        try:
            probabilities = pipeline.predict_proba(frame[feature_columns]).max(axis=1)
        except Exception as exc:
            log.warning("predict_proba_unavailable", error=str(exc))

    session = session_scope()
    try:
        wells = {w.name: w.id for w in session.execute(select(Well)).scalars()}
        model_row = (
            session.execute(
                select(ModelVersion)
                .where(ModelVersion.name == MODEL_NAME)
                .order_by(ModelVersion.trained_at.desc())
            )
            .scalars()
            .first()
        )

        target_well_ids = [
            wells[name] for name in frame[well_column].astype(str).unique()
            if name in wells
        ]
        if target_well_ids:
            session.execute(
                delete(LithologyPrediction).where(
                    LithologyPrediction.well_id.in_(target_well_ids)
                )
            )

        rows, skipped = [], set()
        actuals = frame[target_column].to_numpy()
        depths = frame[depth_column].to_numpy()
        names = frame[well_column].astype(str).to_numpy()

        for index in range(len(frame)):
            well_id = wells.get(names[index])
            if well_id is None:
                skipped.add(names[index])
                continue
            code = int(predictions[index])
            actual = actuals[index]
            rows.append(
                {
                    "well_id": well_id,
                    "depth_md_m": float(depths[index]),
                    "lithology_code": code,
                    "lithology_name": class_names.get(code, str(code)),
                    "probability": (
                        round(float(probabilities[index]), 4)
                        if probabilities is not None else None
                    ),
                    "actual_code": None if pd.isna(actual) else int(actual),
                    "model_version_id": model_row.id if model_row else None,
                }
            )

        for start in range(0, len(rows), args.batch_size):
            session.bulk_insert_mappings(
                LithologyPrediction, rows[start : start + args.batch_size]
            )
        session.commit()

        matched = sum(
            1 for r in rows if r["actual_code"] is not None
            and r["actual_code"] == r["lithology_code"]
        )
        labelled = sum(1 for r in rows if r["actual_code"] is not None)

        log.info(
            "predictions_stored",
            stored=len(rows),
            wells=len(target_well_ids),
            algorithm=algorithm,
            # Agreement over the decimated rows. Not a held-out score: most of these
            # wells were in training. The honest evaluation lives in the model card.
            agreement_with_label=round(matched / labelled, 4) if labelled else None,
            wells_not_in_database=sorted(skipped) or None,
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
