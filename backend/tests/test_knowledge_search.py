"""Tests for document embedding, semantic search and grouped cross-validation.

These target the ways the new stages could quietly produce something untrue:

* a search index built from vectors of the wrong dimensionality, or from a different
  model than the one that encoded the corpus;
* a search that reports a whole-corpus result when only part of the corpus is indexed;
* cross-validation folds that share a well between fitting and evaluation, which would
  inflate every score;
* a selection procedure that reaches the test wells.

None of these tests require a trained model, a GPU, or a network round trip.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pytest

from nwis_common import get_config


# ------------------------------------------------------- embedding configuration


def test_document_vectors_are_a_different_space_from_segment_vectors():
    """Report passages and petrophysical segments must not share a dimensionality.

    They are produced by unrelated models. If the two constants ever collide, a cosine
    comparison between them becomes silently possible instead of impossible.
    """
    from backend.app.models import EMBEDDING_DIMENSIONS, TEXT_EMBEDDING_DIMENSIONS

    assert TEXT_EMBEDDING_DIMENSIONS != EMBEDDING_DIMENSIONS


def test_document_embedding_dimension_matches_the_column():
    from backend.app.models import TEXT_EMBEDDING_DIMENSIONS

    configured = int(get_config().get("documents.embedding.dimensions"))
    assert configured == TEXT_EMBEDDING_DIMENSIONS


def test_chunk_column_rejects_a_vector_of_the_wrong_width():
    """The storage type is the last line of defence against a mismatched encoder."""
    from backend.app.models import TEXT_EMBEDDING_DIMENSIONS
    from backend.app.models.types import Vector

    column = Vector(TEXT_EMBEDDING_DIMENSIONS)

    class _SQLite:
        name = "sqlite"

    with pytest.raises(ValueError):
        column.process_bind_param([0.0] * (TEXT_EMBEDDING_DIMENSIONS - 1), _SQLite())

    stored = column.process_bind_param([0.0] * TEXT_EMBEDDING_DIMENSIONS, _SQLite())
    assert column.process_result_value(stored, _SQLite()) == [0.0] * TEXT_EMBEDDING_DIMENSIONS


def test_encoder_load_refuses_a_config_schema_mismatch(monkeypatch):
    """A configured width that disagrees with the column must fail before encoding."""
    from data_pipeline.documents import embed

    config = get_config()
    original = config.get

    def fake_get(key, *args, **kwargs):
        if key == "documents.embedding.dimensions":
            return 7
        return original(key, *args, **kwargs)

    monkeypatch.setattr(config, "get", fake_get)
    with pytest.raises(ValueError, match="dimensions"):
        embed.load_encoder(config)


# ------------------------------------------------------------------ search behaviour


class _FakeSession:
    """Minimal stand-in: search must not need a live database to be tested."""

    def __init__(self, counts=(0, 0)):
        self._total, self._embedded = counts

    def execute(self, statement):
        class _Result:
            @staticmethod
            def all():
                return []

        return _Result()

    def scalar(self, statement):
        # corpus_status asks for the total first, then the embedded count.
        value = self._total if self._total_pending() else self._embedded
        return value

    def _total_pending(self):
        if not hasattr(self, "_asked"):
            self._asked = True
            return True
        return False


def test_search_requires_a_query():
    from backend.app.services import document_search

    with pytest.raises(document_search.SearchUnavailable):
        document_search.search(_FakeSession(), "   ")


def test_search_reports_an_unbuilt_index_rather_than_no_matches():
    """An empty index must not look like 'nothing in the reports matches'."""
    from backend.app.services import document_search

    with pytest.raises(document_search.SearchUnavailable) as excinfo:
        document_search.search(_FakeSession(counts=(75, 0)), "lost circulation")
    assert "embed" in str(excinfo.value).lower()


def test_similarity_threshold_and_result_cap_come_from_config():
    embedding = get_config().section("documents.embedding")
    assert -1.0 <= float(embedding["min_similarity"]) <= 1.0
    assert int(embedding["max_results"]) > 0


def test_normalised_vectors_make_the_dot_product_the_cosine():
    """The search ranks by dot product, which is only the cosine on unit vectors."""
    generator = np.random.default_rng(0)
    raw = generator.normal(size=(5, 16))
    unit = raw / np.linalg.norm(raw, axis=1, keepdims=True)
    query = unit[0]

    dot = unit @ query
    cosine = np.array(
        [float(np.dot(v, query) / (np.linalg.norm(v) * np.linalg.norm(query))) for v in unit]
    )
    assert np.allclose(dot, cosine)
    assert dot[0] == pytest.approx(1.0)


# ----------------------------------------------------- cross-validation discipline


def test_cross_validation_never_uses_the_test_wells():
    """The configured fold source must exclude the split every headline number rests on."""
    use_splits = list(get_config().get("lithology_model.cross_validation.use_splits"))
    assert "test" not in use_splits
    assert use_splits, "cross-validation needs at least one selection split"


def test_cross_validation_rejects_a_config_that_reaches_the_test_wells(monkeypatch):
    """Editing the config to include 'test' must fail loudly, not train quietly."""
    import pandas as pd

    from ml.lithology import cross_validate

    config = get_config()
    original = config.get

    def fake_get(key, *args, **kwargs):
        if key == "lithology_model.cross_validation.use_splits":
            return ["train", "test"]
        return original(key, *args, **kwargs)

    monkeypatch.setattr(config, "get", fake_get)
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("read before check"))
    monkeypatch.setattr(sys, "argv", ["cross_validate"])

    with pytest.raises(ValueError, match="test"):
        cross_validate.main()


def test_group_k_fold_never_shares_a_well_between_sides():
    """The guarantee the whole procedure rests on: no well on both sides of a fold."""
    from sklearn.model_selection import GroupKFold

    wells = np.repeat([f"well-{i}" for i in range(12)], 20)
    splitter = GroupKFold(n_splits=4)
    for train_index, eval_index in splitter.split(np.zeros(len(wells)), groups=wells):
        assert not set(wells[train_index]) & set(wells[eval_index])


def test_early_stopping_wells_are_whole_wells_from_the_fold(monkeypatch):
    from ml.lithology.cross_validate import _fold_early_stopping_wells

    wells = np.array([f"well-{i}" for i in range(20)])
    chosen = _fold_early_stopping_wells(wells, 0.15, seed=1)
    assert chosen <= set(wells)
    assert len(chosen) == 3
    # Never empty, even for a tiny fold: boosting would otherwise have no stopping set.
    assert len(_fold_early_stopping_wells(np.array(["only-well"]), 0.15, seed=1)) == 1


def test_comparison_reports_spread_and_a_caveat_not_just_a_winner():
    """A mean without its spread would overstate what five folds can establish."""
    from ml.lithology.cross_validate import compare

    per_fold = {
        "random_forest": [{"macro_f1": f, "fit_seconds": 1.0} for f in (0.30, 0.34, 0.28, 0.36, 0.32)],
        "xgboost": [{"macro_f1": f, "fit_seconds": 1.0} for f in (0.29, 0.35, 0.26, 0.33, 0.31)],
    }
    result = compare(per_fold)

    assert result["ranking"][0] == "random_forest"
    assert result["per_model"]["random_forest"]["macro_f1_std"] is not None
    assert len(result["per_model"]["xgboost"]["macro_f1_per_fold"]) == 5

    comparison = result["comparison"]
    # The mean winner does not win every fold (it loses fold 2, 0.34 to 0.35), which is
    # exactly the situation the per-fold record exists to make visible.
    assert comparison["folds_won"] == 4
    assert comparison["folds_total"] == 5
    assert comparison["difference_per_fold"][1] < 0
    assert comparison["mean_difference"] > 0
    assert "anti-conservative" in comparison["caveat"]


def test_comparison_of_a_single_candidate_makes_no_claim():
    from ml.lithology.cross_validate import compare

    result = compare({"random_forest": [{"macro_f1": 0.3, "fit_seconds": 1.0}]})
    assert "comparison" not in result
    assert result["ranking"] == ["random_forest"]


# ------------------------------------------------- ingesting deeper pages of a report


def test_chunking_covers_the_page_without_losing_text():
    """Overlapping windows must tile the page, not drop the tail."""
    from data_pipeline.documents.ingest import chunk_text

    text = "".join(f"sentence {i}. " for i in range(400))
    chunks = chunk_text(text, size=1200, overlap=150)

    assert len(chunks) > 1
    assert text.startswith(chunks[0])
    assert text.endswith(chunks[-1])
    # Consecutive windows overlap, so a phrase on a boundary is retrievable from one
    # of them rather than being split beyond recognition in both.
    assert chunks[0][-150:] in chunks[1]


def test_chunking_discards_a_blank_page_rather_than_storing_an_empty_passage():
    from data_pipeline.documents.ingest import chunk_text

    assert chunk_text("", 1200, 150) == []
    assert chunk_text("   \n  \n", 1200, 150) == []


def test_page_ranges_are_cached_separately_so_a_deeper_pass_is_a_new_read():
    """The OCR cache key must include the page range, or a deeper pass would be served
    the shallow pass's text."""
    from data_pipeline.documents.ocr import _cache_path

    shallow = _cache_path("report.pdf", 30, 0)
    deep = _cache_path("report.pdf", 167, 30)
    assert shallow != deep
    assert "p0_30" in shallow.name
    assert "p30_167" in deep.name


def test_ingest_exposes_a_start_page_argument():
    """The OCR layer always supported start_page; the CLI must too, or the drilling
    narrative deeper in a report is unreachable."""
    import inspect

    from data_pipeline.documents.ingest import ingest_document

    signature = inspect.signature(ingest_document)
    assert "start_page" in signature.parameters
    assert signature.parameters["start_page"].default == 0
