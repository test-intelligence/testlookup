"""
ML Classifier — scikit-learn HistGradientBoostingClassifier wrapper.

Provides test failure classification without LLM dependency.
Model is trained from human-verified feedback + high-confidence LLM analyses.
Inference is ~1ms per test on CPU — handles 100K tests/day easily.

The classifier is loaded once per worker process and cached in module state.
Model hot-swap: retrain writes a new versioned .joblib file; the next
classification call detects the newer version and loads it automatically.

Usage:
    from app.services.ml.classifier import MLClassifier
    result = MLClassifier.classify(features_dict)
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger("services.ml.classifier")

# Category labels in the order used by the model
CATEGORY_LABELS = [
    "PRODUCT_BUG",
    "INFRASTRUCTURE",
    "TEST_DATA",
    "AUTOMATION_DEFECT",
    "FLAKY",
    "UNKNOWN",
]

# Module-level model cache
_model: Any = None
_model_version: Optional[str] = None
_model_path: Optional[str] = None
_last_version_check: float = 0
_VERSION_CHECK_INTERVAL = 60  # seconds between checking for new model on disk


class MLClassifier:
    """Stateless ML classification wrapper."""

    @staticmethod
    def is_available() -> bool:
        """Check whether a trained model exists on disk."""
        model_dir = Path(settings.ML_MODEL_DIR)
        return any(model_dir.glob("classifier_v*.joblib"))

    @staticmethod
    def get_model_info() -> dict:
        """Return metadata about the currently loaded or latest model."""
        model_dir = Path(settings.ML_MODEL_DIR)
        meta_path = model_dir / "training_metadata.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text())
            except Exception:
                pass
        return {
            "status": "not_trained",
            "version": None,
            "accuracy": None,
            "sample_count": 0,
        }

    @staticmethod
    def classify(features: dict[str, float]) -> dict[str, Any]:
        """Classify a single test case from its feature vector.

        Args:
            features: Dict of 32 numeric features from feature_extractor.

        Returns:
            Dict matching AIAnalysis shape with failure_category,
            confidence_score, is_flaky, etc.

        Raises:
            RuntimeError: If no trained model is available.
        """
        model = _load_model()
        if model is None:
            raise RuntimeError("No trained ML model available")

        from app.services.ml.feature_extractor import features_to_array

        feature_array = [features_to_array(features)]

        # Predict category + calibrated probabilities
        predicted_idx = model.predict(feature_array)[0]
        probabilities = model.predict_proba(feature_array)[0]

        predicted_category = CATEGORY_LABELS[predicted_idx]
        confidence = int(max(probabilities) * 100)
        is_flaky = predicted_category == "FLAKY"

        # Secondary: check historical flakiness override
        # Tightened from 10-90% to 15-85% with minimum 10 runs to reduce false positives
        hist_failure_rate = features.get("historical_failure_rate", -1)
        hist_run_count = features.get("historical_run_count", 0)
        if 0.15 <= hist_failure_rate <= 0.85 and hist_run_count >= 10:
            is_flaky = True

        # Use second-highest probability as uncertainty indicator
        sorted_probs = sorted(probabilities, reverse=True)
        uncertainty = sorted_probs[1] / sorted_probs[0] if sorted_probs[0] > 0 else 1.0
        needs_review = confidence < settings.AI_CONFIDENCE_THRESHOLD or uncertainty > 0.7

        return {
            "root_cause_summary": _generate_summary(predicted_category, confidence, features),
            "failure_category": predicted_category,
            "backend_error_found": predicted_category == "INFRASTRUCTURE",
            "pod_issue_found": False,
            "is_flaky": is_flaky,
            "confidence_score": confidence,
            "recommended_actions": _default_actions(predicted_category),
            "evidence_references": [],
            "requires_human_review": needs_review,
            "classified_by": "ml_classifier",
            "tools_used": [],
            "llm_provider": "none",
            "llm_model": f"ml_classifier_v{_model_version or 'unknown'}",
        }


def _load_model() -> Any:
    """Load the latest model from disk with version-aware hot-swap.

    Checks for a newer model file every _VERSION_CHECK_INTERVAL seconds.
    If a newer version is found, it replaces the cached model atomically.
    """
    global _model, _model_version, _model_path, _last_version_check

    now = time.monotonic()

    # If model is cached, periodically check for newer version
    if _model is not None:
        if (now - _last_version_check) < _VERSION_CHECK_INTERVAL:
            return _model
        # Check if a newer model exists
        _last_version_check = now
        latest = _find_latest_model()
        if latest and str(latest) != _model_path:
            logger.info("Detected newer model: %s (was: %s)", latest.name, _model_path)
            loaded = _try_load(latest)
            if loaded is not None:
                return loaded
        return _model

    # First load
    latest = _find_latest_model()
    if latest is None:
        return None
    return _try_load(latest)


def _find_latest_model() -> Optional[Path]:
    """Find the most recent model file on disk."""
    model_dir = Path(settings.ML_MODEL_DIR)
    model_files = sorted(model_dir.glob("classifier_v*.joblib"), reverse=True)
    return model_files[0] if model_files else None


def _try_load(path: Path) -> Any:
    """Attempt to load a model from the given path, updating cache on success."""
    global _model, _model_version, _model_path, _last_version_check

    try:
        import joblib
    except ImportError:
        logger.warning("joblib not installed — ML classifier unavailable")
        return None

    try:
        loaded = joblib.load(path)
        _model = loaded
        _model_version = path.stem.split("_v")[-1]
        _model_path = str(path)
        _last_version_check = time.monotonic()
        logger.info("Loaded ML classifier model: %s", path.name)
        return _model
    except Exception as exc:
        logger.error("Failed to load ML model %s: %s", path, exc)
        return None


def _generate_summary(category: str, confidence: int, features: dict) -> str:
    """Generate a root cause summary from ML prediction + key features."""
    friendly = category.lower().replace("_", " ")
    parts = [f"ML classifier identified this as {friendly} (confidence: {confidence}%)."]

    hist_rate = features.get("historical_failure_rate", -1)
    if hist_rate >= 0:
        parts.append(f"Historical failure rate: {hist_rate*100:.0f}%.")

    dur_ratio = features.get("duration_vs_median_ratio", -1)
    if dur_ratio > 2.0:
        parts.append(f"Duration {dur_ratio:.1f}x above median — possible performance issue.")

    if features.get("is_new_test", 0) > 0:
        parts.append("This is a new test with limited history.")

    return " ".join(parts)


def _default_actions(category: str) -> list[str]:
    """Category-specific recommended actions."""
    actions_map = {
        "INFRASTRUCTURE": [
            "Check service health and infrastructure status",
            "Review recent deployment or environment changes",
        ],
        "TEST_DATA": [
            "Verify test data setup and prerequisites",
            "Check shared fixtures and environment state",
        ],
        "AUTOMATION_DEFECT": [
            "Review test code for null checks and timing issues",
            "Update locators if UI has changed",
        ],
        "FLAKY": [
            "Quarantine flaky test to prevent blocking releases",
            "Investigate race conditions and timing dependencies",
        ],
        "PRODUCT_BUG": [
            "Review recent code changes in the affected component",
            "Check assertion details for expected vs actual values",
        ],
    }
    return actions_map.get(category, ["Review error details manually"])
