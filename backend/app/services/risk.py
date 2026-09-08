"""Risk engine and alert generation.

The engine combines three independent signals into one bounded score:

    anomaly              how unusual current telemetry is, from the Isolation Forest
    historical_evidence  what happened in analogue wells at comparable depth
    rules                deterministic checks on current measurements

**Mode matters.** ``risk.mode: auto`` inspects how many labelled historical events the
database actually holds. Unless that count exceeds
``risk.min_labelled_events_for_supervised``, the engine runs as a
**hybrid_indicator** and reports no probability — because nothing in the data would make
a probability meaningful. Only a genuinely supervised, calibrated model may populate
``probability``. This distinction is the difference between a defensible system and one
that prints invented confidence.

Every assessment carries the evidence that produced it: which wells, which depth
intervals, which measurements, and which historical records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import DrillingEvent, Mitigation, RiskAlert, RiskAssessment, Well
from backend.app.services.analogue import AnalogueMatch, find_analogues
from nwis_common import get_config, get_logger

log = get_logger("nwis.risk")

SUPERVISED = "supervised"
HYBRID_INDICATOR = "hybrid_indicator"


@dataclass
class RiskComponent:
    name: str
    value: float
    weight: float
    explanation: str
    available: bool = True


@dataclass
class HistoricalEvidence:
    well_name: str
    similarity_score: float
    event_id: int
    event_type: str
    depth_start_m: float | None
    depth_end_m: float | None
    distance_from_bit_m: float | None
    description: str
    formation_name: str | None
    depth_source: str | None
    source_dataset: str | None
    source_reference: str | None
    mitigations: list[dict] = field(default_factory=list)


@dataclass
class RiskResult:
    well_id: int
    well_name: str
    depth_m: float | None
    mode: str
    score: float
    risk_level: str
    # False when not one component could be computed. The score is meaningless then, and
    # a caller must say so rather than render it.
    evaluated: bool
    probability: float | None
    components: list[RiskComponent]
    evidence: list[HistoricalEvidence]
    analogue_wells: list[dict]
    contributing_features: list[dict]
    narrative: str
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "well_id": self.well_id,
            "well_name": self.well_name,
            "depth_m": self.depth_m,
            "mode": self.mode,
            "evaluated": self.evaluated,
            "score": round(self.score, 4),
            "risk_level": self.risk_level,
            "probability": self.probability,
            "components": [asdict(c) for c in self.components],
            "historical_evidence": [asdict(e) for e in self.evidence],
            "analogue_wells": self.analogue_wells,
            "contributing_features": self.contributing_features,
            "narrative": self.narrative,
            "notes": self.notes,
        }


def resolve_mode(session: Session, config) -> tuple[str, str]:
    """Decide whether a supervised model is justified by the data actually present."""
    configured = str(config.get("risk.mode", "auto")).lower()
    if configured in {SUPERVISED, HYBRID_INDICATOR}:
        return configured, f"mode forced to {configured!r} by configuration"

    threshold = int(config.get("risk.min_labelled_events_for_supervised"))
    labelled = int(
        session.scalar(
            select(func.count())
            .select_from(DrillingEvent)
            .where(DrillingEvent.category.isnot(None))
        )
        or 0
    )
    if labelled >= threshold:
        return SUPERVISED, f"{labelled} labelled events meet the threshold of {threshold}"
    return (
        HYBRID_INDICATOR,
        f"only {labelled} categorised historical events exist (threshold {threshold}), "
        "so risk is reported as a historical indicator rather than a supervised "
        "prediction, and no probability is produced",
    )


def classify_level(score: float, thresholds: dict[str, float]) -> str:
    """Map a bounded score onto an alert level using configured thresholds."""
    ordered = sorted(thresholds.items(), key=lambda kv: float(kv[1]))
    level = ordered[0][0]
    for name, minimum in ordered:
        if score >= float(minimum):
            level = name
    return level


def gather_historical_evidence(
    session: Session,
    matches: list[AnalogueMatch],
    depth_m: float | None,
    lookahead_m: float,
    min_similarity: float,
) -> list[HistoricalEvidence]:
    """Historical **risk** events in analogue wells near or just ahead of the bit.

    Only categorised events count. The Volve WITSML message stream is an operations log,
    not an incident log: 184 of its 185 records are uncategorised remarks such as
    "Toolbox Talk Prior to Rig Up Tubing Equipment" and "Prep move to F14". Counting
    those as historical evidence produced a MEDIUM indicator "supported by 65 historical
    records" that were, in fact, routine workover housekeeping — an invented risk signal
    assembled from real rows, which is the most dangerous kind.

    A category is assigned only where the extractor recognised an actual problem class
    (wellbore instability, losses, and so on), so ``category IS NOT NULL`` is exactly the
    line between "something went wrong here" and "something happened here". Uncategorised
    remarks stay visible on the borehole track as recorded events; they simply do not
    raise a risk indicator.
    """
    if depth_m is None:
        return []

    evidence: list[HistoricalEvidence] = []
    for match in matches:
        if match.score < min_similarity:
            continue
        rows = (
            session.execute(
                select(DrillingEvent)
                .where(DrillingEvent.well_id == match.well.id)
                .where(DrillingEvent.category.isnot(None))
                .where(DrillingEvent.depth_start_m.isnot(None))
                .where(DrillingEvent.depth_start_m >= depth_m - lookahead_m)
                .where(DrillingEvent.depth_start_m <= depth_m + lookahead_m)
            )
            .scalars()
            .all()
        )
        for event in rows:
            mitigations = (
                session.execute(
                    select(Mitigation).where(Mitigation.event_id == event.id)
                )
                .scalars()
                .all()
            )
            evidence.append(
                HistoricalEvidence(
                    well_name=match.well.name,
                    similarity_score=round(match.score, 4),
                    event_id=event.id,
                    event_type=event.event_type,
                    depth_start_m=event.depth_start_m,
                    depth_end_m=event.depth_end_m,
                    distance_from_bit_m=(
                        round(event.depth_start_m - depth_m, 1)
                        if event.depth_start_m is not None
                        else None
                    ),
                    description=event.description,
                    formation_name=event.formation_name,
                    depth_source=event.depth_source,
                    source_dataset=event.source_dataset,
                    source_reference=event.source_reference,
                    mitigations=[
                        {
                            "action_taken": m.action_taken,
                            "outcome": m.outcome,
                            "outcome_status": m.outcome_status,
                            # Provenance travels with the action. The investigation
                            # drawer renders it; without these keys the "source" line
                            # was silently absent on every mitigation.
                            "source_dataset": m.source_dataset,
                            "source_reference": m.source_reference,
                        }
                        for m in mitigations
                    ],
                )
            )
    evidence.sort(key=lambda e: (-e.similarity_score, abs(e.distance_from_bit_m or 0)))
    return evidence


def nearest_offset_m(evidence: list[HistoricalEvidence], lookahead_m: float) -> float:
    """How far the closest piece of evidence sits from the bit, in metres.

    An event with no recorded offset counts as the full look-ahead window away — the
    weakest position — because an unplaced record is not evidence about this depth.

    Extracted from the scoring expression because the obvious one-liner,
    ``min(abs(e.distance_from_bit_m or lookahead) ...)``, is wrong in a way that reads as
    correct: an offset of exactly 0.0 is falsy, so a historical event recorded at
    precisely the depth the bit has reached — the strongest evidence there is — was
    replaced by the full window and scored as the weakest. Measured on real data before
    the fix: Volve F-4 reaches 2368.4 m, 16/7-5 records a fishing operation at 2368 m,
    and the historical-evidence component came back 0.000.
    """
    offsets = [
        abs(e.distance_from_bit_m) if e.distance_from_bit_m is not None else lookahead_m
        for e in evidence
    ]
    return min(offsets) if offsets else lookahead_m


def evaluate_rules(measurements: dict, config) -> tuple[float, list[str]]:
    """Deterministic checks on current measurements.

    Each rule is a configured band on a channel. Rules are transparent and auditable,
    and exist so that an obviously dangerous reading is never masked by a model that
    happens to find it statistically unremarkable.
    """
    rules = config.section("risk.rules")
    triggered: list[str] = []
    for channel, bounds in rules.items():
        value = measurements.get(channel)
        if value is None:
            continue
        low = bounds.get("min")
        high = bounds.get("max")
        if low is not None and float(value) < float(low):
            triggered.append(f"{channel} = {float(value):.2f} is below the configured "
                             f"minimum of {float(low):.2f}")
        if high is not None and float(value) > float(high):
            triggered.append(f"{channel} = {float(value):.2f} exceeds the configured "
                             f"maximum of {float(high):.2f}")
    if not rules:
        return 0.0, []
    score = min(1.0, len(triggered) / max(1, len(rules)))
    return score, triggered


def assess(
    session: Session,
    *,
    well: Well,
    depth_m: float | None,
    anomaly_score: float | None,
    contributing_features: list[dict] | None = None,
    measurements: dict | None = None,
) -> RiskResult:
    """Produce one explainable risk assessment."""
    config = get_config()
    weights = {k: float(v) for k, v in config.section("risk.weights").items()}
    lookahead = float(config.get("risk.lookahead_m"))
    min_similarity = float(config.get("risk.evidence.min_analogue_similarity"))
    min_wells = int(config.get("risk.evidence.min_supporting_wells"))
    thresholds = {k: float(v) for k, v in config.section("alerts.thresholds").items()}

    mode, mode_reason = resolve_mode(session, config)
    notes = [mode_reason]

    matches, _diagnostics = find_analogues(session, well=well, depth_m=depth_m)
    evidence = gather_historical_evidence(
        session, matches, depth_m, lookahead, min_similarity
    )

    components: list[RiskComponent] = []

    if anomaly_score is not None:
        components.append(
            RiskComponent(
                "anomaly",
                float(anomaly_score),
                weights.get("anomaly", 0.0),
                f"telemetry anomaly score {float(anomaly_score):.3f} "
                "(unsupervised; not a probability)",
            )
        )
    else:
        notes.append("No telemetry available, so the anomaly component was not computed.")

    supporting_wells = {e.well_name for e in evidence}
    if evidence and len(supporting_wells) >= min_wells:
        # Strength grows with how close the nearest historical event is and how similar
        # the wells that recorded it are.
        nearest = nearest_offset_m(evidence, lookahead)
        proximity = max(0.0, 1.0 - (nearest / lookahead))
        similarity = max(e.similarity_score for e in evidence)
        components.append(
            RiskComponent(
                "historical_evidence",
                float(proximity * similarity),
                weights.get("historical_evidence", 0.0),
                f"{len(evidence)} historical record(s) from {len(supporting_wells)} "
                f"analogue well(s); nearest is {nearest:.0f} m from the bit",
            )
        )
    else:
        notes.append(
            "No categorised historical risk event was found within the look-ahead window "
            "in wells above the similarity threshold. Uncategorised operational remarks "
            "are not counted as risk evidence."
        )

    rule_score, triggered = evaluate_rules(measurements or {}, config)
    if measurements:
        components.append(
            RiskComponent(
                "rules",
                rule_score,
                weights.get("rules", 0.0),
                "; ".join(triggered) if triggered else "no configured rule was breached",
            )
        )

    if components:
        total_weight = sum(c.weight for c in components)
        score = (
            sum(c.value * c.weight for c in components) / total_weight
            if total_weight > 0
            else 0.0
        )
    else:
        # Nothing could be computed: no telemetry, no historical evidence in range, no
        # measurements to apply rules to. A score of 0.0 classified as INFO reads as
        # "assessed, and it is fine" — which is the opposite of the truth and exactly the
        # zero-for-unknown this project refuses everywhere else.
        score = 0.0
        notes.append(
            "Risk could not be evaluated at this depth: no anomaly score, no historical "
            "evidence within the look-ahead window, and no measurements to apply rules "
            "to. This is an absence of signal, not a low risk."
        )

    evaluated = bool(components)
    level = classify_level(score, thresholds)

    narrative = _build_narrative(well, depth_m, level, components, evidence, mode)

    return RiskResult(
        well_id=well.id,
        well_name=well.name,
        depth_m=depth_m,
        mode=mode,
        score=float(score),
        risk_level=level,
        evaluated=evaluated,
        # Only a calibrated supervised model may report a probability.
        probability=None,
        components=components,
        evidence=evidence,
        analogue_wells=[
            {
                "well_name": m.well.name,
                "score": round(m.score, 4),
                "distance_km": round(m.distance_km, 2) if m.distance_km is not None else None,
                "segment_top_m": m.segment_top_m,
                "segment_base_m": m.segment_base_m,
                "dominant_formation": m.dominant_formation,
                "dimensions_used": m.dimensions_used,
                "dimensions_unavailable": m.dimensions_unavailable,
            }
            for m in matches
        ],
        contributing_features=contributing_features or [],
        narrative=narrative,
        notes=notes,
    )


def _build_narrative(
    well: Well,
    depth_m: float | None,
    level: str,
    components: list[RiskComponent],
    evidence: list[HistoricalEvidence],
    mode: str,
) -> str:
    """Plain-language summary. States what is known and what is not."""
    parts = [f"{level} indicator for {well.name}"]
    if depth_m is not None:
        parts[0] += f" at {depth_m:.0f} m MD"

    drivers = sorted(components, key=lambda c: c.value * c.weight, reverse=True)
    if drivers:
        parts.append("Driven by " + "; ".join(f"{c.name} ({c.value:.2f})" for c in drivers[:3]))
    if evidence:
        wells = sorted({e.well_name for e in evidence})
        parts.append(
            f"Supported by {len(evidence)} historical record(s) from {', '.join(wells[:3])}"
        )
    else:
        parts.append("No historical analogue evidence was found for this interval")
    if mode == HYBRID_INDICATOR:
        parts.append(
            "This is a historical risk indicator combining unsupervised anomaly "
            "detection with analogue evidence, not a calibrated probability"
        )
    return ". ".join(parts) + "."


# ------------------------------------------------------------------------- alerts


def should_raise(
    session: Session, *, well_id: int, level: str, depth_m: float | None, config
) -> tuple[bool, RiskAlert | None, str]:
    """Cooldown and depth-window deduplication.

    Prevents alert spam: the same concern at effectively the same depth within the
    cooldown window updates the existing alert instead of creating a new one.
    """
    minimum = str(config.get("alerts.minimum_level_to_raise"))
    thresholds = {k: float(v) for k, v in config.section("alerts.thresholds").items()}
    ordered = [name for name, _ in sorted(thresholds.items(), key=lambda kv: float(kv[1]))]
    if ordered.index(level) < ordered.index(minimum):
        return False, None, f"level {level} is below the configured minimum {minimum}"

    cooldown = int(config.get("alerts.cooldown_seconds"))
    depth_window = float(config.get("alerts.dedupe_depth_window_m"))
    since = datetime.now(timezone.utc) - timedelta(seconds=cooldown)

    statement = (
        select(RiskAlert)
        .where(RiskAlert.well_id == well_id)
        .where(RiskAlert.level == level)
        .where(RiskAlert.raised_at >= since)
        .order_by(RiskAlert.raised_at.desc())
    )
    recent = session.execute(statement).scalars().all()
    for alert in recent:
        if depth_m is None or alert.depth_m is None:
            return False, alert, "an alert of this level is already open within the cooldown"
        if abs(alert.depth_m - depth_m) <= depth_window:
            return (
                False,
                alert,
                f"an alert of this level already exists within {depth_window:.0f} m "
                "and the cooldown window",
            )
    return True, None, "no matching recent alert"


def persist(session: Session, result: RiskResult) -> tuple[RiskAssessment, RiskAlert | None, str]:
    """Store the assessment and raise or update an alert as the rules allow."""
    config = get_config()
    now = datetime.now(timezone.utc)

    assessment = RiskAssessment(
        well_id=result.well_id,
        assessed_at=now,
        depth_m=result.depth_m,
        mode=result.mode,
        score=result.score,
        risk_level=result.risk_level,
        probability=result.probability,
        components=[asdict(c) for c in result.components],
        evidence={
            "historical_evidence": [asdict(e) for e in result.evidence],
            "analogue_wells": result.analogue_wells,
            "contributing_features": result.contributing_features,
            "notes": result.notes,
        },
    )
    session.add(assessment)
    session.flush()

    raise_it, existing, reason = should_raise(
        session,
        well_id=result.well_id,
        level=result.risk_level,
        depth_m=result.depth_m,
        config=config,
    )

    if not raise_it:
        if existing is not None:
            existing.occurrence_count += 1
            session.flush()
        return assessment, existing, reason

    dedupe_window = float(config.get("alerts.dedupe_depth_window_m"))
    bucket = int((result.depth_m or 0) // dedupe_window)
    alert = RiskAlert(
        well_id=result.well_id,
        risk_assessment_id=assessment.id,
        raised_at=now,
        depth_m=result.depth_m,
        level=result.risk_level,
        title=f"{result.risk_level} risk indicator at "
              f"{result.depth_m:.0f} m MD" if result.depth_m is not None
              else f"{result.risk_level} risk indicator",
        summary=result.narrative,
        status="open",
        dedupe_key=f"{result.well_id}:{result.risk_level}:{bucket}",
        occurrence_count=1,
    )
    session.add(alert)
    session.flush()
    log.info("alert_raised", well_id=result.well_id, level=result.risk_level,
             depth_m=result.depth_m, alert_id=alert.id)
    return assessment, alert, "raised"
