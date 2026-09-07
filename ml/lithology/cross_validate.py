"""Grouped cross-validation for lithology model selection.

Why this exists
---------------
Training registered two candidates whose unseen-well estimates disagree: RandomForest
wins on the 15 validation wells, XGBoost wins on the 10-well FORCE leaderboard holdout.
Selecting the holdout winner would be selecting on the test set, so the disagreement was
recorded rather than resolved. It is a symptom of well-to-well variance — with 15 wells,
the difference between two models is smaller than the difference between two draws of
wells — and no amount of staring at a single split will settle it.

Cross-validation gives every selection well a turn as unseen data. Because the folds are
*paired* — both candidates are fitted and scored on exactly the same fold — the per-fold
difference cancels the fold-to-fold variance that swamps the single-split comparison.

Discipline
----------
* Folds are grouped **by well**. A well is never on both sides of a fold, matching the
  well-level discipline of the top-level split. Splitting by row would leak: adjacent
  depth samples in one well are nearly identical.
* Only ``lithology_model.cross_validation.use_splits`` wells participate — the train and
  validation wells, both already selection-legal. The **test wells and the FORCE
  leaderboard holdout take no part** and stay untouched by selection.
* Boosting needs an early-stopping set. It is carved out of each fold's *own training
  wells*, so the fold's evaluation wells are never seen during fitting.
* The comparison is reported with its spread and its per-fold record. Five folds is a
  small sample and the paired test is reported with that caveat attached, not as proof.

Usage:
    python -m ml.lithology.cross_validate
    python -m ml.lithology.cross_validate --models random_forest --splits 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline

from data_pipeline.force.profile import LITHOLOGY_CODE_NAMES
from ml.common.metrics import classification_metrics, majority_class_baseline
from ml.lithology.train import MODEL_NAME, build_estimator
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.lithology.cross_validate")


def _fold_early_stopping_wells(
    wells: np.ndarray, fraction: float, seed: int
) -> set[str]:
    """Choose whole wells from a fold's training set to early-stop boosting on.

    Whole wells, not random rows: an early-stopping set sharing wells with the fitting
    data would stop on memorised wells and run far too long.
    """
    count = max(1, int(round(len(wells) * fraction)))
    generator = np.random.default_rng(seed)
    return set(generator.choice(np.asarray(sorted(wells)), size=count, replace=False))


def evaluate_fold(
    kind: str,
    config,
    train_frame: pd.DataFrame,
    eval_frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    target_col: str,
    well_col: str,
    min_class_support: int,
    seed: int,
) -> dict:
    """Fit one candidate on a fold's training wells and score it on the held-out wells."""
    # The early-stopping wells are removed from the fitting data first, because the class
    # index has to describe the rows actually fitted. Deriving it from the full fold and
    # then fitting on a subset that happens to lack a rare class leaves a gap in the
    # label encoding, which XGBoost rejects outright.
    uses_early_stopping = bool(
        dict(config.section(f"lithology_model.{kind}")).get("early_stopping_rounds")
    )
    fitting = train_frame
    early_stopping_wells: set[str] = set()
    if uses_early_stopping:
        early_stopping_wells = _fold_early_stopping_wells(
            train_frame[well_col].unique(),
            float(config.get("lithology_model.cross_validation.early_stopping_well_fraction")),
            seed,
        )
        fitting = train_frame[~train_frame[well_col].isin(early_stopping_wells)]

    classes = sorted(fitting[target_col].astype(int).unique())
    class_index = {c: i for i, c in enumerate(classes)}
    inverse = {i: c for c, i in class_index.items()}

    dropped_classes = sorted(set(train_frame[target_col].astype(int).unique()) - set(classes))
    if dropped_classes:
        log.warning(
            "fold_classes_absent_from_fit",
            model=kind,
            classes=[LITHOLOGY_CODE_NAMES.get(c, str(c)) for c in dropped_classes],
            reason="present only in the wells reserved for early stopping",
        )

    imputer, estimator, params = build_estimator(kind, config, len(classes))

    x_fit = fitting[feature_columns]
    y_fit = fitting[target_col].astype(int).map(class_index).to_numpy()

    started = time.perf_counter()
    x_fit_imputed = imputer.fit_transform(x_fit)

    sample_weight = None
    if params.get("class_balancing") == "balanced":
        from sklearn.utils.class_weight import compute_sample_weight

        sample_weight = compute_sample_weight("balanced", y_fit)

    if early_stopping_wells:
        stopping = train_frame[train_frame[well_col].isin(early_stopping_wells)]
        labels = stopping[target_col].astype(int)
        encodable = labels.isin(class_index)
        estimator.fit(
            x_fit_imputed,
            y_fit,
            sample_weight=sample_weight,
            eval_set=[
                (
                    imputer.transform(stopping.loc[encodable, feature_columns]),
                    labels[encodable].map(class_index).to_numpy(),
                )
            ],
            verbose=False,
        )
    else:
        estimator.fit(x_fit_imputed, y_fit, sample_weight=sample_weight)

    pipeline = Pipeline([("impute", imputer), ("model", estimator)])
    elapsed = time.perf_counter() - started

    y_true = eval_frame[target_col].astype(int).to_numpy()
    encoded = pipeline.predict(eval_frame[feature_columns])
    y_pred = np.array([inverse[int(v)] for v in encoded])

    metrics = classification_metrics(
        y_true,
        y_pred,
        class_names=LITHOLOGY_CODE_NAMES,
        min_class_support=min_class_support,
        split_name="cv_fold",
    )
    return {
        "model": kind,
        "macro_f1": metrics["macro_f1"],
        "balanced_accuracy": metrics["balanced_accuracy"],
        "accuracy": metrics["accuracy"],
        "cohen_kappa": metrics.get("cohen_kappa"),
        "majority_class_baseline": majority_class_baseline(y_true),
        "fit_seconds": round(elapsed, 1),
        "best_iteration": getattr(estimator, "best_iteration", None),
        "train_wells": int(fitting[well_col].nunique()),
        "early_stopping_wells": sorted(early_stopping_wells) or None,
        "eval_wells": int(eval_frame[well_col].nunique()),
        "eval_rows": int(len(eval_frame)),
        "classes_fitted": len(classes),
        "classes_absent_from_fit": [
            LITHOLOGY_CODE_NAMES.get(c, str(c)) for c in dropped_classes
        ] or None,
    }


