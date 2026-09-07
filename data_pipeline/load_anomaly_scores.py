"""Load computed anomaly scores into the database.

Without this the risk engine has no stored anomaly to look up, so the anomaly component
is only present when a caller passes a score explicitly. Loading the scores makes
`GET /api/wells/{id}/risk` work end to end on its own.

Scores are matched to wells by name and stamped with the model version that produced
them, so a score can always be traced to a specific training run.

Usage:
    python -m data_pipeline.load_anomaly_scores
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import delete, select

from backend.app.core.database import session_scope
from backend.app.models import AnomalyScore, ModelVersion, Well
from nwis_common import get_config, get_logger

log = get_logger("nwis.load.anomaly")


def main() -> int:
    config = get_config()
    path = config.path("paths.data_processed") / "volve" / "anomaly_scores.parquet"
    if not path.exists():
        log.error("anomaly_scores_missing", path=str(path),
                  hint="run python -m ml.anomaly.train")
        return 1

    frame = pd.read_parquet(path)
    log.info("anomaly_scores_loaded", rows=len(frame))

    session = session_scope()
    try:
        wells = {w.name: w.id for w in session.execute(select(Well)).scalars()}
        model = (
            session.execute(
                select(ModelVersion)
                .where(ModelVersion.name == "anomaly")
                .order_by(ModelVersion.trained_at.desc())
            )
            .scalars()
            .first()
        )
        if model is None:
            log.warning("anomaly_model_not_registered",
                        note="scores will be stored without a model version reference")

        session.execute(delete(AnomalyScore))

        rows, skipped = [], set()
        for record in frame.itertuples(index=False):
            values = record._asdict()
            well_id = wells.get(str(values["well_name"]))
            if well_id is None:
                skipped.add(str(values["well_name"]))
                continue
            depth = values.get("bit_depth_m")
            rows.append(
                {
                    "well_id": well_id,
                    "recorded_at": pd.Timestamp(values["timestamp"]).to_pydatetime(),
                    "depth_m": None if pd.isna(depth) else float(depth),
                    "score": float(values["anomaly_score"]),
                    "is_anomaly": bool(values["is_anomaly"]),
                    # The detector reports unusual behaviour, not a named failure mode,
                    # so no event type is recorded here.
                    "contributing_features": [],
                    "model_version_id": model.id if model else None,
                }
            )

        if rows:
            session.bulk_insert_mappings(AnomalyScore, rows)
        session.commit()

        flagged = sum(1 for r in rows if r["is_anomaly"])
        log.info(
            "anomaly_scores_stored",
            stored=len(rows),
            flagged_anomalous=flagged,
            anomaly_rate=round(flagged / len(rows), 4) if rows else 0.0,
            wells_not_in_database=sorted(skipped) or None,
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
