from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _Span:
    def __init__(self, attributes: dict):
        self.attributes = dict(attributes)
        self.exceptions: list[BaseException] = []

    def set_attribute(self, name, value):
        self.attributes[name] = value

    def record_exception(self, exc):
        self.exceptions.append(exc)


class _SpanManager:
    def __init__(self, span: _Span):
        self.span = span
        self.exit_error = None

    def __enter__(self):
        return self.span

    def __exit__(self, exc_type, exc, traceback):
        self.exit_error = exc
        return False


class _Tracer:
    def __init__(self):
        self.calls: list[tuple[str, _SpanManager]] = []

    def start_as_current_span(self, name, *, attributes):
        manager = _SpanManager(_Span(attributes))
        self.calls.append((name, manager))
        return manager


class _Inner:
    max_tokens = 100

    async def ainvoke(self, *_args, **_kwargs):
        return SimpleNamespace(
            content="ok",
            usage_metadata={"input_tokens": 21, "output_tokens": 8},
            response_metadata={},
        )


@pytest.mark.asyncio
async def test_step_span_carries_required_gen_ai_attributes_from_real_llm_boundary(
    monkeypatch,
):
    from app.core.metrics import agent_invocations_total
    from app.core.config import settings
    from app.services import agent_step_tracing as tracing
    from app.services import llm_circuit_breaker
    from app.services.llm_factory import BudgetedLLM
    from app.services.pipeline_budget_service import (
        reset_pipeline_budget_context,
        set_pipeline_budget_context,
    )

    tracer = _Tracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda _name: tracer)
    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", AsyncMock())
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", AsyncMock())

    budget_token = set_pipeline_budget_context(stage_name="summary")
    metric = agent_invocations_total.labels(
        agent="agent.summary.v1", tier="slm", status="success"
    )
    before = metric._value.get()
    try:
        with tracing.trace_agent_step(
            "summary", {"pipeline_run_id": "run-1", "_attempt": 3}
        ):
            result = await BudgetedLLM(
                _Inner(), provider="openai", model="gpt-test"
            ).ainvoke("prompt")
    finally:
        reset_pipeline_budget_context(budget_token)

    assert result.content == "ok"
    assert metric._value.get() == before + 1
    assert len(tracer.calls) == 1
    name, manager = tracer.calls[0]
    assert name == "testlookup.agent.step"
    assert manager.exit_error is None
    assert manager.span.attributes == {
        "gen_ai.operation.name": "agent_step",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-test",
        "gen_ai.usage.input_tokens": 21,
        "gen_ai.usage.output_tokens": 8,
        "testlookup.agent_id": "agent.summary.v1",
        "testlookup.llm_calls": 1,
        "testlookup.pipeline_run_id": "run-1",
        "testlookup.stage.name": "summary",
        "testlookup.step.outcome": "success",
        "tier": "slm",
        "attempt": 3,
    }


def test_step_span_records_error_and_deterministic_zero_usage(monkeypatch):
    from app.core.metrics import agent_invocations_total
    from app.services import agent_step_tracing as tracing

    tracer = _Tracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda _name: tracer)
    error = RuntimeError("broken stage")
    metric = agent_invocations_total.labels(
        agent="agent.ingestion.v1", tier="deterministic", status="error"
    )
    before = metric._value.get()

    with pytest.raises(RuntimeError, match="broken stage"):
        with tracing.trace_agent_step("ingestion", {"pipeline_run_id": "run-2"}):
            raise error

    _name, manager = tracer.calls[0]
    assert manager.exit_error is error
    assert manager.span.attributes["gen_ai.request.model"] == "deterministic"
    assert manager.span.attributes["gen_ai.usage.input_tokens"] == 0
    assert manager.span.attributes["gen_ai.usage.output_tokens"] == 0
    assert manager.span.attributes["tier"] == "deterministic"
    assert manager.span.attributes["attempt"] == 1
    assert manager.span.attributes["testlookup.step.outcome"] == "error"
    assert metric._value.get() == before + 1


