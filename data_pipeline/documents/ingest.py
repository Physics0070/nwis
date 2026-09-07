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
from sqlalchemy import select

from backend.app.core.database import session_scope
from backend.app.models import (
    Document,
    DocumentChunk,
    DrillingEvent,
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


def ingest_document(session, config, *, well: Well, row: pd.Series, max_pages: int) -> dict:
    """Download, read and extract knowledge from one report."""
    raw_dir = ensure_dir(config.get("documents.raw_dir"))
    url = str(row["wlbDocumentUrl"])
    title = str(row.get("wlbDocumentName") or row.get("wlbDocumentType") or "document")
    filename = f"{_slugify(well.name)}__{_slugify(title)}.pdf"
    path = raw_dir / filename

    existing = session.execute(
        select(Document).where(Document.file_path == str(path))
    ).scalars().first()
    if existing is not None:
        log.info("document_already_ingested", document=title, well=well.name)
        return {"skipped": True}

    if download_document(url, path) is None:
        return {"failed": True}

    log.info("document_reading", well=well.name, title=title[:60],
             size_mb=round(path.stat().st_size / 1e6, 1), max_pages=max_pages)

    pages = extract_pages(path, max_pages=max_pages)
    provenance = summarise(pages)
    if provenance["pages_with_text"] == 0:
        log.warning("document_unreadable", document=title,
                    note="no text layer and OCR produced nothing")
        return {"failed": True}

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
    chunk_index = 0
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

    # ---- structured knowledge -------------------------------------------------
    extracted = extractor.extract_from_pages(pages)
    minimum_confidence = float(config.get("documents.nlp.min_confidence"))

    stored_events = 0
    stored_mitigations = 0
    for event in extracted["events"]:
        if event.confidence < minimum_confidence:
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

    formation_records = [
        r for r in extracted["records"] if r.record_type == "formation_interval"
    ]

    session.flush()
    result = {
        "document_id": document.id,
        "well": well.name,
        "title": title,
        **provenance,
        "chunks": chunk_index,
        "events_stored": stored_events,
        "mitigations_stored": stored_mitigations,
        "formation_intervals_found": len(formation_records),
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

        log.info("ingestion_start", documents=len(index), max_pages_each=max_pages)
        results = []
        for _, row in index.iterrows():
            well = wells[str(row["wlbName"]).strip()]
            try:
                results.append(ingest_document(session, config, well=well,
                                               row=row, max_pages=max_pages))
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
