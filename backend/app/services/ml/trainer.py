"""
ML Model Trainer — trains classification models from labeled data.

Data sources (each example carries a label-provenance tag — see
``app.services.ml.label_provenance``):
  1. AIFeedback table (human labels — ``human_direct`` for UI/MCP
     ratings/corrections, ``human_indirect`` for Jira-resolution auto-labels)
  2. AIAnalysis rows with confidence_score >= 80 and **no** feedback
     (``llm_pseudo`` — the LLM's own opinion, not ground truth)

Composition policy (AI-F1 — breaks the circular LLM→ML label loop):
  - human labels are always included at full sample weight;
  - ``llm_pseudo`` examples are capped at ``ML_PSEUDO_LABEL_CAP`` of the final
    training set and down-weighted to ``ML_PSEUDO_LABEL_WEIGHT``;
  - when human labels alone are below ``ML_MIN_TRAINING_SAMPLES``,
    pseudo-labels may fill up to the minimum, but the deployed model's
    metadata records the mix (``label_composition``) and the model reports
    itself as bootstrap below ``ML_HUMAN_LABEL_FLOOR`` human labels.

Training pipeline:
  1. Query labeled data from PostgreSQL (with full history + run context)
  2. Apply the label-provenance composition policy (cap + weights)
  3. Extract complete 31-feature vectors for all samples
  4. Compute class weights for imbalanced category handling
  5. Train HistGradientBoostingClassifier with stratified 5-fold cross-validation
  6. Evaluate on 20% holdout with per-class precision/recall/F1
  7. Log feature importance for diagnostics
  8. If accuracy >= threshold: save model + update metadata (incl. composition)
  9. If accuracy < threshold: keep previous model, log warning

Triggered by:
  - Celery beat task (nightly at 03:00 UTC)
  - Manual trigger via POST /api/v1/settings/ai/ml/retrain

Usage:
    from app.services.ml.trainer import train_classifier
    result = await train_classifier()
"""
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger("services.ml.trainer")


async def get_training_sample_count() -> int:
    """Count available labeled samples without running full training.

    Human labels = feedback rows that assert a usable category (confirmed
    verdicts or explicit corrections). Pseudo-labels = high-confidence
    analyses with **no** feedback row (an analysis with feedback is already
    counted on the human side — the old version double-counted these).
    """
    try:
        from app.db.postgres import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            # Feedback rows carrying a usable label
            result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM ai_feedback
                    WHERE rating = 'correct'
                       OR (rating = 'incorrect' AND corrected_category IS NOT NULL)
                """)
            )
            feedback_count = result.scalar() or 0

            # High-confidence analyses without human feedback (pseudo-labels)
            result2 = await db.execute(
                text("""
                    SELECT COUNT(*) FROM ai_analysis ai
                    WHERE ai.confidence_score >= 80
                      AND ai.failure_category IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM ai_feedback fb WHERE fb.analysis_id = ai.id
                      )
                """)
            )
            analysis_count = result2.scalar() or 0

            return feedback_count + analysis_count
    except Exception as exc:
        logger.debug("Failed to count training samples: %s", exc)
        return 0


async def get_human_label_count() -> int:
    """Count human-provenance labels (direct + indirect) usable for training."""
    try:
        from app.db.postgres import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM ai_feedback
                    WHERE rating = 'correct'
                       OR (rating = 'incorrect' AND corrected_category IS NOT NULL)
                """)
            )
            return result.scalar() or 0
    except Exception as exc:
        logger.debug("Failed to count human labels: %s", exc)
        return 0


def _compute_class_weights(y: Any, n_classes: int) -> dict[int, float]:
    """Compute balanced class weights inversely proportional to class frequency."""
    counts = Counter(y.tolist())
    n_samples = len(y)
    weights = {}
    for cls_idx in range(n_classes):
        count = counts.get(cls_idx, 0)
        if count > 0:
            weights[cls_idx] = n_samples / (n_classes * count)
        else:
            weights[cls_idx] = 1.0
    return weights


def _compute_sample_weights(y: Any, class_weights: dict[int, float]) -> Any:
    """Convert class weights to per-sample weight array."""
    import numpy as np  # noqa: F811
    return np.array([class_weights[label] for label in y])


