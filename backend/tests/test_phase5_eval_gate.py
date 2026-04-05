"""
Phase 5: Evaluation as Release Gate Tests.

Tests for:
  - Golden dataset structure and completeness
  - Extended evaluation metrics (root_cause, duplicate_detection, release_decision)
  - Baseline comparison logic
  - Evaluation gate PASS/FAIL/WARN determination
  - Threshold enforcement
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt",
                checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))
        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security",
            verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"),
            create_access_token=MagicMock(return_value="access_token"),
            create_refresh_token=MagicMock(return_value="refresh_token"),
            decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        from sqlalchemy.orm import DeclarativeBase
        class _Base(DeclarativeBase):
            pass
        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres",
            get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo",
            get_mongo_db=MagicMock(), close_mongo=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client",
            get_redis=MagicMock(), close_redis=MagicMock()))
        yield


# ── Golden Dataset Tests ─────────────────────────────────────────────────────


class TestGoldenDatasets:
    """Golden datasets have correct structure and completeness."""

    def test_all_four_categories_present(self):
        from app.services.golden_datasets import GOLDEN_DATASETS
        assert "classification" in GOLDEN_DATASETS
        assert "root_cause" in GOLDEN_DATASETS
        assert "duplicate_detection" in GOLDEN_DATASETS
        assert "release_decision" in GOLDEN_DATASETS

    def test_classification_items_structure(self):
        from app.services.golden_datasets import get_golden_classification_items
        items = get_golden_classification_items()
        assert len(items) >= 10
        for item in items:
            assert "input" in item
            assert "expected_output" in item
            assert "metadata" in item
            assert "failure_category" in item["input"]
            assert "correct" in item["expected_output"]

    def test_root_cause_items_structure(self):
        from app.services.golden_datasets import get_golden_root_cause_items
        items = get_golden_root_cause_items()
        assert len(items) >= 4
        for item in items:
            assert "input" in item
            assert "expected_output" in item
            assert "has_root_cause" in item["expected_output"]

    def test_duplicate_detection_items_structure(self):
        from app.services.golden_datasets import get_golden_duplicate_detection_items
        items = get_golden_duplicate_detection_items()
        assert len(items) >= 5
        for item in items:
            assert "title_a" in item["input"]
            assert "title_b" in item["input"]
            assert "is_duplicate" in item["expected_output"]

    def test_release_decision_items_structure(self):
        from app.services.golden_datasets import get_golden_release_decision_items
        items = get_golden_release_decision_items()
        assert len(items) >= 5
        for item in items:
            assert "risk_score" in item["input"]
            assert "recommendation" in item["expected_output"]

    def test_get_all_golden_datasets_returns_list(self):
        from app.services.golden_datasets import get_all_golden_datasets
        datasets = get_all_golden_datasets()
        assert len(datasets) == 4
        for ds in datasets:
            assert "name" in ds
            assert "task_type" in ds
            assert "items" in ds
            assert ds["item_count"] == len(ds["items"])

    def test_golden_dataset_labels_unique(self):
        from app.services.golden_datasets import get_all_golden_datasets
        for ds in get_all_golden_datasets():
            labels = [item["metadata"]["label"] for item in ds["items"]]
            assert len(labels) == len(set(labels)), f"Duplicate labels in {ds['task_type']}"


# ── Extended Evaluation Metrics Tests ────────────────────────────────────────


class TestClassificationMetrics:
    """Existing classification metrics still work correctly."""

    def test_perfect_classification(self):
        from app.services.ai_eval_service import compute_classification_metrics
        items = [
            {"input": {"failure_category": "PRODUCT_BUG"}, "expected_output": {"correct": True}},
            {"input": {"failure_category": "FLAKY"}, "expected_output": {"correct": True}},
        ]
        m = compute_classification_metrics(items)
        assert m["accuracy"] == 1.0
        assert m["total"] == 2


class TestRootCauseMetrics:
    """Root cause extraction quality metrics."""

    def test_all_correct_root_causes(self):
        from app.services.ai_eval_service import compute_root_cause_metrics
        items = [
            {
                "input": {"root_cause_summary": "NullPointerException", "failure_category": "PRODUCT_BUG"},
                "expected_output": {"has_root_cause": True, "category": "PRODUCT_BUG"},
            },
            {
                "input": {"root_cause_summary": "OOM kill", "failure_category": "INFRASTRUCTURE"},
                "expected_output": {"has_root_cause": True, "category": "INFRASTRUCTURE"},
            },
        ]
        m = compute_root_cause_metrics(items)
        assert m["accuracy"] == 1.0
        assert m["correct"] == 2

    def test_wrong_category_counted_incorrect(self):
        from app.services.ai_eval_service import compute_root_cause_metrics
        items = [
            {
                "input": {"root_cause_summary": "Connection timeout", "failure_category": "PRODUCT_BUG"},
                "expected_output": {"has_root_cause": True, "category": "INFRASTRUCTURE"},
            },
        ]
        m = compute_root_cause_metrics(items)
        assert m["correct"] == 0
        assert m["accuracy"] == 0.0

    def test_empty_items(self):
        from app.services.ai_eval_service import compute_root_cause_metrics
        m = compute_root_cause_metrics([])
        assert m["total"] == 0
        assert m["accuracy"] is None


class TestDuplicateDetectionMetrics:
    """Duplicate detection precision/recall metrics."""

    def test_golden_duplicate_detection(self):
        from app.services.ai_eval_service import compute_duplicate_detection_metrics
        from app.services.golden_datasets import get_golden_duplicate_detection_items
        items = get_golden_duplicate_detection_items()
        m = compute_duplicate_detection_metrics(items)
        assert m["total"] == 6
        assert 0 <= m["accuracy"] <= 1.0
        assert m["precision"] is not None

    def test_all_true_positives(self):
        from app.services.ai_eval_service import compute_duplicate_detection_metrics
        items = [
            {
                "input": {"title_a": "same error same error", "title_b": "same error same error", "component_a": "svc", "component_b": "svc"},
                "expected_output": {"is_duplicate": True, "similarity_threshold": 0.5},
            },
        ]
        m = compute_duplicate_detection_metrics(items)
        assert m["correct"] == 1

    def test_empty_returns_none(self):
        from app.services.ai_eval_service import compute_duplicate_detection_metrics
        m = compute_duplicate_detection_metrics([])
        assert m["total"] == 0


class TestReleaseDecisionMetrics:
    """Release decision GO/NO_GO accuracy metrics."""

    def test_golden_release_decisions(self):
        from app.services.ai_eval_service import compute_release_decision_metrics
        from app.services.golden_datasets import get_golden_release_decision_items
        items = get_golden_release_decision_items()
        m = compute_release_decision_metrics(items)
        assert m["total"] == 6
        # All golden items should be classified correctly by deterministic thresholds
        assert m["accuracy"] == 1.0

    def test_go_threshold(self):
        from app.services.ai_eval_service import compute_release_decision_metrics
        items = [
            {"input": {"risk_score": 10}, "expected_output": {"recommendation": "GO"}},
            {"input": {"risk_score": 19}, "expected_output": {"recommendation": "GO"}},
        ]
        m = compute_release_decision_metrics(items)
        assert m["accuracy"] == 1.0

    def test_nogo_threshold(self):
        from app.services.ai_eval_service import compute_release_decision_metrics
        items = [
            {"input": {"risk_score": 55}, "expected_output": {"recommendation": "NO_GO"}},
            {"input": {"risk_score": 80}, "expected_output": {"recommendation": "NO_GO"}},
        ]
        m = compute_release_decision_metrics(items)
        assert m["accuracy"] == 1.0

    def test_conditional_go_range(self):
        from app.services.ai_eval_service import compute_release_decision_metrics
        items = [
            {"input": {"risk_score": 30}, "expected_output": {"recommendation": "CONDITIONAL_GO"}},
            {"input": {"risk_score": 50}, "expected_output": {"recommendation": "CONDITIONAL_GO"}},
        ]
        m = compute_release_decision_metrics(items)
        assert m["accuracy"] == 1.0


class TestMetricsDispatch:
    """compute_metrics_for_task_type routes to the right function."""

    def test_dispatches_classification(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        items = [{"input": {"failure_category": "A"}, "expected_output": {"correct": True}}]
        m = compute_metrics_for_task_type("classification", items)
        assert "accuracy" in m

    def test_dispatches_root_cause(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        items = [{"input": {"root_cause_summary": "x"}, "expected_output": {"has_root_cause": True}}]
        m = compute_metrics_for_task_type("root_cause", items)
        assert "accuracy" in m

    def test_dispatches_duplicate(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        items = [{"input": {"title_a": "a", "title_b": "b", "component_a": "c", "component_b": "d"}, "expected_output": {"is_duplicate": False, "similarity_threshold": 0.7}}]
        m = compute_metrics_for_task_type("duplicate_detection", items)
        assert "precision" in m

    def test_dispatches_release(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        items = [{"input": {"risk_score": 10}, "expected_output": {"recommendation": "GO"}}]
        m = compute_metrics_for_task_type("release_decision", items)
        assert "accuracy" in m

    def test_unknown_falls_back_to_classification(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        m = compute_metrics_for_task_type("unknown_type", [])
        assert m["total"] == 0


# ── Evaluation Gate Tests ────────────────────────────────────────────────────


class TestEvalGateRules:
    """Evaluation gate rule enforcement."""

    def test_evaluate_rules_pass_with_good_metrics(self):
        from app.services.eval_gate_service import _evaluate_rules

        class FakeBaseline:
            min_accuracy = 0.80
            min_f1 = 0.75
            max_regression_pct = 5.0
            baseline_accuracy = 0.85

        current = {"accuracy": 0.87, "f1_score": 0.82, "total": 10}
        rules = _evaluate_rules(current, FakeBaseline())

        assert all(r["passed"] for r in rules), f"Some rules failed: {rules}"

    def test_evaluate_rules_fail_below_min_accuracy(self):
        from app.services.eval_gate_service import _evaluate_rules

        class FakeBaseline:
            min_accuracy = 0.80
            min_f1 = 0.75
            max_regression_pct = 5.0
            baseline_accuracy = 0.85

        current = {"accuracy": 0.70, "f1_score": 0.82, "total": 10}
        rules = _evaluate_rules(current, FakeBaseline())

        acc_rule = next(r for r in rules if r["rule"] == "min_accuracy")
        assert acc_rule["passed"] is False

    def test_evaluate_rules_fail_regression(self):
        from app.services.eval_gate_service import _evaluate_rules

        class FakeBaseline:
            min_accuracy = 0.70
            min_f1 = 0.60
            max_regression_pct = 5.0
            baseline_accuracy = 0.90

        current = {"accuracy": 0.80, "f1_score": 0.75, "total": 10}
        rules = _evaluate_rules(current, FakeBaseline())

        reg_rule = next(r for r in rules if r["rule"] == "no_regression")
        assert reg_rule["passed"] is False  # 10% drop > 5% tolerance

    def test_evaluate_rules_warn_minor_regression(self):
        from app.services.eval_gate_service import _evaluate_rules

        class FakeBaseline:
            min_accuracy = 0.70
            min_f1 = 0.60
            max_regression_pct = 5.0
            baseline_accuracy = 0.85

        current = {"accuracy": 0.82, "f1_score": 0.75, "total": 10}
        rules = _evaluate_rules(current, FakeBaseline())

        reg_rule = next(r for r in rules if r["rule"] == "no_regression")
        assert reg_rule["passed"] is True  # 3% drop within 5% tolerance

    def test_evaluate_rules_fail_insufficient_data(self):
        from app.services.eval_gate_service import _evaluate_rules

        current = {"accuracy": 0.90, "f1_score": 0.85, "total": 2}
        rules = _evaluate_rules(current, None)

        data_rule = next(r for r in rules if r["rule"] == "sufficient_data")
        assert data_rule["passed"] is False

    def test_no_baseline_uses_defaults(self):
        from app.services.eval_gate_service import _evaluate_rules

        current = {"accuracy": 0.75, "f1_score": 0.70, "total": 10}
        rules = _evaluate_rules(current, None)

        acc_rule = next(r for r in rules if r["rule"] == "default_min_accuracy")
        assert acc_rule["passed"] is True  # 0.75 >= 0.70 default


# ── Baseline Model Tests ────────────────────────────────────────────────────


class TestBaselineModel:
    """AIEvalBaseline ORM model has the right fields."""

    def test_baseline_fields_exist(self):
        from app.models.postgres import AIEvalBaseline
        for field in (
            "task_type", "agent_name", "prompt_version", "model_name",
            "baseline_accuracy", "baseline_precision", "baseline_recall", "baseline_f1",
            "min_accuracy", "min_f1", "max_regression_pct",
            "eval_run_id", "dataset_id", "is_active", "created_by",
        ):
            assert hasattr(AIEvalBaseline, field), f"Missing field: {field}"


# ── Integration: Gate with golden datasets ───────────────────────────────────


class TestGateWithGoldenDatasets:
    """End-to-end: gate evaluation using golden datasets returns meaningful results."""

    def test_classification_gate_returns_results(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        from app.services.eval_gate_service import _evaluate_rules
        from app.services.golden_datasets import get_golden_classification_items

        items = get_golden_classification_items()
        metrics = compute_metrics_for_task_type("classification", items)
        assert metrics["total"] >= 10
        assert metrics["accuracy"] is not None

        rules = _evaluate_rules(metrics, None)
        assert len(rules) >= 1

    def test_release_decision_gate_passes(self):
        from app.services.ai_eval_service import compute_metrics_for_task_type
        from app.services.eval_gate_service import _evaluate_rules
        from app.services.golden_datasets import get_golden_release_decision_items

        items = get_golden_release_decision_items()
        metrics = compute_metrics_for_task_type("release_decision", items)
        assert metrics["accuracy"] == 1.0

        rules = _evaluate_rules(metrics, None)
        # With no baseline, default threshold (0.70) should pass since accuracy is 1.0
        acc_rule = next(r for r in rules if "accuracy" in r["rule"])
        assert acc_rule["passed"] is True
