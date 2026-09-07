"""NWIS relational schema.

Organised around the product promise: every risk statement must be traceable back to
named wells, depth intervals and source records. That is why nearly every derived table
carries provenance and why alerts reference evidence rows rather than embedding text.

Spatial and vector columns are dialect-adaptive (see ``types.py``): PostGIS geography and
pgvector on PostgreSQL, WKT text and JSON arrays on the local fallback. Latitude and
longitude are always plain float columns, so no query depends on an extension being present.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, ProvenanceMixin, TimestampMixin
from backend.app.models.types import GeographyPoint, Vector

EMBEDDING_DIMENSIONS = 32  # mirrors analogue.embedding_dim; asserted at startup

# Report passages are embedded by a sentence-transformer, not by the petrophysical
# encoder, so they carry that model's dimensionality. The two vector spaces are
# unrelated and must never be compared against each other.
TEXT_EMBEDDING_DIMENSIONS = 384  # mirrors documents.embedding.dimensions


# ============================================================== wells and geometry


class Well(Base, TimestampMixin, ProvenanceMixin):
    """A wellbore. The anchor for everything else in the system."""

    __tablename__ = "wells"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    reference: Mapped[str | None] = mapped_column(String(128), index=True)

    field_name: Mapped[str | None] = mapped_column(String(128), index=True)
    country: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(64))
    operator: Mapped[str | None] = mapped_column(String(128))

    # Null latitude/longitude means "position unknown", never a placeholder position.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location: Mapped[str | None] = mapped_column(GeographyPoint)
    location_source: Mapped[str | None] = mapped_column(String(64))

    water_depth_m: Mapped[float | None] = mapped_column(Float)
    kb_elevation_m: Mapped[float | None] = mapped_column(Float)
    total_depth_md_m: Mapped[float | None] = mapped_column(Float)

    # Which capabilities this well actually has, so the API can degrade honestly.
    has_logs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_telemetry: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_trajectory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[str | None] = mapped_column(String(32))

    trajectory_stations = relationship("TrajectoryStation", back_populates="well",
                                       cascade="all, delete-orphan")
    formation_intervals = relationship("FormationInterval", back_populates="well",
                                       cascade="all, delete-orphan")
    events = relationship("DrillingEvent", back_populates="well",
                          cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="latitude_range"
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="longitude_range"
        ),
        Index("ix_wells_latlon", "latitude", "longitude"),
    )


class TrajectoryStation(Base):
    """One directional survey station."""

    __tablename__ = "trajectory_stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    md_m: Mapped[float] = mapped_column(Float, nullable=False)
    tvd_m: Mapped[float | None] = mapped_column(Float)
    inclination_deg: Mapped[float | None] = mapped_column(Float)
    azimuth_deg: Mapped[float | None] = mapped_column(Float)
    north_offset_m: Mapped[float | None] = mapped_column(Float)
    east_offset_m: Mapped[float | None] = mapped_column(Float)
    dogleg_severity_deg_per_m: Mapped[float | None] = mapped_column(Float)
    station_type: Mapped[str | None] = mapped_column(String(64))

    well = relationship("Well", back_populates="trajectory_stations")

    __table_args__ = (
        UniqueConstraint("well_id", "md_m", name="uq_trajectory_station_depth"),
        Index("ix_trajectory_well_md", "well_id", "md_m"),
    )


# =========================================================== geology and lithology


class FormationInterval(Base, ProvenanceMixin):
    """A named stratigraphic interval in a well.

    Stored as intervals rather than per-sample labels so that "what formation is the bit
    in" is a single indexed range lookup.
    """

    __tablename__ = "formation_intervals"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_name: Mapped[str | None] = mapped_column(String(128), index=True)
    formation_name: Mapped[str | None] = mapped_column(String(128), index=True)
    depth_top_m: Mapped[float] = mapped_column(Float, nullable=False)
    depth_base_m: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int | None] = mapped_column(Integer)

    well = relationship("Well", back_populates="formation_intervals")

    __table_args__ = (
        CheckConstraint("depth_base_m >= depth_top_m", name="interval_ordered"),
        Index("ix_formation_well_depth", "well_id", "depth_top_m", "depth_base_m"),
    )


class WellLogSample(Base):
    """Wireline log readings at a depth.

    Decimated on ingestion (``ingestion.log_depth_step_m``) — the raw FORCE dataset is
    1.17 M rows at 0.152 m spacing, which is far finer than any display or lookup needs.
    The full-resolution data stays in ``data/processed`` for model training.
    """

    __tablename__ = "well_log_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depth_md_m: Mapped[float] = mapped_column(Float, nullable=False)
    tvd_m: Mapped[float | None] = mapped_column(Float)
    # Curve readings vary by well, so they are stored as a JSON map of curve -> value
    # rather than a wide table with columns that would be null for most wells.
    curves: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("well_id", "depth_md_m", name="uq_log_sample_depth"),
        Index("ix_log_sample_well_depth", "well_id", "depth_md_m"),
    )


class LithologyPrediction(Base):
    """Model-predicted lithology at a depth, with the model version that produced it."""

    __tablename__ = "lithology_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depth_md_m: Mapped[float] = mapped_column(Float, nullable=False)
    lithology_code: Mapped[int] = mapped_column(Integer, nullable=False)
    lithology_name: Mapped[str] = mapped_column(String(64), nullable=False)
    probability: Mapped[float | None] = mapped_column(Float)
    actual_code: Mapped[int | None] = mapped_column(Integer)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        UniqueConstraint("well_id", "depth_md_m", "model_version_id",
                         name="uq_lithology_prediction"),
        Index("ix_lithology_well_depth", "well_id", "depth_md_m"),
    )


# ========================================================================= telemetry


class TelemetrySample(Base):
    """Normalised drilling/operations telemetry.

    Becomes a TimescaleDB hypertable on PostgreSQL (see the migration); an ordinary
    indexed table on the fallback backend. Channel values are a JSON map so that wells
    with different sensor sets coexist without a hundred mostly-null columns.
    """

    __tablename__ = "telemetry_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bit_depth_m: Mapped[float | None] = mapped_column(Float)
    hole_depth_m: Mapped[float | None] = mapped_column(Float)
    channels: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Derived rig state, computed in the pipeline from signals that genuinely vary.
    circulating: Mapped[bool | None] = mapped_column(Boolean)
    rotating: Mapped[bool | None] = mapped_column(Boolean)
    tripping: Mapped[bool | None] = mapped_column(Boolean)
    operations_active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_telemetry_well_time", "well_id", "recorded_at"),
        Index("ix_telemetry_well_depth", "well_id", "bit_depth_m"),
    )


class AnomalyScore(Base):
    """Anomaly detector output for one telemetry instant.

    Deliberately carries no event type. The model detects *unusual behaviour*; naming a
    failure mode requires historical evidence, which lives in DrillingEvent.
    """

    __tablename__ = "anomaly_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depth_m: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contributing_features: Mapped[list] = mapped_column(JSON, default=list)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (Index("ix_anomaly_well_time", "well_id", "recorded_at"),)


# ============================================== events, mitigations, knowledge


class DrillingEvent(Base, ProvenanceMixin):
    """A historical operational event, extracted from reports or WITSML messages.

    ``depth_source`` records how the depth was established. In the Volve mirror the
    published message depth is a constant, so depth is recovered by time-joining to
    telemetry; that distinction must remain visible to an engineer reviewing evidence.
    """

    __tablename__ = "drilling_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    severity: Mapped[str | None] = mapped_column(String(32))

    depth_start_m: Mapped[float | None] = mapped_column(Float)
    depth_end_m: Mapped[float | None] = mapped_column(Float)
    depth_source: Mapped[str | None] = mapped_column(String(64))
    formation_name: Mapped[str | None] = mapped_column(String(128), index=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_method: Mapped[str | None] = mapped_column(String(64))

    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )

    well = relationship("Well", back_populates="events")
    mitigations = relationship("Mitigation", back_populates="event",
                               cascade="all, delete-orphan")

    __table_args__ = (Index("ix_event_well_depth", "well_id", "depth_start_m"),)


class Mitigation(Base, ProvenanceMixin):
    """An action actually taken in response to a historical event.

    NWIS never generates a recommendation. It retrieves rows from this table, and if
    there are none it says so.
    """

    __tablename__ = "mitigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("drilling_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text)
    outcome_status: Mapped[str | None] = mapped_column(String(32), index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)

    event = relationship("DrillingEvent", back_populates="mitigations")


class Document(Base, TimestampMixin):
    """A source document (report, log, scan) that knowledge was extracted from."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(64), index=True)
    well_id: Mapped[int | None] = mapped_column(
        ForeignKey("wells.id", ondelete="SET NULL"), index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(1024))
    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ingested_characters: Mapped[int | None] = mapped_column(Integer)


