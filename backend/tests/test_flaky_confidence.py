"""FLK-P3 regression — ML flakiness-confidence model.

Covers the pure feature engineering (train/serve-parity vector), the
never-raise inference wrapper (graceful degradation when no model / a broken
model is present), and the training degrade path. The sklearn-dependent tests
skip cleanly when scikit-learn is unavailable.
"""
from __future__ import annotations

import types

import pytest

from app.services.flaky_signals import IntermittencySignals, compute_intermittency_signals
from app.services.ml import flaky_confidence as fc


def _sig(**kw) -> IntermittencySignals:
    base = dict(
        runs=10, fail_count=4, flip_count=5, status_volatility=0.5,
        error_signature_diversity=0.3, stack_trace_diversity=0.2,
        in_run_retry_rate=0.1, intermittency_label="intermittent_flaky",
    )
    base.update(kw)
    return IntermittencySignals(**base)


class TestFeatureVector:
    def test_has_exactly_the_contract_keys(self):
        feats = fc.build_flaky_feature_vector(0.4, _sig(), [])
        assert set(feats) == set(fc.FLAKY_FEATURE_NAMES)

    def test_carries_signal_values(self):
        feats = fc.build_flaky_feature_vector(0.4, _sig(status_volatility=0.7, flip_count=9), [])
        assert feats["status_volatility"] == 0.7
        assert feats["flip_count"] == 9.0
        assert feats["failure_rate"] == 0.4

    def test_failure_rate_clamped(self):
        assert fc.build_flaky_feature_vector(5.0, _sig(), [])["failure_rate"] == 1.0
        assert fc.build_flaky_feature_vector(-1.0, _sig(), [])["failure_rate"] == 0.0
        assert fc.build_flaky_feature_vector("nan-ish", _sig(), [])["failure_rate"] == 0.0

    def test_failure_rate_trend_increasing(self):
        # Most-recent-first: recent half all FAILED, older half all PASSED → +1.
        recs = [{"status": "FAILED"}] * 4 + [{"status": "PASSED"}] * 4
        assert fc.build_flaky_feature_vector(0.5, _sig(), recs)["failure_rate_trend"] == 1.0

    def test_failure_rate_trend_decreasing(self):
        recs = [{"status": "PASSED"}] * 4 + [{"status": "FAILED"}] * 4
        assert fc.build_flaky_feature_vector(0.5, _sig(), recs)["failure_rate_trend"] == -1.0

    def test_failure_rate_trend_zero_when_too_few(self):
        assert fc.build_flaky_feature_vector(0.5, _sig(), [{"status": "FAILED"}])["failure_rate_trend"] == 0.0

    def test_run_interval_variance_regular_is_low(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recs = [{"status": "FAILED", "created_at": t0 + timedelta(hours=i)} for i in range(8)]
        assert fc.build_flaky_feature_vector(0.5, _sig(), recs)["run_interval_variance"] == 0.0

    def test_run_interval_variance_irregular_is_positive(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        offsets = [0, 1, 100, 101, 5000, 5001]  # bursty
        recs = [{"status": "FAILED", "created_at": t0 + timedelta(hours=h)} for h in offsets]
        assert fc.build_flaky_feature_vector(0.5, _sig(), recs)["run_interval_variance"] > 0.0

    def test_never_raises_on_garbage_records(self):
        for bad in (None, [None, 1, "x"], [{"status": object()}], [{"created_at": "nope"}]):
            feats = fc.build_flaky_feature_vector(0.5, _sig(), bad)
            assert set(feats) == set(fc.FLAKY_FEATURE_NAMES)

    def test_features_to_array_order_and_missing(self):
        arr = fc.flaky_features_to_array({"failure_rate": 0.9})
        assert len(arr) == len(fc.FLAKY_FEATURE_NAMES)
        assert arr[0] == 0.9          # failure_rate is first
        assert arr[1] == 0.0          # missing → 0.0

    def test_features_to_array_coerces_bad_values(self):
        arr = fc.flaky_features_to_array({"failure_rate": "x", "flip_count": None})
        assert all(isinstance(v, float) for v in arr)


class TestInferenceGracefulDegradation:
    def setup_method(self):
        fc._reset_cache_for_tests()

    def test_predict_none_when_no_model(self, monkeypatch, tmp_path):
        # Point at an empty model dir so no flaky_confidence_v*.joblib exists.
        monkeypatch.setattr(fc.settings, "ML_MODEL_DIR", str(tmp_path))
        fc._reset_cache_for_tests()
        assert fc.FlakyConfidenceModel.is_available() is False
        assert fc.FlakyConfidenceModel.predict({"failure_rate": 0.5}) is None

    def test_predict_none_when_model_raises(self, monkeypatch):
        broken = types.SimpleNamespace(
            predict_proba=lambda X: (_ for _ in ()).throw(RuntimeError("boom")),
            classes_=[0, 1],
        )
        monkeypatch.setattr(fc, "_load_model", lambda: broken)
        assert fc.FlakyConfidenceModel.predict({"failure_rate": 0.5}) is None

    def test_predict_returns_unit_interval_with_injected_model(self, monkeypatch):
        class _FakeModel:
            classes_ = [0, 1]
            def predict_proba(self, X):
                return [[0.2, 0.8] for _ in X]
        monkeypatch.setattr(fc, "_load_model", lambda: _FakeModel())
        conf = fc.FlakyConfidenceModel.predict({"failure_rate": 0.5})
        assert conf == 0.8

    def test_predict_handles_single_class_model(self, monkeypatch):
        class _OneClass:
            classes_ = [0]
            def predict_proba(self, X):
                return [[1.0] for _ in X]
        monkeypatch.setattr(fc, "_load_model", lambda: _OneClass())
        conf = fc.FlakyConfidenceModel.predict({"failure_rate": 0.5})
        assert 0.0 <= conf <= 1.0


class TestTrainedModelRoundTrip:
    def test_sklearn_model_predicts_in_unit_interval(self, monkeypatch):
        sk = pytest.importorskip("sklearn.ensemble")
        import numpy as np
        from app.services.ml.flaky_confidence import flaky_features_to_array

        # Synthetic: high-volatility rows are flaky (1), low-volatility are not (0).
        rng = np.random.RandomState(0)
        X, y = [], []
        for _ in range(60):
            vol = rng.uniform(0.5, 1.0)
            X.append(flaky_features_to_array({"failure_rate": 0.5, "status_volatility": vol, "flip_count": 8}))
            y.append(1)
        for _ in range(60):
            vol = rng.uniform(0.0, 0.2)
            X.append(flaky_features_to_array({"failure_rate": 0.9, "status_volatility": vol, "flip_count": 1}))
            y.append(0)
        model = sk.HistGradientBoostingClassifier(max_iter=50, random_state=0).fit(np.array(X), np.array(y))
        monkeypatch.setattr(fc, "_load_model", lambda: model)

        flaky_feats = {"failure_rate": 0.5, "status_volatility": 0.9, "flip_count": 8}
        steady_feats = {"failure_rate": 0.9, "status_volatility": 0.05, "flip_count": 1}
        c_flaky = fc.FlakyConfidenceModel.predict(flaky_feats)
        c_steady = fc.FlakyConfidenceModel.predict(steady_feats)
        assert 0.0 <= c_flaky <= 1.0 and 0.0 <= c_steady <= 1.0
        assert c_flaky > c_steady  # the model learned the separation


class TestTrainingDegradePath:
    async def test_training_without_data_returns_status_string(self):
        # No live DB in unit env → _gather catches and returns [] → insufficient
        # data (or 'error' if sklearn is somehow absent). Never raises.
        result = await fc.train_flaky_confidence_model()
        assert isinstance(result, dict)
        assert result["status"] in {
            "insufficient_data", "insufficient_class_diversity", "error",
        }


class TestFeatureParityWithSignals:
    def test_builder_consumes_compute_intermittency_output(self):
        recs = [
            {"status": "FAILED", "error_message": "Timeout", "stack_trace": "a", "retry_count": 1},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "Conn reset", "stack_trace": "b"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "Timeout", "stack_trace": "a"},
        ]
        signals = compute_intermittency_signals(recs)
        feats = fc.build_flaky_feature_vector(0.6, signals, recs)
        assert feats["status_volatility"] == signals.status_volatility
        assert feats["in_run_retry_rate"] == signals.in_run_retry_rate
        assert feats["stack_trace_diversity"] == signals.stack_trace_diversity
