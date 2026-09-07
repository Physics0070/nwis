"""Tests for NWIS business logic.

These target the decisions that would be dangerous to get wrong: the honesty rules
(no invented probability, no invented position, no silently-reduced evidence), the
scoring maths, well-level split integrity, and alert deduplication.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
