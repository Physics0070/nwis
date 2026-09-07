"""Semantic search over report passages.

An engineer asking "what went wrong in the Hod formation" will not find it by keyword: the
report says "losses observed while drilling 12 1/4in section", and shares no words with the
question. Embedding the question into the same space as the passages finds it anyway.

What this returns is **passages, with their page numbers** — not answers. Nothing here
summarises, paraphrases or concludes; the engineer reads the actual scanned text and the
citation tells them exactly which page of which report to open. That boundary is
deliberate: a retrieval score is evidence, a generated summary is not.

Similarity is raw cosine in [-1, 1], the conventional measure for sentence-transformer
vectors — deliberately *not* the [0, 1] remapping used by :mod:`analogue`, whose
components are blended into a weighted score and so need a common positive range.
Passages are stored L2-normalised, so this is a dot product.

On PostgreSQL the same ranking is available through the pgvector cosine index; the NumPy
path below is the fallback, and over a corpus of this size it is exact and fast.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Document, DocumentChunk, Well
from nwis_common import get_config, get_logger

log = get_logger("nwis.document_search")


@dataclass
class PassageMatch:
    """One retrieved passage, with everything needed to go and read the original."""

    chunk_id: int
    document_id: int
    document_title: str
    well_id: int | None
    well_name: str | None
    page_number: int | None
    similarity: float
    text: str


class SearchUnavailable(RuntimeError):
    """Raised when the corpus cannot be searched, with the reason stated."""


_encoder = None


def _get_encoder(config):
    """Load the query encoder once per process.

    It must be the same model that produced the stored vectors: encoding a query with a
    different model would compare two unrelated spaces and return confident nonsense.
    """
    global _encoder
    if _encoder is None:
        from data_pipeline.documents.embed import load_encoder

        _encoder = load_encoder(config)
    return _encoder


def corpus_status(session: Session) -> dict:
    """How much of the corpus is actually searchable."""
    from sqlalchemy import func

    total = int(session.scalar(select(func.count()).select_from(DocumentChunk)) or 0)
    embedded = int(
        session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.embedding.is_not(None))
        )
        or 0
    )
    return {
        "chunks_total": total,
        "chunks_embedded": embedded,
        "searchable": embedded > 0,
        "model": str(get_config().get("documents.embedding.model")),
    }


def search(
    session: Session,
    query: str,
    *,
    limit: int | None = None,
    well_id: int | None = None,
    min_similarity: float | None = None,
) -> tuple[list[PassageMatch], dict]:
    """Rank stored passages against a natural-language query.

    Returns the matches and a provenance dictionary describing what was searched, so a
    caller can report "12 of 75 passages are indexed" rather than implying the whole
    corpus was consulted.
    """
    config = get_config()
    if not bool(config.get("documents.embedding.enabled")):
        raise SearchUnavailable(
            "Document embedding is disabled (documents.embedding.enabled is false), so "
            "report passages cannot be searched."
        )

    query = (query or "").strip()
    if not query:
        raise SearchUnavailable("A search query is required.")

    limit = limit or int(config.get("documents.embedding.max_results"))
    threshold = (
        float(config.get("documents.embedding.min_similarity"))
        if min_similarity is None
        else float(min_similarity)
    )

    statement = (
        select(DocumentChunk, Document, Well)
        .join(Document, Document.id == DocumentChunk.document_id)
        .outerjoin(Well, Well.id == Document.well_id)
        .where(DocumentChunk.embedding.is_not(None))
    )
    if well_id is not None:
        statement = statement.where(Document.well_id == well_id)

    rows = list(session.execute(statement).all())
    status = corpus_status(session)
    if not rows:
        raise SearchUnavailable(
            "No report passages have embeddings yet. Run "
            "`python -m data_pipeline.documents.embed` to build the index."
            if status["chunks_total"]
            else "No report passages have been ingested."
        )

    vector = np.asarray(
        _get_encoder(config).encode(
            [query], normalize_embeddings=True, convert_to_numpy=True
        )[0],
        dtype="float32",
    )
    matrix = np.asarray([r[0].embedding for r in rows], dtype="float32")
    # Stored vectors are unit length, so the dot product is the cosine directly.
    similarities = matrix @ vector

    order = np.argsort(-similarities)[: max(limit, 0)]
    matches = []
    for index in order:
        score = float(similarities[index])
        if score < threshold:
            break  # sorted descending, so nothing further can qualify
        chunk, document, well = rows[index]
        matches.append(
            PassageMatch(
                chunk_id=chunk.id,
                document_id=document.id,
                document_title=document.title,
                well_id=well.id if well is not None else None,
                well_name=well.name if well is not None else None,
                page_number=chunk.page_number,
                similarity=round(score, 4),
                text=chunk.text,
            )
        )

    provenance = {
        "query": query,
        "passages_searched": len(rows),
        "passages_indexed": status["chunks_embedded"],
        "passages_total": status["chunks_total"],
        "model": status["model"],
        "similarity_metric": "cosine",
        "min_similarity": threshold,
        "restricted_to_well_id": well_id,
    }
    if status["chunks_embedded"] < status["chunks_total"]:
        provenance["note"] = (
            f"{status['chunks_total'] - status['chunks_embedded']} stored passage(s) have "
            "no embedding and were not searched."
        )
    log.info("document_search", results=len(matches), **provenance)
    return matches, provenance
