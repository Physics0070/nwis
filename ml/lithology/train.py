"""Train the FORCE 2020 lithology classifier.

Evaluation discipline:

* Model selection uses the validation *wells* only.
* Two independent unseen-well estimates are reported:
    - internal test wells (15 wells held out of the 98 training wells)
    - the official FORCE leaderboard set (10 wells that appear nowhere in training)
* Headline metrics are macro F1 and balanced accuracy, next to a majority-class
  baseline, because accuracy on this class distribution is not informative on its own.

No metric in this file is written by hand. Whatever the model scores is what gets
registered and served to the UI.

Usage:
    python -m ml.lithology.train
    python -m ml.lithology.train --models random_forest
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from data_pipeline.force.prepare import build_features, select_feature_curves
from data_pipeline.force.profile import LITHOLOGY_CODE_NAMES
from ml.common.metrics import (
    classification_metrics,
    force_penalty_score,
    majority_class_baseline,
)
from ml.common.registry import ModelRecord, ModelRegistry, new_version
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.lithology.train")

MODEL_NAME = "lithology"


def negative_macro_f1(y_true, y_pred):
    """Early-stopping metric for boosting.

    Stopping on mlogloss halts as soon as the majority class stops improving, long
    before the rare lithologies are learned. Macro F1 is the metric actually reported,
    so it is the one to stop on. Negated because XGBoost minimises a custom metric.

    Defined at module level so the fitted estimator stays picklable.
    """
    from sklearn.metrics import f1_score

    labels = y_pred.argmax(axis=1) if getattr(y_pred, "ndim", 1) > 1 else y_pred
    return -f1_score(y_true, labels, average="macro", zero_division=0)


def resolve_device(preference: str) -> str:
    """Pick the XGBoost compute device.

    "auto" tries a tiny GPU fit and falls back to CPU if anything about the CUDA path
    is unavailable, so the same configuration runs on a workstation with a GPU and on
    a CI box without one. The resolved device is recorded in the model registry.
    """
    preference = (preference or "auto").lower()
    if preference != "auto":
        return preference
    try:
        import numpy as _np
        from xgboost import XGBClassifier as _XGB

        _XGB(n_estimators=1, device="cuda", tree_method="hist", verbosity=0).fit(
            _np.zeros((8, 2), dtype="float32"), _np.array([0, 1] * 4)
        )
        log.info("gpu_detected", device="cuda")
        return "cuda"
    except Exception as exc:
        log.info("gpu_unavailable", device="cpu", reason=type(exc).__name__)
        return "cpu"


# ----------------------------------------------------------------------- estimators


def build_estimator(kind: str, config, n_classes: int):
    """Construct the imputer and the classifier.

    Median imputation with missingness indicators: log curves are missing for physical
    reasons (a tool was not run over an interval), so *that a curve is absent* is itself
    informative and is passed to the model rather than being silently filled away.

    The imputer is returned separately from the model so that boosting can be given a
    genuine validation set for early stopping. They are recombined into a Pipeline
    before the artifact is saved, so inference stays a single call.
    """
    params = dict(config.section(f"lithology_model.{kind}"))
    imputer = SimpleImputer(strategy="median", add_indicator=True)

    if kind == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        estimator = RandomForestClassifier(**params)
    elif kind == "xgboost":
        from xgboost import XGBClassifier

        early_stopping = params.pop("early_stopping_rounds", None)
        balancing = params.pop("class_balancing", "none")
        device = resolve_device(config.get("lithology_model.device", "auto"))

        estimator = XGBClassifier(
            **params,
            objective="multi:softprob",
            num_class=n_classes,
            tree_method="hist",
            device=device,
            n_jobs=-1,
            eval_metric=negative_macro_f1,
            early_stopping_rounds=early_stopping,
        )
        params["early_stopping_rounds"] = early_stopping
        params["device"] = device
        params["class_balancing"] = balancing
        params["early_stopping_metric"] = "macro_f1"
    else:
        raise ValueError(f"Unsupported lithology model: {kind!r}")

    return imputer, estimator, params


# ------------------------------------------------------------------ external holdout


def load_leaderboard_holdout(config, feature_columns: list[str]) -> pd.DataFrame | None:
    """Load the official FORCE leaderboard test wells and feature them identically."""
    raw_dir = config.path("datasets.force.raw_dir")
    separator = config.get("datasets.force.csv_separator")
    well_col = config.get("force_pipeline.well_column")
    depth_col = config.get("force_pipeline.depth_column")
    target_col = config.get("force_pipeline.target_column")

    features_path = raw_dir / "leaderboard_test_features.csv"
    target_path = raw_dir / "leaderboard_test_target.csv"
    if not (features_path.exists() and target_path.exists()):
        log.warning("leaderboard_holdout_missing", features=str(features_path))
        return None

    frame = pd.read_csv(features_path, sep=separator, low_memory=False)
    targets = pd.read_csv(target_path, sep=separator, low_memory=False)
    frame = frame.merge(targets, on=[well_col, depth_col], how="inner")

    # Same curve selection and feature engineering as training, so the model sees
    # exactly the shape of input it was fitted on.
    base_curves, _ = select_feature_curves(frame, config)
    frame, _, _ = build_features(frame, base_curves, config)

    missing = [c for c in feature_columns if c not in frame.columns]
    for column in missing:
        frame[column] = np.nan
    if missing:
        log.warning(
            "leaderboard_missing_features",
            count=len(missing),
            note="curves absent from the holdout wells; imputed as missing",
            features=missing[:10],
        )

    frame = frame.dropna(subset=[target_col])
    log.info(
        "leaderboard_holdout_loaded",
        rows=len(frame),
        wells=int(frame[well_col].nunique()),
    )
    return frame


# ------------------------------------------------------------------------- training


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the FORCE lithology model")
    parser.add_argument("--models", action="append", default=None,
                        help="restrict to specific candidates (repeatable)")
    parser.add_argument("--select-only", action="store_true",
                        help="recompute model selection from the registry without training")
    parser.add_argument("--smoke", action="store_true",
                        help="fast end-to-end validation on a few wells and few trees; "
                             "results are NOT registered as a usable model")
    parser.add_argument("--smoke-wells", type=int, default=6,
                        help="wells per split when --smoke is set")
    args = parser.parse_args()

    config = get_config()
    processed = config.path("paths.data_processed") / "force"
    features_path = processed / "features.parquet"
    spec_path = processed / "feature_spec.json"

    if not features_path.exists():
        log.error("features_missing", path=str(features_path),
                  hint="run python -m data_pipeline.force.prepare")
        return 1

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    feature_columns: list[str] = spec["feature_columns"]
    well_col = spec["well_column"]
    target_col = spec["target_column"]
    min_support = int(config.get("lithology_model.min_class_support"))

    log.info("training_start", features=len(feature_columns))
    frame = pd.read_parquet(features_path)

    splits = {
        name: frame[frame["split"] == name]
        for name in ("train", "validation", "test")
    }

    if args.smoke:
        # Validate the whole path cheaply before committing to a long fit. Whole wells
        # are kept, so the well-level discipline still holds in the smoke run.
        log.warning("smoke_mode", wells_per_split=args.smoke_wells,
                    note="metrics from this run are not representative")
        splits = {
            name: subset[
                subset[well_col].isin(
                    sorted(subset[well_col].unique())[: args.smoke_wells]
                )
            ]
            for name, subset in splits.items()
        }
    for name, subset in splits.items():
        log.info(
            "split_loaded",
            split=name,
            rows=len(subset),
            wells=int(subset[well_col].nunique()),
            classes=int(subset[target_col].nunique()),
        )

    x_train = splits["train"][feature_columns]
    y_train = splits["train"][target_col].astype(int)
    classes = sorted(y_train.unique())
    class_index = {c: i for i, c in enumerate(classes)}
    inverse = {i: c for c, i in class_index.items()}

    penalty_path = config.path("datasets.force.raw_dir") / "penalty_matrix.npy"
    penalty_matrix = np.load(penalty_path) if penalty_path.exists() else None
    penalty_order = list(LITHOLOGY_CODE_NAMES.keys())

    candidates = [] if args.select_only else (
        args.models or list(config.get("lithology_model.candidates"))
    )
    registry = ModelRegistry(config.get("paths.models"))
    artifacts_dir = ensure_dir(Path(config.get("paths.models")) / MODEL_NAME)

    def evaluate(pipeline, subset: pd.DataFrame, split_name: str) -> dict:
        if subset.empty:
            return {"split": split_name, "status": "no rows in split"}
        y_true = subset[target_col].astype(int).to_numpy()
        encoded = pipeline.predict(subset[feature_columns])
        y_pred = np.array([inverse[int(v)] for v in encoded])
        metrics = classification_metrics(
            y_true,
            y_pred,
            class_names=LITHOLOGY_CODE_NAMES,
            min_class_support=min_support,
            split_name=split_name,
        )
        metrics["wells"] = int(subset[well_col].nunique())
        metrics["majority_class_baseline"] = majority_class_baseline(y_true)
        if penalty_matrix is not None:
            try:
                metrics["force_penalty_score"] = force_penalty_score(
                    y_true, y_pred, penalty_matrix, penalty_order
                )
            except (KeyError, IndexError) as exc:
                log.warning("penalty_score_unavailable", error=str(exc))
        return metrics

    holdout = load_leaderboard_holdout(config, feature_columns)
    results: dict[str, dict] = {}

    for kind in candidates:
        imputer, estimator, params = build_estimator(kind, config, len(classes))
        if args.smoke:
            estimator.set_params(n_estimators=20)
            params = {**params, "n_estimators": 20, "smoke_run": True}
        y_encoded = y_train.map(class_index).to_numpy()

        log.info("fit_start", model=kind, rows=len(x_train), features=len(feature_columns))
        started = time.perf_counter()

        x_train_imputed = imputer.fit_transform(x_train)

        # Shale is 61.6% of rows and Basement is 0.01%. Without rebalancing, boosting
        # optimises the common classes and effectively ignores the rare ones. The
        # RandomForest already does this via class_weight="balanced_subsample".
        sample_weight = None
        if params.get("class_balancing") == "balanced":
            from sklearn.utils.class_weight import compute_sample_weight

            sample_weight = compute_sample_weight("balanced", y_encoded)
            log.info(
                "class_balancing_applied",
                model=kind,
                weight_min=round(float(sample_weight.min()), 4),
                weight_max=round(float(sample_weight.max()), 2),
            )

        if params.get("early_stopping_rounds") and not splits["validation"].empty:
            # Stop when validation loss stops improving, rather than always running the
            # full round budget. The validation wells are never used for fitting.
            validation = splits["validation"]
            # A validation well can carry a lithology that no training well contains.
            # Those rows have no encodable label, so they are excluded from the early
            # stopping signal (and counted) rather than being silently coerced.
            val_labels = validation[target_col].astype(int)
            encodable = val_labels.isin(class_index)
            dropped = int((~encodable).sum())
            if dropped:
                log.warning(
                    "eval_set_rows_excluded",
                    rows=dropped,
                    reason="lithology class absent from the training wells",
                    classes=sorted(
                        {int(c) for c in val_labels[~encodable].unique()}
                    ),
                )
            estimator.fit(
                x_train_imputed,
                y_encoded,
                sample_weight=sample_weight,
                eval_set=[
                    (
                        imputer.transform(validation.loc[encodable, feature_columns]),
                        val_labels[encodable].map(class_index).to_numpy(),
                    )
                ],
                verbose=False,
            )
        else:
            estimator.fit(x_train_imputed, y_encoded, sample_weight=sample_weight)

        pipeline = Pipeline([("impute", imputer), ("model", estimator)])
        elapsed = time.perf_counter() - started
        log.info(
            "fit_complete",
            model=kind,
            seconds=round(elapsed, 1),
            best_iteration=getattr(estimator, "best_iteration", None),
        )

        metrics = {
            "validation": evaluate(pipeline, splits["validation"], "validation"),
            "test": evaluate(pipeline, splits["test"], "test"),
        }
        if holdout is not None:
            metrics["leaderboard_holdout"] = evaluate(
                pipeline, holdout, "leaderboard_holdout"
            )

        artifact_path = artifacts_dir / f"{kind}.joblib"
        joblib.dump(
            {
                "pipeline": pipeline,
                "feature_columns": feature_columns,
                "class_index": class_index,
                "inverse_index": inverse,
                "class_names": LITHOLOGY_CODE_NAMES,
            },
            artifact_path,
        )

        limitations = [
            "Trained on Norwegian North Sea wells (FORCE 2020); behaviour outside that "
            "basin is unverified.",
            "Absolute position (X_LOC/Y_LOC) is deliberately excluded, so the model "
            "cannot lean on geography to identify lithology.",
        ]
        absent = json.loads((processed / "split_manifest.json").read_text(encoding="utf-8"))
        for split_name, missing_classes in absent["classes_absent_from_split"].items():
            if missing_classes:
                names = [LITHOLOGY_CODE_NAMES.get(c, str(c)) for c in missing_classes]
                limitations.append(
                    f"Classes absent from the {split_name} split, so unmeasurable there: "
                    f"{', '.join(names)}."
                )

        record = ModelRecord(
            name=MODEL_NAME,
            version=f"{new_version()}-{kind}",
            task="lithology_classification",
            algorithm=kind,
            dataset="FORCE 2020 train (well-level split)",
            dataset_rows=int(len(frame)),
            feature_columns=feature_columns,
            target_column=target_col,
            hyperparameters=params,
            split_strategy=absent["strategy"],
            split_summary={
                name: {
                    "rows": int(len(subset)),
                    "wells": int(subset[well_col].nunique()),
                }
                for name, subset in splits.items()
            },
            metrics=metrics,
            artifact_path=str(artifact_path),
            training_seconds=round(elapsed, 1),
            limitations=limitations,
        )
        if args.smoke:
            # A smoke model is trained on a handful of wells with a token number of
            # trees. Registering it would let the API serve its metrics as real.
            record.status = "smoke_run_not_registered"
            log.warning("registration_skipped", model=kind, reason="smoke run")
        else:
            registry.register(record)
        results[kind] = metrics

        log.info(
            "model_registered",
            model=kind,
            validation_macro_f1=metrics["validation"].get("macro_f1"),
            test_macro_f1=metrics["test"].get("macro_f1"),
            holdout_macro_f1=metrics.get("leaderboard_holdout", {}).get("macro_f1"),
        )

    # Selection on validation only, never on test or the external holdout.
    # Every registered version is considered, not just the ones trained in this run, so
    # training a single candidate cannot silently promote it over a better stored model.
    if not args.smoke:
        candidates_seen: dict[str, dict] = {}
        for version in registry.list_versions(MODEL_NAME):
            record = json.loads(
                (registry.model_dir(MODEL_NAME) / f"{version}.json").read_text(encoding="utf-8")
            )
            if record.get("status") != "trained":
                continue
            algorithm = record["algorithm"]
            score = record["metrics"].get("validation", {}).get("macro_f1")
            if score is None:
                continue
            previous = candidates_seen.get(algorithm)
            if previous is None or record["trained_at"] > previous["trained_at"]:
                candidates_seen[algorithm] = {
                    "validation_macro_f1": score,
                    "test_macro_f1": record["metrics"].get("test", {}).get("macro_f1"),
                    "holdout_macro_f1": record["metrics"]
                    .get("leaderboard_holdout", {})
                    .get("macro_f1"),
                    "trained_at": record["trained_at"],
                    "version": version,
                }

        if candidates_seen:
            best = max(
                candidates_seen, key=lambda k: candidates_seen[k]["validation_macro_f1"]
            )
            # Flag when the unseen-well estimates disagree with the selection: that is a
            # symptom of well-to-well variance, and hiding it would overstate confidence.
            holdout_best = max(
                (k for k in candidates_seen if candidates_seen[k]["holdout_macro_f1"]),
                key=lambda k: candidates_seen[k]["holdout_macro_f1"],
                default=None,
            )
            selection = {
                "selected_model": best,
                "selection_metric": "validation macro F1",
                "selection_rule": "validation wells only; test and external holdout are "
                                  "never consulted for selection",
                "candidates": candidates_seen,
            }
            if holdout_best and holdout_best != best:
                selection["disagreement_warning"] = (
                    f"{best!r} wins on validation but {holdout_best!r} scores higher on "
                    "the external holdout wells. The two unseen-well estimates disagree, "
                    "which indicates high well-to-well variance rather than a clear "
                    "winner. Grouped cross-validation over training wells would give a "
                    "more stable selection."
                )
                log.warning("selection_disagreement",
                            validation_winner=best, holdout_winner=holdout_best)

            (artifacts_dir / "selected.json").write_text(
                json.dumps(selection, indent=2), encoding="utf-8"
            )
            log.info(
                "model_selected",
                selected=best,
                validation_macro_f1=candidates_seen[best]["validation_macro_f1"],
                considered=sorted(candidates_seen),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
