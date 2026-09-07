"""Embed report passages so they can be searched by meaning rather than by keyword.

Ingestion stores every chunk with ``embedding = NULL``; this stage fills them in. It is a
separate step on purpose: OCR and NLP extraction are expensive and rarely repeated, while
the embedding model is a choice that may be revisited without re-reading any PDF.

The encoder is a pretrained sentence-transformer. Its vectors have nothing to do with the
32-dimension petrophysical segment embeddings used by the analogue engine — different
model, different space, different meaning — so the two are stored with different
dimensionalities and are never compared against each other.

Vectors are L2-normalised at write time, which makes cosine similarity a dot product and
lets the pgvector cosine index and the NumPy fallback agree exactly.

Usage:
    python -m data_pipeline.documents.embed
    python -m data_pipeline.documents.embed --rebuild
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from sqlalchemy import func, select

from backend.app.core.database import session_scope
from backend.app.models import TEXT_EMBEDDING_DIMENSIONS, DocumentChunk
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.documents.embed")


def load_encoder(config):
    """Load the configured sentence-transformer.

    Raises rather than falling back to a random or hashed vector: a search index built on
    meaningless vectors would return confident nonsense, which is worse than no search.
    """
    name = str(config.get("documents.embedding.model"))
    expected = int(config.get("documents.embedding.dimensions"))
    if expected != TEXT_EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"documents.embedding.dimensions is {expected} but the document_chunks column "
            f"is {TEXT_EMBEDDING_DIMENSIONS}-dimensional. Change both together."
        )

    from sentence_transformers import SentenceTransformer

    started = time.time()
    encoder = SentenceTransformer(name)
    actual = int(encoder.get_sentence_embedding_dimension())
    if actual != expected:
        raise ValueError(
            f"{name} produces {actual}-dimension vectors but the configuration and schema "
            f"expect {expected}. Update documents.embedding.dimensions and "
            "TEXT_EMBEDDING_DIMENSIONS to match the model."
        )
    log.info("encoder_loaded", model=name, dimensions=actual,
             seconds=round(time.time() - started, 1))
    return encoder


def encode(encoder, texts: list[str], batch_size: int) -> np.ndarray:
    """Encode passages into unit-length vectors."""
    return np.asarray(
        encoder.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ),
        dtype="float32",
    )


def embed_chunks(session, config, *, rebuild: bool = False, limit: int | None = None) -> dict:
    """Populate embeddings for stored chunks. Returns a report of what was done."""
    statement = select(DocumentChunk).order_by(DocumentChunk.id)
    if not rebuild:
        statement = statement.where(DocumentChunk.embedding.is_(None))
    if limit:
        statement = statement.limit(limit)

    chunks = list(session.execute(statement).scalars())
    if not chunks:
        log.info("nothing_to_embed", rebuild=rebuild,
                 note="every stored chunk already has an embedding")
        return {"chunks_embedded": 0, "already_embedded": True}

    encoder = load_encoder(config)
    batch_size = int(config.get("documents.embedding.batch_size"))

    started = time.time()
    vectors = encode(encoder, [c.text for c in chunks], batch_size)
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = [float(v) for v in vector]
    session.flush()
    elapsed = time.time() - started

    report = {
        "chunks_embedded": len(chunks),
        "documents_covered": len({c.document_id for c in chunks}),
        "model": str(config.get("documents.embedding.model")),
        "dimensions": TEXT_EMBEDDING_DIMENSIONS,
        "rebuild": rebuild,
        "seconds": round(elapsed, 1),
        "mean_characters_per_chunk": int(np.mean([len(c.text) for c in chunks])),
    }
    log.info("chunks_embedded", **report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed document chunks for semantic search")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-embed every chunk, not only those without a vector")
    parser.add_argument("--limit", type=int, default=None,
                        help="embed at most this many chunks (for a quick check)")
    args = parser.parse_args()

    config = get_config()
    if not bool(config.get("documents.embedding.enabled")):
        log.warning("embedding_disabled", note="documents.embedding.enabled is false")
        return 0

    session = session_scope()
    try:
        report = embed_chunks(session, config, rebuild=args.rebuild, limit=args.limit)
        session.commit()

        remaining = int(
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.embedding.is_(None))
            ) or 0
        )
        report["chunks_still_without_embedding"] = remaining

        path = ensure_dir("artifacts/profiling") / "document_embeddings.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("embedding_complete", report=str(path), **report)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
