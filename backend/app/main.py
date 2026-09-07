"""NWIS FastAPI application.

Error handling is deliberate: the dashboard must degrade into an informative state rather
than crash. Missing telemetry, an untrained model, or an absent analogue all produce a
clear, specific message the UI can display, never a stack trace and never a fabricated
value.
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.routes import intelligence, replay, wells
from backend.app.core.database import get_capabilities, get_engine
from backend.app.core.startup import run_startup_checks
from backend.app.models import Base
from nwis_common import configure_logging, get_config, get_logger

log = get_logger("nwis.api")


def _silence_windows_disconnect_noise() -> None:
    """Stop abrupt websocket disconnects filling the log with tracebacks.

    On Windows the proactor event loop raises ConnectionResetError from
    _call_connection_lost when a client closes a socket without a clean handshake, which
    a browser tab does every time it is refreshed. It is expected, not an error, and the
    traceback obscures real problems.
    """
    if not sys.platform.startswith("win"):
        return

    def handler(loop, context):
        if isinstance(context.get("exception"), ConnectionResetError):
            return
        loop.default_exception_handler(context)

    try:
        asyncio.get_running_loop().set_exception_handler(handler)
    except RuntimeError:  # pragma: no cover - called outside a running loop
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    _silence_windows_disconnect_noise()
    config = get_config()
    engine = get_engine()
    Base.metadata.create_all(engine)
    capabilities = get_capabilities()
    log.info(
        "api_startup",
        environment=config.environment,
        **capabilities.as_dict(),
    )
    if capabilities.fallback_active:
        log.warning(
            "storage_fallback_active",
            note="PostGIS/TimescaleDB/pgvector are not in use; equivalent fallback "
                 "implementations are serving spatial, time-series and vector queries",
        )

    # Fails fast in production on a fallback database, a placeholder password or a
    # wildcard CORS origin. Warnings only in development.
    run_startup_checks()
    yield
    log.info("api_shutdown")


def create_app() -> FastAPI:
    config = get_config()
    application = FastAPI(
        title=str(config.get("app.name")),
        version="0.1.0",
        description=(
            "Nearby Wells Intelligence System — offset well knowledge and decision "
            "support for drilling operations. Every value served here originates in the "
            "database, a trained model, or configuration."
        ),
        lifespan=lifespan,
        root_path=str(config.get("api.root_path", "")),
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.get("api.cors_origins")),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    application.include_router(wells.router)
    application.include_router(intelligence.router)
    application.include_router(replay.router)
    application.include_router(replay.ws_router)

    @application.exception_handler(SQLAlchemyError)
    async def _database_error(request: Request, exc: SQLAlchemyError):
        # Log the detail; return a sanitised message so internals are not exposed.
        log.error("database_error", path=str(request.url.path), error=str(exc))
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The knowledge base is temporarily unavailable. No data was "
                          "returned; nothing shown is estimated."
            },
        )

    @application.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("unhandled_error", path=str(request.url.path),
                  error=str(exc), error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected error occurred while serving this request."},
        )

    @application.get("/health", tags=["system"])
    def health():
        capabilities = get_capabilities()
        return {
            "status": "ok",
            "database": capabilities.dialect,
            "fallback_active": capabilities.fallback_active,
        }

    return application


app = create_app()
