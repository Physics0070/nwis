"""Well, geology, telemetry, event and analogue endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_session
from backend.app.models import (
    DrillingEvent,
    FormationInterval,
    LithologyPrediction,
    Mitigation,
    TelemetrySample,
    TrajectoryStation,
    Well,
    WellLogSample,
)
from backend.app.repositories import wells as repo
from backend.app.schemas.models import (
    AnalogueResponse,
    AnalogueWellOut,
    DrillingEventOut,
    FormationIntervalOut,
    LithologyPredictionOut,
    LogSampleOut,
    MitigationOut,
    NearbyWellOut,
    PaginatedWells,
    TelemetrySampleOut,
    TrajectoryStationOut,
    WellSummary,
)
from backend.app.services.analogue import find_analogues
from nwis_common import get_config

router = APIRouter(prefix="/api", tags=["wells"])


def _require_well(session: Session, well_id: int) -> Well:
    well = repo.get_well(session, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"Well {well_id} not found")
    return well


@router.get("/wells", response_model=PaginatedWells)
def list_wells(
    session: Session = Depends(get_session),
    limit: int = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    field_name: str | None = None,
    dataset: str | None = None,
    has_telemetry: bool | None = None,
    search: str | None = None,
):
    config = get_config()
    limit = min(limit or int(config.get("api.default_page_size")),
                int(config.get("api.max_page_size")))
    rows, total = repo.list_wells(
        session,
        limit=limit,
        offset=offset,
        field_name=field_name,
        dataset=dataset,
        has_telemetry=has_telemetry,
        search=search,
    )
    return PaginatedWells(
        items=[WellSummary.model_validate(w) for w in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/wells/{well_id}", response_model=WellSummary)
def get_well(well_id: int, session: Session = Depends(get_session)):
    return WellSummary.model_validate(_require_well(session, well_id))


@router.get("/wells/{well_id}/nearby", response_model=list[NearbyWellOut])
def nearby_wells(
    well_id: int,
    session: Session = Depends(get_session),
    radius_km: float | None = Query(default=None, gt=0),
    limit: int = Query(default=25, ge=1, le=200),
):
    """Wells within a radius, computed server-side in the database."""
    well = _require_well(session, well_id)
    if well.latitude is None or well.longitude is None:
        raise HTTPException(
            status_code=409,
            detail=f"{well.name} has no established surface position, so nearby wells "
                   "cannot be determined. No position is assumed.",
        )
    config = get_config()
    radius = radius_km or float(config.get("analogue.geography.search_radius_km"))
    results = repo.find_nearby_wells(
        session,
        latitude=well.latitude,
        longitude=well.longitude,
        radius_km=radius,
        limit=limit,
        exclude_well_id=well.id,
    )
    return [
        NearbyWellOut(well=WellSummary.model_validate(r.well), distance_km=round(r.distance_km, 3))
        for r in results
    ]


@router.get("/wells/{well_id}/analogues", response_model=AnalogueResponse)
def analogue_wells(
    well_id: int,
    session: Session = Depends(get_session),
    depth_m: float | None = Query(default=None, ge=0),
    top_k: int | None = Query(default=None, ge=1, le=50),
):
    """Contextual analogue ranking. Not a proximity ranking."""
    well = _require_well(session, well_id)
    config = get_config()
    matches, diagnostics = find_analogues(
        session, well=well, depth_m=depth_m, top_k=top_k
    )
    return AnalogueResponse(
        query_well=WellSummary.model_validate(well),
        query_depth_m=diagnostics["query_depth_m"],
        top_k=top_k or int(config.get("analogue.top_k")),
        weights=diagnostics["configured_weights"],
        matches=[
            AnalogueWellOut(
                well=WellSummary.model_validate(m.well),
                score=round(m.score, 4),
                segment_top_m=m.segment_top_m,
                segment_base_m=m.segment_base_m,
                distance_km=round(m.distance_km, 3) if m.distance_km is not None else None,
                dominant_formation=m.dominant_formation,
                dominant_group=m.dominant_group,
                components=[
                    {
                        "name": c.name,
                        "value": round(c.value, 4),
                        "weight": c.weight,
                        "detail": c.detail,
                    }
                    for c in m.components
                ],
                dimensions_used=m.dimensions_used,
                dimensions_unavailable=m.dimensions_unavailable,
            )
            for m in matches
        ],
        diagnostics=diagnostics,
    )


@router.get("/wells/{well_id}/trajectory", response_model=list[TrajectoryStationOut])
def trajectory(well_id: int, session: Session = Depends(get_session)):
    _require_well(session, well_id)
    rows = (
        session.execute(
            select(TrajectoryStation)
            .where(TrajectoryStation.well_id == well_id)
            .order_by(TrajectoryStation.md_m)
        )
        .scalars()
        .all()
    )
    return [TrajectoryStationOut.model_validate(r) for r in rows]


@router.get("/wells/{well_id}/formations", response_model=list[FormationIntervalOut])
def formations(well_id: int, session: Session = Depends(get_session)):
    _require_well(session, well_id)
    rows = (
        session.execute(
            select(FormationInterval)
            .where(FormationInterval.well_id == well_id)
            .order_by(FormationInterval.depth_top_m)
        )
        .scalars()
        .all()
    )
    return [FormationIntervalOut.model_validate(r) for r in rows]


@router.get("/wells/{well_id}/formation-at", response_model=FormationIntervalOut)
def formation_at_depth(
    well_id: int, depth_m: float = Query(..., ge=0), session: Session = Depends(get_session)
):
    """The stratigraphic interval containing a depth."""
    _require_well(session, well_id)
    row = (
        session.execute(
            select(FormationInterval)
            .where(FormationInterval.well_id == well_id)
            .where(FormationInterval.depth_top_m <= depth_m)
            .where(FormationInterval.depth_base_m >= depth_m)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No stratigraphic interval recorded at {depth_m} m for this well",
        )
    return FormationIntervalOut.model_validate(row)


@router.get("/wells/{well_id}/logs", response_model=list[LogSampleOut])
def well_logs(
    well_id: int,
    session: Session = Depends(get_session),
    depth_min: float | None = Query(default=None, ge=0),
    depth_max: float | None = Query(default=None, ge=0),
    limit: int = Query(default=2000, ge=1, le=20000),
):
    _require_well(session, well_id)
    statement = select(WellLogSample).where(WellLogSample.well_id == well_id)
    if depth_min is not None:
        statement = statement.where(WellLogSample.depth_md_m >= depth_min)
    if depth_max is not None:
        statement = statement.where(WellLogSample.depth_md_m <= depth_max)
    rows = (
        session.execute(statement.order_by(WellLogSample.depth_md_m).limit(limit))
        .scalars()
        .all()
    )
    return [LogSampleOut.model_validate(r) for r in rows]


@router.get("/wells/{well_id}/events", response_model=list[DrillingEventOut])
def well_events(
    well_id: int,
    session: Session = Depends(get_session),
    depth_min: float | None = Query(default=None, ge=0),
    depth_max: float | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
):
    _require_well(session, well_id)
    statement = select(DrillingEvent).where(DrillingEvent.well_id == well_id)
    if depth_min is not None:
        statement = statement.where(DrillingEvent.depth_start_m >= depth_min)
    if depth_max is not None:
        statement = statement.where(DrillingEvent.depth_start_m <= depth_max)
    rows = (
        session.execute(statement.order_by(DrillingEvent.occurred_at).limit(limit))
        .scalars()
        .all()
    )
    return [DrillingEventOut.model_validate(r) for r in rows]


@router.get("/wells/{well_id}/telemetry", response_model=list[TelemetrySampleOut])
def well_telemetry(
    well_id: int,
    session: Session = Depends(get_session),
    limit: int = Query(default=1000, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
    active_only: bool = False,
):
    well = _require_well(session, well_id)
    if not well.has_telemetry:
        raise HTTPException(
            status_code=409,
            detail=f"{well.name} has no telemetry in the knowledge base.",
        )
    statement = select(TelemetrySample).where(TelemetrySample.well_id == well_id)
    if active_only:
        statement = statement.where(TelemetrySample.operations_active.is_(True))
    rows = (
        session.execute(
            statement.order_by(TelemetrySample.recorded_at).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return [TelemetrySampleOut.model_validate(r) for r in rows]


@router.get("/wells/{well_id}/lithology", response_model=list[LithologyPredictionOut])
def well_lithology(
    well_id: int,
    session: Session = Depends(get_session),
    limit: int = Query(default=2000, ge=1, le=20000),
):
    _require_well(session, well_id)
    rows = (
        session.execute(
            select(LithologyPrediction)
            .where(LithologyPrediction.well_id == well_id)
            .order_by(LithologyPrediction.depth_md_m)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No lithology predictions stored for this well. Run the lithology "
                   "prediction step, or the model may not be trained.",
        )
    return [LithologyPredictionOut.model_validate(r) for r in rows]


@router.get("/events/{event_id}/mitigations", response_model=list[MitigationOut])
def event_mitigations(event_id: int, session: Session = Depends(get_session)):
    """Historically recorded actions for an event.

    An empty list is a meaningful answer: no validated historical mitigation exists.
    NWIS never generates one.
    """
    event = session.get(DrillingEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    rows = (
        session.execute(select(Mitigation).where(Mitigation.event_id == event_id))
        .scalars()
        .all()
    )
    return [MitigationOut.model_validate(r) for r in rows]
