"""Knowledge extraction from drilling report text.

Extracts three things, each with a confidence and a pointer back to the exact source text:

    formation intervals   "Utsira Formation (847 - 1053 m KB)"
    measurements          depths, hole sizes, mud weights, casing sizes with units
    operational events    problem / action / outcome statements

Two deliberate choices:

* **Patterns are grounded in the corpus.** They were written after reading OCR output from
  real Sodir completion reports, not invented from a template of what drilling reports
  might look like.
* **A general-purpose NER model does not understand petroleum terminology.** spaCy supplies
  sentence segmentation and generic entities; the domain extraction is explicit, auditable
  patterns plus a domain lexicon. Anything spaCy contributes is labelled as such, so a
  reviewer can see which parts of a record came from a statistical model.

Every record keeps the sentence it came from, the page it was on, and a confidence, so an
engineer can always inspect the original evidence behind a claim.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from nwis_common import get_config, get_logger

log = get_logger("nwis.documents.extract")


# --------------------------------------------------------------------- domain lexicon

# Terms that indicate an operational problem. Kept as data so the vocabulary can be
# reviewed and extended by a drilling engineer without touching code.
PROBLEM_TERMS: dict[str, str] = {
    "stuck pipe": "stuck_pipe",
    "stuck": "stuck_pipe",
    "pack off": "pack_off",
    "packed off": "pack_off",
    "lost circulation": "lost_circulation",
    "loss of circulation": "lost_circulation",
    "lost returns": "lost_circulation",
    "mud loss": "lost_circulation",
    "losses": "lost_circulation",
    "kick": "well_control",
    "influx": "well_control",
    "gas cut": "well_control",
    "blowout": "well_control",
    "overpull": "overpull",
    "tight hole": "tight_hole",
    "tight spot": "tight_hole",
    "excessive drag": "tight_hole",
    "hole collapse": "wellbore_instability",
    "caving": "wellbore_instability",
    "washout": "wellbore_instability",
    "twist off": "equipment_failure",
    "twistoff": "equipment_failure",
    "fishing": "fishing",
    "fish": "fishing",
    "junk": "fishing",
    "leak": "equipment_failure",
    "failure": "equipment_failure",
    "failed": "equipment_failure",
    "breakdown": "equipment_failure",
    "waiting on weather": "npt_weather",
    "wait on weather": "npt_weather",
}

# Verbs that introduce a remedial action.
ACTION_TERMS = [
    "circulated", "circulate", "increased", "reduced", "pumped", "spotted", "worked",
    "reamed", "back reamed", "jarred", "pulled out", "ran in", "displaced", "raised",
    "lowered", "changed", "replaced", "installed", "set", "cemented", "squeezed",
    "milled", "washed over", "sidetracked", "plugged",
]

# Words that indicate how a situation resolved.
OUTCOME_TERMS = {
    "regained": "resolved",
    "restored": "resolved",
    "freed": "resolved",
    "released": "resolved",
    "successful": "resolved",
    "successfully": "resolved",
    "resumed": "resolved",
    "cured": "resolved",
    "stabilised": "resolved",
    "stabilized": "resolved",
    "controlled": "resolved",
    "unsuccessful": "not_resolved",
    "failed to": "not_resolved",
    "abandoned": "not_resolved",
    "sidetrack": "not_resolved",
}

# ------------------------------------------------------------------------- patterns

# "Utsira Formation (847 - 1053 m KB)" / "Hordaland Group 1053-1600 m"
FORMATION_INTERVAL = re.compile(
    r"(?P<name>[A-Z][A-Za-zaeoAEOÀ-ſ\s\-\.]{2,40}?\s+(?:Formation|Fm\.?|Group|Gp\.?))"
    r"\s*[\(\[]?\s*"
    r"(?P<top>\d{1,5}(?:[.,]\d+)?)\s*(?:-|–|to)\s*(?P<base>\d{1,5}(?:[.,]\d+)?)"
    r"\s*(?P<unit>m|meters|metres|ft|feet)\b",
    re.IGNORECASE,
)

# A depth mentioned in prose: "at 2345 m", "@ 1200 mMD", "3510 m KB"
DEPTH_MENTION = re.compile(
    r"(?:at|@|depth of|from)?\s*(?P<value>\d{2,5}(?:[.,]\d+)?)\s*"
    r"(?P<unit>m|meters|metres|ft|feet)\s*(?P<datum>MD|KB|RKB|TVD|BRT)?\b",
    re.IGNORECASE,
)

# "12 1/4\" hole", "9 5/8\" casing"
HOLE_OR_CASING = re.compile(
    r"(?P<size>\d{1,2}\s*(?:\d/\d)?)\s*(?:\"|''|inch|in\b)\s*(?P<kind>hole|casing|liner|bit)",
    re.IGNORECASE,
)

# "mud weight 1.35 sg", "MW 1.35 s.g."
MUD_WEIGHT = re.compile(
    r"(?:mud\s*weight|MW)\s*(?:of|=|:)?\s*(?P<value>\d\.\d{1,3})\s*(?P<unit>sg|s\.g\.|ppg|kg/m3)?",
    re.IGNORECASE,
)


@dataclass
class ExtractedRecord:
    """One structured fact, always traceable to its source."""

    record_type: str
    value: dict[str, Any]
    source_text: str
    page_number: int
    confidence: float
    method: str
    document_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedEvent:
    """A problem / action / outcome chain found in narrative text."""

    event_type: str
    category: str
    description: str
    source_text: str
    page_number: int
    confidence: float
    depth_m: float | None = None
    formation_name: str | None = None
    actions: list[str] = field(default_factory=list)
    outcome: str | None = None
    outcome_status: str | None = None


@lru_cache(maxsize=1)
def _nlp():
    """spaCy is used for sentence segmentation and generic entities only."""
    import spacy

    model = str(get_config().get("documents.nlp.spacy_model"))
    try:
        return spacy.load(model, disable=["lemmatizer"])
    except OSError:
        log.warning(
            "spacy_model_missing",
            model=model,
            note="falling back to regex sentence splitting; entity labels unavailable",
        )
        return None


def split_sentences(text: str) -> list[str]:
    nlp = _nlp()
    if nlp is None:
        return [s.strip() for s in re.split(r"(?<=[.;:\n])\s+", text) if s.strip()]
    # OCR output can be long; process in bounded chunks to stay within spaCy limits.
    limit = 90_000
    sentences: list[str] = []
    for start in range(0, len(text), limit):
        document = nlp(text[start : start + limit])
        sentences.extend(s.text.strip() for s in document.sents if s.text.strip())
    return sentences


def _to_metres(value: float, unit: str) -> float:
    return value * 0.3048 if unit.lower().startswith(("ft", "feet")) else value


def _number(raw: str) -> float:
    return float(raw.replace(",", "."))


def extract_formation_intervals(
    text: str, page_number: int, page_confidence: float | None
) -> list[ExtractedRecord]:
    """Formation names with an explicit depth interval."""
    records: list[ExtractedRecord] = []
    base_confidence = page_confidence if page_confidence is not None else 0.5

    for match in FORMATION_INTERVAL.finditer(text):
        top = _to_metres(_number(match.group("top")), match.group("unit"))
        base = _to_metres(_number(match.group("base")), match.group("unit"))
        if base <= top or base > 12000:
            continue  # not a plausible interval; discard rather than store nonsense
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        records.append(
            ExtractedRecord(
                record_type="formation_interval",
                value={
                    "formation_name": name,
                    "depth_top_m": round(top, 2),
                    "depth_base_m": round(base, 2),
                    "declared_unit": match.group("unit"),
                },
                source_text=match.group(0).strip(),
                page_number=page_number,
                # A regex match on OCR text is only as good as the OCR beneath it.
                confidence=round(min(1.0, base_confidence * 0.95), 4),
                method="pattern:FORMATION_INTERVAL",
            )
        )
    return records


def extract_measurements(
    text: str, page_number: int, page_confidence: float | None
) -> list[ExtractedRecord]:
    records: list[ExtractedRecord] = []
    base_confidence = page_confidence if page_confidence is not None else 0.5

    for match in HOLE_OR_CASING.finditer(text):
        records.append(
            ExtractedRecord(
                record_type="equipment_dimension",
                value={
                    "size_inches_text": re.sub(r"\s+", " ", match.group("size")).strip(),
                    "component": match.group("kind").lower(),
                },
                source_text=match.group(0).strip(),
                page_number=page_number,
                confidence=round(min(1.0, base_confidence * 0.9), 4),
                method="pattern:HOLE_OR_CASING",
            )
        )

    for match in MUD_WEIGHT.finditer(text):
        records.append(
            ExtractedRecord(
                record_type="mud_weight",
                value={
                    "value": _number(match.group("value")),
                    "unit": (match.group("unit") or "sg").lower(),
                },
                source_text=match.group(0).strip(),
                page_number=page_number,
                confidence=round(min(1.0, base_confidence * 0.9), 4),
                method="pattern:MUD_WEIGHT",
            )
        )
    return records


def _first_depth(sentence: str) -> float | None:
    match = DEPTH_MENTION.search(sentence)
    if not match:
        return None
    try:
        depth = _to_metres(_number(match.group("value")), match.group("unit"))
    except ValueError:
        return None
    return round(depth, 2) if 0 < depth <= 12000 else None


def extract_events(
    text: str, page_number: int, page_confidence: float | None
) -> list[ExtractedEvent]:
    """Problem statements, with any action and outcome stated nearby.

    Confidence is deliberately conservative: a term match in OCR text is evidence that
    something was mentioned, not proof that an incident occurred. Records reaching the
    database are always reviewable against ``source_text``.
    """
    events: list[ExtractedEvent] = []
    base_confidence = page_confidence if page_confidence is not None else 0.5
    sentences = split_sentences(text)

    for index, sentence in enumerate(sentences):
        lowered = sentence.lower()
        matched = [
            (term, category) for term, category in PROBLEM_TERMS.items() if term in lowered
        ]
        if not matched:
            continue
        # Prefer the most specific (longest) matching term.
        term, category = max(matched, key=lambda pair: len(pair[0]))

        # Look at the sentence and the one after it for a remedial action and outcome.
        window = " ".join(sentences[index : index + 2]).lower()
        actions = sorted({verb for verb in ACTION_TERMS if verb in window})
        outcome_status = None
        outcome_text = None
        for word, status in OUTCOME_TERMS.items():
            if word in window:
                outcome_status = status
                outcome_text = word
                break

        # Confidence: OCR quality, tempered by how much structure was actually found.
        confidence = base_confidence * 0.6
        if actions:
            confidence += 0.15
        if outcome_status:
            confidence += 0.15

        events.append(
            ExtractedEvent(
                event_type=category,
                category=category,
                description=sentence.strip()[:500],
                source_text=sentence.strip()[:1000],
                page_number=page_number,
                confidence=round(min(1.0, confidence), 4),
                depth_m=_first_depth(sentence),
                actions=actions,
                outcome=outcome_text,
                outcome_status=outcome_status,
            )
        )
    return events


def extract_from_pages(pages: Iterable) -> dict[str, list]:
    """Run every extractor over a document's pages."""
    records: list[ExtractedRecord] = []
    events: list[ExtractedEvent] = []

    for page in pages:
        if page.is_empty:
            continue
        records.extend(extract_formation_intervals(page.text, page.page_number, page.confidence))
        records.extend(extract_measurements(page.text, page.page_number, page.confidence))
        events.extend(extract_events(page.text, page.page_number, page.confidence))

    log.info(
        "extraction_complete",
        records=len(records),
        events=len(events),
        record_types=sorted({r.record_type for r in records}),
        event_categories=sorted({e.category for e in events}),
    )
    return {"records": records, "events": events}
