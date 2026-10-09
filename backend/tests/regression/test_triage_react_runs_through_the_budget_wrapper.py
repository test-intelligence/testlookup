"""Regression: the ReAct triage slow path could not run with the model get_llm() returns.

``run_triage_agent`` handed ``get_llm()``'s result -- a ``BudgetedLLM``, which
is not a LangChain ``Runnable`` -- to ``create_react_agent``, which raised
"Expected a Runnable, callable or dict" every time the slow path was reached.
The router caught it and fell back to rules; the homelab's ``ai_analysis``
holds one such analysis (2026-09-11, fallback_reason "llm_error: TypeError:
Expected a Runnable ..."), and building the agent in the backend pod
reproduced it on 2026-10-09. The Ask-AI chat had the same crash. No test saw
either: they patched ``get_llm`` with a raw chat model.

These tests drive ``run_triage_agent`` with the REAL ``BudgetedLLM`` around a
scripted model; only the gates' stores are stubbed.

Also pinned: the "model not installed" fallback passed ``stack_trace=`` to
``RulesEngine.classify_test``, which takes no such argument, so it always
ended in the canned stub instead of the rules analysis it logs.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

import app.services.agent as triage
from app.core.config import settings
from app.services import llm_circuit_breaker, llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM, BudgetedRunnable

pytestmark = pytest.mark.regression

FINAL = json.dumps({
    "root_cause_summary": "The flakiness history shows a real regression in refunds.",
    "failure_category": "PRODUCT_BUG",
    "backend_error_found": True,
    "pod_issue_found": False,
    "is_flaky": False,
    "confidence_score": 82,
    "recommended_actions": ["Fix the refund state machine"],
    "role_actions": {},
    "evidence_references": [],
})


@tool
def check_test_flakiness(test_name: str) -> str:
    """How often the test failed recently."""
    return f"{test_name}: failed 1 of the last 12 runs."


@pytest.fixture
def gates(monkeypatch):
    calls: dict = {"reserve": 0, "failure": []}

    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        calls["reserve"] += 1
        return Reservation(key="k", project_id="p", estimated_usd=0.01, ttl_seconds=60)

    async def settle(*_a, **_k):
        return None

    async def record_failure(provider, base_url, exc):
        calls["failure"].append(exc)

    async def record_success(provider, base_url):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(record_failure))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(record_success))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", settle)
    monkeypatch.setattr(triage, "_store_audit_trail", AsyncMock())
    monkeypatch.setattr(triage, "_emit_event", AsyncMock())
    monkeypatch.setattr(triage, "_get_tools", lambda: [check_test_flakiness])

    async def passes(score):
        return {"passed": True, "threshold": 70, "score": score}

    monkeypatch.setattr("app.services.confidence_gate.check_confidence", passes)
    return calls


def _wrapped(responses: list[str]) -> BudgetedLLM:
    return BudgetedLLM(FakeListChatModel(responses=responses), provider="openrouter", model="scripted")


async def _triage(monkeypatch, llm) -> dict:
    async def get_llm(*_a, **_k):
        return llm

    monkeypatch.setattr(triage, "get_llm", get_llm)
    return await triage.run_triage_agent(
        test_case_id="tc-1",
        test_name="test_refund_flow",
        error_message="AssertionError: refund status 'pending', expected 'completed'",
        skip_fast_classifier=True,
        skip_cache=True,
    )


@pytest.mark.asyncio
async def test_the_react_slow_path_runs_through_the_budgeted_wrapper(monkeypatch, gates):
    llm = _wrapped([
        "Thought: check its history first\nAction: check_test_flakiness\nAction Input: test_refund_flow",
        f"Thought: I now know the final answer\nFinal Answer: {FINAL}",
    ])
    analysis = await _triage(monkeypatch, llm)

    assert analysis["_routing"]["execution_path"] == "react_agent"
    assert analysis["_routing"]["mode_used"] == "llm"
    assert analysis["_routing"]["fallback_reason"] is None
    assert analysis["failure_category"] == "PRODUCT_BUG"
    assert analysis["confidence_score"] == 82
    assert analysis["tools_used"] == ["check_test_flakiness"]
    # Both model calls went through the wrapper's gates.
    assert gates["reserve"] == 2


@pytest.mark.asyncio
async def test_a_tool_name_in_backticks_still_runs_the_tool(monkeypatch, gates):
    """Measured: the model wrote ``Action: `fetch_allure_stacktrace` `` and the
    executor answered "not a valid tool" until the iteration cap."""
    llm = _wrapped([
        "Thought: check its history first\nAction: `check_test_flakiness`\nAction Input: `test_refund_flow`",
        f"Thought: I now know the final answer\nFinal Answer: {FINAL}",
    ])
    analysis = await _triage(monkeypatch, llm)
    assert analysis["tools_used"] == ["check_test_flakiness"]
    assert analysis["failure_category"] == "PRODUCT_BUG"


@pytest.mark.asyncio
async def test_a_construction_failure_is_a_recorded_fallback_not_an_exception(monkeypatch, gates):
    import langchain.agents as agents

    def broken(**_k):
        raise TypeError("Expected a Runnable, callable or dict")

    monkeypatch.setattr(agents, "create_react_agent", broken)
    analysis = await _triage(monkeypatch, _wrapped(["unused"]))

    assert analysis["_routing"]["fallback_from"] == "llm"
    assert analysis["_routing"]["fallback_reason"] == "llm_error"
    assert "Expected a Runnable" in analysis["root_cause_summary"]


@pytest.mark.asyncio
async def test_a_missing_model_falls_back_to_the_rules_engine(monkeypatch, gates):
    class ModelMissing(FakeListChatModel):
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("model 'qwen2.5:7b' not found, try pulling it first")

    llm = BudgetedLLM(ModelMissing(responses=["x"]), provider="ollama", model="qwen2.5:7b")
    analysis = await _triage(monkeypatch, llm)

    assert analysis["analysis_engine"] == "rules"
    assert analysis["_routing"]["mode_used"] == "rules"
    assert analysis["_routing"]["fallback_reason"] == "llm_model_not_available"


@tool
async def query_logs(service_name: str, timestamp_utc: str) -> str:
    """Backend errors around the failure time."""
    return f"{service_name} @ {timestamp_utc}: 3 HTTP 500s from refund-service"


@pytest.mark.asyncio
async def test_a_tool_with_several_arguments_takes_the_json_the_model_writes(monkeypatch, gates):
    """The first real run of the slow path (homelab pod, 2026-10-09) ended on
    "timestamp_utc: Field required": the ReAct agent sends ONE string, and the
    model's JSON object went to the first argument only."""
    monkeypatch.setattr(triage, "_get_tools", lambda: [triage._single_input(query_logs)])
    llm = _wrapped([
        'Thought: check the backend\nAction: query_logs\n'
        'Action Input: {"service_name": "refunds", "timestamp_utc": "2026-08-07T00:24:58Z"}',
        f"Thought: I now know the final answer\nFinal Answer: {FINAL}",
    ])
    analysis = await _triage(monkeypatch, llm)

    assert analysis["_routing"]["mode_used"] == "llm"
    assert analysis["tools_used"] == ["query_logs"]


