"""FLK-P3 — ML flakiness-confidence model.

A focused binary model that scores ``is_flaky_confidence`` ∈ [0, 1] for a test
fingerprint from its intermittency signals. It complements (does not replace)
the 6-class triage classifier in ``ml/classifier.py``: that one answers *what
kind of failure*; this one answers *how confident are we this is a flake* on a
flaky-specific feature set, trained on the human quarantine decisions.

Design mirrors ``ml/classifier.py`` + ``ml/trainer.py`` deliberately:
  * versioned ``flaky_confidence_v*.joblib`` artifacts under ``ML_MODEL_DIR``,
  * a module-level cache with periodic hot-swap to the newest version,
  * a persisted feature contract that is validated before a model is trusted,
  * ``joblib`` / ``scikit-learn`` are optional — absent deps or absent model
    degrade to "unavailable" (``predict`` returns ``None``), never an exception.

Ground truth = ``FlakyQuarantineRequest`` outcomes: a QA lead APPROVED
(/QUARANTINED/RE_QUARANTINED) request is a confirmed flake (label 1); a REJECTED
request is a confirmed non-flake (label 0). Undecided lifecycle states
(DETECTED/PROPOSED/EXPIRED) carry no human label and are excluded.

Inference and training build the feature vector through the SAME pure
``build_flaky_feature_vector`` so the model never sees train/serve skew.
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from app.core.config import settings
from app.services.flaky_signals import IntermittencySignals

logger = logging.getLogger("services.ml.flaky_confidence")

# Feature order is the model contract — persisted to metadata and validated on
# load. Append-only; reordering or removing requires retraining every model.
FLAKY_FEATURE_NAMES = [
    "failure_rate",
    "status_volatility",
    "flip_count",
    "error_signature_diversity",
    "failure_rate_trend",
    "run_interval_variance",
    "in_run_retry_rate",
    "stack_trace_diversity",
]

_MODEL_GLOB = "flaky_confidence_v*.joblib"
_METADATA_NAME = "flaky_confidence_metadata.json"

# Statuses that count as a "failure" for the trend computation.
_FAILED = {"FAILED", "BROKEN"}

# Module-level model cache (mirrors ml/classifier.py).
_model: Any = None
_model_version: Optional[str] = None
_model_path: Optional[str] = None
_last_version_check: float = 0.0
_VERSION_CHECK_INTERVAL = 60  # seconds


# ── Feature engineering (pure, never-raise) ──────────────────────────────────


def _norm_status(value: Any) -> str:
    try:
        text = str(value).upper().strip()
    except Exception:
        return "UNKNOWN"
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _to_epoch(value: Any) -> Optional[float]:
    """Best-effort conversion of a created_at value to epoch seconds."""
    if value is None:
        return None
    try:
        # datetime-like
        return float(value.timestamp())
    except Exception:
        pass
    try:
        # already numeric
        return float(value)
    except (TypeError, ValueError):
        return None


def _failure_rate_trend(records: list[Mapping[str, Any]]) -> float:
    """Recent-half failure rate minus older-half failure rate, in [-1, 1].

    ``records`` are most-recent-first (the order ``refresh_flaky_coach`` and the
    training query both produce). A positive value means failures are
    *increasing* (drift toward a regression); near zero means a stable flake.
    """
    n = len(records)
    if n < 4:
        return 0.0
    statuses = [_norm_status(r.get("status")) for r in records]
    half = n // 2
    recent = statuses[:half]          # most recent
    older = statuses[half:]
    recent_rate = sum(1 for s in recent if s in _FAILED) / len(recent) if recent else 0.0
    older_rate = sum(1 for s in older if s in _FAILED) / len(older) if older else 0.0
    return round(recent_rate - older_rate, 4)


def _run_interval_variance(records: list[Mapping[str, Any]]) -> float:
    """Coefficient of variation of inter-run time gaps, clamped to [0, 5].

    Regular cadence (CI on a schedule) → low; bursty/irregular execution →
    high. A scale-free signal so it generalises across projects with different
    run frequencies. Missing/unparseable timestamps degrade to 0.
    """
    epochs = sorted(
        e for e in (_to_epoch(r.get("created_at")) for r in records) if e is not None
    )
    if len(epochs) < 3:
        return 0.0
    gaps = [b - a for a, b in zip(epochs, epochs[1:]) if b - a >= 0]
    if len(gaps) < 2:
        return 0.0
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return 0.0
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    cv = math.sqrt(var) / mean
    return round(min(5.0, max(0.0, cv)), 4)


def build_flaky_feature_vector(
    failure_rate: float,
    signals: IntermittencySignals,
    records: Iterable[Mapping[str, Any]],
) -> dict[str, float]:
    """Assemble the FLK-P3 feature dict from a failure rate, FLK-P1 signals, and
    the per-run window. Pure and never-raise — used by BOTH inference and
    training so the model never sees train/serve skew.
    """
    rows: list[Mapping[str, Any]] = [r for r in (records or []) if isinstance(r, Mapping)]
    try:
        fr = float(failure_rate)
    except (TypeError, ValueError):
        fr = 0.0
    fr = min(1.0, max(0.0, fr))
    return {
        "failure_rate": round(fr, 4),
        "status_volatility": float(signals.status_volatility),
        "flip_count": float(signals.flip_count),
        "error_signature_diversity": float(signals.error_signature_diversity),
        "failure_rate_trend": _failure_rate_trend(rows),
        "run_interval_variance": _run_interval_variance(rows),
        "in_run_retry_rate": float(signals.in_run_retry_rate),
        "stack_trace_diversity": float(signals.stack_trace_diversity),
    }


def flaky_features_to_array(features: Mapping[str, Any]) -> list[float]:
    """Order a feature dict into the model's input vector. Missing → 0.0."""
    out: list[float] = []
    for name in FLAKY_FEATURE_NAMES:
        try:
            out.append(float(features.get(name, 0.0)))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


