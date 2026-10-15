"""W03 G-B: Run Compare v2 -- the AI analysis diff in the compare payload and report (AIH-03, §7.1-§7.2).

* ``compare_runs(..., include_ai_analysis=True)`` adds ``ai_analysis_diff``; without
  the flag (the investigator, the chat tool) the payload is unchanged; any
  failure reads ``{"available": false, "reason": "ai_history_unavailable"}`` and
  two projects read ``different_projects`` -- the compare itself never fails.
* ``PROMPT_VERSION`` is ``run_compare_v2`` and keys the cache, so a v1 row is
  never served for a v2 request.
* ``ai_changes`` is attached AFTER parsing: the deterministic lines of the
  brief, whatever the model returned -- through the REAL ``BudgetedLLM``
  wrapper around a scripted model (never a mocked ``get_llm`` result), and on
  every fallback: offline, an unreachable model, the cost cap (D43).
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import FakeListChatModel

from app.agents import run_compare_agent as agent_mod
from app.agents.run_compare_agent import (
    RunCompareAgent,
    _build_context,
    build_validated_fallback_report,
)
from app.core.config import settings
from app.services import llm_circuit_breaker
from app.services import llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
LEFT = uuid.UUID("55555555-5555-4555-8555-555555555555")
RIGHT = uuid.UUID("33333333-3333-4333-8333-333333333333")

AI_CHANGES = [
    "Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.",
    "Verdict GO -> NO_GO (risk 35 -> 72)",
    "New root cause: Timeout in checkout (4 tests)",
]
BRIEF = {
    "available": True, "reason": None, "left_ref": "live", "right_ref": "live",
    "verdict": {"changed": True, "from": "GO", "to": "NO_GO", "direction": "worse", "from_risk": 35, "to_risk": 72,
                "risk_delta": 37},
    "provenance": {"changed": True, "engine_only": False, "note": AI_CHANGES[0], "engine": {"from": "rules", "to": "llm"}},
    "counts": {"clusters_added": 1, "clusters_removed": 0, "tests_changed": 12},
    "clusters_added": [{"label": "Timeout in checkout", "size": 4}], "clusters_removed": [],
    "test_changes": [
        {"test_name": f"test_{i}", "suite_name": "checkout", "category_from": "INFRASTRUCTURE",
         "category_to": "PRODUCT_BUG", "confidence_from": 40, "confidence_to": 85}
        for i in range(12)
    ],
    "ai_changes": AI_CHANGES,
}


def _payload(brief=BRIEF):
    payload = {
        "scope": "suite", "suite_name": "checkout", "new_failures": 1, "regressed": 0, "fixed": 0,
        "still_failing": 0, "duration_spikes": 0, "delta_pass_rate": -5.0,
        "test_deltas": [{"classification": "new_failure", "test_name": "test_pay"}],
    }
    if brief is not None:
        payload["ai_analysis_diff"] = brief
    return payload


MODEL_ANSWER = json.dumps({
    "executive_summary": "The payment gateway regressed between the two runs.",
    "risk_level": "HIGH",
    "key_differences": ["1 new failure"],
    "new_risks": ["test_pay"],
    "resolved_risks": [],
    "duration_concerns": [],
    "recommended_actions": ["Fix the gateway timeout"],
    "confidence": 80,
    "confidence_reason": "grounded in the diff",
    "ai_changes": ["MODEL INVENTED A CHANGE"],
})


# ── The cache key ────────────────────────────────────────────────────────────


def test_the_prompt_version_is_v2():
    assert agent_mod.PROMPT_VERSION == "run_compare_v2"


@pytest.mark.asyncio
async def test_a_v1_cached_row_is_never_read_for_a_v2_request_and_new_rows_are_v2():
    from app.services import run_compare_ai_service as service

    seen = []

    async def execute(stmt, *a, **k):
        seen.append(stmt)
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), add=MagicMock(), flush=AsyncMock())
    assert await service.get_cached_report(db, project_id=PROJECT, left_run_id=LEFT, right_run_id=RIGHT,
                                           suite_name="Checkout") is None
    params = seen[0].compile().params
    assert "run_compare_v2" in params.values() and "run_compare_v1" not in params.values()
    await service.mark_queued(db, project_id=PROJECT, left_run_id=LEFT, right_run_id=RIGHT, suite_name="Checkout",
                              compare_payload=_payload())
    row = db.add.call_args.args[0]
    assert row.prompt_version == "run_compare_v2" and row.compare_payload["ai_analysis_diff"]["available"] is True


# ── The context and the deterministic report ─────────────────────────────────


def test_the_llm_context_carries_the_ai_changes_only_when_they_could_be_computed():
    context = _build_context(_payload())
    changes = context["ai_analysis_changes"]
    assert len(changes["test_changes"]) == 10 and changes["ai_changes"] == AI_CHANGES
    assert len(BRIEF["test_changes"]) == 12, "the payload itself is not trimmed"
    assert "ai_analysis_changes" not in _build_context(_payload({"available": False, "reason": "different_projects"}))
    assert "ai_analysis_changes" not in _build_context(_payload(None))


def test_the_deterministic_report_lists_the_ai_changes_in_its_markdown():
    report = build_validated_fallback_report(_payload())
    assert report["ai_changes"] == AI_CHANGES and report["fallback_used"] is True
    assert "## AI analysis changes\n- " + AI_CHANGES[0] in report["markdown_report"]
    plain = build_validated_fallback_report(_payload(None))
    assert plain["ai_changes"] == [] and "AI analysis changes" not in plain["markdown_report"]


# ── The LLM path through the real budget wrapper ─────────────────────────────


@pytest.fixture
def gates(monkeypatch):
    calls = {"reserve": 0}

    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        calls["reserve"] += 1
        return Reservation(key="k", project_id=str(PROJECT), estimated_usd=0.01, ttl_seconds=60)

    async def settle(*_a, **_k):
        return None

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(quiet))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(quiet))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", settle)
    return calls


def _serve(monkeypatch, llm):
    async def get_llm(*_a, **_k):
        return llm

    monkeypatch.setattr("app.services.llm_factory.get_llm", get_llm)


@pytest.mark.asyncio
async def test_the_model_writes_the_narrative_but_never_the_ai_changes(monkeypatch, gates):
    _serve(monkeypatch, BudgetedLLM(FakeListChatModel(responses=[MODEL_ANSWER]), provider="ollama", model="llama3.1"))
    report = await RunCompareAgent().generate(_payload())
    assert gates["reserve"] == 1, "the call went through the budget wrapper"
    assert report["fallback_used"] is False and report["status"] == "ready"
    assert report["executive_summary"] == "The payment gateway regressed between the two runs."
    assert report["ai_changes"] == AI_CHANGES
    assert "MODEL INVENTED" not in json.dumps(report)
    assert "## AI analysis changes" in report["markdown_report"]


@pytest.mark.asyncio
async def test_an_unreachable_model_falls_back_with_the_same_ai_changes(monkeypatch, gates):
    class _Down(FakeListChatModel):
        async def ainvoke(self, *a, **k):
            raise ConnectionError("connection refused")

    _serve(monkeypatch, BudgetedLLM(_Down(responses=["x"]), provider="ollama", model="llama3.1"))
    report = await RunCompareAgent().generate(_payload())
    assert report["fallback_used"] is True and report["status"] == "ready"
    assert report["ai_changes"] == AI_CHANGES


@pytest.mark.asyncio
async def test_offline_mode_refuses_an_external_provider_and_the_report_is_deterministic(monkeypatch):
    """The REAL get_llm: AI_OFFLINE_MODE with an external provider refuses before any call."""
    async def no_overrides():
        return {}

    monkeypatch.setattr("app.services.ai_config_resolver.get_effective_ai_config", no_overrides)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    report = await RunCompareAgent().generate(_payload())
    assert report["fallback_used"] is True and report["status"] == "ready"
    assert report["ai_changes"] == AI_CHANGES


@pytest.mark.asyncio
async def test_the_cost_cap_builds_the_deterministic_report_without_an_agent(monkeypatch):
    from app.services import llm_cost_budget
    from app.services import run_compare_ai_service as service
    from app.services.llm_cost_budget import CapDecision

    row = SimpleNamespace()
    db = SimpleNamespace(flush=AsyncMock())
    monkeypatch.setattr(service, "mark_queued", AsyncMock())
    monkeypatch.setattr(service, "_get_row", AsyncMock(return_value=row))
    monkeypatch.setattr(llm_cost_budget, "check_and_apply_cap", AsyncMock(return_value=CapDecision(
        action="HARD_BLOCK", block=True, rationale="monthly cap reached", utilization_pct=100.0,
    )))
    monkeypatch.setattr(service, "RunCompareAgent", MagicMock(side_effect=AssertionError("no LLM agent when capped")))
    report = await service.generate_and_save_report(
        db, project_id=PROJECT, left_run_id=LEFT, right_run_id=RIGHT, suite_name="checkout",
        compare_payload=_payload(),
    )
    assert report["ai_changes"] == AI_CHANGES and row.fallback_used is True and row.status == "ready"
    assert row.ai_report["ai_changes"] == AI_CHANGES


# ── compare_runs(include_ai_analysis=...) ────────────────────────────────────


def _run_row(run_id, project_id):
    from app.models.postgres import TestRun

    return TestRun(id=run_id, project_id=project_id, build_number=str(run_id)[:4], status="FAILED", total_tests=1,
                   passed_tests=0, failed_tests=1, broken_tests=0, skipped_tests=0, pass_rate=0.0)


@pytest.fixture
def compare(monkeypatch):
    from app.services import ai_history_read_service as reads
    from app.services import run_compare_service as rcs

    runs = {LEFT: _run_row(LEFT, PROJECT), RIGHT: _run_row(RIGHT, PROJECT)}
    monkeypatch.setattr(rcs, "_load_summary", AsyncMock(side_effect=lambda db, rid: runs[rid]))
    monkeypatch.setattr(rcs, "_load_test_rows", AsyncMock(return_value={}))
    brief = AsyncMock(return_value=dict(BRIEF))
    monkeypatch.setattr(reads, "compare_brief", brief)
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: "mongo")
    return SimpleNamespace(rcs=rcs, runs=runs, brief=brief, reads=reads)


@pytest.mark.asyncio
async def test_the_ai_diff_is_added_only_when_asked_for(compare):
    plain = await compare.rcs.compare_runs(None, LEFT, RIGHT)
    assert "ai_analysis_diff" not in plain
    compare.brief.assert_not_awaited()

    payload = await compare.rcs.compare_runs(None, LEFT, RIGHT, include_ai_analysis=True)
    assert payload["ai_analysis_diff"] == BRIEF
    kwargs = compare.brief.await_args.kwargs
    assert kwargs["baseline"] is compare.runs[LEFT] and kwargs["run"] is compare.runs[RIGHT], "left = baseline"
    assert compare.brief.await_args.args[1] == "mongo"

    from app.models.schemas import RunCompareResponse

    served = RunCompareResponse.model_validate(payload).model_dump(mode="json")
    assert served["ai_analysis_diff"]["ai_changes"] == AI_CHANGES
    assert RunCompareResponse.model_validate(plain).ai_analysis_diff is None


@pytest.mark.asyncio
async def test_an_ai_history_failure_never_fails_the_compare(compare):
    compare.brief.side_effect = RuntimeError("relation ai_analysis_versions does not exist")
    payload = await compare.rcs.compare_runs(None, LEFT, RIGHT, include_ai_analysis=True)
    assert payload["ai_analysis_diff"] == {"available": False, "reason": "ai_history_unavailable"}
    assert payload["new_failures"] == 0, "the deterministic compare is intact"


@pytest.mark.asyncio
async def test_two_projects_are_never_ai_compared(monkeypatch):
    """The REAL compare_brief: two projects' runs never reach a digest read."""
    from app.services import ai_history_read_service as reads
    from app.services import run_compare_service as rcs

    runs = {LEFT: _run_row(LEFT, PROJECT), RIGHT: _run_row(RIGHT, uuid.uuid4())}
    monkeypatch.setattr(rcs, "_load_summary", AsyncMock(side_effect=lambda db, rid: runs[rid]))
    monkeypatch.setattr(rcs, "_load_test_rows", AsyncMock(return_value={}))
    monkeypatch.setattr(reads, "build_digest", AsyncMock(side_effect=AssertionError("no digest across projects")))
    payload = await rcs.compare_runs(None, LEFT, RIGHT, include_ai_analysis=True)
    assert payload["ai_analysis_diff"] == {"available": False, "reason": "different_projects"}


# ── Who asks for it ──────────────────────────────────────────────────────────


def _compare_calls(func) -> list[ast.Call]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) == "compare_runs" or getattr(node.func, "id", None) == "compare_runs")
    ]


def _asks_for_ai(call: ast.Call) -> bool:
    return any(
        kw.arg == "include_ai_analysis" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def test_the_run_compare_routes_and_report_tasks_ask_for_the_ai_diff_and_the_others_do_not():
    from app.agents.investigator import workflow as investigator
    from app.routers import run_compare
    from app.tools import chat_read_tools
    from app.worker import tasks

    asking = [run_compare.compare_two_runs, run_compare.compare_latest_suite_runs,
              tasks.generate_run_compare_report.run, tasks.precompute_suite_comparisons_for_run.run]
    for func in asking:
        calls = _compare_calls(func)
        assert calls and all(_asks_for_ai(c) for c in calls), func.__qualname__
    for module in (investigator, chat_read_tools):
        calls = _compare_calls(module)
        assert calls and not any(_asks_for_ai(c) for c in calls), module.__name__
