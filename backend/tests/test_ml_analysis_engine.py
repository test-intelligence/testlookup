"""
Unit tests for the ML Analysis Engine — LLM-free test intelligence.

Covers:
  - RulesEngine: classification, flakiness detection, summary generation
  - MLFeatureExtractor: feature vector extraction
  - AnalysisRouter: mode dispatch logic
  - MLSummaryGenerator: template-based summary generation
  - Config: ANALYSIS_MODE settings
"""
import json

import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RulesEngine — Classification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.rules_engine import RulesEngine  # noqa: E402


class TestRulesEngineClassification:
    def test_oom_classified_as_infrastructure(self):
        result = RulesEngine.classify_test(error_message="OOMKilled: container exceeded memory limit")
        assert result["failure_category"] == "INFRASTRUCTURE"
        assert result["confidence_score"] >= 60

    def test_connection_refused_classified_as_infrastructure(self):
        result = RulesEngine.classify_test(error_message="Connection refused to payment-service:8080")
        assert result["failure_category"] == "INFRASTRUCTURE"

    def test_assertion_error_classified_as_product_bug(self):
        result = RulesEngine.classify_test(error_message="AssertionError: expected 200 but was 404")
        assert result["failure_category"] == "PRODUCT_BUG"

    def test_null_pointer_classified_as_automation_defect(self):
        result = RulesEngine.classify_test(error_message="NullPointerException at com.test.LoginTest:42")
        assert result["failure_category"] == "AUTOMATION_DEFECT"

    def test_setup_failed_classified_as_test_data(self):
        result = RulesEngine.classify_test(error_message="@BeforeAll setup failed: database not available")
        assert result["failure_category"] == "TEST_DATA"

    def test_unknown_error_returns_unknown(self):
        result = RulesEngine.classify_test(error_message="Something completely novel happened")
        assert result["failure_category"] == "UNKNOWN"
        assert result["confidence_score"] <= 40

    def test_empty_error_returns_unknown(self):
        result = RulesEngine.classify_test(error_message="")
        assert result["failure_category"] == "UNKNOWN"

    def test_none_error_returns_unknown(self):
        result = RulesEngine.classify_test(error_message=None)
        assert result["failure_category"] == "UNKNOWN"

    def test_classified_by_is_rules_engine(self):
        result = RulesEngine.classify_test(error_message="timeout")
        assert result["classified_by"] == "rules_engine"

    def test_result_has_required_fields(self):
        result = RulesEngine.classify_test(error_message="timeout")
        required = {
            "root_cause_summary", "failure_category", "confidence_score",
            "is_flaky", "recommended_actions", "requires_human_review",
        }
        assert required.issubset(set(result.keys()))


class TestRulesEngineFlakiness:
    def test_historical_flaky_detected(self):
        """Test with 50% failure rate over 10 runs is classified as FLAKY."""
        result = RulesEngine.classify_test(
            error_message="intermittent failure",
            history={"pass_count": 5, "fail_count": 5},
        )
        assert result["failure_category"] == "FLAKY"
        assert result["is_flaky"] is True

    def test_always_failing_not_flaky(self):
        """Test that never passes is not flaky."""
        result = RulesEngine.classify_test(
            error_message="always fails",
            history={"pass_count": 0, "fail_count": 10},
        )
        assert result["is_flaky"] is False

    def test_mostly_passing_not_flaky(self):
        """Test with >90% pass rate is not considered flaky."""
        result = RulesEngine.classify_test(
            error_message="rare failure",
            history={"pass_count": 95, "fail_count": 5},
        )
        # 5% failure rate — below 10% threshold
        assert result["failure_category"] != "FLAKY" or result["is_flaky"] is False

    def test_insufficient_history_skips_flakiness(self):
        """Need at least 5 runs for flakiness check."""
        result = RulesEngine.classify_test(
            error_message="unknown",
            history={"pass_count": 1, "fail_count": 1},
        )
        # Only 2 total runs — not enough for flakiness
        assert result["failure_category"] != "FLAKY"


class TestRulesEngineRegression:
    def test_regression_after_passing_streak(self):
        result = RulesEngine.classify_test(
            error_message="new failure",
            history={"consecutive_passes": 5, "pass_count": 10, "fail_count": 0},
        )
        assert result["failure_category"] == "PRODUCT_BUG"
        assert "regression" in result["root_cause_summary"].lower()


class TestRulesEngineDuration:
    def test_slow_test_flagged_as_infrastructure(self):
        result = RulesEngine.classify_test(
            error_message="test failed",
            duration_ms=30000,
            history={"median_duration_ms": 5000, "pass_count": 5, "fail_count": 5},
        )
        # Flakiness takes priority with 50/50 pass/fail, but duration anomaly
        # would trigger if flakiness check didn't match first
        assert result["failure_category"] in ("FLAKY", "INFRASTRUCTURE")


