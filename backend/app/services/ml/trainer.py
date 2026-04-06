"""
ML Model Trainer — trains classification models from labeled data.

Data sources:
  1. AIFeedback table (human-verified labels — highest quality)
  2. AIAnalysis table with confidence_score >= 80 (LLM pseudo-labels)

Training pipeline:
  1. Query labeled data from PostgreSQL (with full history + run context)
  2. Extract complete 32-feature vectors for all samples
  3. Compute class weights for imbalanced category handling
  4. Train HistGradientBoostingClassifier with stratified 5-fold cross-validation
  5. Select best hyperparameters via RandomizedSearchCV
  6. Evaluate on 20% holdout with per-class precision/recall/F1
  7. Log feature importance for diagnostics
  8. If accuracy >= threshold: save model + update metadata
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
    """Count available labeled samples without running full training."""
    try:
        from app.db.postgres import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            # Count AIFeedback entries with explicit labels
            result = await db.execute(
                text("SELECT COUNT(*) FROM ai_feedback WHERE rating IN ('correct', 'incorrect')")
            )
            feedback_count = result.scalar() or 0

            # Count high-confidence AIAnalysis entries (pseudo-labels)
            result2 = await db.execute(
                text("SELECT COUNT(*) FROM ai_analysis WHERE confidence_score >= 80")
            )
            analysis_count = result2.scalar() or 0

            return feedback_count + analysis_count
    except Exception as exc:
        logger.debug("Failed to count training samples: %s", exc)
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

    # ── 1. Gather training data (with full history + run context) ───────
    samples, labels = await _gather_training_data()
    if len(samples) < settings.ML_MIN_TRAINING_SAMPLES:
        return {
            "status": "insufficient_data",
            "message": f"Need {settings.ML_MIN_TRAINING_SAMPLES} samples, have {len(samples)}.",
            "sample_count": len(samples),
        }

    # ── 2. Convert to arrays ────────────────────────────────────────────
    from app.services.ml.feature_extractor import features_to_array
    from app.services.ml.classifier import CATEGORY_LABELS

    label_to_idx = {label: idx for idx, label in enumerate(CATEGORY_LABELS)}
    X = np.array([features_to_array(s) for s in samples])
    y = np.array([label_to_idx.get(lbl, label_to_idx["UNKNOWN"]) for lbl in labels])

    # Log class distribution for diagnostics
    class_dist = Counter(labels)
    logger.info("Training class distribution: %s", dict(class_dist))

    # ── 3. Compute class weights (handle imbalanced categories) ─────────
    class_weights = _compute_class_weights(y, len(CATEGORY_LABELS))
    logger.info("Class weights: %s", {CATEGORY_LABELS[k]: round(v, 2) for k, v in class_weights.items()})

    # ── 4. Train/test split ─────────────────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    # ── 5. Cross-validation to estimate generalization ──────────────────
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    sample_weights_train = _compute_sample_weights(y_train, class_weights)

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

    cv_scores = cross_val_score(
        model, X_train, y_train, cv=cv, scoring="accuracy",
        fit_params={"sample_weight": sample_weights_train},
    )
    logger.info(
        "Cross-validation scores: %s (mean=%.3f ± %.3f)",
        [round(s, 3) for s in cv_scores], cv_scores.mean(), cv_scores.std(),
    )

    # ── 6. Final training on full train set with sample weights ─────────
    model.fit(X_train, y_train, sample_weight=sample_weights_train)

    # ── 7. Evaluate on held-out test set ────────────────────────────────
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    report = classification_report(
        y_test, y_pred, target_names=CATEGORY_LABELS, output_dict=True, zero_division=0
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
        joblib.dump(model, model_path)

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
        (model_dir / "training_metadata.json").write_text(
            json.dumps(metadata, indent=2, default=str)
        )

        logger.info("Model saved: %s (accuracy: %.3f)", model_path, accuracy)
        return {
            "status": "trained",
            "accuracy": accuracy,
            "cv_mean_accuracy": round(float(cv_scores.mean()), 4),
            "sample_count": len(samples),
            "version": version,
            "feature_importance": importances,
            "per_class_metrics": per_class,
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
        }


async def _gather_training_data() -> tuple[list[dict], list[str]]:
    """Gather labeled feature vectors from DB with full history and run context.

    Queries test case data, historical pass/fail stats, and run-level context
    to produce complete feature vectors. This ensures training uses the same
    feature set as inference.

    Returns (features_list, labels_list) where each feature is a dict
    from feature_extractor and each label is a FailureCategory string.
    """
    from app.services.ml.feature_extractor import extract_features

    samples: list[dict] = []
    labels: list[str] = []

    try:
        from app.db.postgres import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            # Fetch analyses joined with test case, run context, and historical stats
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
                JOIN test_runs tr ON tr.id = tc.run_id
                WHERE ai.confidence_score >= 80
                  AND ai.failure_category IS NOT NULL
                LIMIT 50000
            """))
            analysis_rows = result.fetchall()

            # Build a set of test case IDs to batch-fetch history
            tc_ids = [row.tc_id for row in analysis_rows]

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

            for row in analysis_rows:
                tc_id_str = str(row.tc_id)
                history = history_map.get(tc_id_str, {})

                # Build run context from the test run
                run_context: dict[str, Any] = {
                    "pass_rate": float(row.run_pass_rate) if row.run_pass_rate is not None else 0,
                    "failed_tests": row.run_failed_tests or 0,
                    "start_time": row.run_started_at,
                }

                features = extract_features(
                    test_case={
                        "error_message": row.error_message,
                        "duration_ms": row.duration_ms,
                        "severity": row.severity,
                    },
                    history=history,
                    run_context=run_context,
                )
                samples.append(features)
                labels.append(row.failure_category)

    except Exception as exc:
        logger.error("Failed to gather training data: %s", exc)

    return samples, labels