class DocumentChunk(Base):
    """A retrievable passage with an embedding, keeping page-level traceability."""

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list | None] = mapped_column(Vector(TEXT_EMBEDDING_DIMENSIONS))

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),
    )


class WellEmbedding(Base):
    """Vector representation of a well or of one depth segment of a well.

    Segment-level rows are what the analogue engine searches: similarity is asked at a
    depth interval, not for a whole well.
    """

    __tablename__ = "well_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_top_m: Mapped[float | None] = mapped_column(Float)
    segment_base_m: Mapped[float | None] = mapped_column(Float)
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    feature_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        Index("ix_well_embedding_segment", "well_id", "segment_top_m", "segment_base_m"),
    )


# ============================================================ risk, alerts, feedback


class RiskAssessment(Base):
    """One evaluation of the risk engine.

    ``mode`` distinguishes a supervised prediction from a hybrid historical indicator.
    ``probability`` is populated only when a calibrated probabilistic model produced it;
    an indicator leaves it null rather than dressing a heuristic score as a probability.
    """

    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depth_m: Mapped[float | None] = mapped_column(Float)

    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    probability: Mapped[float | None] = mapped_column(Float)

    # The full explanation payload: component scores, contributing telemetry features,
    # supporting analogue wells and historical intervals.
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_unit_interval"),
        Index("ix_risk_well_time", "well_id", "assessed_at"),
    )


