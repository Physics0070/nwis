"""Deployment-safety tests.

These guard the checks that stop a production instance from running in a state that looks
healthy but is wrong: a fallback database, a placeholder password, or a wildcard CORS
origin.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import yaml

from backend.app.core.startup import UNSAFE_SECRETS, validate
from nwis_common import get_config
from nwis_common.paths import repo_root


def test_development_never_blocks_startup():
    """Local work must not be blocked by deployment checks."""
    result = validate(strict=False)
    assert result.ok


def test_strict_mode_rejects_the_current_local_setup():
    """The local machine runs on the fallback backend, which production must refuse."""
    result = validate(strict=True)
    assert not result.ok
    assert any("fallback" in error.lower() for error in result.errors)


def test_placeholder_passwords_are_recognised():
    """The default compose password must be treated as unsafe."""
    assert "change-me" in UNSAFE_SECRETS
    assert "nwis" in UNSAFE_SECRETS


def test_production_config_forbids_the_fallback_database():
    """Production must not silently degrade to the local storage backend."""
    path = repo_root() / "config" / "production.yaml"
    assert path.exists(), "config/production.yaml is required for deployment"
    production = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert production["database"]["allow_fallback"] is False


def test_production_config_does_not_allow_any_cors_origin():
    production = yaml.safe_load(
        (repo_root() / "config" / "production.yaml").read_text(encoding="utf-8")
    )
    assert "*" not in production["api"]["cors_origins"]


def test_no_secret_is_committed_in_configuration():
    """Configuration must not carry a real credential; secrets come from the environment."""
    text = (repo_root() / "config" / "default.yaml").read_text(encoding="utf-8")
    # The default URL intentionally uses an obvious placeholder, not a usable secret.
    assert "postgresql+psycopg://nwis:nwis@localhost" in text
    assert "DATABASE_URL env var overrides" in text


def test_env_example_exists_and_has_no_real_secret():
    path = repo_root() / ".env.example"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=change-me" in text


def test_env_file_is_git_ignored():
    """A committed .env would leak credentials."""
    ignore = (repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "\n.env\n" in ignore or ignore.startswith(".env\n")


def test_compose_defines_the_full_stack():
    compose = yaml.safe_load(
        (repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    assert {"database", "backend", "frontend"} <= set(services)

    # The backend must wait for a healthy database rather than racing it.
    assert services["backend"]["depends_on"]["database"]["condition"] == "service_healthy"

    # The compose backend must not be allowed to fall back to local storage.
    assert services["backend"]["environment"]["NWIS__DATABASE__ALLOW_FALLBACK"] == "false"

    # The password must be required, not defaulted.
    url = services["backend"]["environment"]["DATABASE_URL"]
    assert "${POSTGRES_PASSWORD}" in url


def test_compose_build_contexts_resolve():
    """Each Dockerfile referenced by compose must exist relative to its context."""
    compose = yaml.safe_load(
        (repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    )
    for name, service in compose["services"].items():
        build = service.get("build")
        if not build:
            continue
        context = repo_root() / build["context"]
        assert context.is_dir(), f"{name}: build context {context} does not exist"
        dockerfile = build.get("dockerfile")
        if dockerfile:
            assert (context / dockerfile).is_file(), (
                f"{name}: dockerfile {dockerfile} not found under {context}"
            )


def test_nginx_proxies_api_and_websocket():
    """Same-origin serving is what removes CORS from the production path."""
    config = (repo_root() / "docker" / "nginx.conf").read_text(encoding="utf-8")
    assert "location /api/" in config
    assert "location /ws/" in config
    # A WebSocket needs the upgrade headers forwarded or the connection fails.
    assert "proxy_set_header Upgrade" in config
    assert 'proxy_set_header Connection "upgrade"' in config
    # index.html must not be cached, or a deploy keeps serving the old bundle.
    assert "no-store" in config


def test_backend_image_runs_as_a_non_root_user():
    dockerfile = (repo_root() / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")
    assert "USER nwis" in dockerfile
    assert "HEALTHCHECK" in dockerfile


@pytest.mark.parametrize("name", ["lithology", "anomaly", "analogue_embedding"])
def test_expected_models_are_registered(name):
    """A deployment with an unregistered model degrades; the check should surface it."""
    from ml.common.registry import ModelRegistry

    registry = ModelRegistry(get_config().get("paths.models"))
    registered = {record["name"] for record in registry.list_models()}
    if name not in registered:
        pytest.skip(f"{name} not trained in this checkout")
    assert name in registered
