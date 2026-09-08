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
    #
    # TimescaleDB requires the partitioning column to appear in every unique index on the
    # table, and the schema's primary key is `id` alone. `create_hypertable` therefore
    # failed outright with "cannot create a unique index without the column
    # \"recorded_at\" (used in partitioning)" — so the hypertable was never created on
    # any Docker run, and /api/status reported an ordinary indexed table.
    #
    # Widening the key to (id, recorded_at) is the standard TimescaleDB pattern. `id` is
    # still generated from its own sequence and still uniquely identifies a row, so the
    # ORM mapping is unaffected. This runs on PostgreSQL only; the fallback backend keeps
    # the plain single-column key.
    op.execute(
        sa.text(
            "ALTER TABLE telemetry_samples DROP CONSTRAINT IF EXISTS pk_telemetry_samples"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE telemetry_samples DROP CONSTRAINT IF EXISTS telemetry_samples_pkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE telemetry_samples "
            "ADD CONSTRAINT pk_telemetry_samples PRIMARY KEY (id, recorded_at)"
        )
    )
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
