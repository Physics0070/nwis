"""Initial NWIS schema.

Creates all 16 tables from the SQLAlchemy metadata, then applies the PostgreSQL-only
storage features: extensions, the telemetry hypertable, spatial and vector indexes.

Dialect-aware by design. On PostgreSQL the extension objects are created; on the fallback
backend the same migration produces a working schema using plain columns, so a developer
without Docker gets the same tables.

Revision ID: 20260907_0000
Revises:
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from backend.app.models import Base

revision = "20260907_0000"
down_revision = None
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()

    # Extensions must exist before columns that use their types are created.
    if _is_postgres():
        for extension in ("postgis", "timescaledb", "vector"):
            op.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))

    Base.metadata.create_all(bind=bind)

    if not _is_postgres():
        # The fallback backend has no extensions to configure. Ordinary indexes were
        # already created with the tables.
        return

    # Telemetry is append-only time series: a hypertable partitions it by time.
    op.execute(
        sa.text(
            "SELECT create_hypertable('telemetry_samples', 'recorded_at', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
    )

    # Spatial index for nearby-well search.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_wells_location_gist "
            "ON wells USING GIST (location)"
        )
    )

    # Approximate nearest-neighbour indexes for the vector columns. Cosine distance,
    # matching the similarity metric the analogue engine uses.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_well_embeddings_vector "
            "ON well_embeddings USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_vector "
            "ON document_chunks USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _is_postgres():
        for index in (
            "ix_document_chunks_vector",
            "ix_well_embeddings_vector",
            "ix_wells_location_gist",
        ):
            op.execute(sa.text(f"DROP INDEX IF EXISTS {index}"))
    Base.metadata.drop_all(bind=bind)