class TestRulesEngineSuiteLevel:
    def test_suite_failure_detected(self):
        result = RulesEngine.classify_test(
            error_message="timeout connecting to DB",
            run_context={"suite_failure_rate": 0.9, "suite_test_count": 10},
        )
        assert result["failure_category"] == "INFRASTRUCTURE"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RulesEngine — Summary Generation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRulesEngineSummary:
    def test_all_green_summary(self):
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 100, "failed_tests": 0, "passed_tests": 100,
                      "skipped_tests": 0, "pass_rate": 100.0, "build_number": "42", "branch": "main"},
            classifications={},
        )
        assert "successfully" in summary["layer1_executive_summary"].lower()
        assert summary["layer2_incident_view"]["release_impact"] == "GO"
        assert summary["layer2_incident_view"]["criticality"] == "LOW"

    def test_failure_summary_has_all_layers(self):
        classifications = {
            "tc1": {"failure_category": "PRODUCT_BUG", "is_flaky": False, "recommended_actions": ["Fix bug"]},
            "tc2": {"failure_category": "INFRASTRUCTURE", "is_flaky": False, "recommended_actions": []},
            "tc3": {"failure_category": "FLAKY", "is_flaky": True, "recommended_actions": []},
        }
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 50, "failed_tests": 3, "passed_tests": 47,
                      "skipped_tests": 0, "pass_rate": 94.0, "build_number": "43", "branch": "develop"},
            classifications=classifications,
        )
        assert "layer1_executive_summary" in summary
        assert "layer2_incident_view" in summary
        assert "layer3_evidence_pack" in summary
        assert "layer4_action_plan" in summary

    def test_failure_breakdown_counts_categories(self):
        classifications = {
            "tc1": {"failure_category": "PRODUCT_BUG", "is_flaky": False},
            "tc2": {"failure_category": "PRODUCT_BUG", "is_flaky": False},
            "tc3": {"failure_category": "FLAKY", "is_flaky": True},
        }
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 50, "failed_tests": 3, "passed_tests": 47,
                      "skipped_tests": 0, "pass_rate": 94.0, "build_number": "44", "branch": "main"},
            classifications=classifications,
        )
        breakdown = summary["layer2_incident_view"]["failure_breakdown"]
        assert breakdown["product_bugs"] == 2
        assert breakdown["flaky"] == 1

    def test_critical_failure_is_no_go(self):
        classifications = {f"tc{i}": {"failure_category": "PRODUCT_BUG", "is_flaky": False} for i in range(20)}
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 50, "failed_tests": 20, "passed_tests": 30,
                      "skipped_tests": 0, "pass_rate": 60.0, "build_number": "45", "branch": "main"},
            classifications=classifications,
        )
        assert summary["layer2_incident_view"]["release_impact"] == "NO_GO"
        assert summary["layer2_incident_view"]["criticality"] == "CRITICAL"

    def test_action_plan_has_owner_hints(self):
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 10, "failed_tests": 2, "passed_tests": 8,
                      "skipped_tests": 0, "pass_rate": 80.0, "build_number": "46", "branch": "main"},
            classifications={"tc1": {"failure_category": "INFRASTRUCTURE", "is_flaky": False}},
        )
        hints = summary["layer4_action_plan"]["owner_hints"]
        assert "qa" in hints
        assert "developer" in hints
        assert "sre" in hints
        assert "release_manager" in hints


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ML Feature Extractor
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.ml.feature_extractor import (  # noqa: E402
    FEATURE_NAMES,
    extract_features,
    features_to_array,
)