class RiskAlert(Base, TimestampMixin):
    """An alert raised from a risk assessment, subject to cooldown and deduplication."""

    __tablename__ = "risk_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_assessment_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="SET NULL"), index=True
    )
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depth_m: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    # Groups repeat alerts for the same concern so cooldown can suppress duplicates.
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    actions = relationship("EngineerAction", back_populates="alert",
                           cascade="all, delete-orphan")

    __table_args__ = (Index("ix_alert_well_raised", "well_id", "raised_at"),)


class EngineerAction(Base, TimestampMixin):
    """Engineer response to an alert: the feedback loop into institutional memory."""

    __tablename__ = "engineer_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("risk_alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_description: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    engineer_name: Mapped[str | None] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Marks the row as eligible training signal for a future retrain. Nothing retrains
    # automatically; the feedback is stored and made available.
    available_for_training: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    alert = relationship("RiskAlert", back_populates="actions")


class ModelVersion(Base, TimestampMixin):
    """Registry mirror: what the API reports about a model.

    Populated from ``artifacts/models/*/latest.json``. If a model has not been trained,
    there is no row, and the API reports it as unavailable rather than inventing metrics.
    """

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(256))
    dataset_rows: Mapped[int | None] = mapped_column(Integer)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_seconds: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    feature_columns: Mapped[list] = mapped_column(JSON, default=list)
    hyperparameters: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    split_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    artifact_path: Mapped[str | None] = mapped_column(String(1024))

    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_version"),)


__all__ = [
    "Well",
    "TrajectoryStation",
    "FormationInterval",
    "WellLogSample",
    "LithologyPrediction",
    "TelemetrySample",
    "AnomalyScore",
    "DrillingEvent",
    "Mitigation",
    "Document",
    "DocumentChunk",
    "WellEmbedding",
    "RiskAssessment",
    "RiskAlert",
    "EngineerAction",
    "ModelVersion",
    "EMBEDDING_DIMENSIONS",
    "TEXT_EMBEDDING_DIMENSIONS",
]
