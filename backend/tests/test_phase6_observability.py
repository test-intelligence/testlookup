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


# ── Cost Estimation Tests ────────────────────────────────────────────────────


class TestCostEstimation:
    """Per-provider token cost estimation."""

    def test_ollama_is_free(self):
        from app.services.agent_cost_service import estimate_cost
        cost = estimate_cost("ollama", 1000, 500)
        assert cost == 0.0

    def test_openai_nonzero(self):
        from app.services.agent_cost_service import estimate_cost
        cost = estimate_cost("openai", 1000, 500)
        assert cost > 0
        # 1K input @ $0.005 + 0.5K output @ $0.015 = $0.005 + $0.0075 = $0.0125
        assert abs(cost - 0.0125) < 0.001

    def test_gemini_cost(self):
        from app.services.agent_cost_service import estimate_cost
        cost = estimate_cost("gemini", 2000, 1000)
        assert cost > 0
        # 2K input @ $0.00125 + 1K output @ $0.005 = $0.0025 + $0.005 = $0.0075
        assert abs(cost - 0.0075) < 0.001

    def test_unknown_provider_uses_default(self):
        from app.services.agent_cost_service import estimate_cost
        cost = estimate_cost("some_new_provider", 1000, 500)
        assert cost > 0  # Default rates applied

    def test_zero_tokens_zero_cost(self):
        from app.services.agent_cost_service import estimate_cost
        cost = estimate_cost("openai", 0, 0)
        assert cost == 0.0

    def test_lmstudio_is_free(self):
        from app.services.agent_cost_service import estimate_cost
        cost = estimate_cost("lmstudio", 5000, 2000)
        assert cost == 0.0


# ── Error Classification Tests ───────────────────────────────────────────────


class TestErrorClassification:
    """Error taxonomy classification from error messages."""

    def test_timeout(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("Request timed out after 30s") == "timeout"

    def test_token_limit(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("Maximum context length exceeded: 4096 tokens") == "token_limit"

    def test_transient_connection(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("Connection refused to localhost:11434") == "transient"

    def test_provider_rate_limit(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("Rate limit exceeded (429 Too Many Requests)") == "provider_error"

    def test_permanent_parse_error(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("JSON parse error: invalid syntax") == "permanent"

    def test_empty_string_unknown(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("") == "unknown"

    def test_generic_error_permanent(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("Something completely unexpected happened") == "permanent"

    def test_dns_failure_transient(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("DNS resolution failed for api.example.com") == "transient"

    def test_503_transient(self):
        from app.services.agent_cost_service import classify_error
        assert classify_error("HTTP 503 Service Unavailable from LLM provider") == "transient"


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