# ── Inference wrapper ────────────────────────────────────────────────────────


class FlakyConfidenceModel:
    """Stateless flaky-confidence inference wrapper."""

    @staticmethod
    def is_available() -> bool:
        try:
            return any(Path(settings.ML_MODEL_DIR).glob(_MODEL_GLOB))
        except Exception:
            return False

    @staticmethod
    def get_model_info() -> dict:
        meta_path = Path(settings.ML_MODEL_DIR) / _METADATA_NAME
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text())
            except Exception:
                pass
        return {"status": "not_trained", "version": None, "auc": None, "sample_count": 0}

    @staticmethod
    def predict(features: Mapping[str, Any]) -> Optional[float]:
        """Return ``is_flaky_confidence`` ∈ [0, 1], or ``None`` when no model is
        available. Never raises — a degraded model/feature row yields ``None``
        so the caller falls back to the deterministic Wilson/intermittency
        signals.
        """
        model = _load_model()
        if model is None:
            return None
        try:
            arr = [flaky_features_to_array(features)]
            proba = model.predict_proba(arr)[0]
            # Positive class (flaky=1) is the second column for a 2-class model
            # trained with labels [0, 1]; guard degenerate single-class models.
            classes = list(getattr(model, "classes_", [0, 1]))
            if 1 in classes:
                conf = float(proba[classes.index(1)])
            else:
                conf = float(max(proba))
            if not math.isfinite(conf):
                return None
            return round(min(1.0, max(0.0, conf)), 4)
        except Exception as exc:
            logger.debug("Flaky-confidence inference failed: %s", exc)
            return None


def _load_model() -> Any:
    global _model, _model_version, _model_path, _last_version_check
    now = time.monotonic()
    if _model is not None:
        if (now - _last_version_check) < _VERSION_CHECK_INTERVAL:
            return _model
        _last_version_check = now
        latest = _find_latest_model()
        if latest and str(latest) != _model_path:
            loaded = _try_load(latest)
            if loaded is not None:
                return loaded
        return _model
    latest = _find_latest_model()
    if latest is None:
        return None
    return _try_load(latest)


def _find_latest_model() -> Optional[Path]:
    try:
        files = sorted(Path(settings.ML_MODEL_DIR).glob(_MODEL_GLOB), reverse=True)
    except Exception:
        return None
    return files[0] if files else None


def _try_load(path: Path) -> Any:
    global _model, _model_version, _model_path, _last_version_check
    try:
        import joblib
    except ImportError:
        logger.warning("joblib not installed — flaky-confidence model unavailable")
        return None
    try:
        loaded = joblib.load(path)
    except Exception as exc:
        logger.error("Failed to load flaky-confidence model %s: %s", path, exc)
        return None
    if not _model_contract_ok(loaded, path):
        return None
    _model = loaded
    _model_version = path.stem.split("_v")[-1]
    _model_path = str(path)
    _last_version_check = time.monotonic()
    logger.info("Loaded flaky-confidence model: %s", path.name)
    return _model


