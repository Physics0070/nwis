"""Well queries, including server-side geospatial search.

Nearby-well search runs in the database, not in the frontend. On PostgreSQL it uses
PostGIS ``ST_DWithin``/``ST_Distance`` on the geography column; elsewhere it uses an
equivalent haversine expression in SQL. Both return great-circle distances in kilometres,
so callers get the same answer either way.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Float, and_, func, select, text
from sqlalchemy.orm import Session

from backend.app.core.database import get_capabilities
from backend.app.models import Well

EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius


@dataclass
class NearbyWell:
    well: Well
    distance_km: float


def get_well(session: Session, well_id: int) -> Well | None:
    return session.get(Well, well_id)


def get_well_by_name(session: Session, name: str) -> Well | None:
    return session.execute(select(Well).where(Well.name == name)).scalars().first()


def list_wells(
    session: Session,
    *,
    limit: int,
    offset: int = 0,
    field_name: str | None = None,
    dataset: str | None = None,
    has_telemetry: bool | None = None,
    search: str | None = None,
) -> tuple[list[Well], int]:
    """Paginated well listing with optional filters. Returns (rows, total)."""
    statement = select(Well)
    conditions = []
    if field_name:
        conditions.append(Well.field_name == field_name)
    if dataset:
        conditions.append(Well.source_dataset == dataset)
    if has_telemetry is not None:
        conditions.append(Well.has_telemetry.is_(has_telemetry))
    if search:
        conditions.append(Well.name.ilike(f"%{search}%"))
    if conditions:
        statement = statement.where(and_(*conditions))

    total = session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    rows = (
        session.execute(statement.order_by(Well.name).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), int(total)


def _haversine_km_expression(latitude: float, longitude: float):
    """Great-circle distance in km as a SQL expression.

    Uses only sin/cos/acos/radians, which both PostgreSQL and SQLite 3.35+ provide.
    """
    lat_radians = func.radians(Well.latitude)
    lon_radians = func.radians(Well.longitude)
    target_lat = func.radians(float(latitude))
    target_lon = func.radians(float(longitude))

    cosine = (
        func.sin(target_lat) * func.sin(lat_radians)
        + func.cos(target_lat) * func.cos(lat_radians) * func.cos(lon_radians - target_lon)
    )
    # Clamp for floating point drift; acos(1.0000000002) is a domain error.
    clamped = func.min(func.max(cosine, -1.0), 1.0)
    return (EARTH_RADIUS_KM * func.acos(clamped)).cast(Float)


def find_nearby_wells(
    session: Session,
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    limit: int,
    exclude_well_id: int | None = None,
) -> list[NearbyWell]:
    """Wells within ``radius_km`` of a point, nearest first.

    Executed in the database on both backends. Wells without a position are excluded:
    an unmapped well cannot be truthfully described as near anything.
    """
    capabilities = get_capabilities()

    if capabilities.postgis:
        distance = (
            func.ST_Distance(
                Well.location,
                func.ST_GeogFromText(f"SRID=4326;POINT({longitude} {latitude})"),
            )
            / 1000.0
        ).label("distance_km")
        statement = (
            select(Well, distance)
            .where(Well.location.isnot(None))
            .where(
                func.ST_DWithin(
                    Well.location,
                    func.ST_GeogFromText(f"SRID=4326;POINT({longitude} {latitude})"),
                    float(radius_km) * 1000.0,
                )
            )
        )
    else:
        distance = _haversine_km_expression(latitude, longitude).label("distance_km")
        statement = (
            select(Well, distance)
            .where(Well.latitude.isnot(None), Well.longitude.isnot(None))
            .where(distance <= float(radius_km))
        )

    if exclude_well_id is not None:
        statement = statement.where(Well.id != exclude_well_id)

    rows = session.execute(statement.order_by(text("distance_km")).limit(limit)).all()
    return [NearbyWell(well=row[0], distance_km=float(row[1])) for row in rows]


def count_unmapped(session: Session) -> int:
    """Wells with no established surface position."""
    return int(
        session.scalar(
            select(func.count()).select_from(Well).where(Well.latitude.is_(None))
        )
        or 0
    )
