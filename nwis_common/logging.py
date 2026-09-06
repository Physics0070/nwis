"""Structured logging shared by services and pipelines.

Emits JSON in deployment and a readable console format during development, so that
ingestion, model execution, alert generation and engineer actions are all traceable.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_CONFIGURED = False

# Keys whose values must never reach the logs.
_REDACT_KEYS = {"password", "token", "secret", "api_key", "authorization", "database_url"}


def _redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "***redacted***"
    return event_dict


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Configure structlog + stdlib logging once per process."""
    global _CONFIGURED
    from nwis_common.config import get_config

    config = get_config()
    level_name = (level or config.get("logging.level", "INFO")).upper()
    output = (fmt or config.get("logging.format", "console")).lower()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level_name, logging.INFO),
        force=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if output == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level_name, logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None):
    """Return a bound structured logger, configuring logging on first use."""
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)
