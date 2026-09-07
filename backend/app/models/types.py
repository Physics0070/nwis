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
from sqlalchemy.types import TypeDecorator

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
            from geoalchemy2 import Geography

            return dialect.type_descriptor(
                Geography(geometry_type="POINT", srid=4326, spatial_index=False)
            )
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
