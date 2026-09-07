"""Page text extraction with a pluggable OCR engine.

Strategy, in order:

1. If a PDF page has a text layer, use it. It is exact and free.
2. Otherwise rasterise the page and OCR it.

Two OCR engines are supported. Tesseract is the project's specified engine; RapidOCR is a
pip-installable ONNX engine used when the Tesseract binary is not present, so the pipeline
remains functional on machines where a system install is not possible.

Which engine ran, and the confidence it reported, is recorded on every extracted page.
Nothing downstream is allowed to treat OCR output as though it were certain.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from nwis_common import get_config, get_logger

log = get_logger("nwis.documents.ocr")

TEXT_LAYER = "pdf_text_layer"
TESSERACT = "tesseract"
RAPIDOCR = "rapidocr"


@dataclass
class PageText:
    """Text recovered from one page, with how it was obtained."""

    page_number: int
    text: str
    method: str
    confidence: float | None = None
    line_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@lru_cache(maxsize=1)
def resolve_engine() -> str:
    """Pick an OCR engine, preferring the configured one when it is actually usable."""
    config = get_config()
    preference = str(config.get("documents.ocr.engine", "auto")).lower()

    tesseract_path = config.get("documents.ocr.tesseract_cmd", None) or shutil.which("tesseract")
    tesseract_available = bool(tesseract_path)

    if preference == TESSERACT:
        if not tesseract_available:
            raise RuntimeError(
                "documents.ocr.engine is 'tesseract' but the tesseract binary was not "
                "found. Install it, set documents.ocr.tesseract_cmd, or use 'auto'."
            )
        return TESSERACT
    if preference == RAPIDOCR:
        return RAPIDOCR

    if tesseract_available:
        log.info("ocr_engine_selected", engine=TESSERACT, path=str(tesseract_path))
        return TESSERACT
    log.warning(
        "ocr_engine_fallback",
        engine=RAPIDOCR,
        reason="tesseract binary not found on this machine",
        note="page text will be attributed to rapidocr in the provenance record",
    )
    return RAPIDOCR


@lru_cache(maxsize=1)
def _rapidocr():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _ocr_image(image: np.ndarray) -> tuple[str, float | None, int]:
    engine = resolve_engine()

    if engine == TESSERACT:
        import pytesseract
        from PIL import Image

        config = get_config()
        command = config.get("documents.ocr.tesseract_cmd", None)
        if command:
            pytesseract.pytesseract.tesseract_cmd = str(command)
        language = str(config.get("documents.ocr.language"))
        data = pytesseract.image_to_data(
            Image.fromarray(image), lang=language, output_type=pytesseract.Output.DICT
        )
        words, confidences = [], []
        for word, confidence in zip(data["text"], data["conf"]):
            if word and word.strip():
                words.append(word)
                try:
                    value = float(confidence)
                    if value >= 0:
                        confidences.append(value / 100.0)
                except (TypeError, ValueError):
                    pass
        return " ".join(words), (float(np.mean(confidences)) if confidences else None), len(words)

    result, _ = _rapidocr()(image)
    if not result:
        return "", None, 0
    lines = [row[1] for row in result]
    confidences = [float(row[2]) for row in result if row[2] is not None]
    return (
        "\n".join(lines),
        (float(np.mean(confidences)) if confidences else None),
        len(lines),
    )


def _cache_path(pdf_path, max_pages: int | None, start_page: int):
    """Where the page text for this extraction is cached."""
    from nwis_common.paths import ensure_dir

    directory = ensure_dir(get_config().get("documents.cache_dir"))
    stem = Path(pdf_path).stem
    return directory / f"{stem}__p{start_page}_{max_pages}.json"


def extract_pages(
    pdf_path,
    *,
    max_pages: int | None = None,
    start_page: int = 0,
    use_cache: bool = True,
) -> list[PageText]:
    """Extract text from a PDF, using the text layer where present and OCR where not.

    OCR costs roughly a second per page per 100 DPI, so page text is cached to disk.
    Re-running extraction to improve the NLP rules then costs nothing, which matters
    because the extraction patterns are expected to be iterated on.
    """
    import json

    import pymupdf

    config = get_config()

    cache_file = _cache_path(pdf_path, max_pages, start_page)
    if use_cache and cache_file.exists():
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        log.info("page_text_cache_hit", file=cache_file.name, pages=len(payload))
        return [PageText(**entry) for entry in payload]
    dpi = int(config.get("documents.ocr.dpi"))
    min_text_chars = int(config.get("documents.ocr.min_text_layer_chars"))

    document = pymupdf.open(pdf_path)
    last = document.page_count if max_pages is None else min(
        document.page_count, start_page + max_pages
    )

    pages: list[PageText] = []
    for index in range(start_page, last):
        page = document[index]
        layer = page.get_text().strip()

        if len(layer) >= min_text_chars:
            pages.append(
                PageText(
                    page_number=index + 1,
                    text=layer,
                    method=TEXT_LAYER,
                    confidence=1.0,
                    line_count=layer.count("\n") + 1,
                )
            )
            continue

        pixmap = page.get_pixmap(dpi=dpi)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        if pixmap.n == 4:
            image = image[:, :, :3]

        try:
            text, confidence, lines = _ocr_image(image)
        except Exception as exc:  # a bad page must not kill the document
            log.warning("ocr_page_failed", page=index + 1, error=str(exc))
            pages.append(
                PageText(
                    page_number=index + 1,
                    text="",
                    method=resolve_engine(),
                    confidence=None,
                    warnings=[f"OCR failed: {exc}"],
                )
            )
            continue

        pages.append(
            PageText(
                page_number=index + 1,
                text=text,
                method=resolve_engine(),
                confidence=confidence,
                line_count=lines,
            )
        )

    document.close()

    if use_cache:
        cache_file.write_text(
            json.dumps([p.__dict__ for p in pages], indent=1), encoding="utf-8"
        )
        log.info("page_text_cached", file=cache_file.name, pages=len(pages))
    return pages


def summarise(pages: list[PageText]) -> dict[str, Any]:
    """Aggregate provenance for a processed document."""
    confidences = [p.confidence for p in pages if p.confidence is not None]
    methods: dict[str, int] = {}
    for page in pages:
        methods[page.method] = methods.get(page.method, 0) + 1
    return {
        "pages_processed": len(pages),
        "pages_with_text": sum(1 for p in pages if not p.is_empty),
        "characters": sum(len(p.text) for p in pages),
        "methods": methods,
        "mean_confidence": round(float(np.mean(confidences)), 4) if confidences else None,
        "min_confidence": round(float(np.min(confidences)), 4) if confidences else None,
    }
