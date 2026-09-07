"""Database engine, session management and capability detection.

NWIS targets PostgreSQL with PostGIS, TimescaleDB and pgvector. When that stack is not
reachable and ``database.allow_fallback`` is set, it falls back to the configured local
database so the prototype still runs end to end.

The active capabilities are detected at startup and exposed through the API, so the UI can
state truthfully which storage features are in play rather than implying all of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from nwis_common import get_config, get_logger

log = get_logger("nwis.database")


@dataclass
class DatabaseCapabilities:
    """What the connected database can actually do."""

    dialect: str
    url_scheme: str
    is_postgres: bool = False
    postgis: bool = False
    timescaledb: bool = False
    pgvector: bool = False
    fallback_active: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dialect": self.dialect,
            "url_scheme": self.url_scheme,
            "postgis": self.postgis,
            "timescaledb": self.timescaledb,
            "pgvector": self.pgvector,
            "fallback_active": self.fallback_active,
            "spatial_query": "postgis" if self.postgis else "sql_haversine",
            "vector_search": "pgvector" if self.pgvector else "numpy_cosine",
            "timeseries_storage": "timescaledb_hypertable" if self.timescaledb else "indexed_table",
            "notes": self.notes,
        }


def _detect_capabilities(engine: Engine, fallback_active: bool) -> DatabaseCapabilities:
    dialect = engine.dialect.name
    capabilities = DatabaseCapabilities(
        dialect=dialect,
        url_scheme=engine.url.get_backend_name(),
        is_postgres=dialect == "postgresql",
        fallback_active=fallback_active,
    )

    if not capabilities.is_postgres:
        capabilities.notes.append(
            "Running on the local fallback backend. Nearby-well search uses a SQL "
            "haversine expression and vector search uses NumPy cosine similarity. "
            "Results are equivalent; PostGIS and pgvector make them faster and exact."
        )
        return capabilities

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT extname FROM pg_extension")
            ).scalars().all()
        installed = {str(r) for r in rows}
        capabilities.postgis = "postgis" in installed
        capabilities.timescaledb = "timescaledb" in installed
        capabilities.pgvector = "vector" in installed
        missing = [
            name
            for name, present in (
                ("postgis", capabilities.postgis),
                ("timescaledb", capabilities.timescaledb),
                ("vector", capabilities.pgvector),
            )
            if not present
        ]
        if missing:
            capabilities.notes.append(
                f"PostgreSQL is connected but these extensions are not installed: "
                f"{', '.join(missing)}. The matching fallback path is used for each."
            )
    except SQLAlchemyError as exc:
        capabilities.notes.append(f"Extension detection failed: {exc}")

    return capabilities


def _make_engine(url: str, config) -> Engine:
    kwargs: dict = {"echo": bool(config.get("logging.sql_echo", False)), "future": True}
    if url.startswith("postgresql"):
        kwargs["pool_size"] = int(config.get("database.pool_size"))
        kwargs["max_overflow"] = int(config.get("database.max_overflow"))
        kwargs["pool_pre_ping"] = True
        # Without an explicit timeout a probe against an absent server can hang for the
        # OS default, which would stall API startup instead of falling back promptly.
        kwargs["connect_args"] = {
            "connect_timeout": int(config.get("database.connect_timeout_seconds"))
        }
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def _engine_and_capabilities() -> tuple[Engine, DatabaseCapabilities]:
    config = get_config()
    primary = str(config.get("database.url"))
    allow_fallback = bool(config.get("database.allow_fallback"))

    try:
        engine = _make_engine(primary, config)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        log.info("database_connected", backend=engine.dialect.name, fallback=False)
        return engine, _detect_capabilities(engine, fallback_active=False)
    except Exception as exc:
        if not allow_fallback:
            log.error("database_unavailable", error=str(exc))
            raise
        fallback = str(config.get("database.fallback_url"))
        log.warning(
            "database_fallback_engaged",
            reason=type(exc).__name__,
            detail=str(exc)[:200],
            note="primary database unreachable; using local fallback",
        )
        engine = _make_engine(fallback, config)
        if engine.dialect.name == "sqlite":
            _enable_sqlite_pragmas(engine)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return engine, _detect_capabilities(engine, fallback_active=True)


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """Foreign keys are off by default in SQLite; NWIS relies on them."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # pragma: no cover - driver callback
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_engine() -> Engine:
    return _engine_and_capabilities()[0]


def get_capabilities() -> DatabaseCapabilities:
    return _engine_and_capabilities()[1]


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def session_scope() -> Session:
    """Create a new session. Caller is responsible for closing it."""
    return _session_factory()()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = session_scope()
    try:
        yield session
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Used by tests that point the application at a different database."""
    _engine_and_capabilities.cache_clear()
    _session_factory.cache_clear()