class TestFeatureExtractor:
    def test_returns_all_features(self):
        features = extract_features({"error_message": "timeout"})
        assert len(features) == len(FEATURE_NAMES)
        assert set(features.keys()) == set(FEATURE_NAMES)

    def test_all_values_are_numeric(self):
        features = extract_features({"error_message": "NullPointerException"})
        for name, val in features.items():
            assert isinstance(val, (int, float)), f"{name} is {type(val)}"

    def test_timeout_keyword_detected(self):
        features = extract_features({"error_message": "timed out after 30s"})
        assert features["has_timeout_keyword"] == 1.0
        assert features["has_assertion_keyword"] == 0.0

    def test_assertion_keyword_detected(self):
        features = extract_features({"error_message": "AssertionError: expected 5 but was 3"})
        assert features["has_assertion_keyword"] == 1.0

    def test_duration_ratio_calculated(self):
        features = extract_features(
            {"error_message": "", "duration_ms": 10000},
            history={"median_duration_ms": 2000},
        )
        assert features["duration_vs_median_ratio"] == 5.0

    def test_missing_duration_history_returns_minus_one(self):
        features = extract_features({"error_message": ""})
        assert features["duration_vs_median_ratio"] == -1.0

    def test_historical_failure_rate(self):
        features = extract_features(
            {"error_message": ""},
            history={"pass_count": 7, "fail_count": 3},
        )
        assert abs(features["historical_failure_rate"] - 0.3) < 0.01

    def test_new_test_flagged(self):
        features = extract_features(
            {"error_message": ""},
            history={"pass_count": 1, "fail_count": 0},
        )
        assert features["is_new_test"] == 1.0

    def test_established_test_not_flagged(self):
        features = extract_features(
            {"error_message": ""},
            history={"pass_count": 50, "fail_count": 5},
        )
        assert features["is_new_test"] == 0.0

    def test_features_to_array_order(self):
        features = extract_features({"error_message": "timeout"})
        arr = features_to_array(features)
        assert len(arr) == len(FEATURE_NAMES)
        assert arr[0] == features["error_msg_length"]

    def test_error_word_count(self):
        features = extract_features({"error_message": "connection refused to payment service"})
        assert features["error_word_count"] == 5.0

    def test_log_length_feature(self):
        features = extract_features({"error_message": "timeout"})
        import math
        assert abs(features["error_msg_log_length"] - math.log1p(7)) < 0.01

    def test_java_stack_trace_detected(self):
        error = "NullPointerException\n\tat com.example.Foo.bar(Foo.java:42)\n\tat com.example.Main.run(Main.java:10)"
        features = extract_features({"error_message": error})
        assert features["has_real_stack_trace"] == 1.0
        assert features["stack_trace_depth"] >= 2.0

    def test_python_stack_trace_detected(self):
        error = 'Traceback (most recent call last):\n  File "app.py", line 10, in main\nValueError: bad'
        features = extract_features({"error_message": error})
        assert features["has_real_stack_trace"] == 1.0
        assert features["stack_trace_depth"] >= 1.0

    def test_no_stack_trace_plain_error(self):
        features = extract_features({"error_message": "connection refused"})
        assert features["has_real_stack_trace"] == 0.0
        assert features["stack_trace_depth"] == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Analysis Router — Mode Dispatch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.analysis_router import AnalysisMode, classify_test  # noqa: E402


class TestAnalysisRouter:
    @pytest.mark.asyncio
    async def test_rules_mode_returns_classification(self):
        result = await classify_test(
            test_case={"error_message": "Connection refused", "test_name": "test_api"},
            mode=AnalysisMode.RULES,
        )
        assert result["failure_category"] == "INFRASTRUCTURE"
        assert result["classified_by"] == "rules_engine"

    @pytest.mark.asyncio
    async def test_rules_mode_handles_empty_error(self):
        result = await classify_test(
            test_case={"error_message": "", "test_name": "test_empty"},
            mode=AnalysisMode.RULES,
        )
        assert result["failure_category"] == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_ml_mode_falls_back_to_rules_when_no_model(self):
        """ML mode with no trained model should gracefully fall back to rules."""
        result = await classify_test(
            test_case={"error_message": "OOMKilled", "test_name": "test_oom"},
            mode=AnalysisMode.ML,
        )
        # Should fall back to rules since no model is trained
        assert result["failure_category"] == "INFRASTRUCTURE"
        assert result["classified_by"] == "rules_engine"


class TestAnalysisModeConstants:
    def test_mode_values(self):
        assert AnalysisMode.LLM == "llm"
        assert AnalysisMode.ML == "ml"
        assert AnalysisMode.RULES == "rules"
        assert AnalysisMode.AUTO == "auto"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ML Summary Generator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.ml.summary_generator import MLSummaryGenerator  # noqa: E402


