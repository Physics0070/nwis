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


def test_blank_search_query_is_a_client_error_not_a_service_outage(client, loaded):
    """A whitespace query is bad input, not a broken search index.

    `q="  "` clears FastAPI's min_length=2 check and only collapses to empty after
    stripping. Reporting that as 503 tells the caller the service is down, which is both
    wrong and indistinguishable from the genuine "index not built yet" case.
    """
    response = client.get("/api/documents/search", params={"q": "  "})
    assert response.status_code == 422, response.text
    assert response.status_code != 503


def test_search_still_reports_a_real_outage_as_503(client, loaded, monkeypatch):
    """The 4xx fix must not swallow the genuine unavailable case."""
    from backend.app.services import document_search

    def unavailable(*args, **kwargs):
        raise document_search.SearchUnavailable("No passage embeddings yet.")

    monkeypatch.setattr(document_search, "search", unavailable)
    response = client.get("/api/documents/search", params={"q": "stuck pipe"})
    assert response.status_code == 503, response.text


def test_status_serves_the_ui_thresholds(client):
    """The frontend must not carry its own copy of a threshold.

    Every value the UI renders with — the depth step it quantises to, the speeds it
    offers, the look-ahead window it draws the radar over — is served from here, so the
    picture and the query behind it cannot disagree.
    """
    config = client.get("/api/status").json()["config"]
    for key in (
        "depth_context_step_m",
        "lithology_match_tolerance_m",
        "page_size",
        "replay_speeds",
        "risk_lookahead_m",
    ):
        assert key in config, f"{key} is not served to the UI"
    assert config["depth_context_step_m"] > 0
    assert config["risk_lookahead_m"] > 0
    assert config["replay_speeds"], "the UI has no speeds to offer"
    # Every offered speed must be one the replay engine will actually accept.
    for speed in config["replay_speeds"]:
        assert config["replay_min_speed"] <= speed <= config["replay_max_speed"]


def test_risk_uses_telemetry_from_the_queried_depth(client, loaded):
    """Risk at a depth must describe that depth.

    Resolving telemetry as "the most recent stored sample" froze the measured half of
    every assessment at the end of the recording: two very different depths returned
    identical rule inputs and an identical anomaly score, while the historical half moved
    with the bit. The regression is subtle and entirely invisible in the UI, so it is
    pinned here.
    """
    wells = client.get("/api/wells?has_telemetry=true&limit=1").json()["items"]
    if not wells:
        pytest.skip("no well with telemetry is loaded")
    well_id = wells[0]["id"]

    samples = client.get(f"/api/wells/{well_id}/telemetry?limit=20000").json()
    depths = sorted({s["bit_depth_m"] for s in samples if s["bit_depth_m"] is not None})
    if len(depths) < 2:
        pytest.skip("this well never changes depth, so there is nothing to distinguish")

    shallow = client.get(f"/api/wells/{well_id}/risk?depth_m={depths[0]}").json()
    deep = client.get(f"/api/wells/{well_id}/risk?depth_m={depths[-1]}").json()

    def note_text(payload):
        return " ".join(payload["notes"])

    # Each assessment must say where its telemetry came from, and the two must not be
    # the same instant.
    assert "Telemetry taken from the sample recorded at" in note_text(
        shallow
    ) or "No telemetry within" in note_text(shallow)
    assert note_text(shallow) != note_text(deep), (
        "both depths resolved to the same telemetry, so depth is being ignored"
    )


def test_risk_refuses_telemetry_from_a_different_part_of_the_hole(client, loaded):
    """Beyond the configured tolerance, no measurement is borrowed.

    A reading from 2 km away is not evidence about conditions here. The engine must say
    so rather than let the nearest row stand in.
    """
    wells = client.get("/api/wells?has_telemetry=true&limit=1").json()["items"]
    if not wells:
        pytest.skip("no well with telemetry is loaded")
    well_id = wells[0]["id"]

    # A depth far past the end of any Volve recording.
    payload = client.get(f"/api/wells/{well_id}/risk?depth_m=9000").json()
    notes = " ".join(payload["notes"])
    assert "No telemetry within" in notes
    assert all(c["name"] != "rules" for c in payload["components"]), (
        "rules were evaluated on measurements from a different depth"
    )
