"""Load segment embeddings into the database vector store.

Writes to ``well_embeddings``, which is a pgvector column on PostgreSQL and a JSON float
array on the fallback backend. Either way the analogue engine reads the same rows.

Usage:
    python -m data_pipeline.load_embeddings
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sqlalchemy import delete, select

from backend.app.core.database import get_capabilities, session_scope
from backend.app.models import ModelVersion, Well, WellEmbedding
from nwis_common import get_config, get_logger

log = get_logger("nwis.load.embeddings")


def main() -> int:
    config = get_config()
    model_dir = Path(config.get("paths.models")) / "analogue_embedding"
    from nwis_common.paths import resolve_path

    model_dir = resolve_path(model_dir)

    vectors_path = model_dir / "embeddings.npy"
    segments_path = model_dir / "segments.json"
    if not (vectors_path.exists() and segments_path.exists()):
        log.error("embeddings_missing", path=str(model_dir),
                  hint="run python -m ml.similarity.build_embeddings")
        return 1

    vectors = np.load(vectors_path)
    segments = json.loads(segments_path.read_text(encoding="utf-8"))
    if len(vectors) != len(segments):
        log.error("embedding_segment_mismatch", vectors=len(vectors), segments=len(segments))
        return 1

    capabilities = get_capabilities()
    session = session_scope()
    try:
        wells = {
            well.name: well.id
            for well in session.execute(select(Well)).scalars()
        }
        model_version = (
            session.execute(
                select(ModelVersion)
                .where(ModelVersion.name == "analogue_embedding")
                .order_by(ModelVersion.trained_at.desc())
            )
            .scalars()
            .first()
        )

        session.execute(delete(WellEmbedding))

        inserted = 0
        skipped: set[str] = set()
        for vector, segment in zip(vectors, segments):
            well_id = wells.get(segment["well_name"])
            if well_id is None:
                skipped.add(segment["well_name"])
                continue
            session.add(
                WellEmbedding(
                    well_id=well_id,
                    segment_top_m=segment["segment_top_m"],
                    segment_base_m=segment["segment_base_m"],
                    embedding=[float(v) for v in vector],
                    feature_summary={
                        "sample_count": segment["sample_count"],
                        "groups": segment["groups"],
                        "formations": segment["formations"],
                        "dominant_group": segment["dominant_group"],
                        "dominant_formation": segment["dominant_formation"],
                    },
                    model_version_id=model_version.id if model_version else None,
                )
            )
            inserted += 1

        session.commit()
        log.info(
            "embeddings_loaded",
            inserted=inserted,
            dimensions=int(vectors.shape[1]),
            wells_not_in_database=sorted(skipped) or None,
            vector_backend=capabilities.as_dict()["vector_search"],
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
