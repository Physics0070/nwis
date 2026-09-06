"""Layered configuration.

Precedence (highest last):
    1. config/default.yaml
    2. config/<environment>.yaml
    3. .env / process environment (NWIS__SECTION__KEY=value, plus a few well-known names)

Application code must never contain thresholds, weights, window sizes or K values.
It reads them from here.
"""
from __future__ import annotations

import ast
import os
from copy import deepcopy
from functools import lru_cache
from typing import Any, Iterable, Mapping

import yaml

from nwis_common.paths import repo_root, resolve_path

ENV_PREFIX = "NWIS__"
_MISSING = object()

# Environment variables that map onto a config path without the prefix convention.
_DIRECT_ENV_MAP: dict[str, tuple[str, ...]] = {
    "DATABASE_URL": ("database", "url"),
    "NWIS_ENV": ("app", "environment"),
    "LOG_LEVEL": ("logging", "level"),
    "API_PORT": ("api", "port"),
}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _coerce(raw: str) -> Any:
    """Turn an environment string into an int/float/bool/list where unambiguous."""
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


def _load_dotenv(path) -> None:
    """Minimal .env loader; does not overwrite variables already in the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _set_path(target: dict[str, Any], path: Iterable[str], value: Any) -> None:
    keys = list(path)
    cursor = target
    for key in keys[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[key] = nxt
        cursor = nxt
    cursor[keys[-1]] = value


class Config:
    """Read-only view over the merged configuration tree."""

    def __init__(self, data: Mapping[str, Any]):
        self._data: dict[str, Any] = dict(data)

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        """Fetch a value by dotted path, e.g. ``config.get("analogue.top_k")``."""
        cursor: Any = self._data
        for part in dotted.split("."):
            if not isinstance(cursor, Mapping) or part not in cursor:
                if default is _MISSING:
                    raise ConfigError(f"Missing configuration key: {dotted!r}")
                return default
            cursor = cursor[part]
        return deepcopy(cursor) if isinstance(cursor, (dict, list)) else cursor

    def section(self, dotted: str) -> dict[str, Any]:
        value = self.get(dotted)
        if not isinstance(value, dict):
            raise ConfigError(f"Configuration key {dotted!r} is not a section")
        return value

    def path(self, dotted: str):
        """Fetch a configured path, resolved against the repository root."""
        return resolve_path(str(self.get(dotted)))

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    @property
    def environment(self) -> str:
        return str(self.get("app.environment"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Config(environment={self.environment!r}, sections={sorted(self._data)})"


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(data)
    for name, raw in os.environ.items():
        if name in _DIRECT_ENV_MAP:
            _set_path(result, _DIRECT_ENV_MAP[name], _coerce(raw))
        elif name.startswith(ENV_PREFIX):
            parts = [p.lower() for p in name[len(ENV_PREFIX):].split("__") if p]
            if parts:
                _set_path(result, parts, _coerce(raw))
    return result


def load_config(environment: str | None = None) -> Config:
    """Build the merged configuration tree. Prefer :func:`get_config` in application code."""
    root = repo_root()
    _load_dotenv(root / ".env")

    default_file = root / "config" / "default.yaml"
    if not default_file.exists():
        raise ConfigError(f"Base configuration not found at {default_file}")
    data: dict[str, Any] = yaml.safe_load(default_file.read_text(encoding="utf-8")) or {}

    env_name = environment or os.environ.get("NWIS_ENV") or data.get("app", {}).get("environment", "development")
    env_file = root / "config" / f"{env_name}.yaml"
    if env_file.exists():
        overlay = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, overlay)

    data = _apply_env_overrides(data)
    _set_path(data, ("app", "environment"), env_name)
    return Config(data)


@lru_cache(maxsize=4)
def _cached_config(environment: str | None) -> Config:
    return load_config(environment)


def get_config(environment: str | None = None) -> Config:
    """Return the process-wide configuration (cached)."""
    return _cached_config(environment)


def reset_config_cache() -> None:
    """Clear the cache; used by tests that manipulate environment variables."""
    _cached_config.cache_clear()
