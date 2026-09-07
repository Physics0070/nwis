"""Document ingestion: scanned well reports to searchable institutional memory.

    PDF -> page text (text layer or OCR) -> cleaning -> NLP extraction
        -> structured knowledge -> database

Every stored record keeps its document, page number, source text and confidence, because
the product promise is evidence-backed recommendations: a record an engineer cannot trace
back to a page in a report is not evidence.

Usage:
    python -m data_pipeline.documents.ingest --list
    python -m data_pipeline.documents.ingest --well 15/9-13
    python -m data_pipeline.documents.ingest --limit 3 --max-pages 40
    python -m data_pipeline.documents.ingest --start-page 30 --max-pages 202
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import pandas as pd
from sqlalchemy import func, select

from backend.app.core.database import session_scope
from backend.app.models import (
    Document,
    DocumentChunk,
    DrillingEvent,
    FormationInterval,
    Mitigation,
    Well,
)
from data_pipeline.documents import extract as extractor
from data_pipeline.documents.ocr import extract_pages, summarise
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.documents.ingest")


def load_document_index(config) -> pd.DataFrame:
    """The Sodir wellbore document index: which reports exist for which wellbore."""
    path = config.path("datasets.npd.raw_dir") / "wellbore_document.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run scripts/download_datasets.py --only npd"
        )
    frame = pd.read_csv(path, low_memory=False)
    wanted = list(config.get("documents.wanted_document_types"))
    if wanted:
        frame = frame[frame["wlbDocumentType"].isin(wanted)]
    return frame


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")


def download_document(url: str, destination: Path) -> Path | None:
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=180.0) as response:
            response.raise_for_status()
            temporary = destination.with_suffix(destination.suffix + ".part")
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
            temporary.replace(destination)
        return destination
    except Exception as exc:
        log.warning("document_download_failed", url=url, error=str(exc))
        return None


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split page text into overlapping passages for retrieval."""
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks, start = [], 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start <= 0:
            break
    return [c for c in chunks if c.strip()]


def _deepest_page_ingested(session, document_id: int) -> int:
    """The highest page number already stored for a document.

    Derived from the stored chunks rather than from ``page_count`` because a document can
    be ingested in several passes over different page ranges. Pages that produced no text
    leave no chunk, so this can understate the pages *read* — re-reading such a page on a
    later pass is free (the OCR cache holds it) and stores nothing twice.
    """
    return int(
        session.execute(
            select(func.max(DocumentChunk.page_number)).where(
                DocumentChunk.document_id == document_id
            )
        ).scalar()
        or 0
    )