def _model_contract_ok(loaded: Any, path: Path) -> bool:
    """Reject a model whose feature width / persisted feature_names no longer
    match the current contract — otherwise it would score against a misaligned
    vector and silently mis-rank.
    """
    n_in = getattr(loaded, "n_features_in_", None)
    if n_in is not None and n_in != len(FLAKY_FEATURE_NAMES):
        logger.warning(
            "Rejecting flaky-confidence model %s: expects %s features, code provides %s",
            path.name, n_in, len(FLAKY_FEATURE_NAMES),
        )
        return False
    meta_path = path.parent / _METADATA_NAME
    if meta_path.exists():
        try:
            persisted = json.loads(meta_path.read_text()).get("feature_names")
        except Exception:
            persisted = None
        if persisted is not None and persisted != FLAKY_FEATURE_NAMES:
            logger.warning(
                "Rejecting flaky-confidence model %s: persisted feature_names differ",
                path.name,
            )
            return False
    return True


def _reset_cache_for_tests() -> None:
    """Test hook — clear the module-level cache so a fresh model is loaded."""
    global _model, _model_version, _model_path, _last_version_check
    _model = None
    _model_version = None
    _model_path = None
    _last_version_check = 0.0


# ── Training ─────────────────────────────────────────────────────────────────

# Quarantine outcomes that constitute a confirmed-flaky (1) vs confirmed-not (0)
# human label. Undecided states carry no label and are excluded from training.
_LABEL_FLAKY = {"APPROVED", "QUARANTINED", "RE_QUARANTINED"}
_LABEL_NOT_FLAKY = {"REJECTED"}


async def train_flaky_confidence_model() -> dict[str, Any]:
    """Train the flaky-confidence model from human quarantine decisions.

    Returns a status dict mirroring ``ml/trainer.train_classifier``. Degrades
    gracefully (status strings, never an exception) when deps/data are missing.
    """
    try:
        import joblib
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import roc_auc_score
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        return {"status": "error", "message": f"ML dependencies not installed: {exc}."}

    samples, labels = await _gather_flaky_training_data()
    if len(samples) < settings.ML_MIN_TRAINING_SAMPLES:
        return {
            "status": "insufficient_data",
            "message": f"Need {settings.ML_MIN_TRAINING_SAMPLES} labeled quarantine decisions, have {len(samples)}.",
            "sample_count": len(samples),
        }
    if len(set(labels)) < 2:
        return {
            "status": "insufficient_class_diversity",
            "message": "Need both APPROVED and REJECTED quarantine decisions to train.",
            "sample_count": len(samples),
        }

    X = np.array([flaky_features_to_array(s) for s in samples])
    y = np.array(labels)

    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    can_stratify = min(n_pos, n_neg) >= 2

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y if can_stratify else None,
    )

    # Balance the (usually skewed) approve/reject classes by per-sample weights.
    pos_w = len(y_train) / (2 * max(1, int(y_train.sum())))
    neg_w = len(y_train) / (2 * max(1, int(len(y_train) - y_train.sum())))
    sample_weight = np.where(y_train == 1, pos_w, neg_w)

    model = HistGradientBoostingClassifier(
        max_iter=300, max_depth=6, learning_rate=0.05, min_samples_leaf=5,
        l2_regularization=0.1, max_bins=128, early_stopping=True,
        n_iter_no_change=15, validation_fraction=0.15, random_state=42,
    )
    model.fit(X_train, y_train, sample_weight=sample_weight)

    # AUC is the deploy gate — accuracy is misleading on an imbalanced flake set.
    try:
        proba_test = model.predict_proba(X_test)
        classes = list(model.classes_)
        pos_col = classes.index(1) if 1 in classes else len(classes) - 1
        auc = float(roc_auc_score(y_test, proba_test[:, pos_col])) if len(set(y_test.tolist())) > 1 else 0.0
    except Exception:
        auc = 0.0

    importances: dict[str, float] = {}
    if hasattr(model, "feature_importances_"):
        for name, imp in sorted(
            zip(FLAKY_FEATURE_NAMES, model.feature_importances_), key=lambda x: -x[1]
        ):
            importances[name] = round(float(imp), 4)

    from datetime import datetime, timezone
    import os
    now = datetime.now(timezone.utc)
    version = now.strftime("%Y%m%d_%H%M%S")
    model_dir = Path(settings.ML_MODEL_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)

    if auc < settings.ML_ACCURACY_THRESHOLD:
        logger.warning("Flaky-confidence AUC %.3f below threshold %.3f — not deployed", auc, settings.ML_ACCURACY_THRESHOLD)
        return {
            "status": "below_threshold", "auc": round(auc, 4),
            "threshold": settings.ML_ACCURACY_THRESHOLD, "sample_count": len(samples),
        }

    model_path = model_dir / f"flaky_confidence_v{version}.joblib"
    tmp_model = model_path.with_suffix(".joblib.tmp")
    joblib.dump(model, tmp_model)
    os.replace(tmp_model, model_path)

    metadata = {
        "status": "trained", "version": version, "auc": round(auc, 4),
        "sample_count": len(samples), "positive_count": n_pos, "negative_count": n_neg,
        "feature_names": FLAKY_FEATURE_NAMES, "feature_importance": importances,
        "trained_at": now.isoformat(),
    }
    meta_path = model_dir / _METADATA_NAME
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(metadata, indent=2, default=str))
    os.replace(tmp_meta, meta_path)

    # Re-audit M14: every other pod gets this version from the store.
    from app.services.ml import model_store

    await model_store.publish(model_path, meta_path)

    logger.info("Flaky-confidence model saved: %s (auc=%.3f, n=%d)", model_path.name, auc, len(samples))
    return {
        "status": "trained", "auc": round(auc, 4), "version": version,
        "sample_count": len(samples), "feature_importance": importances,
    }


