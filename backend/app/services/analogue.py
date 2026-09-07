"""Contextual analogue engine.

Ranks historical wells by how similar their situation is to the active well at a given
depth. Explicitly **not** a proximity ranking: geographic distance is one weighted
component among several, and by default it is not the largest.

Scoring dimensions (weights come from ``analogue.weights`` in configuration):

    geology            cosine similarity of standardised petrophysical segment vectors
    formation          overlap of stratigraphic names over the depth window
    depth              closeness of the compared interval to the query interval
    drilling_behaviour similarity of recorded operating envelopes
    geography          distance decay from the active well

**Honest degradation.** A dimension that cannot be computed for a candidate — a well with
no logs has no geology vector, a well with no telemetry has no behaviour profile — is
excluded and the remaining weights are renormalised. The response always reports which
dimensions actually contributed, so a score is never silently built from fewer signals
than the engineer assumes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import FormationInterval, TelemetrySample, Well, WellEmbedding
from backend.app.repositories.wells import EARTH_RADIUS_KM
from nwis_common import get_config, get_logger

log = get_logger("nwis.analogue")


@dataclass
class ComponentScore:
    name: str
    value: float
    weight: float
    detail: str


@dataclass
class AnalogueMatch:
    well: Well
    score: float
    segment_top_m: float | None
    segment_base_m: float | None
    components: list[ComponentScore]
    dimensions_used: list[str]
    dimensions_unavailable: list[str]
    distance_km: float | None
    dominant_formation: str | None
    dominant_group: str | None
    feature_summary: dict = field(default_factory=dict)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    h = (
        sin(delta_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, h)))


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity mapped from [-1, 1] onto [0, 1]."""
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    raw = float(np.dot(a, b) / denominator)
    return max(0.0, min(1.0, (raw + 1.0) / 2.0))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _behaviour_profile(session: Session, well_id: int, channels: list[str]) -> dict | None:
    """Median operating values over rows where the rig was actually working.

    Idle rows would make every well look alike, so only operationally active samples
    count. Returns None when the well has no telemetry.
    """
    rows = (
        session.execute(
            select(TelemetrySample.channels)
            .where(TelemetrySample.well_id == well_id)
            .where(TelemetrySample.operations_active.is_(True))
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    collected: dict[str, list[float]] = {name: [] for name in channels}
    for payload in rows:
        if not payload:
            continue
        for name in channels:
            value = payload.get(name)
            if value is not None:
                collected[name].append(float(value))
    profile = {
        name: float(np.median(values)) for name, values in collected.items() if values
    }
    return profile or None


def _behaviour_similarity(left: dict, right: dict, scales: dict[str, float]) -> tuple[float, int]:
    """Similarity of two operating envelopes over their shared channels."""
    shared = [name for name in left if name in right and name in scales]
    if not shared:
        return 0.0, 0
    scores = []
    for name in shared:
        scale = float(scales[name]) or 1.0
        difference = abs(left[name] - right[name]) / scale
        scores.append(float(np.exp(-difference)))
    return float(np.mean(scores)), len(shared)


def _formations_in_window(
    session: Session, well_id: int, top: float, base: float
) -> tuple[set[str], set[str]]:
    rows = (
        session.execute(
            select(FormationInterval)
            .where(FormationInterval.well_id == well_id)
            .where(FormationInterval.depth_base_m >= top)
            .where(FormationInterval.depth_top_m <= base)
        )
        .scalars()
        .all()
    )
    formations = {r.formation_name for r in rows if r.formation_name}
    groups = {r.group_name for r in rows if r.group_name}
    return formations, groups


def find_analogues(
    session: Session,
    *,
    well: Well,
    depth_m: float | None,
    top_k: int | None = None,
) -> tuple[list[AnalogueMatch], dict]:
    """Rank analogue wells for one well at one depth.

    Returns (matches, diagnostics). Diagnostics record the effective weights and which
    dimensions were available, so the API can explain the ranking rather than assert it.
    """
    config = get_config()
    weights: dict[str, float] = {
        key: float(value) for key, value in config.section("analogue.weights").items()
    }
    top_k = top_k or int(config.get("analogue.top_k"))
    candidate_pool = int(config.get("analogue.candidate_pool"))
    search_radius = float(config.get("analogue.geography.search_radius_km"))
    decay_km = float(config.get("analogue.geography.distance_decay_km"))
    depth_tolerance = float(config.get("analogue.depth.tolerance_m"))
    half_window = float(config.get("analogue.segment_length_m")) / 2.0
    behaviour_channels = list(config.get("analogue.behaviour_channels"))
    behaviour_scales = config.section("analogue.behaviour_scales")

    if depth_m is None:
        depth_m = float(well.total_depth_md_m or 0.0)
    window_top = max(0.0, depth_m - half_window)
    window_base = depth_m + half_window

    # ---- query representation -------------------------------------------------
    query_vector = _segment_vector(session, well.id, depth_m)
    query_formations, query_groups = _formations_in_window(
        session, well.id, window_top, window_base
    )
    query_behaviour = _behaviour_profile(session, well.id, behaviour_channels)

    diagnostics = {
        "query_well": well.name,
        "query_depth_m": depth_m,
        "query_window_m": [window_top, window_base],
        "configured_weights": weights,
        "query_has_geology_vector": query_vector is not None,
        "query_formations": sorted(query_formations),
        "query_has_behaviour_profile": query_behaviour is not None,
        "candidate_pool": candidate_pool,
    }

    # ---- candidates -----------------------------------------------------------
    candidates = (
        session.execute(select(Well).where(Well.id != well.id)).scalars().all()
    )

    matches: list[AnalogueMatch] = []
    for candidate in candidates:
        components: list[ComponentScore] = []
        unavailable: list[str] = []

        # geology
        candidate_segment = _best_segment(session, candidate.id, query_vector, depth_m)
        if query_vector is not None and candidate_segment is not None:
            similarity = cosine_similarity(query_vector, np.asarray(candidate_segment.embedding))
            components.append(
                ComponentScore(
                    "geology",
                    similarity,
                    weights.get("geology", 0.0),
                    f"cosine similarity of 32-dimension petrophysical vectors over "
                    f"{candidate_segment.segment_top_m:.0f}-{candidate_segment.segment_base_m:.0f} m",
                )
            )
        else:
            unavailable.append("geology")

        # formation
        candidate_formations, candidate_groups = _formations_in_window(
            session,
            candidate.id,
            (candidate_segment.segment_top_m if candidate_segment else window_top),
            (candidate_segment.segment_base_m if candidate_segment else window_base),
        )
        if query_formations or query_groups:
            if candidate_formations or candidate_groups:
                formation_score = max(
                    _jaccard(query_formations, candidate_formations),
                    _jaccard(query_groups, candidate_groups),
                )
                shared = sorted(query_formations & candidate_formations) or sorted(
                    query_groups & candidate_groups
                )
                components.append(
                    ComponentScore(
                        "formation",
                        formation_score,
                        weights.get("formation", 0.0),
                        f"shared stratigraphy: {', '.join(shared) if shared else 'none'}",
                    )
                )
            else:
                unavailable.append("formation")
        else:
            unavailable.append("formation")

        # depth
        if candidate.total_depth_md_m:
            reference = (
                (candidate_segment.segment_top_m + candidate_segment.segment_base_m) / 2.0
                if candidate_segment
                else float(candidate.total_depth_md_m)
            )
            difference = abs(reference - depth_m)
            depth_score = float(np.exp(-difference / depth_tolerance))
            components.append(
                ComponentScore(
                    "depth",
                    depth_score,
                    weights.get("depth", 0.0),
                    f"compared interval is {difference:.0f} m from the query depth",
                )
            )
        else:
            unavailable.append("depth")

        # drilling behaviour
        candidate_behaviour = _behaviour_profile(session, candidate.id, behaviour_channels)
        if query_behaviour and candidate_behaviour:
            behaviour_score, shared_count = _behaviour_similarity(
                query_behaviour, candidate_behaviour, behaviour_scales
            )
            if shared_count:
                components.append(
                    ComponentScore(
                        "drilling_behaviour",
                        behaviour_score,
                        weights.get("drilling_behaviour", 0.0),
                        f"{shared_count} shared operating channels compared on active rows",
                    )
                )
            else:
                unavailable.append("drilling_behaviour")
        else:
            unavailable.append("drilling_behaviour")

        # geography
        distance_km = None
        if None not in (well.latitude, well.longitude, candidate.latitude, candidate.longitude):
            distance_km = haversine_km(
                well.latitude, well.longitude, candidate.latitude, candidate.longitude
            )
            geography_score = float(np.exp(-distance_km / decay_km))
            components.append(
                ComponentScore(
                    "geography",
                    geography_score,
                    weights.get("geography", 0.0),
                    f"{distance_km:.1f} km from the active well",
                )
            )
        else:
            unavailable.append("geography")

        if not components:
            continue

        # Renormalise over the dimensions that were actually computable, so a candidate
        # is never penalised simply for lacking a data type.
        total_weight = sum(component.weight for component in components)
        if total_weight <= 0:
            continue
        score = sum(c.value * c.weight for c in components) / total_weight

        matches.append(
            AnalogueMatch(
                well=candidate,
                score=float(score),
                segment_top_m=candidate_segment.segment_top_m if candidate_segment else None,
                segment_base_m=candidate_segment.segment_base_m if candidate_segment else None,
                components=components,
                dimensions_used=[c.name for c in components],
                dimensions_unavailable=unavailable,
                distance_km=distance_km,
                dominant_formation=(candidate_segment.feature_summary or {}).get(
                    "dominant_formation"
                ) if candidate_segment else None,
                dominant_group=(candidate_segment.feature_summary or {}).get(
                    "dominant_group"
                ) if candidate_segment else None,
                feature_summary=(candidate_segment.feature_summary or {})
                if candidate_segment else {},
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    diagnostics["candidates_scored"] = len(matches)
    diagnostics["search_radius_km"] = search_radius
    return matches[:top_k], diagnostics


def _segment_vector(session: Session, well_id: int, depth_m: float) -> np.ndarray | None:
    """The embedding of the segment containing this depth, or the nearest one."""
    rows = (
        session.execute(select(WellEmbedding).where(WellEmbedding.well_id == well_id))
        .scalars()
        .all()
    )
    if not rows:
        return None
    containing = [
        row for row in rows
        if row.segment_top_m is not None
        and row.segment_top_m <= depth_m <= (row.segment_base_m or depth_m)
    ]
    chosen = containing[0] if containing else min(
        rows,
        key=lambda r: abs(((r.segment_top_m or 0) + (r.segment_base_m or 0)) / 2 - depth_m),
    )
    return np.asarray(chosen.embedding, dtype="float64")


def _best_segment(
    session: Session, well_id: int, query_vector: np.ndarray | None, depth_m: float
) -> WellEmbedding | None:
    """The candidate segment most similar to the query.

    On PostgreSQL this is where a pgvector nearest-neighbour operator applies; on the
    fallback backend the same comparison is done with NumPy over the well's segments,
    which is a small set per well.
    """
    rows = (
        session.execute(select(WellEmbedding).where(WellEmbedding.well_id == well_id))
        .scalars()
        .all()
    )
    if not rows:
        return None
    if query_vector is None:
        return min(
            rows,
            key=lambda r: abs(((r.segment_top_m or 0) + (r.segment_base_m or 0)) / 2 - depth_m),
        )
    return max(
        rows,
        key=lambda r: cosine_similarity(query_vector, np.asarray(r.embedding)),
    )
