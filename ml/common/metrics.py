"""Classification metrics with honest reporting of imbalance.

Accuracy alone is misleading on the FORCE lithology task: always predicting Shale
scores about 61.6%. Every report therefore carries macro F1 and balanced accuracy
alongside accuracy, plus a full per-class table that marks which classes had too
little support for their metrics to mean anything.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)


def classification_metrics(
    y_true: Sequence,
    y_pred: Sequence,
    *,
    class_names: Mapping[int, str] | None = None,
    min_class_support: int = 0,
    split_name: str = "",
) -> dict[str, Any]:
    """Compute the full metric set for one evaluation split."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    labels = sorted({int(v) for v in np.unique(np.concatenate([y_true, y_pred]))})
    names = class_names or {}

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )

    per_class = []
    low_support = []
    for idx, label in enumerate(labels):
        entry = {
            "code": int(label),
            "name": names.get(int(label), str(label)),
            "support": int(support[idx]),
            "precision": round(float(precision[idx]), 4),
            "recall": round(float(recall[idx]), 4),
            "f1": round(float(f1[idx]), 4),
            "reliable": bool(support[idx] >= min_class_support),
        }
        if not entry["reliable"]:
            low_support.append(entry["name"])
        per_class.append(entry)

    matrix = confusion_matrix(y_true, y_pred, labels=labels)

    return {
        "split": split_name,
        "samples": int(len(y_true)),
        "classes_evaluated": len(labels),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4),
        "weighted_f1": round(
            float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4
        ),
        "cohen_kappa": round(float(cohen_kappa_score(y_true, y_pred)), 4),
        "per_class": per_class,
        "classes_below_min_support": {
            "threshold": min_class_support,
            "classes": low_support,
        },
        "confusion_matrix": {
            "labels": [int(v) for v in labels],
            "label_names": [names.get(int(v), str(v)) for v in labels],
            "matrix": matrix.tolist(),
        },
        "sklearn_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=[names.get(int(v), str(v)) for v in labels],
            zero_division=0,
            output_dict=True,
        ),
    }


def majority_class_baseline(y_true: Sequence) -> dict[str, Any]:
    """The score to beat: predict the most frequent class for everything.

    Reported next to model metrics so an impressive-looking accuracy on an imbalanced
    dataset can be read in context.
    """
    y_true = np.asarray(y_true)
    values, counts = np.unique(y_true, return_counts=True)
    majority = int(values[int(np.argmax(counts))])
    y_pred = np.full_like(y_true, majority)
    return {
        "strategy": "majority_class",
        "predicted_class": majority,
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(
            float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4
        ),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 4),
    }


def force_penalty_score(
    y_true: Sequence,
    y_pred: Sequence,
    penalty_matrix: np.ndarray,
    label_order: Sequence[int],
) -> float:
    """The official FORCE 2020 competition metric.

    A cost matrix over confusions: mistaking sandstone for shale is penalised more
    heavily than mistaking sandstone for sandstone/shale. Lower is better, 0 is perfect.
    Reported so results are comparable to the published competition leaderboard.
    """
    index = {int(label): i for i, label in enumerate(label_order)}
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    total = 0.0
    for actual, predicted in zip(y_true, y_pred):
        total += float(penalty_matrix[index[int(actual)], index[int(predicted)]])
    return round(total / len(y_true), 4)