async def _gather_flaky_training_data() -> tuple[list[dict], list[int]]:
    """Build labeled feature vectors from human quarantine decisions.

    For each APPROVED/REJECTED ``FlakyQuarantineRequest`` we recompute the
    intermittency signals + feature vector from that fingerprint's windowed
    history (same code path as inference), so the label attaches to the exact
    signal shape the model serves on. Returns ``(features, labels)``.
    """
    samples: list[dict] = []
    labels: list[int] = []
    try:
        import uuid as _uuid
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func as sa_func, select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import (
            FlakyQuarantineRequest,
            TestCase,
            TestCaseHistory,
            TestRun,
        )
        from app.services.flaky_signals import compute_intermittency_signals

        async with AsyncSessionLocal() as db:
            decided = (await db.execute(
                select(
                    FlakyQuarantineRequest.project_id,
                    FlakyQuarantineRequest.test_fingerprint,
                    FlakyQuarantineRequest.status,
                ).where(
                    FlakyQuarantineRequest.status.in_(
                        sorted(_LABEL_FLAKY | _LABEL_NOT_FLAKY)
                    )
                )
            )).all()

            cutoff = datetime.now(timezone.utc) - timedelta(days=90)
            for row in decided:
                pid: _uuid.UUID = row.project_id
                fp: str = row.test_fingerprint
                if not fp:
                    continue
                _rn = sa_func.row_number().over(
                    partition_by=TestCaseHistory.test_fingerprint,
                    order_by=TestCaseHistory.created_at.desc(),
                ).label("rn")
                ranked = (
                    select(
                        TestCaseHistory.status.label("status"),
                        TestCaseHistory.created_at.label("created_at"),
                        TestCase.error_message.label("error_message"),
                        TestCase.stack_trace.label("stack_trace"),
                        TestCase.retry_count.label("retry_count"),
                        TestCase.is_flaky_run.label("is_flaky_run"),
                        _rn,
                    )
                    .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
                    .join(TestCase, TestCase.id == TestCaseHistory.test_case_id, isouter=True)
                    .where(
                        TestRun.project_id == pid,
                        TestCaseHistory.test_fingerprint == fp,
                        TestCaseHistory.created_at >= cutoff,
                    )
                    .subquery()
                )
                window = (await db.execute(
                    select(
                        ranked.c.status, ranked.c.created_at, ranked.c.error_message,
                        ranked.c.stack_trace, ranked.c.retry_count, ranked.c.is_flaky_run,
                    ).where(ranked.c.rn <= 30).order_by(ranked.c.created_at.desc())
                )).all()
                if len(window) < 3:
                    continue
                records = [
                    {
                        "status": w.status, "created_at": w.created_at,
                        "error_message": w.error_message, "stack_trace": w.stack_trace,
                        "retry_count": w.retry_count, "is_flaky_run": w.is_flaky_run,
                    }
                    for w in window
                ]
                statuses = [_norm_status(r["status"]) for r in records]
                total = len(statuses)
                failed = sum(1 for s in statuses if s in _FAILED)
                signals = compute_intermittency_signals(records)
                features = build_flaky_feature_vector(failed / total, signals, records)
                samples.append(features)
                labels.append(1 if row.status in _LABEL_FLAKY else 0)
    except Exception as exc:
        logger.error("Failed to gather flaky training data: %s", exc)
    return samples, labels