def compare(per_fold: dict[str, list[dict]]) -> dict:
    """Compare candidates on their paired per-fold scores."""
    summary = {}
    for kind, folds in per_fold.items():
        scores = np.asarray([f["macro_f1"] for f in folds], dtype="float64")
        summary[kind] = {
            "folds": len(scores),
            "macro_f1_mean": round(float(scores.mean()), 4),
            "macro_f1_std": round(float(scores.std(ddof=1)), 4) if len(scores) > 1 else None,
            "macro_f1_min": round(float(scores.min()), 4),
            "macro_f1_max": round(float(scores.max()), 4),
            "macro_f1_per_fold": [round(float(s), 4) for s in scores],
            "total_fit_seconds": round(sum(f["fit_seconds"] for f in folds), 1),
        }

    result: dict = {"per_model": summary}
    kinds = sorted(summary, key=lambda k: -summary[k]["macro_f1_mean"])
    result["ranking"] = kinds
    if len(kinds) < 2:
        return result

    best, runner_up = kinds[0], kinds[1]
    a = np.asarray([f["macro_f1"] for f in per_fold[best]], dtype="float64")
    b = np.asarray([f["macro_f1"] for f in per_fold[runner_up]], dtype="float64")
    differences = a - b

    comparison = {
        "models": [best, runner_up],
        "mean_difference": round(float(differences.mean()), 4),
        "difference_per_fold": [round(float(d), 4) for d in differences],
        "folds_won": int((differences > 0).sum()),
        "folds_total": len(differences),
    }
    # Paired t-test over folds. Reported with its caveat: CV folds share training data,
    # so the test is anti-conservative, and five folds is a small sample. It is a
    # summary of the spread, not proof of a difference.
    if len(differences) > 1 and differences.std(ddof=1) > 0:
        from scipy import stats

        statistic, p_value = stats.ttest_rel(a, b)
        comparison["paired_t_statistic"] = round(float(statistic), 4)
        comparison["paired_p_value"] = round(float(p_value), 4)
    comparison["caveat"] = (
        "Cross-validation folds share training data, so a paired t-test over folds is "
        "anti-conservative; with this few folds it indicates the size of the gap "
        "relative to the spread, and is not evidence of a significant difference."
    )
    result["comparison"] = comparison
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grouped (by well) cross-validation for lithology model selection"
    )
    parser.add_argument("--models", action="append", default=None,
                        help="restrict to specific candidates (repeatable)")
    parser.add_argument("--splits", type=int, default=None,
                        help="override lithology_model.cross_validation.splits")
    parser.add_argument("--verdict-only", action="store_true",
                        help="recompute the verdict from an existing "
                             "cross_validation.json without refitting anything")
    parser.add_argument("--max-wells", type=int, default=None,
                        help="use only the first N selection wells (fast smoke check; "
                             "results are written with smoke_run set and are not "
                             "used for selection)")
    args = parser.parse_args()

    config = get_config()

    if args.verdict_only:
        artifacts_dir = Path(config.get("paths.models")) / MODEL_NAME
        report_path = artifacts_dir / "cross_validation.json"
        if not report_path.exists():
            log.error("no_cross_validation_report", path=str(report_path),
                      hint="run python -m ml.lithology.cross_validate first")
            return 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("smoke_run"):
            log.error("smoke_report", path=str(report_path),
                      note="this report came from --max-wells and must not drive selection")
            return 1
        _record_verdict(artifacts_dir, report)
        return 0

    processed = config.path("paths.data_processed") / "force"
    features_path = processed / "features.parquet"
    if not features_path.exists():
        log.error("features_missing", path=str(features_path),
                  hint="run python -m data_pipeline.force.prepare")
        return 1

    spec = json.loads((processed / "feature_spec.json").read_text(encoding="utf-8"))
    feature_columns: list[str] = spec["feature_columns"]
    well_col = spec["well_column"]
    target_col = spec["target_column"]
    min_class_support = int(config.get("lithology_model.min_class_support"))

    n_splits = args.splits or int(config.get("lithology_model.cross_validation.splits"))
    seed = int(config.get("lithology_model.cross_validation.random_state"))
    use_splits = list(config.get("lithology_model.cross_validation.use_splits"))
    if "test" in use_splits:
        raise ValueError(
            "lithology_model.cross_validation.use_splits contains 'test'. Selecting on "
            "the test wells would invalidate every unseen-well number this project "
            "reports."
        )

    frame = pd.read_parquet(features_path)
    selection = frame[frame["split"].isin(use_splits)]
    if selection.empty:
        log.error("no_selection_rows", use_splits=use_splits)
        return 1

    if args.max_wells:
        keep = sorted(selection[well_col].unique())[: args.max_wells]
        selection = selection[selection[well_col].isin(keep)]
        log.warning("smoke_mode", wells=len(keep),
                    note="results are not representative and must not drive selection")

    wells = selection[well_col].to_numpy()
    unique_wells = sorted(set(wells))
    n_splits = min(n_splits, len(unique_wells))

    log.info(
        "cross_validation_start",
        wells=len(unique_wells),
        rows=len(selection),
        splits=n_splits,
        use_splits=use_splits,
        excluded="test wells and the FORCE leaderboard holdout",
    )

    candidates = args.models or list(config.get("lithology_model.candidates"))
    splitter = GroupKFold(n_splits=n_splits)
    folds = list(splitter.split(selection, groups=wells))

    per_fold: dict[str, list[dict]] = {kind: [] for kind in candidates}
    for fold_number, (train_index, eval_index) in enumerate(folds, start=1):
        train_frame = selection.iloc[train_index]
        eval_frame = selection.iloc[eval_index]
        overlap = set(train_frame[well_col]) & set(eval_frame[well_col])
        if overlap:  # a grouping bug would silently inflate every score
            raise AssertionError(f"fold {fold_number} shares wells: {sorted(overlap)[:5]}")

        for kind in candidates:
            result = evaluate_fold(
                kind, config, train_frame, eval_frame,
                feature_columns=feature_columns,
                target_col=target_col,
                well_col=well_col,
                min_class_support=min_class_support,
                seed=seed + fold_number,
            )
            result["fold"] = fold_number
            per_fold[kind].append(result)
            log.info("fold_complete", fold=fold_number, model=kind,
                     macro_f1=result["macro_f1"], seconds=result["fit_seconds"],
                     eval_wells=result["eval_wells"])

    report = {
        "strategy": "GroupKFold by well",
        "splits": n_splits,
        "wells": len(unique_wells),
        "rows": int(len(selection)),
        "use_splits": use_splits,
        "selection_metric": "macro F1",
        "excluded_from_selection": [
            "test wells (15)",
            "FORCE leaderboard holdout wells (10)",
        ],
        "smoke_run": bool(args.max_wells),
        **compare(per_fold),
        "folds": {kind: results for kind, results in per_fold.items()},
    }

    artifacts_dir = ensure_dir(Path(config.get("paths.models")) / MODEL_NAME)
    path = artifacts_dir / "cross_validation.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    log.info("cross_validation_complete", report=str(path), ranking=report["ranking"],
             **{k: v["macro_f1_mean"] for k, v in report["per_model"].items()})

    if not args.max_wells:
        _record_verdict(artifacts_dir, report)
    return 0


