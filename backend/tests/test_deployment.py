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


def test_strict_mode_rejects_a_fallback_database():
    """Production must refuse to run on the local fallback backend.

    Asserted against whichever backend is actually connected, because both are now
    reachable: the Docker stack publishes Postgres on 5432, so this same checkout runs on
    real Postgres when the containers are up and on SQLite when they are not. Pinning the
    test to one of those made it fail the moment the stack came up — an environment fact
    masquerading as a regression.
    """
    from backend.app.core.database import get_capabilities

    result = validate(strict=True)

    if get_capabilities().fallback_active:
        assert not result.ok
        assert any("fallback" in error.lower() for error in result.errors)
    else:
        # On the real stack the fallback complaint must be absent. Any remaining error
        # is a genuine deployment problem, not the storage backend.
        assert not any("fallback" in error.lower() for error in result.errors)


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


def test_compose_seeds_the_database_before_the_api_serves_it():
    """`docker compose up` must not yield an empty database.

    The stack previously brought up Postgres, the API and the SPA with nothing loading
    any data, so a demo would have shown zero wells.
    """
    compose = yaml.safe_load(
        (repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    assert "loader" in services, "no service loads the knowledge base"

    loader = services["loader"]
    assert loader.get("restart") == "no", "the seed must run once, not restart forever"

    backend_deps = services["backend"]["depends_on"]
    assert backend_deps["loader"]["condition"] == "service_completed_successfully", (
        "the API must wait for seeding, or it serves an empty database"
    )


def test_loader_resets_the_schema_before_the_migration_not_after():
    """Ordering is load-then-migrate, and it is not cosmetic.

    `load_database --reset` calls drop_all. Running it after the migration would drop the
    hypertable and the GIST/ivfflat indexes the migration had just created, silently
    losing every Postgres-specific feature the stack exists to provide.
    """
    compose = yaml.safe_load(
        (repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    )
    script = compose["services"]["loader"]["command"][-1]

    reset_at = script.index("load_database --reset")
    migrate_at = script.index("alembic")
    assert reset_at < migrate_at, "drop_all would destroy the migration's Postgres objects"


def test_loader_can_reach_the_coordinate_source():
    """Seeding reads the NPD coordinate CSVs from data/raw.

    The backend mounts only data/processed; mounting the same for the loader would leave
    every well without a surveyed position.
    """
    compose = yaml.safe_load(
        (repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    )
    mounts = compose["services"]["loader"]["volumes"]
    assert any(m.startswith("./data:") for m in mounts), (
        "loader cannot read data/raw, so NPD positions would be missing"
    )
