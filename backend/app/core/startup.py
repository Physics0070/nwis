"""Startup validation.

Deployment mistakes are cheap to make and expensive to notice: a production instance
running on the local fallback database, or with a default password, looks perfectly
healthy from the outside. These checks fail fast instead.

In development the same problems are reported as warnings, so local work is never blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.core.database import get_capabilities
from nwis_common import get_config, get_logger

log = get_logger("nwis.startup")

# Placeholder credentials that must never reach a deployed instance.
UNSAFE_SECRETS = {"nwis", "change-me", "password", "postgres", "changeme", "secret"}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(strict: bool | None = None) -> ValidationResult:
    """Check the running configuration. ``strict`` defaults to production behaviour."""
    config = get_config()
    result = ValidationResult()
    is_production = config.environment == "production"
    strict = is_production if strict is None else strict

    capabilities = get_capabilities()
    database_url = str(config.get("database.url"))

    # --- storage ------------------------------------------------------------
    if capabilities.fallback_active:
        message = (
            "The application is running on the local fallback storage backend. PostGIS, "
            "TimescaleDB and pgvector are not in use."
        )
        (result.errors if strict else result.warnings).append(message)

    if strict and not capabilities.fallback_active:
        for extension, present in (
            ("PostGIS", capabilities.postgis),
            ("TimescaleDB", capabilities.timescaledb),
            ("pgvector", capabilities.pgvector),
        ):
            if not present:
                result.warnings.append(
                    f"{extension} is not installed in the connected database; the "
                    "equivalent fallback implementation will be used for those queries."
                )

    # --- credentials ---------------------------------------------------------
    lowered = database_url.lower()
    if strict and any(f":{secret}@" in lowered for secret in UNSAFE_SECRETS):
        result.errors.append(
            "The database URL contains a placeholder password. Set POSTGRES_PASSWORD to "
            "a real secret before deploying."
        )

    if strict and lowered.startswith("sqlite"):
        result.errors.append("A production deployment must not use a SQLite database.")

    # --- CORS ----------------------------------------------------------------
    origins = list(config.get("api.cors_origins", []))
    if strict and "*" in origins:
        result.errors.append(
            "CORS is configured to allow any origin, which is unsafe with credentials "
            "enabled. List the exact origins that need access."
        )

    # --- models ---------------------------------------------------------------
    from ml.common.registry import ModelRegistry

    registry = ModelRegistry(config.get("paths.models"))
    registered = {record["name"] for record in registry.list_models()}
    for name in ("lithology", "anomaly", "analogue_embedding"):
        if name not in registered:
            result.warnings.append(
                f"Model {name!r} is not registered. Endpoints that depend on it will "
                "report that it is unavailable rather than returning a value."
            )

    return result


def run_startup_checks() -> None:
    """Log the outcome, and refuse to start when a strict check fails."""
    result = validate()

    for warning in result.warnings:
        log.warning("startup_check_warning", detail=warning)

    if not result.ok:
        for error in result.errors:
            log.error("startup_check_failed", detail=error)
        raise RuntimeError(
            "Startup checks failed:\n  - " + "\n  - ".join(result.errors)
        )

    log.info("startup_checks_passed", warnings=len(result.warnings))