def ingest_document(
    session,
    config,
    *,
    well: Well,
    row: pd.Series,
    max_pages: int,
    start_page: int = 0,
) -> dict:
    """Download, read and extract knowledge from one report.

    ``start_page`` is a zero-based page offset. The first thirty pages of these reports
    are geological sample descriptions; the drilling-operations narrative — where
    problem, action and outcome chains actually live — is deeper in. Passing a
    ``start_page`` past an already-ingested range **extends** the stored document rather
    than skipping it or creating a duplicate.
    """
    raw_dir = ensure_dir(config.get("documents.raw_dir"))
    url = str(row["wlbDocumentUrl"])
    title = str(row.get("wlbDocumentName") or row.get("wlbDocumentType") or "document")
    filename = f"{_slugify(well.name)}__{_slugify(title)}.pdf"
    path = raw_dir / filename

    document = session.execute(
        select(Document).where(Document.file_path == str(path))
    ).scalars().first()
    extending = document is not None
    already_ingested_to = _deepest_page_ingested(session, document.id) if extending else 0

    if extending and start_page + 1 <= already_ingested_to:
        log.info(
            "document_already_ingested",
            document=title,
            well=well.name,
            pages_stored_to=already_ingested_to,
            note=f"pass --start-page {already_ingested_to} or higher to read deeper",
        )
        return {"skipped": True}

    if download_document(url, path) is None:
        return {"failed": True}

    log.info("document_reading", well=well.name, title=title[:60],
             size_mb=round(path.stat().st_size / 1e6, 1),
             start_page=start_page, max_pages=max_pages, extending=extending)

    pages = extract_pages(path, max_pages=max_pages, start_page=start_page)
    if extending:
        # Guard against overlap even when the caller passes a range that reaches back
        # into stored pages: a chunk is never written for the same page twice.
        pages = [p for p in pages if p.page_number > already_ingested_to]
        if not pages:
            log.info("no_new_pages", document=title, pages_stored_to=already_ingested_to)
            return {"skipped": True}

    provenance = summarise(pages)
    if provenance["pages_with_text"] == 0:
        log.warning("document_unreadable", document=title,
                    note="no text layer and OCR produced nothing")
        return {"failed": True}

    if extending:
        document.page_count = (document.page_count or 0) + provenance["pages_processed"]
        document.ingested_characters = (
            (document.ingested_characters or 0) + provenance["characters"]
        )
        document.ocr_applied = document.ocr_applied or any(
            p.method != "pdf_text_layer" for p in pages
        )
    else:
        document = Document(
            title=title,
            document_type=str(row.get("wlbDocumentType") or "unknown"),
            well_id=well.id,
            file_path=str(path),
            page_count=provenance["pages_processed"],
            ocr_applied=any(p.method != "pdf_text_layer" for p in pages),
            ingested_characters=provenance["characters"],
        )
        session.add(document)
    session.flush()

    # ---- retrievable passages ------------------------------------------------
    chunk_size = int(config.get("documents.chunk.size_chars"))
    overlap = int(config.get("documents.chunk.overlap_chars"))
    # Continue the existing numbering; (document_id, chunk_index) is unique.
    chunk_index = int(
        session.execute(
            select(func.max(DocumentChunk.chunk_index)).where(
                DocumentChunk.document_id == document.id
            )
        ).scalar()
        or -1
    ) + 1
    chunks_added = 0
    for page in pages:
        for passage in chunk_text(page.text, chunk_size, overlap):
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=chunk_index,
                    page_number=page.page_number,
                    text=passage,
                    embedding=None,  # populated by the embedding step
                )
            )
            chunk_index += 1
            chunks_added += 1

    # ---- structured knowledge -------------------------------------------------
    extracted = extractor.extract_from_pages(pages)
    minimum_confidence = float(config.get("documents.nlp.min_confidence"))

    stored_events = 0
    stored_mitigations = 0
    for event in extracted["events"]:
        if event.confidence < minimum_confidence:
            continue
        # Extending a document re-reads pages whose text produced no chunk, so the same
        # extracted event can surface twice. Identity is the document, the page and the
        # exact source text it came from.
        duplicate = session.execute(
            select(DrillingEvent)
            .where(DrillingEvent.document_id == document.id)
            .where(DrillingEvent.raw_text == event.source_text)
            .where(DrillingEvent.event_type == event.event_type)
        ).scalars().first()
        if duplicate is not None:
            continue
        record = DrillingEvent(
            well_id=well.id,
            occurred_at=None,
            event_type=event.event_type,
            category=event.category,
            depth_start_m=event.depth_m,
            depth_end_m=event.depth_m,
            depth_source="document_text" if event.depth_m is not None else None,
            formation_name=event.formation_name,
            description=event.description,
            raw_text=event.source_text,
            extraction_confidence=event.confidence,
            extraction_method="nlp_document_extraction",
            document_id=document.id,
            source_dataset="Sodir wellbore documents",
            source_reference=f"{title} p.{event.page_number}",
        )
        session.add(record)
        session.flush()
        stored_events += 1

        # A mitigation is only recorded when an action was actually stated.
        if event.actions:
            session.add(
                Mitigation(
                    event_id=record.id,
                    action_taken="; ".join(event.actions),
                    outcome=event.outcome,
                    outcome_status=event.outcome_status,
                    raw_text=event.source_text,
                    extraction_confidence=event.confidence,
                    source_dataset="Sodir wellbore documents",
                    source_reference=f"{title} p.{event.page_number}",
                )
            )
            stored_mitigations += 1

    # Formation tops with depth intervals are the highest-value knowledge in these
    # reports, so they become first-class stratigraphy rows rather than free text.
    # They are marked as document-derived, keeping them distinguishable from the
    # FORCE-derived intervals, and a duplicate interval is not inserted twice.
    formation_records = [
        r for r in extracted["records"] if r.record_type == "formation_interval"
    ]
    stored_formations = 0
    for record in formation_records:
        if record.confidence < minimum_confidence:
            continue
        name = record.value["formation_name"]
        top = record.value["depth_top_m"]
        base = record.value["depth_base_m"]
        already = session.execute(
            select(FormationInterval)
            .where(FormationInterval.well_id == well.id)
            .where(FormationInterval.formation_name == name)
            .where(FormationInterval.depth_top_m == top)
        ).scalars().first()
        if already is not None:
            continue
        session.add(
            FormationInterval(
                well_id=well.id,
                group_name=None,
                formation_name=name,
                depth_top_m=top,
                depth_base_m=base,
                sample_count=None,
                source_dataset="Sodir wellbore documents",
                source_reference=f"{title} p.{record.page_number} "
                                 f"(confidence {record.confidence})",
            )
        )
        stored_formations += 1

    session.flush()
    result = {
        "document_id": document.id,
        "well": well.name,
        "title": title,
        **provenance,
        "extended_existing_document": extending,
        "start_page": start_page,
        "pages_stored_before": already_ingested_to,
        "chunks": chunks_added,
        "events_stored": stored_events,
        "mitigations_stored": stored_mitigations,
        "formation_intervals_found": len(formation_records),
        "formation_intervals_stored": stored_formations,
        "records_found": len(extracted["records"]),
    }
    log.info("document_ingested", **{k: v for k, v in result.items() if k != "methods"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest well report documents")
    parser.add_argument("--well", action="append", default=None,
                        help="restrict to specific wellbore names (repeatable)")
    parser.add_argument("--limit", type=int, default=None,
                        help="maximum number of documents to ingest")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="override documents.max_pages_per_document")
    parser.add_argument("--start-page", type=int, default=0,
                        help="zero-based first page to read. Past an already-ingested "
                             "range this extends the stored document instead of "
                             "skipping it, which is how the drilling-operations "
                             "narrative deeper in a report gets ingested.")
    parser.add_argument("--list", action="store_true",
                        help="list available documents without ingesting")
    args = parser.parse_args()

    config = get_config()
    index = load_document_index(config)
    max_pages = args.max_pages or int(config.get("documents.max_pages_per_document"))

    session = session_scope()
    try:
        wells = {
            well.name.replace("NO ", "").strip(): well
            for well in session.execute(select(Well)).scalars()
        }
        index = index[index["wlbName"].astype(str).str.strip().isin(wells)]
        if args.well:
            wanted = {w.strip() for w in args.well}
            index = index[index["wlbName"].astype(str).str.strip().isin(wanted)]

        if args.list:
            log.info("documents_available", count=len(index),
                     wells=int(index["wlbName"].nunique()))
            for _, row in index.head(40).iterrows():
                print(f"  {row['wlbName']:<12} {row['wlbDocumentType']:<24} "
                      f"{str(row['wlbDocumentSize']):>7} KB  {row['wlbDocumentName']}")
            return 0

        # Largest documents first: they are the substantive completion reports.
        index = index.sort_values("wlbDocumentSize", ascending=False)
        if args.limit:
            index = index.head(args.limit)

        log.info("ingestion_start", documents=len(index),
                 max_pages_each=max_pages, start_page=args.start_page)
        results = []
        for _, row in index.iterrows():
            well = wells[str(row["wlbName"]).strip()]
            try:
                results.append(ingest_document(session, config, well=well,
                                               row=row, max_pages=max_pages,
                                               start_page=args.start_page))
                session.commit()
            except Exception as exc:
                session.rollback()
                log.error("document_ingest_failed", well=well.name,
                          document=str(row.get("wlbDocumentName")), error=str(exc))

        ingested = [r for r in results if r.get("document_id")]
        summary = {
            "documents_ingested": len(ingested),
            "events_stored": sum(r.get("events_stored", 0) for r in ingested),
            "mitigations_stored": sum(r.get("mitigations_stored", 0) for r in ingested),
            "formations_stored": sum(r.get("formation_intervals_stored", 0) for r in ingested),
            "chunks": sum(r.get("chunks", 0) for r in ingested),
            "characters": sum(r.get("characters", 0) for r in ingested),
        }
        log.info("ingestion_complete", **summary)

        report_path = ensure_dir("artifacts/profiling") / "document_ingestion.json"
        report_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