@pytest.mark.asyncio
async def test_checkpoint_wrapper_traces_skipped_and_executed_step_paths(monkeypatch):
    from app.agents import workflow

    observed: list[tuple[str, int]] = []
    node_calls = 0

    @contextmanager
    def capture(stage_name, state):
        observed.append((stage_name, state["_attempt"]))
        yield

    async def node(_state):
        nonlocal node_calls
        node_calls += 1
        return {"completed_stages": ["ingestion"]}

    monkeypatch.setattr(workflow, "trace_agent_step", capture)
    monkeypatch.setattr(workflow, "_raise_if_pipeline_cancelled", AsyncMock())
    monkeypatch.setattr(workflow, "_mark_stage_restored", AsyncMock())
    monkeypatch.setattr(workflow, "emit_event", AsyncMock())
    wrapped = workflow._make_checkpointed_node(node, "ingestion")
    restored = await wrapped(
        {
            "pipeline_run_id": "run-1",
            "_attempt": 2,
            "_checkpoint_stages": ["ingestion"],
        }
    )
    executed = await wrapped({"pipeline_run_id": "", "_attempt": 3})

    assert restored == {"completed_stages": ["ingestion"], "current_stage": "ingestion"}
    assert executed == {"completed_stages": ["ingestion"]}
    assert node_calls == 1
    assert observed == [("ingestion", 2), ("ingestion", 3)]


@pytest.mark.asyncio
async def test_pipeline_row_attempt_is_forwarded_to_the_graph_state(monkeypatch):
    from app.agents import workflow
    from app.services.llm_cost_budget import CapDecision

    setup = {
        "test_run_id": "run-3",
        "project_id": "project-3",
        "workflow_type": "offline",
        "attempt": 4,
        "initial_workflow_plan": {"stages": []},
        "cluster_child_settings": {},
        "async_decision_report_supersession_enabled": False,
        "contract_agent_settings": {"enabled": False},
        "defect_commander_settings": {"enabled": False},
        "log_intelligence_settings": {"enabled": False},
        "regression_watchman_settings": {"enabled": False},
        "change_ownership_settings": {"enabled": False},
        "resume_attempt": 0,
    }
    monkeypatch.setattr(workflow, "_create_pipeline_run", AsyncMock(return_value=setup))
    monkeypatch.setattr(workflow, "_load_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(
        workflow,
        "_resolve_analysis_mode_snapshot",
        AsyncMock(
            return_value={
                "requested": "rules",
                "resolved": "rules",
                "resolution_reason": "configured",
            }
        ),
    )
    monkeypatch.setattr(workflow, "_persist_execution_context", AsyncMock())
    monkeypatch.setattr(workflow, "_mark_pipeline_done", AsyncMock())
    monkeypatch.setattr(workflow, "emit_event", AsyncMock())
    monkeypatch.setattr(
        "app.services.llm_cost_budget.check_and_apply_cap",
        AsyncMock(return_value=CapDecision(action="UNLIMITED")),
    )
    seen: list[int] = []

    class _Graph:
        async def ainvoke(self, state):
            seen.append(state["_attempt"])
            raise RuntimeError("stop after observing state")

    monkeypatch.setattr(workflow, "_offline_app", _Graph())

    with pytest.raises(RuntimeError, match="stop after observing state"):
        await workflow.run_offline_pipeline(
            test_run_id="run-3", project_id="project-3", build_number="1"
        )
    assert seen == [4]


def test_grafana_provisions_two_search_panels_for_agent_step_traces():
    root = Path(__file__).resolve().parents[3]
    dashboard = json.loads(
        (root / "infra/monitoring/grafana/dashboards/testlookup-overview.json").read_text(
            encoding="utf-8"
        )
    )
    panels = [panel for panel in dashboard["panels"] if panel.get("id") in {50, 51}]

    assert len(panels) == 2
    for panel in panels:
        assert panel["datasource"] == {"type": "jaeger", "uid": "jaeger"}
        assert panel["targets"] == [
            {
                "datasource": {"type": "jaeger", "uid": "jaeger"},
                "limit": 50,
                "operation": "testlookup.agent.step",
                "queryType": "search",
                "refId": "A",
                "service": "testlookup",
                **({"tags": "error=true"} if panel["id"] == 51 else {}),
            }
        ]

    datasource = (
        root / "infra/monitoring/grafana/provisioning/datasources/prometheus.yml"
    ).read_text(encoding="utf-8")
    assert "  - name: Jaeger\n    uid: jaeger\n    type: jaeger\n" in datasource
