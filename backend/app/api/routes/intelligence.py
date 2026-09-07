"""Risk, alert, engineer-feedback, model and system-status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_capabilities, get_session
from backend.app.models import (
    AnomalyScore,
    DrillingEvent,
    EngineerAction,
    ModelVersion,
    RiskAlert,
    RiskAssessment,
    TelemetrySample,
    Well,
    WellEmbedding,
)
from backend.app.schemas.models import (
    EngineerActionIn,
    EngineerActionOut,
    ModelVersionOut,
    RiskAlertOut,
    RiskAssessmentOut,
    SystemStatus,
)
from backend.app.services import risk as risk_service
from nwis_common import get_config

router = APIRouter(prefix="/api", tags=["intelligence"])

VALID_DECISIONS = {"accepted", "rejected", "investigating", "resolved"}


@router.get("/wells/{well_id}/risk", response_model=RiskAssessmentOut)
def evaluate_risk(
    well_id: int,
    session: Session = Depends(get_session),
    depth_m: float | None = Query(default=None, ge=0),
    anomaly_score: float | None = Query(default=None, ge=0, le=1),
    persist: bool = Query(default=False, description="store the assessment and raise an alert"),
):
    """Evaluate risk at a depth and return the full explanation.

    ``anomaly_score`` may be supplied by the live pipeline; when omitted the most recent
    stored score for the well is used, and if there is none the anomaly component is
    reported as unavailable rather than assumed to be zero.
    """
    well = session.get(Well, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"Well {well_id} not found")

    measurements: dict = {}
    if anomaly_score is None:
        latest = (
            session.execute(
                select(AnomalyScore)
                .where(AnomalyScore.well_id == well_id)
                .order_by(AnomalyScore.recorded_at.desc())
            )
            .scalars()
            .first()
        )
        if latest is not None:
            anomaly_score = latest.score

    sample = (
        session.execute(
            select(TelemetrySample)
            .where(TelemetrySample.well_id == well_id)
            .order_by(TelemetrySample.recorded_at.desc())
        )
        .scalars()
        .first()
    )
    if sample is not None and sample.channels:
        measurements = {k: v for k, v in sample.channels.items() if v is not None}
        if depth_m is None:
            depth_m = sample.bit_depth_m

    result = risk_service.assess(
        session,
        well=well,
        depth_m=depth_m,
        anomaly_score=anomaly_score,
        measurements=measurements,
    )
    if persist:
        risk_service.persist(session, result)
        session.commit()
    return RiskAssessmentOut(**result.to_payload())


@router.get("/wells/{well_id}/alerts", response_model=list[RiskAlertOut])
def well_alerts(
    well_id: int,
    session: Session = Depends(get_session),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    statement = select(RiskAlert).where(RiskAlert.well_id == well_id)
    if status:
        statement = statement.where(RiskAlert.status == status)
    rows = (
        session.execute(statement.order_by(RiskAlert.raised_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [RiskAlertOut.model_validate(r) for r in rows]


@router.get("/alerts", response_model=list[RiskAlertOut])
def all_alerts(
    session: Session = Depends(get_session),
    status: str | None = None,
    level: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    statement = select(RiskAlert)
    if status:
        statement = statement.where(RiskAlert.status == status)
    if level:
        statement = statement.where(RiskAlert.level == level)
    rows = (
        session.execute(statement.order_by(RiskAlert.raised_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [RiskAlertOut.model_validate(r) for r in rows]


@router.get("/alerts/{alert_id}/explanation", response_model=RiskAssessmentOut)
def alert_explanation(alert_id: int, session: Session = Depends(get_session)):
    """The stored evidence behind an alert.

    Read back from the assessment recorded at the time, not recomputed, so the engineer
    sees what the system actually acted on.
    """
    alert = session.get(RiskAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    assessment = (
        session.get(RiskAssessment, alert.risk_assessment_id)
        if alert.risk_assessment_id
        else None
    )
    if assessment is None:
        raise HTTPException(
            status_code=404,
            detail="The risk assessment behind this alert is no longer available.",
        )
    well = session.get(Well, assessment.well_id)
    evidence = assessment.evidence or {}
    return RiskAssessmentOut(
        well_id=assessment.well_id,
        well_name=well.name if well else str(assessment.well_id),
        depth_m=assessment.depth_m,
        mode=assessment.mode,
        score=assessment.score,
        risk_level=assessment.risk_level,
        probability=assessment.probability,
        components=assessment.components or [],
        historical_evidence=evidence.get("historical_evidence", []),
        analogue_wells=evidence.get("analogue_wells", []),
        contributing_features=evidence.get("contributing_features", []),
        narrative=alert.summary,
        notes=evidence.get("notes", []),
    )


@router.post("/engineer-actions", response_model=EngineerActionOut, status_code=201)
def record_engineer_action(
    payload: EngineerActionIn, session: Session = Depends(get_session)
):
    """Record an engineer decision. This is the feedback loop into institutional memory.

    The action is stored and marked available for future retraining. Nothing retrains
    automatically.
    """
    if payload.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of {sorted(VALID_DECISIONS)}",
        )
    alert = session.get(RiskAlert, payload.alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {payload.alert_id} not found")

    action = EngineerAction(
        alert_id=alert.id,
        well_id=alert.well_id,
        decision=payload.decision,
        action_description=payload.action_description,
        outcome=payload.outcome,
        engineer_name=payload.engineer_name,
        recorded_at=datetime.now(timezone.utc),
        available_for_training=True,
    )
    if payload.decision in {"accepted", "rejected", "resolved"}:
        alert.status = "closed" if payload.decision == "resolved" else "reviewed"
    session.add(action)
    session.commit()
    session.refresh(action)
    return EngineerActionOut.model_validate(action)


@router.get("/wells/{well_id}/engineer-actions", response_model=list[EngineerActionOut])
def well_engineer_actions(well_id: int, session: Session = Depends(get_session)):
    rows = (
        session.execute(
            select(EngineerAction)
            .where(EngineerAction.well_id == well_id)
            .order_by(EngineerAction.recorded_at.desc())
        )
        .scalars()
        .all()
    )
    return [EngineerActionOut.model_validate(r) for r in rows]


@router.get("/models", response_model=list[ModelVersionOut])
def list_models(session: Session = Depends(get_session)):
    """Every registered model with its actually-measured metrics."""
    rows = (
        session.execute(select(ModelVersion).order_by(ModelVersion.name))
        .scalars()
        .all()
    )
    return [ModelVersionOut.model_validate(r) for r in rows]


@router.get("/models/{name}", response_model=ModelVersionOut)
def get_model(name: str, session: Session = Depends(get_session)):
    row = (
        session.execute(
            select(ModelVersion)
            .where(ModelVersion.name == name)
            .order_by(ModelVersion.trained_at.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model {name!r} has not been trained. No metrics exist for it.",
        )
    return ModelVersionOut.model_validate(row)


@router.get("/status", response_model=SystemStatus)
def system_status(session: Session = Depends(get_session)):
    """What the system actually holds, and which storage features are active."""
    config = get_config()
    capabilities = get_capabilities()

    counts = {
        "wells": int(session.scalar(select(func.count()).select_from(Well)) or 0),
        "wells_with_position": int(
            session.scalar(
                select(func.count()).select_from(Well).where(Well.latitude.isnot(None))
            ) or 0
        ),
        "wells_with_telemetry": int(
            session.scalar(
                select(func.count()).select_from(Well).where(Well.has_telemetry.is_(True))
            ) or 0
        ),
        "telemetry_samples": int(
            session.scalar(select(func.count()).select_from(TelemetrySample)) or 0
        ),
        "drilling_events": int(
            session.scalar(select(func.count()).select_from(DrillingEvent)) or 0
        ),
        "well_embeddings": int(
            session.scalar(select(func.count()).select_from(WellEmbedding)) or 0
        ),
        "risk_alerts": int(session.scalar(select(func.count()).select_from(RiskAlert)) or 0),
        "engineer_actions": int(
            session.scalar(select(func.count()).select_from(EngineerAction)) or 0
        ),
    }

    models = [
        {
            "name": m.name,
            "algorithm": m.algorithm,
            "version": m.version,
            "trained_at": m.trained_at.isoformat() if m.trained_at else None,
        }
        for m in session.execute(select(ModelVersion)).scalars()
    ]

    warnings = list(capabilities.notes)
    if counts["wells"] and counts["wells_with_position"] < counts["wells"]:
        warnings.append(
            f"{counts['wells'] - counts['wells_with_position']} well(s) have no "
            "established surface position and are excluded from map and proximity results."
        )
    if not models:
        warnings.append("No models are registered; prediction endpoints will report "
                        "that a model is unavailable.")

    return SystemStatus(
        application=str(config.get("app.name")),
        environment=config.environment,
        database=capabilities.as_dict(),
        counts=counts,
        models=models,
        warnings=warnings,
    )
