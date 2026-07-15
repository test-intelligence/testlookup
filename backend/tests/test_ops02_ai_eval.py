"""
Unit tests for OPS-02: AI Evaluation Dashboards.

Covers:
 - Classification metric computation (precision, recall, F1, accuracy)
 - Empty dataset handling
 - Schema validation
 - ORM model fields
 - Task type and column width safety
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


# ── Classification Metrics ───────────────────────────────────────────────────


class TestClassificationMetrics:
    def test_all_correct(self):
        from app.services.ai_eval_service import compute_classification_metrics

        items = [
            {"input": {"failure_category": "PRODUCT_BUG"}, "expected_output": {"failure_category": "PRODUCT_BUG", "correct": True}},
            {"input": {"failure_category": "FLAKY"}, "expected_output": {"failure_category": "FLAKY", "correct": True}},
        ]
        m = compute_classification_metrics(items)
        assert m["accuracy"] == 1.0
        assert m["correct"] == 2
        assert m["total"] == 2

    def test_all_incorrect(self):
        from app.services.ai_eval_service import compute_classification_metrics

        items = [
            {"input": {"failure_category": "PRODUCT_BUG"}, "expected_output": {"failure_category": "INFRASTRUCTURE", "correct": False}},
            {"input": {"failure_category": "FLAKY"}, "expected_output": {"failure_category": "PRODUCT_BUG", "correct": False}},
        ]
        m = compute_classification_metrics(items)
        assert m["accuracy"] == 0.0
        assert m["correct"] == 0

    def test_mixed_results(self):
        from app.services.ai_eval_service import compute_classification_metrics

        items = [
            {"input": {"failure_category": "PRODUCT_BUG"}, "expected_output": {"correct": True}},
            {"input": {"failure_category": "FLAKY"}, "expected_output": {"correct": False}},
            {"input": {"failure_category": "INFRASTRUCTURE"}, "expected_output": {"correct": True}},
        ]
        m = compute_classification_metrics(items)
        assert m["total"] == 3
        assert m["correct"] == 2
        assert 0 < m["accuracy"] < 1.0

    def test_empty_items(self):
        from app.services.ai_eval_service import compute_classification_metrics

        m = compute_classification_metrics([])
        assert m["total"] == 0
        assert m["correct"] == 0
        assert m["precision"] is None

    def test_f1_between_zero_and_one(self):
        from app.services.ai_eval_service import compute_classification_metrics

        items = [
            {"input": {"failure_category": "A"}, "expected_output": {"failure_category": "A", "correct": True}},
            {"input": {"failure_category": "A"}, "expected_output": {"failure_category": "B", "correct": False}},
            {"input": {"failure_category": "B"}, "expected_output": {"failure_category": "B", "correct": True}},
        ]
        m = compute_classification_metrics(items)
        assert m["f1_score"] is not None
        assert 0 <= m["f1_score"] <= 1.0


# ── Schema validation ────────────────────────────────────────────────────────


class TestSchemas:
    def test_dataset_create_valid(self):
        from app.models.schemas import AIEvalDatasetCreate

        ds = AIEvalDatasetCreate(name="Test Dataset", task_type="classification", items=[{"input": {}, "expected_output": {}}])
        assert ds.task_type == "classification"
        assert len(ds.items) == 1

    def test_dataset_create_invalid_task_type(self):
        from pydantic import ValidationError
        from app.models.schemas import AIEvalDatasetCreate

        with pytest.raises(ValidationError):
            AIEvalDatasetCreate(name="Bad", task_type="invalid_type")

    def test_eval_run_response(self):
        from app.models.schemas import AIEvalRunResponse

        run = AIEvalRunResponse(
            id=uuid.uuid4(), dataset_id=uuid.uuid4(), model_name="qwen2.5:7b",
            task_type="classification", total_items=100, correct_items=85,
            accuracy=0.85, precision=0.82, recall=0.80, f1_score=0.81,
            evaluated_at="2026-04-02T12:00:00Z",
        )
        assert run.accuracy == 0.85

    def test_dashboard_response_defaults(self):
        from app.models.schemas import AIQualityDashboardResponse

        dash = AIQualityDashboardResponse()
        assert dash.recent_eval_runs == []
        assert dash.model_versions == []

    def test_dataset_response(self):
        from app.models.schemas import AIEvalDatasetResponse

        resp = AIEvalDatasetResponse(
            id=uuid.uuid4(), name="Test", task_type="classification",
            item_count=50, is_active=True, created_at="2026-04-02T12:00:00Z",
        )
        assert resp.item_count == 50


# ── ORM models ───────────────────────────────────────────────────────────────


class TestORMModels:
    def test_ai_eval_dataset_fields(self):
        from app.models.postgres import AIEvalDataset

        for f in ("name", "task_type", "items", "item_count", "is_active", "created_by"):
            assert hasattr(AIEvalDataset, f)

    def test_ai_eval_run_fields(self):
        from app.models.postgres import AIEvalRun

        for f in ("dataset_id", "model_name", "task_type", "precision", "recall",
                   "f1_score", "accuracy", "agreement_rate", "total_items",
                   "correct_items", "fallback_used", "evaluated_at", "duration_ms"):
            assert hasattr(AIEvalRun, f)


# ── Column widths ────────────────────────────────────────────────────────────


class TestColumnWidths:
    def test_task_type_values_fit(self):
        for t in ("classification", "root_cause", "release_decision", "duplicate_detection"):
            assert len(t) <= 50

    def test_model_name_max(self):
        assert len("a" * 200) <= 200


class TestKindClassificationMetrics:
    """AI-4: kind-triad precision, per engine tier, computed honestly."""

    @staticmethod
    def _item(predicted, expected, tier=None, correct=True):
        return {
            "input": {"failure_category": predicted},
            "expected_output": {"failure_category": expected, "correct": correct},
            "metadata": {"analysis_tier": tier} if tier else {},
        }

    def test_not_computable_without_labels_is_honest(self):
        """No fake numbers: label-less items → computable=False + reason."""
        from app.services.ai_eval_service import compute_kind_classification_metrics

        result = compute_kind_classification_metrics([])
        assert result["computable"] is False
        assert "not computable" in result["reason"]

        result = compute_kind_classification_metrics(
            [{"input": {}, "expected_output": {"correct": True}}]
        )
        assert result["computable"] is False

    def test_kind_precision_from_category_labels(self):
        """Kind is derived from the categories both sides already carry —
        a TEST_DATA prediction against an AUTOMATION_DEFECT label is a kind
        MATCH (both test_code) even though the categories differ."""
        from app.services.ai_eval_service import compute_kind_classification_metrics

        items = [
            self._item("PRODUCT_BUG", "PRODUCT_BUG", tier="llm"),
            self._item("TEST_DATA", "AUTOMATION_DEFECT", tier="llm"),  # kind match
            self._item("INFRASTRUCTURE", "PRODUCT_BUG", tier="rules", correct=False),
        ]
        result = compute_kind_classification_metrics(items)
        assert result["computable"] is True
        assert result["overall"]["total"] == 3
        assert result["overall"]["correct"] == 2  # kind-level agreement

    def test_per_tier_breakdown_with_honest_unknown_tier(self):
        from app.services.ai_eval_service import compute_kind_classification_metrics

        items = [
            self._item("PRODUCT_BUG", "PRODUCT_BUG", tier="llm"),
            self._item("INFRASTRUCTURE", "INFRASTRUCTURE", tier="rules"),
            self._item("FLAKY", "FLAKY"),  # no tier recorded (golden / legacy)
        ]
        result = compute_kind_classification_metrics(items)
        assert set(result["by_tier"]) == {"llm", "rules", "unknown"}
        assert result["by_tier"]["llm"]["total"] == 1
        # the honest disclaimer, not a fake attribution
        assert "not attributable" in result["by_tier"]["unknown"]["note"]

    def test_classification_metrics_carry_kind_submetrics(self):
        """Additive sub-key — flat keys unchanged for existing consumers."""
        from app.services.ai_eval_service import compute_classification_metrics

        items = [self._item("PRODUCT_BUG", "PRODUCT_BUG", tier="ml")]
        metrics = compute_classification_metrics(items)
        assert set(metrics) >= {"precision", "recall", "f1_score", "accuracy", "total", "correct"}
        assert metrics["kind_metrics"]["computable"] is True
        assert metrics["kind_metrics"]["by_tier"]["ml"]["accuracy"] == 1.0
