"""
Phase 6: Observability and Cost Control Tests.

Tests for:
  - Cost estimation per provider
  - Error classification taxonomy
  - BaseAgent mark_stage_done observability kwargs
  - Prometheus metric registration
  - AgentStageResult observability fields
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


# Note: TestCostEstimation and TestErrorClassification classes were removed
# in item #10 cleanup. The ``estimate_cost`` and ``classify_error`` helpers
# they covered had no production callers — only their own unit tests. They
# were removed from agent_cost_service.py at the same time.


# ── Prometheus Metrics Registration ──────────────────────────────────────────


class TestPrometheusMetrics:
    """New Phase 6 Prometheus metrics are registered."""

    def test_stage_tokens_metric_exists(self):
        from app.core.metrics import pipeline_stage_tokens_total
        assert pipeline_stage_tokens_total is not None

    def test_stage_cost_metric_exists(self):
        from app.core.metrics import pipeline_stage_cost_usd
        assert pipeline_stage_cost_usd is not None

    def test_stage_llm_calls_metric_exists(self):
        from app.core.metrics import pipeline_stage_llm_calls_total
        assert pipeline_stage_llm_calls_total is not None

    def test_fallback_metric_exists(self):
        from app.core.metrics import pipeline_fallback_total
        assert pipeline_fallback_total is not None

    def test_errors_by_category_metric_exists(self):
        from app.core.metrics import pipeline_stage_errors_by_category
        assert pipeline_stage_errors_by_category is not None


# ── AgentStageResult Observability Fields ────────────────────────────────────


class TestStageResultFields:
    """AgentStageResult ORM model has the Phase 6 observability fields."""

    def test_token_fields(self):
        from app.models.postgres import AgentStageResult
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            assert hasattr(AgentStageResult, field), f"Missing: {field}"

    def test_cost_field(self):
        from app.models.postgres import AgentStageResult
        assert hasattr(AgentStageResult, "cost_usd")

    def test_llm_calls_count_field(self):
        from app.models.postgres import AgentStageResult
        assert hasattr(AgentStageResult, "llm_calls_count")

    def test_error_category_field(self):
        from app.models.postgres import AgentStageResult
        assert hasattr(AgentStageResult, "error_category")

    def test_confidence_field(self):
        from app.models.postgres import AgentStageResult
        assert hasattr(AgentStageResult, "confidence_score")

    def test_evidence_count_field(self):
        from app.models.postgres import AgentStageResult
        assert hasattr(AgentStageResult, "evidence_count")

    def test_route_rationale_field(self):
        from app.models.postgres import AgentStageResult
        assert hasattr(AgentStageResult, "route_rationale")


# ── WorkflowState stage_metrics ──────────────────────────────────────────────


class TestWorkflowState:
    """WorkflowState has stage_metrics field."""

    def test_stage_metrics_in_state(self):
        from app.agents.state import WorkflowState
        hints = WorkflowState.__annotations__
        assert "stage_metrics" in hints


# ── Agent Observability Summary ──────────────────────────────────────────────


class TestAgentObservabilitySummary:
    """Decision-centric observability rollups are deterministic."""

    def test_summary_rolls_up_cost_latency_fallback_errors_and_quality(self):
        from datetime import datetime, timedelta, timezone

        from app.services.agent_cost_service import build_agent_observability_summary

        now = datetime.now(timezone.utc)
        stages = [
            types.SimpleNamespace(
                stage_name="analysis",
                status="completed",
                started_at=now,
                completed_at=now + timedelta(seconds=2),
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                llm_calls_count=1,
                cost_usd=0.0123,
                fallback_used=False,
                fallback_reason=None,
                error_category=None,
                confidence_score=90,
                evidence_count=3,
                route_rationale="failed tests found",
            ),
            types.SimpleNamespace(
                stage_name="summary",
                status="failed",
                started_at=now + timedelta(seconds=3),
                completed_at=now + timedelta(seconds=6),
                input_tokens=200,
                output_tokens=75,
                total_tokens=275,
                llm_calls_count=1,
                cost_usd=0.045,
                fallback_used=True,
                fallback_reason="llm_timeout",
                error_category="timeout",
                confidence_score=60,
                evidence_count=5,
                route_rationale="summary required",
            ),
        ]

        summary = build_agent_observability_summary(
            stages,
            cost_summary={
                "total_input_tokens": 300,
                "total_output_tokens": 125,
                "total_tokens": 425,
                "total_llm_calls": 2,
                "total_cost_usd": 0.0573,
            },
            alerts=[{"type": "repeated_failure"}],
        )

        assert summary["stage_count"] == 2
        assert summary["latency"]["total_stage_duration_seconds"] == 5
        assert summary["tokens"]["total"] == 425
        assert summary["cost"]["total_usd"] == 0.0573
        assert summary["fallback"]["count"] == 1
        assert summary["errors"]["by_category"] == {"timeout": 1}
        assert summary["quality"]["avg_confidence_score"] == 75
        assert summary["quality"]["total_evidence_count"] == 8
        assert summary["alerts"]["by_type"] == {"repeated_failure": 1}
        assert summary["per_agent"][0]["route_rationale"] == "failed tests found"


# ── Alert Thresholds ─────────────────────────────────────────────────────────


class TestAlertThresholds:
    """Alert threshold constants are sensible."""

    def test_consecutive_failures_threshold(self):
        from app.services.agent_cost_service import ALERT_CONSECUTIVE_FAILURES
        assert ALERT_CONSECUTIVE_FAILURES >= 2
        assert ALERT_CONSECUTIVE_FAILURES <= 10

    def test_cost_budget_positive(self):
        from app.services.agent_cost_service import ALERT_COST_BUDGET_USD
        assert ALERT_COST_BUDGET_USD > 0

    def test_spike_factor_reasonable(self):
        from app.services.agent_cost_service import ALERT_COST_SPIKE_FACTOR
        assert ALERT_COST_SPIKE_FACTOR >= 2.0
        assert ALERT_COST_SPIKE_FACTOR <= 10.0
