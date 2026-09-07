"""API integration tests.

Run against the real application and the loaded database, because the contract that
matters is what the frontend actually receives. Tests skip (rather than fail) when the
knowledge base has not been loaded, so a fresh clone does not report false failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def loaded(client):
    counts = client.get("/api/status").json()["counts"]
    if counts["wells"] == 0:
        pytest.skip("knowledge base is empty; run data_pipeline.load_database")
    return counts


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_reports_storage_backend_truthfully(client):
    body = client.get("/api/status").json()
    database = body["database"]
    # The API must state which implementation is serving each concern.
    assert database["spatial_query"] in {"postgis", "sql_haversine"}
    assert database["vector_search"] in {"pgvector", "numpy_cosine"}
    assert database["timeseries_storage"] in {"timescaledb_hypertable", "indexed_table"}
    if database["fallback_active"]:
        assert body["warnings"], "a fallback backend must be surfaced as a warning"


def test_wells_are_paginated(client, loaded):
    body = client.get("/api/wells?limit=5").json()
    assert body["total"] == loaded["wells"]
    assert len(body["items"]) <= 5


def test_unknown_well_returns_404(client, loaded):
    assert client.get("/api/wells/99999999").status_code == 404


def test_nearby_returns_ascending_distances(client, loaded):
    well_id = client.get("/api/wells?limit=1").json()["items"][0]["id"]
    response = client.get(f"/api/wells/{well_id}/nearby?radius_km=100&limit=10")
    assert response.status_code in (200, 409)
    if response.status_code == 200:
        distances = [row["distance_km"] for row in response.json()]
        assert distances == sorted(distances)


def test_analogue_response_declares_its_weights_and_gaps(client, loaded):
    well_id = client.get("/api/wells?limit=1").json()["items"][0]["id"]
    body = client.get(f"/api/wells/{well_id}/analogues?top_k=3").json()
    assert set(body["weights"]) >= {"geology", "formation", "depth", "geography"}
    for match in body["matches"]:
        # A match must say which dimensions it could and could not use.
        assert match["dimensions_used"], "a match with no usable dimension must not rank"
        assert isinstance(match["dimensions_unavailable"], list)
        assert 0.0 <= match["score"] <= 1.0


def test_risk_never_invents_a_probability(client, loaded):
    """The core honesty contract: hybrid_indicator mode must not report a probability."""
    well_id = client.get("/api/wells?has_telemetry=true&limit=1").json()["items"][0]["id"]
    body = client.get(f"/api/wells/{well_id}/risk?depth_m=800").json()
    assert body["mode"] in {"supervised", "hybrid_indicator"}
    if body["mode"] == "hybrid_indicator":
        assert body["probability"] is None
    assert 0.0 <= body["score"] <= 1.0
    assert body["narrative"]
    assert body["notes"], "the reason for the chosen mode must be stated"


def test_risk_components_are_explained(client, loaded):
    well_id = client.get("/api/wells?has_telemetry=true&limit=1").json()["items"][0]["id"]
    body = client.get(f"/api/wells/{well_id}/risk?depth_m=800&anomaly_score=0.9").json()
    for component in body["components"]:
        assert component["explanation"], "every component must carry an explanation"
        assert 0.0 <= component["value"] <= 1.0


def test_models_report_only_measured_metrics(client, loaded):
    models = client.get("/api/models").json()
    for model in models:
        assert model["trained_at"], "a registered model must record when it was trained"
        assert model["feature_columns"], "a model must record its feature list"
        # Metrics must be a real measurement structure, never a bare number typed in.
        assert isinstance(model["metrics"], dict)


def test_untrained_model_reports_unavailable_rather_than_zero(client):
    response = client.get("/api/models/does-not-exist")
    assert response.status_code == 404
    assert "not been trained" in response.json()["detail"]


def test_telemetry_absent_is_a_clear_conflict_not_an_empty_list(client, loaded):
    """A well with no telemetry must say so, not return [] which reads as 'all quiet'."""
    wells = client.get("/api/wells?has_telemetry=false&limit=1").json()["items"]
    if not wells:
        pytest.skip("every well has telemetry")
    response = client.get(f"/api/wells/{wells[0]['id']}/telemetry")
    assert response.status_code == 409
    assert "no telemetry" in response.json()["detail"].lower()


def test_engineer_action_requires_a_valid_decision(client, loaded):
    alerts = client.get("/api/alerts?limit=1").json()
    if not alerts:
        pytest.skip("no alerts raised yet")
    response = client.post(
        "/api/engineer-actions",
        json={"alert_id": alerts[0]["id"], "decision": "maybe"},
    )
    assert response.status_code == 422


def test_engineer_action_is_stored_for_future_training(client, loaded):
    alerts = client.get("/api/alerts?limit=1").json()
    if not alerts:
        pytest.skip("no alerts raised yet")
    response = client.post(
        "/api/engineer-actions",
        json={
            "alert_id": alerts[0]["id"],
            "decision": "investigating",
            "action_description": "test action",
            "engineer_name": "pytest",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["available_for_training"] is True
    assert body["decision"] == "investigating"


def test_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert "/api/wells" in schema["paths"]
    assert "/api/wells/{well_id}/analogues" in schema["paths"]
