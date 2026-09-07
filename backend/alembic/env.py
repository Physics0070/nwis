"""Alembic environment.

The database URL comes from the application configuration, not from alembic.ini, so there
is one source of truth and no credentials in version control.

Migrations are dialect-aware. PostGIS, TimescaleDB and pgvector objects are only created
when running against PostgreSQL; the same migration is valid on the fallback backend, which
uses the equivalent plain-column representations.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.models import Base
from nwis_common import get_config

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Prefer the primary URL; fall back only if the application config allows it."""
    config = get_config()
    return str(config.get("database.url"))


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = alembic_config.get_section(alembic_config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Extension-owned tables must not be diffed into NWIS migrations.
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


def _include_object(obj, name, type_, reflected, compare_to):
    """Ignore objects created by PostGIS and TimescaleDB."""
    if type_ == "table" and name in {"spatial_ref_sys", "geography_columns", "geometry_columns"}:
        return False
    if type_ == "table" and name.startswith(("_hyper_", "_timescaledb")):
        return False
    return True


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