def _record_verdict(artifacts_dir: Path, report: dict) -> None:
    """Write the cross-validated verdict into ``selected.json``.

    The verdict is attached, not silently substituted: ``selected_model`` still records
    what the pre-committed single-split rule chose, and the cross-validated result sits
    beside it saying whether that choice survives a stronger test. Anyone reading the
    file can see both, and which one the served model reflects.
    """
    path = artifacts_dir / "selected.json"
    if not path.exists():
        log.warning("selected_json_missing", path=str(path),
                    note="cross-validation report written, but no selection file to annotate")
        return

    selection = json.loads(path.read_text(encoding="utf-8"))
    comparison = report.get("comparison")
    winner = report["ranking"][0]

    verdict = {
        "method": f"GroupKFold by well, {report['splits']} folds over "
                  f"{report['wells']} selection wells",
        "metric": "macro F1",
        "winner": winner,
        "mean_macro_f1": {k: v["macro_f1_mean"] for k, v in report["per_model"].items()},
        "std_macro_f1": {k: v["macro_f1_std"] for k, v in report["per_model"].items()},
        "report": "cross_validation.json",
    }
    separable = True
    if comparison:
        verdict["folds_won"] = f"{comparison['folds_won']} of {comparison['folds_total']}"
        verdict["mean_difference"] = comparison["mean_difference"]
        verdict["paired_p_value"] = comparison.get("paired_p_value")
        verdict["caveat"] = comparison["caveat"]

        # The question that decides what this run actually established: is the gap
        # between the models larger than the scatter between folds? When it is not, the
        # honest finding is that the data cannot tell them apart — reporting a "winner"
        # would dress up noise as a result.
        spreads = [
            v["macro_f1_std"] for v in report["per_model"].values()
            if v.get("macro_f1_std") is not None
        ]
        widest_spread = max(spreads) if spreads else None
        gap = abs(comparison["mean_difference"])
        if widest_spread:
            separable = gap > widest_spread
            verdict["difference_vs_fold_spread"] = {
                "mean_difference": round(gap, 4),
                "widest_fold_spread": round(widest_spread, 4),
                "spread_is_this_many_times_the_difference": round(widest_spread / gap, 1)
                if gap
                else None,
                "models_separable_on_this_evidence": separable,
            }

    previous = selection.get("selected_model")

    if not separable:
        verdict["conclusion"] = (
            f"Cross-validation cannot separate these models. {winner!r} has the higher "
            f"mean macro F1, but the difference ({comparison['mean_difference']}) is "
            f"smaller than the spread between folds, and the paired test over folds does "
            f"not distinguish them "
            f"(p = {comparison.get('paired_p_value')}). The original disagreement between "
            "the validation wells and the external holdout is therefore explained: it was "
            "never a contest between the models, it was the variation between draws of "
            f"wells. The served model stays {previous!r}, and no claim that either model "
            "is better is supported by this evidence."
        )
    elif previous == winner:
        verdict["conclusion"] = (
            f"Cross-validation agrees with the single-split selection: {winner!r} is "
            "still the choice, now on evidence from every selection well rather than "
            "from one 15-well draw."
        )
    else:
        verdict["conclusion"] = (
            f"Cross-validation disagrees with the single-split selection: {winner!r} "
            f"has the higher mean macro F1 across folds, while the pre-committed rule "
            f"selected {previous!r} on the validation wells alone. The served model is "
            f"still {previous!r}; changing it is a deliberate decision to re-open a "
            "pre-committed selection rule, and is recorded here rather than applied "
            "silently."
        )

    selection["cross_validation"] = verdict
    path.write_text(json.dumps(selection, indent=2), encoding="utf-8")
    log.info("selection_annotated", path=str(path), winner=winner,
             single_split_choice=previous, agrees=previous == winner)


if __name__ == "__main__":
    raise SystemExit(main())
