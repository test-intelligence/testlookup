"""
Label-strategy comparison harness (AI-F1 eval evidence).

Trains two classifiers on the *same* labeled pool and compares them on a
held-out set of **human-labeled** examples only:

  - ``pseudo_heavy``   — the pre-AI-F1 regime: every example (human + LLM
    pseudo-label) at uniform weight, no cap.
  - ``human_weighted`` — the AI-F1 composition policy: pseudo-labels capped
    and down-weighted, human labels at full weight.

Because the holdout contains only human-verified labels, the comparison
measures agreement with ground truth rather than agreement with the LLM —
which is exactly the distinction the composition policy exists to protect.

Pure function over in-memory arrays: no DB, no model files. Used by the
regression suite with synthetic fixtures and available for ad-hoc analysis
on exported training data.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from app.core.config import settings

logger = logging.getLogger("services.ml.label_eval")


def compare_label_strategies(
    samples: Sequence[dict],
    labels: Sequence[str],
    provenances: Sequence[str],
    *,
    pseudo_cap: Optional[float] = None,
    pseudo_weight: Optional[float] = None,
    min_samples: Optional[int] = None,
    holdout_fraction: float = 0.3,
    random_state: int = 42,
) -> dict[str, Any]:
    """Train pseudo-heavy vs human-weighted models on one pool; compare on a
    human-labeled holdout.

    Returns a dict with per-strategy metrics (accuracy + per-class
    precision/recall/F1 on the holdout), per-class deltas
    (human_weighted − pseudo_heavy), the composition record of the
    human-weighted training set, and the holdout size.

    Raises ``ValueError`` when there are too few human-labeled examples to
    carve out a meaningful holdout (< 10) or fewer than 2 classes in it.
    """
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import accuracy_score, precision_recall_fscore_support
    from sklearn.model_selection import train_test_split

    from app.services.ml.classifier import CATEGORY_LABELS
    from app.services.ml.feature_extractor import features_to_array
    from app.services.ml.label_provenance import (
        LLM_PSEUDO,
        apply_composition_policy,
    )

    cap = settings.ML_PSEUDO_LABEL_CAP if pseudo_cap is None else pseudo_cap
    weight = (
        settings.ML_PSEUDO_LABEL_WEIGHT if pseudo_weight is None else pseudo_weight
    )
    floor = (
        settings.ML_MIN_TRAINING_SAMPLES if min_samples is None else min_samples
    )

    label_to_idx = {label: idx for idx, label in enumerate(CATEGORY_LABELS)}

    human_idx = [i for i, p in enumerate(provenances) if p != LLM_PSEUDO]
    pseudo_idx = [i for i, p in enumerate(provenances) if p == LLM_PSEUDO]
    if len(human_idx) < 10:
        raise ValueError(
            f"Need >=10 human-labeled examples for a holdout, have {len(human_idx)}."
        )

    # ── Split the human pool: train portion + ground-truth holdout ─────────
    y_human = np.array([label_to_idx.get(labels[i], label_to_idx["UNKNOWN"]) for i in human_idx])
    _, class_counts = np.unique(y_human, return_counts=True)
    stratify = y_human if class_counts.min() >= 2 else None
    human_train_idx, holdout_idx = train_test_split(
        human_idx, test_size=holdout_fraction, random_state=random_state,
        stratify=stratify,
    )
    if len({labels[i] for i in holdout_idx}) < 2:
        raise ValueError("Holdout needs >=2 distinct human-labeled classes.")

    X_holdout = np.array([features_to_array(samples[i]) for i in holdout_idx])
    y_holdout = np.array(
        [label_to_idx.get(labels[i], label_to_idx["UNKNOWN"]) for i in holdout_idx]
    )

    pool_idx = list(human_train_idx) + list(pseudo_idx)
    pool_samples = [samples[i] for i in pool_idx]
    pool_labels = [labels[i] for i in pool_idx]
    pool_prov = [provenances[i] for i in pool_idx]

    def _fit(train_samples, train_labels, sample_weights):
        X = np.array([features_to_array(s) for s in train_samples])
        y = np.array(
            [label_to_idx.get(lbl, label_to_idx["UNKNOWN"]) for lbl in train_labels]
        )
        model = HistGradientBoostingClassifier(
            max_iter=80,
            max_depth=6,
            learning_rate=0.1,
            min_samples_leaf=5,
            random_state=random_state,
        )
        model.fit(X, y, sample_weight=np.array(sample_weights, dtype=float))
        return model

    def _evaluate(model) -> dict[str, Any]:
        y_pred = model.predict(X_holdout)
        accuracy = float(accuracy_score(y_holdout, y_pred))
        precision, recall, f1, support = precision_recall_fscore_support(
            y_holdout, y_pred,
            labels=list(range(len(CATEGORY_LABELS))), zero_division=0,
        )
        per_class = {
            label: {
                "precision": round(float(precision[idx]), 4),
                "recall": round(float(recall[idx]), 4),
                "f1": round(float(f1[idx]), 4),
                "support": int(support[idx]),
            }
            for idx, label in enumerate(CATEGORY_LABELS)
        }
        return {"accuracy": round(accuracy, 4), "per_class": per_class}

    # ── Strategy A: pseudo-heavy (all examples, uniform weight, no cap) ─────
    model_pseudo = _fit(pool_samples, pool_labels, [1.0] * len(pool_samples))
    pseudo_metrics = _evaluate(model_pseudo)
    pseudo_metrics["train_size"] = len(pool_samples)

    # ── Strategy B: human-weighted (AI-F1 composition policy) ───────────────
    hw_samples, hw_labels, _hw_prov, hw_weights, composition = (
        apply_composition_policy(
            pool_samples, pool_labels, pool_prov,
            pseudo_cap=cap,
            pseudo_weight=weight,
            min_samples=floor,
            human_label_floor=settings.ML_HUMAN_LABEL_FLOOR,
        )
    )
    model_human = _fit(hw_samples, hw_labels, hw_weights)
    human_metrics = _evaluate(model_human)
    human_metrics["train_size"] = len(hw_samples)

    per_class_delta = {
        label: {
            metric: round(
                human_metrics["per_class"][label][metric]
                - pseudo_metrics["per_class"][label][metric],
                4,
            )
            for metric in ("precision", "recall", "f1")
        }
        for label in CATEGORY_LABELS
    }

    result = {
        "holdout_size": len(holdout_idx),
        "holdout_provenance": "human_only",
        "pseudo_heavy": pseudo_metrics,
        "human_weighted": human_metrics,
        "delta": {
            "accuracy": round(
                human_metrics["accuracy"] - pseudo_metrics["accuracy"], 4
            ),
            "per_class": per_class_delta,
        },
        "human_weighted_composition": composition,
        "params": {
            "pseudo_cap": float(cap),
            "pseudo_weight": float(weight),
            "min_samples": int(floor),
            "holdout_fraction": float(holdout_fraction),
            "random_state": int(random_state),
        },
    }
    logger.info(
        "Label-strategy comparison: pseudo_heavy=%.3f human_weighted=%.3f (holdout=%d human labels)",
        pseudo_metrics["accuracy"], human_metrics["accuracy"], len(holdout_idx),
    )
    return result
