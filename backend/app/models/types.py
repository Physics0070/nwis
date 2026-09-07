"""Dialect-aware column types.

NWIS targets PostgreSQL with PostGIS, TimescaleDB and pgvector. So that the same
schema can also run against the local SQLite fallback (see docs/ASSUMPTIONS.md),
spatial and vector columns are expressed as dialect-adaptive types:

    Postgres : geography(Point, 4326)  /  vector(n)
    SQLite   : WKT TEXT                /  JSON array of floats

Latitude and longitude are always stored as ordinary float columns as well, so no
capability is *required* for the system to function — PostGIS and pgvector make the
queries faster and exact, they are not a data dependency.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy import Float, String
from sqlalchemy.types import TypeDecorator, UserDefinedType

POSTGRES_DIALECTS = {"postgresql"}


class Vector(TypeDecorator):
    """Embedding column: pgvector on Postgres, JSON text elsewhere."""

    impl = String
    cache_ok = True

    def __init__(self, dimensions: int):
        if dimensions <= 0:
            raise ValueError("Vector dimensions must be positive")
        self.dimensions = dimensions
        super().__init__()

    def load_dialect_impl(self, dialect):
        if dialect.name in POSTGRES_DIALECTS:
            from pgvector.sqlalchemy import Vector as PGVector

            return dialect.type_descriptor(PGVector(self.dimensions))
        return dialect.type_descriptor(String())

    def process_bind_param(self, value: Sequence[float] | None, dialect) -> Any:
        if value is None:
            return None
        values = [float(v) for v in value]
        if len(values) != self.dimensions:
            raise ValueError(
                f"Embedding has {len(values)} dimensions, expected {self.dimensions}"
            )
        if dialect.name in POSTGRES_DIALECTS:
            return values
        return json.dumps(values)

    def process_result_value(self, value, dialect) -> list[float] | None:
        if value is None:
            return None
        if dialect.name in POSTGRES_DIALECTS:
            return [float(v) for v in value]
        return [float(v) for v in json.loads(value)]


class _PostgresGeography(UserDefinedType):
    """Emits ``geography(Point,4326)`` in DDL. PostgreSQL only.

    Deliberately not GeoAlchemy2. That library installs global ``before_create`` /
    ``after_create`` listeners on every Table: the first stashes the column list in
    ``table.info["_saved_columns"]``, the second pops it unconditionally. It identifies
    geometry columns with ``isinstance``, which cannot see through a ``TypeDecorator``, so
    on this schema the pair desynchronised and ``create_all`` aborted on Postgres with
    ``KeyError: '_saved_columns'``. That is what stopped the first real
    ``docker compose up``.

    Nothing else here needed GeoAlchemy2 — the proximity queries in
    ``repositories/wells.py`` call ``ST_Distance`` and ``ST_DWithin`` as ordinary SQL
    functions — so the dependency is gone rather than worked around.
    """

    cache_ok = True

    def get_col_spec(self, **kw):
        return "geography(Point,4326)"


class GeographyPoint(TypeDecorator):
    """Surface location: PostGIS geography(Point,4326) on Postgres, WKT text elsewhere.

    Implemented as a TypeDecorator rather than a UserDefinedType because the latter emits
    its get_col_spec into DDL regardless of dialect, producing invalid SQL on the
    fallback backend.
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name in POSTGRES_DIALECTS:
            return dialect.type_descriptor(_PostgresGeography())
        return dialect.type_descriptor(String())

    def process_bind_param(self, value, dialect):
        # Both backends accept the extended WKT literal produced by point_wkt().
        return value

    def process_result_value(self, value, dialect):
        return value if value is None else str(value)


def point_wkt(longitude: float, latitude: float) -> str:
    """Build the WKT literal used for both backends."""
    return f"SRID=4326;POINT({longitude} {latitude})"


__all__ = ["Vector", "GeographyPoint", "point_wkt", "Float"]