@pytest.mark.asyncio
async def test_input_that_does_not_fit_is_an_observation_not_the_end():
    adapted = triage._single_input(query_logs)
    assert await adapted.ainvoke({"tool_input": '{"service_name": "refunds"}'}) == (
        "query_logs needs a JSON object with the keys service_name, timestamp_utc (missing: timestamp_utc)."
    )
    ok = await adapted.ainvoke({"tool_input": '```{"service_name": "a", "timestamp_utc": "t"}```'})
    assert ok == "a @ t: 3 HTTP 500s from refund-service"
    pairs = await adapted.ainvoke({"tool_input": "service_name=a, timestamp_utc=2026-08-07T00:24:58Z"})
    assert pairs == "a @ 2026-08-07T00:24:58Z: 3 HTTP 500s from refund-service"


@pytest.mark.asyncio
async def test_a_one_argument_tool_gets_the_value_not_key_equals_value():
    """Measured: the model wrote ``test_case_id=4b47…``; the stack-trace tool
    compared that whole string with the bound test id and refused the lookup,
    and the model then abandoned the ReAct format for the rest of the run."""
    seen: list[str] = []

    @tool
    async def fetch_trace(test_case_id: str) -> str:
        """The stack trace of the test."""
        seen.append(test_case_id)
        return "trace"

    adapted = triage._single_input(fetch_trace)
    for written in ("test_case_id=tc-1", '"tc-1"', '{"test_case_id": "tc-1"}', "tc-1"):
        assert await adapted.ainvoke({"tool_input": written}) == "trace"
    assert seen == ["tc-1"] * 4


@pytest.mark.asyncio
async def test_the_runnable_passes_bound_arguments_to_the_gated_call(gates):
    seen: dict = {}

    class Recording(FakeListChatModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen.update(kwargs)
            return await super().ainvoke(input, config, **kwargs)

    runnable = BudgetedLLM(Recording(responses=["ok"]), provider="openrouter", model="m").as_runnable()
    assert isinstance(runnable, BudgetedRunnable)
    out = await runnable.bind(stop=["\nObservation"]).ainvoke([HumanMessage(content="hi")])

    assert out.content == "ok"
    assert seen["stop"] == ["\nObservation"]
    assert gates["reserve"] == 1
