"""Pydantic response models.

Optional fields are genuinely optional: a null latitude means the position is unknown, a
null probability means no calibrated model produced one. The frontend renders those as
explicit states, never as zero or a placeholder.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WellSummary(ORMModel):
    id: int
    name: str
    reference: str | None = None
    field_name: str | None = None
    operator: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_source: str | None = None
    total_depth_md_m: float | None = None
    water_depth_m: float | None = None
    has_logs: bool
    has_telemetry: bool
    has_trajectory: bool
    is_active: bool
    status: str | None = None
    source_dataset: str | None = None


class NearbyWellOut(BaseModel):
    well: WellSummary
    distance_km: float


class PaginatedWells(BaseModel):
    items: list[WellSummary]
    total: int
    limit: int
    offset: int


class TrajectoryStationOut(ORMModel):
    md_m: float
    tvd_m: float | None = None
    inclination_deg: float | None = None
    azimuth_deg: float | None = None
    north_offset_m: float | None = None
    east_offset_m: float | None = None
    dogleg_severity_deg_per_m: float | None = None
    station_type: str | None = None


class FormationIntervalOut(ORMModel):
    group_name: str | None = None
    formation_name: str | None = None
    depth_top_m: float
    depth_base_m: float
    sample_count: int | None = None
    source_dataset: str | None = None


class LogSampleOut(ORMModel):
    depth_md_m: float
    tvd_m: float | None = None
    curves: dict[str, float | None]


class TelemetrySampleOut(ORMModel):
    recorded_at: datetime
    bit_depth_m: float | None = None
    hole_depth_m: float | None = None
    channels: dict[str, float | None]
    circulating: bool | None = None
    rotating: bool | None = None
    tripping: bool | None = None
    operations_active: bool | None = None


class DrillingEventOut(ORMModel):
    id: int
    well_id: int
    occurred_at: datetime | None = None
    event_type: str
    category: str | None = None
    severity: str | None = None
    depth_start_m: float | None = None
    depth_end_m: float | None = None
    depth_source: str | None = Field(
        None,
        description="How the depth was established. 'telemetry_time_join' means the "
                    "source record carried no usable depth and it was recovered by "
                    "matching the event timestamp to telemetry.",
    )
    formation_name: str | None = None
    description: str
    extraction_method: str | None = None
    source_dataset: str | None = None
    source_reference: str | None = None


class MitigationOut(ORMModel):
    id: int
    event_id: int
    action_taken: str
    outcome: str | None = None
    outcome_status: str | None = None
    source_dataset: str | None = None


class AnalogueComponentOut(BaseModel):
    name: str
    value: float
    weight: float
    detail: str


class AnalogueWellOut(BaseModel):
    well: WellSummary
    score: float
    segment_top_m: float | None = None
    segment_base_m: float | None = None
    distance_km: float | None = None
    dominant_formation: str | None = None
    dominant_group: str | None = None
    components: list[AnalogueComponentOut]
    dimensions_used: list[str]
    dimensions_unavailable: list[str] = Field(
        default_factory=list,
        description="Similarity dimensions that could not be computed for this "
                    "candidate. Remaining weights were renormalised.",
    )


class AnalogueResponse(BaseModel):
    query_well: WellSummary
    query_depth_m: float | None
    top_k: int
    weights: dict[str, float]
    matches: list[AnalogueWellOut]
    diagnostics: dict[str, Any]


class RiskComponentOut(BaseModel):
    name: str
    value: float
    weight: float
    explanation: str
    available: bool = True


class HistoricalEvidenceOut(BaseModel):
    well_name: str
    similarity_score: float
    event_id: int
    event_type: str
    depth_start_m: float | None = None
    depth_end_m: float | None = None
    distance_from_bit_m: float | None = None
    description: str
    formation_name: str | None = None
    depth_source: str | None = None
    source_dataset: str | None = None
    source_reference: str | None = None
    mitigations: list[dict[str, Any]] = Field(default_factory=list)


class RiskAssessmentOut(BaseModel):
    well_id: int
    well_name: str
    depth_m: float | None
    mode: str = Field(
        description="'supervised' only when a calibrated model was trained on sufficient "
                    "labelled events; otherwise 'hybrid_indicator'."
    )
    score: float
    risk_level: str
    probability: float | None = Field(
        None,
        description="Populated only by a calibrated supervised model. Null under "
                    "hybrid_indicator mode, by design.",
    )
    components: list[RiskComponentOut]
    historical_evidence: list[HistoricalEvidenceOut]
    analogue_wells: list[dict[str, Any]]
    contributing_features: list[dict[str, Any]]
    narrative: str
    notes: list[str]


class RiskAlertOut(ORMModel):
    id: int
    well_id: int
    risk_assessment_id: int | None = None
    raised_at: datetime
    depth_m: float | None = None
    level: str
    title: str
    summary: str
    status: str
    occurrence_count: int


class EngineerActionIn(BaseModel):
    alert_id: int
    decision: str = Field(description="accepted | rejected | investigating | resolved")
    action_description: str | None = None
    outcome: str | None = None
    engineer_name: str | None = None


class EngineerActionOut(ORMModel):
    id: int
    alert_id: int
    well_id: int
    decision: str
    action_description: str | None = None
    outcome: str | None = None
    engineer_name: str | None = None
    recorded_at: datetime
    available_for_training: bool


class ModelVersionOut(ORMModel):
    id: int
    name: str
    version: str
    task: str
    algorithm: str
    dataset: str | None = None
    dataset_rows: int | None = None
    trained_at: datetime | None = None
    training_seconds: float | None = None
    is_active: bool
    feature_columns: list[str]
    hyperparameters: dict[str, Any]
    metrics: dict[str, Any]
    split_summary: dict[str, Any]
    limitations: list[str]


class LithologyPredictionOut(ORMModel):
    depth_md_m: float
    lithology_code: int
    lithology_name: str
    probability: float | None = None


class ReplayState(BaseModel):
    well_id: int | None = None
    well_name: str | None = None
    status: str
    speed: float
    current_index: int
    total_samples: int
    current_timestamp: datetime | None = None
    current_depth_m: float | None = None
    source: str = Field(
        description="Identifies the data behind the stream, e.g. 'Volve WITSML replay'."
    )


class SystemStatus(BaseModel):
    application: str
    environment: str
    database: dict[str, Any]
    counts: dict[str, int]
    models: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