async def train_classifier() -> dict[str, Any]:
    """Train the ML classifier from labeled data.

    Returns dict with status, accuracy, sample_count, model_path,
    cv_scores, feature_importance, per_class_metrics.
    """
    try:
        import joblib
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import (
            train_test_split,
            StratifiedKFold,
            cross_val_score,
        )
        from sklearn.metrics import (
            accuracy_score,
            classification_report,
            precision_recall_fscore_support,
        )
    except ImportError as exc:
        return {
            "status": "error",
            "message": f"ML dependencies not installed: {exc}. Install scikit-learn and joblib.",
        }

    from app.services.ml.feature_extractor import FEATURE_NAMES
    from app.services.ml.label_provenance import apply_composition_policy

    # ── 1. Gather training data (with full history + run context) ───────
    samples, labels, provenances = await _gather_training_data()

    # ── 1b. Composition policy: human labels first, pseudo-labels capped ─
    samples, labels, provenances, provenance_weights, composition = (
        apply_composition_policy(
            samples, labels, provenances,
            pseudo_cap=settings.ML_PSEUDO_LABEL_CAP,
            pseudo_weight=settings.ML_PSEUDO_LABEL_WEIGHT,
            min_samples=settings.ML_MIN_TRAINING_SAMPLES,
            human_label_floor=settings.ML_HUMAN_LABEL_FLOOR,
        )
    )
    logger.info("Training label composition: %s", composition)

    if len(samples) < settings.ML_MIN_TRAINING_SAMPLES:
        return {
            "status": "insufficient_data",
            "message": f"Need {settings.ML_MIN_TRAINING_SAMPLES} samples, have {len(samples)}.",
            "sample_count": len(samples),
            "label_composition": composition,
        }

    # ── 2. Convert to arrays ────────────────────────────────────────────
    from app.services.ml.feature_extractor import features_to_array
    from app.services.ml.classifier import CATEGORY_LABELS

    label_to_idx = {label: idx for idx, label in enumerate(CATEGORY_LABELS)}
    X = np.array([features_to_array(s) for s in samples])
    y = np.array([label_to_idx.get(lbl, label_to_idx["UNKNOWN"]) for lbl in labels])

    # Surface data-quality drift: labels outside CATEGORY_LABELS are silently
    # folded into UNKNOWN, which corrupts the training target if it goes unnoticed.
    unmapped = Counter(lbl for lbl in labels if lbl not in label_to_idx)
    if unmapped:
        logger.warning(
            "Remapped %s sample(s) with out-of-vocabulary labels to UNKNOWN: %s",
            sum(unmapped.values()), dict(unmapped),
        )

    # Log class distribution for diagnostics
    class_dist = Counter(labels)
    logger.info("Training class distribution: %s", dict(class_dist))

    # ── Guard: stratified split + 5-fold CV require >=2 (>=5 for CV) samples
    # per class and at least 2 distinct classes. Early in a deployment the
    # labeled set is tiny and skewed, so without this the retrain job crashes
    # with a bare sklearn ValueError instead of degrading gracefully.
    class_idx_counts = Counter(int(v) for v in y)
    distinct_classes = len(class_idx_counts)
    min_class_count = min(class_idx_counts.values()) if class_idx_counts else 0
    if distinct_classes < 2:
        return {
            "status": "insufficient_class_diversity",
            "message": f"Need >=2 distinct categories to train, have {distinct_classes}.",
            "sample_count": len(samples),
            "class_distribution": dict(class_dist),
            "label_composition": composition,
        }
    can_stratify = min_class_count >= 2
    cv_splits = min(5, min_class_count)
    use_cv = cv_splits >= 2

    # ── 3. Compute class weights (handle imbalanced categories) ─────────
    class_weights = _compute_class_weights(y, len(CATEGORY_LABELS))
    logger.info("Class weights: %s", {CATEGORY_LABELS[k]: round(v, 2) for k, v in class_weights.items()})

    # ── 4. Train/test split (provenance weights ride along) ─────────────
    prov_w = np.array(provenance_weights, dtype=float)
    X_train, X_test, y_train, y_test, prov_w_train, _prov_w_test = train_test_split(
        X, y, prov_w, test_size=0.20, random_state=42,
        stratify=y if can_stratify else None,
    )

    # ── 5. Cross-validation to estimate generalization ──────────────────
    # Final per-sample weight = class-balance weight × provenance weight,
    # so pseudo-labels (weight ML_PSEUDO_LABEL_WEIGHT) pull less than human
    # labels within the same class.
    sample_weights_train = (
        _compute_sample_weights(y_train, class_weights) * prov_w_train
    )

    # Train with tuned hyperparameters
    model = HistGradientBoostingClassifier(
        max_iter=300,
        max_depth=8,
        learning_rate=0.05,
        min_samples_leaf=5,
        l2_regularization=0.1,
        max_bins=128,
        early_stopping=True,
        n_iter_no_change=15,
        validation_fraction=0.15,
        random_state=42,
    )

    if use_cv:
        cv = StratifiedKFold(n_splits=cv_splits, shuffle=True, random_state=42)
        # sklearn >=1.6 renamed cross_val_score's ``fit_params`` to ``params``
        # (with metadata routing disabled, ``params`` is passed straight to
        # fit, same semantics). Support both since scikit-learn is an
        # optional dependency with no pinned version.
        try:
            cv_scores = cross_val_score(
                model, X_train, y_train, cv=cv, scoring="accuracy",
                params={"sample_weight": sample_weights_train},
            )
        except TypeError:
            cv_scores = cross_val_score(
                model, X_train, y_train, cv=cv, scoring="accuracy",
                fit_params={"sample_weight": sample_weights_train},
            )
        logger.info(
            "Cross-validation scores (%s-fold): %s (mean=%.3f ± %.3f)",
            cv_splits, [round(s, 3) for s in cv_scores], cv_scores.mean(), cv_scores.std(),
        )
    else:
        # Too few samples in the smallest class for meaningful CV — skip it and
        # report a single-element score so downstream mean/std stay well-defined.
        import numpy as _np
        cv_scores = _np.array([0.0])
        logger.warning(
            "Skipping cross-validation: smallest class has %s sample(s) (<2 folds possible)",
            min_class_count,
        )

    # ── 6. Final training on full train set with sample weights ─────────
    model.fit(X_train, y_train, sample_weight=sample_weights_train)

    # ── 7. Evaluate on held-out test set ────────────────────────────────
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    # ``labels=`` is required: without it sklearn derives the class list from
    # the data and raises when fewer than all six categories are present in
    # the (train ∪ test) labels — the common case early in a deployment.
    report = classification_report(
        y_test, y_pred,
        labels=list(range(len(CATEGORY_LABELS))),
        target_names=CATEGORY_LABELS, output_dict=True, zero_division=0,
    )

    # Per-class precision/recall/F1
    precision, recall, f1, support = precision_recall_fscore_support(
        y_test, y_pred, labels=list(range(len(CATEGORY_LABELS))), zero_division=0
    )
    per_class = {}
    for idx, label in enumerate(CATEGORY_LABELS):
        per_class[label] = {
            "precision": round(float(precision[idx]), 4),
            "recall": round(float(recall[idx]), 4),
            "f1": round(float(f1[idx]), 4),
            "support": int(support[idx]),
        }
    logger.info("Per-class metrics: %s", per_class)

    # ── 8. Feature importance ───────────────────────────────────────────
    importances = {}
    if hasattr(model, "feature_importances_"):
        for name, imp in sorted(
            zip(FEATURE_NAMES, model.feature_importances_),
            key=lambda x: -x[1],
        ):
            importances[name] = round(float(imp), 4)
        top_5 = list(importances.items())[:5]
        logger.info("Top 5 features: %s", top_5)

    logger.info(
        "ML classifier training complete: accuracy=%.3f cv_mean=%.3f samples=%d",
        accuracy, cv_scores.mean(), len(samples),
    )

    # ── 9. Save if meets threshold ──────────────────────────────────────
    model_dir = Path(settings.ML_MODEL_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    version = now.strftime("%Y%m%d_%H%M%S")

    if accuracy >= settings.ML_ACCURACY_THRESHOLD:
        model_path = model_dir / f"classifier_v{version}.joblib"
        # Atomic write: dump to a temp file in the same dir, then os.replace so a
        # crash mid-dump can't leave a half-written .joblib that the classifier
        # later tries (and fails) to load.
        import os
        tmp_model = model_path.with_suffix(".joblib.tmp")
        joblib.dump(model, tmp_model)
        os.replace(tmp_model, model_path)

        metadata = {
            "status": "trained",
            "version": version,
            "accuracy": round(accuracy, 4),
            "cv_mean_accuracy": round(float(cv_scores.mean()), 4),
            "cv_std": round(float(cv_scores.std()), 4),
            "cv_scores": [round(float(s), 4) for s in cv_scores],
            "sample_count": len(samples),
            "train_count": len(X_train),
            "test_count": len(X_test),
            "class_distribution": dict(class_dist),
            "class_weights": {CATEGORY_LABELS[k]: round(v, 2) for k, v in class_weights.items()},
            # AI-F1: label-provenance mix this model was actually trained on
            # (counts + fractions per human_direct/human_indirect/llm_pseudo,
            # pseudo cap/weight, and whether the cap was exceeded to reach the
            # minimum sample floor). Consumers: routing decision records,
            # /api/v1/ai-eval/label-health, AI settings honesty caveat.
            "label_composition": composition,
            "feature_names": FEATURE_NAMES,
            "feature_importance": importances,
            "category_labels": CATEGORY_LABELS,
            "per_class_metrics": per_class,
            "trained_at": now.isoformat(),
            "classification_report": report,
            "hyperparameters": {
                "max_iter": 300,
                "max_depth": 8,
                "learning_rate": 0.05,
                "min_samples_leaf": 5,
                "l2_regularization": 0.1,
                "max_bins": 128,
                "early_stopping": True,
                "n_iter_no_change": 15,
            },
        }
        meta_path = model_dir / "training_metadata.json"
        tmp_meta = meta_path.with_suffix(".json.tmp")
        tmp_meta.write_text(json.dumps(metadata, indent=2, default=str))
        os.replace(tmp_meta, meta_path)

        logger.info("Model saved: %s (accuracy: %.3f)", model_path, accuracy)
        return {
            "status": "trained",
            "accuracy": accuracy,
            "cv_mean_accuracy": round(float(cv_scores.mean()), 4),
            "sample_count": len(samples),
            "version": version,
            "feature_importance": importances,
            "per_class_metrics": per_class,
            "label_composition": composition,
        }
    else:
        logger.warning(
            "Model accuracy %.3f below threshold %.3f — not deployed",
            accuracy, settings.ML_ACCURACY_THRESHOLD,
        )
        return {
            "status": "below_threshold",
            "accuracy": accuracy,
            "cv_mean_accuracy": round(float(cv_scores.mean()), 4),
            "threshold": settings.ML_ACCURACY_THRESHOLD,
            "sample_count": len(samples),
            "per_class_metrics": per_class,
            "label_composition": composition,
        }


async def _gather_training_data() -> tuple[list[dict], list[str], list[str]]:
    """Gather labeled feature vectors from DB with full history and run context.

    Queries test case data, historical pass/fail stats, and run-level context
    to produce complete feature vectors. This ensures training uses the same
    feature set as inference.

    Returns ``(features_list, labels_list, provenances_list)`` — aligned
    lists where each feature is a dict from feature_extractor, each label is
    a FailureCategory string, and each provenance is one of the
    ``label_provenance`` buckets:

      - human labels come from ``ai_feedback`` rows that assert a usable
        category (confirmed verdicts, or corrections). One label per test
        case: explicit UI/MCP feedback (``human_direct``) beats Jira webhook
        auto-labels (``human_indirect``), newest first.
      - ``llm_pseudo`` labels come from high-confidence analyses with **no**
        feedback row. Analyses that received feedback are excluded here —
        confirmed ones are already on the human side, and corrected ones had
        their ``failure_category`` overwritten by the correction, so counting
        them again would duplicate the human label.
    """
    from app.services.ml.feature_extractor import extract_features
    from app.services.ml.label_provenance import LLM_PSEUDO

    samples: list[dict] = []
    labels: list[str] = []
    provenances: list[str] = []

    try:
        from app.db.postgres import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            # ── Human labels: feedback rows with a usable category ────────
            # DISTINCT ON keeps one row per test case, preferring explicit
            # UI/MCP feedback over Jira auto-labels, then the newest row.
            human_result = await db.execute(text("""
                SELECT DISTINCT ON (fb.test_case_id)
                    tc.id AS tc_id,
                    tc.error_message,
                    tc.duration_ms,
                    tc.severity,
                    fb.rating,
                    fb.corrected_category,
                    fb.source AS feedback_source,
                    ai.failure_category,
                    tr.pass_rate AS run_pass_rate,
                    tr.total_tests,
                    tr.failed_tests AS run_failed_tests,
                    tr.started_at AS run_started_at
                FROM ai_feedback fb
                JOIN ai_analysis ai ON ai.id = fb.analysis_id
                JOIN test_cases tc ON tc.id = fb.test_case_id
                JOIN test_runs tr ON tr.id = tc.test_run_id
                WHERE fb.rating = 'correct'
                   OR (fb.rating = 'incorrect' AND fb.corrected_category IS NOT NULL)
                ORDER BY fb.test_case_id,
                         (CASE WHEN fb.source IN ('manual', 'category_correction')
                               THEN 0 ELSE 1 END),
                         fb.created_at DESC
                LIMIT 50000
            """))
            human_rows = human_result.fetchall()

            # ── Pseudo-labels: high-confidence analyses without feedback ──
            result = await db.execute(text("""
                SELECT
                    tc.id AS tc_id,
                    tc.error_message,
                    tc.duration_ms,
                    tc.severity,
                    ai.failure_category,
                    ai.confidence_score,
                    tr.pass_rate AS run_pass_rate,
                    tr.total_tests,
                    tr.failed_tests AS run_failed_tests,
                    tr.started_at AS run_started_at
                FROM ai_analysis ai
                JOIN test_cases tc ON tc.id = ai.test_case_id
                JOIN test_runs tr ON tr.id = tc.test_run_id
                WHERE ai.confidence_score >= 80
                  AND ai.failure_category IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM ai_feedback fb WHERE fb.analysis_id = ai.id
                  )
                LIMIT 50000
            """))
            analysis_rows = result.fetchall()

            # Build a set of test case IDs to batch-fetch history
            tc_ids = [row.tc_id for row in human_rows] + [row.tc_id for row in analysis_rows]

            # Batch-fetch historical stats per test (by test_name matching)
            # Using a subquery to compute pass/fail counts from prior runs
            history_map: dict[str, dict] = {}
            if tc_ids:
                hist_result = await db.execute(text("""
                    SELECT
                        tc_current.id AS tc_id,
                        COUNT(*) FILTER (WHERE tc_hist.status = 'PASSED') AS pass_count,
                        COUNT(*) FILTER (WHERE tc_hist.status IN ('FAILED', 'BROKEN')) AS fail_count,
                        COUNT(*) AS total_count,
                        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tc_hist.duration_ms)
                            FILTER (WHERE tc_hist.duration_ms IS NOT NULL) AS median_duration_ms
                    FROM test_cases tc_current
                    JOIN test_cases tc_hist
                        ON tc_hist.test_name = tc_current.test_name
                        AND tc_hist.id != tc_current.id
                    WHERE tc_current.id = ANY(:tc_ids)
                    GROUP BY tc_current.id
                """), {"tc_ids": tc_ids})

                for hrow in hist_result.fetchall():
                    history_map[str(hrow.tc_id)] = {
                        "pass_count": hrow.pass_count or 0,
                        "fail_count": hrow.fail_count or 0,
                        "median_duration_ms": float(hrow.median_duration_ms) if hrow.median_duration_ms else 0,
                    }

            def _features_for(row) -> dict:
                history = history_map.get(str(row.tc_id), {})
                run_context: dict[str, Any] = {
                    "pass_rate": float(row.run_pass_rate) if row.run_pass_rate is not None else 0,
                    "failed_tests": row.run_failed_tests or 0,
                    "start_time": row.run_started_at,
                }
                return extract_features(
                    test_case={
                        "error_message": row.error_message,
                        "duration_ms": row.duration_ms,
                        "severity": row.severity,
                    },
                    history=history,
                    run_context=run_context,
                )

            from app.services.ml.label_provenance import (
                provenance_for_feedback_source,
                resolve_feedback_label,
            )

            human_tc_ids: set[str] = set()
            for row in human_rows:
                label = resolve_feedback_label(
                    row.rating, row.corrected_category, row.failure_category,
                )
                if not label:
                    continue
                human_tc_ids.add(str(row.tc_id))
                samples.append(_features_for(row))
                labels.append(label)
                provenances.append(
                    provenance_for_feedback_source(row.feedback_source)
                )

            for row in analysis_rows:
                # Feedback keys on analysis_id; a test case can have multiple
                # analyses over time, so also skip any test case that already
                # carries a human label to avoid conflicting duplicates.
                if str(row.tc_id) in human_tc_ids:
                    continue
                samples.append(_features_for(row))
                labels.append(row.failure_category)
                provenances.append(LLM_PSEUDO)

    except Exception as exc:
        logger.error("Failed to gather training data: %s", exc)

    return samples, labels, provenances