class TestMLSummaryGenerator:
    def test_generates_all_four_layers(self):
        classifications = {
            "tc1": {"failure_category": "PRODUCT_BUG", "is_flaky": False, "classified_by": "rules_engine", "confidence_score": 65},
        }
        summary = MLSummaryGenerator.generate(
            run_data={"total_tests": 10, "failed_tests": 1, "passed_tests": 9,
                      "skipped_tests": 0, "pass_rate": 90.0, "build_number": "99", "branch": "main"},
            classifications=classifications,
        )
        assert "layer1_executive_summary" in summary
        assert "layer2_incident_view" in summary
        assert "layer3_evidence_pack" in summary
        assert "layer4_action_plan" in summary

    def test_includes_confidence_note(self):
        classifications = {
            "tc1": {"failure_category": "FLAKY", "is_flaky": True, "classified_by": "ml_classifier", "confidence_score": 87},
        }
        summary = MLSummaryGenerator.generate(
            run_data={"total_tests": 10, "failed_tests": 1, "passed_tests": 9,
                      "skipped_tests": 0, "pass_rate": 90.0, "build_number": "100", "branch": "main"},
            classifications=classifications,
            model_version="20260406",
        )
        exec_summary = summary["layer1_executive_summary"]
        assert "ML classifier" in exec_summary
        assert "20260406" in exec_summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Config — ANALYSIS_MODE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestAnalysisModeConfig:
    def test_config_has_analysis_mode(self):
        from app.core.config import settings
        assert hasattr(settings, "ANALYSIS_MODE")
        assert settings.ANALYSIS_MODE in ("auto", "llm", "ml", "rules")

    def test_config_has_ml_settings(self):
        from app.core.config import settings
        assert hasattr(settings, "ML_MODEL_DIR")
        assert hasattr(settings, "ML_MIN_TRAINING_SAMPLES")
        assert hasattr(settings, "ML_ACCURACY_THRESHOLD")
        assert settings.ML_MIN_TRAINING_SAMPLES > 0
        assert 0 < settings.ML_ACCURACY_THRESHOLD <= 1.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ML model load-contract validation (review/analysis-engine Major)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.ml import classifier as _clf  # noqa: E402


class TestModelContractValidation:
    """_model_contract_ok rejects a persisted model whose feature/label contract
    drifted from the current code, so the router degrades to rules instead of
    emitting silently-wrong categories."""

    def test_accepts_matching_contract(self, tmp_path):
        from types import SimpleNamespace

        (tmp_path / "training_metadata.json").write_text(json.dumps({
            "feature_names": FEATURE_NAMES,
            "category_labels": _clf.CATEGORY_LABELS,
        }))
        model = SimpleNamespace(n_features_in_=len(FEATURE_NAMES))
        assert _clf._model_contract_ok(model, tmp_path / "classifier_v1.joblib") is True

    def test_rejects_feature_count_mismatch(self, tmp_path):
        from types import SimpleNamespace

        model = SimpleNamespace(n_features_in_=len(FEATURE_NAMES) + 1)
        assert _clf._model_contract_ok(model, tmp_path / "classifier_v1.joblib") is False

    def test_rejects_persisted_feature_order_drift(self, tmp_path):
        from types import SimpleNamespace

        (tmp_path / "training_metadata.json").write_text(json.dumps({
            "feature_names": list(reversed(FEATURE_NAMES)),
            "category_labels": _clf.CATEGORY_LABELS,
        }))
        model = SimpleNamespace(n_features_in_=len(FEATURE_NAMES))
        assert _clf._model_contract_ok(model, tmp_path / "classifier_v1.joblib") is False

    def test_rejects_persisted_label_drift(self, tmp_path):
        from types import SimpleNamespace

        (tmp_path / "training_metadata.json").write_text(json.dumps({
            "feature_names": FEATURE_NAMES,
            "category_labels": _clf.CATEGORY_LABELS + ["NEW_CATEGORY"],
        }))
        model = SimpleNamespace(n_features_in_=len(FEATURE_NAMES))
        assert _clf._model_contract_ok(model, tmp_path / "classifier_v1.joblib") is False

    def test_accepts_when_metadata_absent(self, tmp_path):
        """No metadata file → fall back to the feature-count check only (don't
        reject a model just because metadata is missing)."""
        from types import SimpleNamespace

        model = SimpleNamespace(n_features_in_=len(FEATURE_NAMES))
        assert _clf._model_contract_ok(model, tmp_path / "classifier_v1.joblib") is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Trainer class-diversity guard (review/analysis-engine Major)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTrainerClassDiversityGuard:
    """train_classifier must not crash on a single-class label set — it should
    return a clean status instead of a bare sklearn ValueError."""

    @pytest.mark.asyncio
    async def test_single_class_returns_insufficient_diversity(self, monkeypatch):
        pytest.importorskip("sklearn")
        pytest.importorskip("numpy")
        from app.services.ml import trainer

        sample = {name: 0.0 for name in FEATURE_NAMES}
        samples = [dict(sample) for _ in range(300)]
        labels = ["PRODUCT_BUG"] * 300

        async def _fake_gather():
            return samples, labels

        monkeypatch.setattr(trainer, "_gather_training_data", _fake_gather)
        result = await trainer.train_classifier()
        assert result["status"] == "insufficient_class_diversity"
