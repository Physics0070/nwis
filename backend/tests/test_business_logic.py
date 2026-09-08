"""Tests for NWIS business logic.

These target the decisions that would be dangerous to get wrong: the honesty rules
(no invented probability, no invented position, no silently-reduced evidence), the
scoring maths, well-level split integrity, and alert deduplication.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import inspect

import numpy as np
import pandas as pd
import pytest

from nwis_common import get_config


# --------------------------------------------------------------------- configuration


def test_no_magic_numbers_thresholds_come_from_config():
    config = get_config()
    thresholds = config.section("alerts.thresholds")
    assert set(thresholds) == {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert thresholds["CRITICAL"] > thresholds["HIGH"] > thresholds["MEDIUM"]


def test_analogue_weights_are_configured_and_positive():
    weights = get_config().section("analogue.weights")
    assert set(weights) == {
        "geology", "formation", "depth", "drilling_behaviour", "geography"
    }
    assert all(value > 0 for value in weights.values())


def test_embedding_dimension_matches_feature_construction():
    """The configured vector width must equal what the builder actually produces."""
    from ml.similarity.build_embeddings import build_feature_names

    config = get_config()
    curves = list(config.get("analogue.embedding_curves"))
    assert len(build_feature_names(curves)) == int(config.get("analogue.embedding_dim"))


# ------------------------------------------------------------------ risk engine rules


def test_classify_level_uses_configured_thresholds():
    from backend.app.services.risk import classify_level

    thresholds = {"INFO": 0.0, "LOW": 0.25, "MEDIUM": 0.45, "HIGH": 0.65, "CRITICAL": 0.85}
    assert classify_level(0.0, thresholds) == "INFO"
    assert classify_level(0.30, thresholds) == "LOW"
    assert classify_level(0.45, thresholds) == "MEDIUM"
    assert classify_level(0.70, thresholds) == "HIGH"
    assert classify_level(0.99, thresholds) == "CRITICAL"


def test_rules_engine_flags_out_of_band_measurements():
    from backend.app.services.risk import evaluate_rules

    config = get_config()
    score, triggered = evaluate_rules({"pit_gain_loss_m3": -500.0}, config)
    assert score > 0
    assert any("pit_gain_loss_m3" in message for message in triggered)


def test_rules_engine_silent_when_measurements_are_normal():
    from backend.app.services.risk import evaluate_rules

    score, triggered = evaluate_rules({"surface_torque_knm": 5.0}, get_config())
    assert score == 0.0
    assert triggered == []


def test_rules_engine_ignores_absent_channels():
    """A channel that was not measured must not be treated as a breach."""
    from backend.app.services.risk import evaluate_rules

    score, triggered = evaluate_rules({}, get_config())
    assert score == 0.0 and triggered == []


# ------------------------------------------------------------------- similarity maths


def test_cosine_similarity_bounds_and_identity():
    from backend.app.services.analogue import cosine_similarity

    vector = np.array([1.0, 2.0, 3.0])
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)
    assert cosine_similarity(vector, -vector) == pytest.approx(0.0)
    assert 0.0 <= cosine_similarity(vector, np.array([3.0, -1.0, 0.5])) <= 1.0


def test_cosine_similarity_handles_zero_vector():
    from backend.app.services.analogue import cosine_similarity

    assert cosine_similarity(np.zeros(3), np.array([1.0, 2.0, 3.0])) == 0.0


def test_haversine_matches_known_distance():
    """Volve to the nearest FORCE well is about 3.6 km; check the maths independently."""
    from backend.app.services.analogue import haversine_km

    volve = (58.440974, 1.885883)
    force_well = (58.445030, 1.946510)
    distance = haversine_km(*volve, *force_well)
    assert 3.0 < distance < 4.2
    assert haversine_km(*volve, *volve) == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------------ unit system


def test_witsml_si_units_convert_to_driller_units():
    from data_pipeline.common.units import convert_series

    values, unit, applied = convert_series(pd.Series([1_000_000.0]), "N")
    assert applied and unit == "kN" and values.iloc[0] == pytest.approx(1000.0)

    values, unit, applied = convert_series(pd.Series([100_000.0]), "Pa")
    assert applied and unit == "bar" and values.iloc[0] == pytest.approx(1.0)

    values, unit, applied = convert_series(pd.Series([273.15]), "K")
    assert applied and unit == "degC" and values.iloc[0] == pytest.approx(0.0)

    values, unit, applied = convert_series(pd.Series([1.0]), "c/s")
    assert applied and unit == "rpm" and values.iloc[0] == pytest.approx(60.0)


def test_unknown_unit_passes_through_untouched():
    """An unrecognised unit must never be silently 'converted' by guesswork."""
    from data_pipeline.common.units import convert_series

    values, unit, applied = convert_series(pd.Series([42.0]), "furlongs")
    assert not applied and unit == "furlongs" and values.iloc[0] == 42.0


# ------------------------------------------------------------------ split integrity


def test_well_level_split_never_shares_a_well_between_splits():
    from data_pipeline.force.prepare import make_well_level_splits

    rng = np.random.default_rng(0)
    frame = pd.DataFrame(
        {
            "WELL": np.repeat([f"W{i}" for i in range(30)], 100),
            "DEPTH_MD": np.tile(np.arange(100, dtype=float), 30),
            "FORCE_2020_LITHOFACIES_LITHOLOGY": rng.choice(
                [65000, 30000, 65030], size=3000
            ),
        }
    )
    assignment, manifest = make_well_level_splits(frame, get_config())

    assert set(assignment) == set(frame["WELL"].unique())
    # Each well appears in exactly one split.
    for split in ("train", "validation", "test"):
        members = set(manifest["coverage"][split]["wells"])
        for other in ("train", "validation", "test"):
            if other != split:
                assert not (members & set(manifest["coverage"][other]["wells"]))


def test_split_fractions_must_sum_to_one():
    from data_pipeline.force.prepare import make_well_level_splits

    class BadConfig:
        def section(self, _key):
            return {
                "strategy": "well_level",
                "train_fraction": 0.5,
                "validation_fraction": 0.3,
                "test_fraction": 0.3,
                "random_seed": 1,
                "stratify_by_class_presence": False,
                "rare_class_max_wells": 5,
            }

        def get(self, key, default=None):
            return {"force_pipeline.well_column": "WELL",
                    "force_pipeline.target_column": "T"}.get(key, default)

    frame = pd.DataFrame({"WELL": ["A", "B"], "T": [1, 2]})
    with pytest.raises(ValueError, match="sum to 1.0"):
        make_well_level_splits(frame, BadConfig())


# --------------------------------------------------------------- telemetry pipeline


def test_constant_channels_are_detected():
    """ROP is constant zero in the Volve mirror; such channels must be reported."""
    from data_pipeline.volve.prepare import find_constant_channels

    frame = pd.DataFrame(
        {"rop_m_per_hr": [0.0] * 10, "hookload_kn": np.linspace(500, 900, 10)}
    )
    constant = find_constant_channels(frame, tolerance=1e-9)
    assert "rop_m_per_hr" in constant
    assert "hookload_kn" not in constant


def test_channel_resolution_falls_back_through_candidates():
    from data_pipeline.volve.prepare import resolve_channels

    resolved = resolve_channels(
        {"WOB", "HKLD_AVG"},
        {"weight_on_bit_kn": ["WOB_AVG", "WOB"], "mse_bar": ["MSE"]},
    )
    assert resolved == {"weight_on_bit_kn": "WOB"}  # mse absent -> simply not resolved


def test_implausible_values_are_flagged_not_deleted_silently():
    from data_pipeline.volve.prepare import flag_quality

    frame = pd.DataFrame({"surface_rpm": [10.0, 5000.0, 20.0]})
    result, report = flag_quality(frame.copy(), get_config())
    assert report["implausible_values_flagged"]["surface_rpm"] == 1
    # The row survives; only the impossible reading is nulled, and it is marked.
    assert len(result) == 3
    assert bool(result.loc[1, "surface_rpm_implausible"]) is True


# ------------------------------------------------------------------ metric honesty


def test_majority_baseline_is_reported_for_context():
    from ml.common.metrics import majority_class_baseline

    y = np.array([1] * 90 + [2] * 10)
    baseline = majority_class_baseline(y)
    assert baseline["accuracy"] == pytest.approx(0.9)
    # Macro F1 exposes what accuracy hides.
    assert baseline["macro_f1"] < 0.5


def test_classification_metrics_marks_low_support_classes():
    from ml.common.metrics import classification_metrics

    y_true = np.array([1] * 100 + [2] * 3)
    y_pred = np.array([1] * 100 + [1] * 3)
    metrics = classification_metrics(
        y_true, y_pred, class_names={1: "Shale", 2: "Basement"}, min_class_support=50
    )
    assert "Basement" in metrics["classes_below_min_support"]["classes"]
    assert metrics["macro_f1"] < metrics["accuracy"]


def test_unevaluable_risk_is_flagged_not_scored_as_zero():
    """A score of 0.0 classified as INFO reads as "assessed, and it is fine".

    When no component can be computed — no telemetry, no historical evidence in range, no
    measurements — the honest answer is that risk is not evaluable at this depth. This is
    the same rule the rest of the system follows: never a zero where the value is unknown.
    """
    from backend.app.core.database import session_scope
    from backend.app.models import Well
    from backend.app.services.risk import assess

    session = session_scope()
    try:
        well = session.query(Well).filter(Well.has_logs.is_(True)).first()
        if well is None:
            pytest.skip("knowledge base is empty")

        # A depth far below anything recorded: nothing can contribute.
        empty = assess(session, well=well, depth_m=9000, anomaly_score=None)
        assert empty.components == []
        assert empty.evaluated is False
        assert any("could not be evaluated" in n for n in empty.notes)

        # A depth with real evidence still evaluates normally.
        real = assess(session, well=well, depth_m=2500, anomaly_score=None)
        if real.components:
            assert real.evaluated is True
    finally:
        session.close()


def test_uncategorised_remarks_are_not_historical_risk_evidence():
    """A routine operations log must not become a risk signal.

    The Volve WITSML message stream is an operations log: "Toolbox Talk Prior to Rig Up
    Tubing Equipment" is a real record, and it is not evidence that anything went wrong.
    Counting uncategorised remarks produced a MEDIUM indicator "supported by 65
    historical records" of exactly that kind — a fabricated risk built out of real rows.
    The query must filter on category, and this pins that it still does.
    """
    from backend.app.services import risk as risk_service

    source = inspect.getsource(risk_service.gather_historical_evidence)
    assert "DrillingEvent.category.isnot(None)" in source, (
        "historical evidence no longer filters to categorised events, so routine "
        "operational remarks can raise a risk indicator again"
    )


def test_evidence_at_the_bit_is_the_strongest_not_the_weakest():
    """An event at exactly the bit's depth must score highest, not lowest.

    The proximity term was written ``abs(e.distance_from_bit_m or lookahead)``. An offset
    of 0.0 is falsy, so the strongest possible evidence — a historical event recorded at
    precisely the depth the bit has reached — was substituted with the full look-ahead
    window and scored as the weakest. Measured on real data: Volve F-4 reaches 2368.4 m,
    16/7-5 records a fishing operation at 2368 m, and the component came back 0.000.
    """
    from backend.app.services.risk import HistoricalEvidence, nearest_offset_m

    def evidence(offset):
        return HistoricalEvidence(
            well_name="16/7-5",
            similarity_score=0.77,
            event_id=1,
            event_type="fishing",
            depth_start_m=2368.0,
            depth_end_m=None,
            distance_from_bit_m=offset,
            description="",
            formation_name=None,
            depth_source=None,
            source_dataset=None,
            source_reference=None,
        )

    lookahead = 150.0
    assert nearest_offset_m([evidence(0.0)], lookahead) == 0.0
    assert nearest_offset_m([evidence(-40.0), evidence(90.0)], lookahead) == 40.0
    # No recorded offset is the weakest position, not the strongest.
    assert nearest_offset_m([evidence(None)], lookahead) == lookahead
    assert nearest_offset_m([], lookahead) == lookahead

    # And the proximity term the engine derives from it must rank them accordingly.
    def proximity(offset):
        return max(0.0, 1.0 - nearest_offset_m([evidence(offset)], lookahead) / lookahead)

    assert proximity(0.0) == 1.0
    assert proximity(0.0) > proximity(75.0) > proximity(149.0)
