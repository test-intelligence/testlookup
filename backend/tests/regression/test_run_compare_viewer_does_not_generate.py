"""A Viewer or Tester opening Run Compare must never generate a paid AI report (D-0003).

GPT-6 (exploratory test, 2026-11-14) opened "Compare latest" as a VIEWER on a
suite pair with no cached report. Both compare routes accept any role and
default ``include_ai_report=True``, so ``_resolve_ai_report`` called
``generate_and_save_report`` on the cache miss: a model call charged to the
project and a saved ``run_comparison_reports`` row, triggered by the role the
app otherwise never lets start AI (``canTriggerLlm`` = QA_ENGINEER and above;
every other LLM route has ``require_role(UserRole.QA_ENGINEER)``).

The rule now: a cached report is served to every role; on a miss, only
QA_ENGINEER and above generate one. Below that the response carries
``ai_report: None`` and nothing is generated, queued or enqueued.

These drive the real handlers with their collaborators replaced by spies, so
no database, no provider and no Celery broker is touched. The positive control
runs the real ``generate_and_save_report`` -> ``RunCompareAgent`` -> the real
``BudgetedLLM`` around a scripted ``FakeListChatModel`` (never a real call), so
"generated" means a model call went through the budget wrapper.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.language_models import FakeListChatModel

from app.core.config import settings
from app.routers import run_compare as router
from app.services import llm_circuit_breaker
from app.services import llm_cost_reservation as cost
from app.services import run_compare_ai_service, run_compare_service
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
LEFT = uuid.UUID("55555555-5555-4555-8555-555555555555")
RIGHT = uuid.UUID("33333333-3333-4333-8333-333333333333")

CACHED = {"status": "ready", "executive_summary": "cached summary", "risk_level": "LOW"}

MODEL_ANSWER = json.dumps({
    "executive_summary": "One test newly fails.",
    "risk_level": "MEDIUM",
    "key_differences": ["1 new failure"],
    "new_risks": [],
    "resolved_risks": [],
    "duration_concerns": [],
    "recommended_actions": [],
    "confidence": 70,
    "confidence_reason": "grounded in the diff",
})


def _user(role: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), is_active=True, role=role)


class _Rows:
    """Answers the two ``select(TestRun.project_id)`` lookups of ``compare_two_runs``."""

    def scalar_one_or_none(self):
        return PROJECT


class _Db:
    async def execute(self, *_a, **_k):
        return _Rows()

    async def rollback(self):
        return None

    async def flush(self):
        return None


@pytest.fixture
def spies(monkeypatch):
    calls: dict[str, list] = {"generate": [], "queue": [], "enqueue": []}
    state = {"cached": None}

    async def _scope(_db, _user, project_id=None):
        return PROJECT, {PROJECT}

    async def _pair(_db, *, project_id, suite_name):
        return SimpleNamespace(id=LEFT, branch="main"), SimpleNamespace(id=RIGHT, branch="main")

    async def _compare(_db, left, right, **_k):
        return {
            "left": {"branch": "main"}, "right": {"branch": "main"},
            "selection": {}, "new_failures": 1, "test_deltas": [],
        }

    async def _cached(_db, **_k):
        return state["cached"]

    async def _generate(_db, **kwargs):
        calls["generate"].append(kwargs)
        return {"status": "ready", "executive_summary": "generated"}

    async def _queue(_db, **kwargs):
        calls["queue"].append(kwargs)

    class _Task:
        @staticmethod
        def delay(**kwargs):
            calls["enqueue"].append(kwargs)

    monkeypatch.setattr(router, "resolve_project_scope", _scope)
    monkeypatch.setattr(run_compare_service, "resolve_latest_suite_pair", _pair)
    monkeypatch.setattr(run_compare_service, "compare_runs", _compare)
    monkeypatch.setattr(run_compare_ai_service, "get_cached_report", _cached)
    monkeypatch.setattr(run_compare_ai_service, "generate_and_save_report", _generate)
    monkeypatch.setattr(run_compare_ai_service, "mark_queued", _queue)
    monkeypatch.setattr("app.worker.tasks.generate_run_compare_report", _Task, raising=False)
    return calls, state


async def _latest(user, include_ai_report=True):
    return await router.compare_latest_suite_runs(
        suite_name="Auth", project_id=PROJECT, include_ai_report=include_ai_report,
        db=_Db(), current_user=user,
    )


async def _manual(user, include_ai_report=True):
    return await router.compare_two_runs(
        left=LEFT, right=RIGHT, suite_name=None, include_ai_report=include_ai_report,
        db=_Db(), current_user=user,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
@pytest.mark.parametrize("route", [_latest, _manual], ids=["latest", "manual"])
async def test_a_role_below_qa_engineer_never_generates_on_a_cache_miss(spies, role, route):
    calls, _ = spies
    result = await route(_user(role))
    assert result["new_failures"] == 1, "the diff itself is still served"
    assert result["ai_report"] is None
    assert calls == {"generate": [], "queue": [], "enqueue": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
@pytest.mark.parametrize("route", [_latest, _manual], ids=["latest", "manual"])
async def test_a_cached_report_is_still_served_to_every_role(spies, role, route):
    calls, state = spies
    state["cached"] = CACHED
    result = await route(_user(role))
    assert result["ai_report"] == CACHED
    assert calls == {"generate": [], "queue": [], "enqueue": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["QA_ENGINEER", "QA_LEAD", "ADMIN"])
@pytest.mark.parametrize("route", [_latest, _manual], ids=["latest", "manual"])
async def test_qa_engineer_and_above_generate_on_a_miss(spies, role, route):
    calls, _ = spies
    user = _user(role)
    result = await route(user)
    assert result["ai_report"] == {"status": "ready", "executive_summary": "generated"}
    assert len(calls["generate"]) == 1
    assert calls["generate"][0]["created_by_user_id"] == user.id


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "ADMIN"])
async def test_opting_out_makes_no_call_for_any_role(spies, role):
    calls, _ = spies
    result = await _latest(_user(role), include_ai_report=False)
    assert "ai_report" not in result
    assert calls == {"generate": [], "queue": [], "enqueue": []}


# ── Positive control through the real budget wrapper ──────────────────────────


@pytest.fixture
def real_generation(monkeypatch):
    """Undo the generate spy: the real service, the real agent, the real BudgetedLLM."""
    reserved = {"n": 0}

    async def _scope(_db, _user, project_id=None):
        return PROJECT, {PROJECT}

    async def _pair(_db, *, project_id, suite_name):
        return SimpleNamespace(id=LEFT, branch="main"), SimpleNamespace(id=RIGHT, branch="main")

    async def _compare(_db, left, right, **_k):
        return {
            "left": {"branch": "main"}, "right": {"branch": "main"}, "selection": {},
            "scope": "suite", "suite_name": "Auth", "new_failures": 1, "regressed": 0,
            "fixed": 0, "still_failing": 0, "duration_spikes": 0, "delta_pass_rate": -5.0,
            "test_deltas": [{"classification": "new_failure", "test_name": "test_login"}],
        }

    async def _none(*_a, **_k):
        return None

    async def _reserve(provider, model, *, input_tokens, max_output_tokens):
        reserved["n"] += 1
        return Reservation(key="k", project_id=str(PROJECT), estimated_usd=0.01, ttl_seconds=60)

    class _Uncapped:
        def is_capped(self):
            return False

    async def _cap(_project_id):
        return _Uncapped()

    async def _get_llm(*_a, **_k):
        return BudgetedLLM(FakeListChatModel(responses=[MODEL_ANSWER]), provider="ollama", model="llama3.1")

    monkeypatch.setattr(router, "resolve_project_scope", _scope)
    monkeypatch.setattr(run_compare_service, "resolve_latest_suite_pair", _pair)
    monkeypatch.setattr(run_compare_service, "compare_runs", _compare)
    monkeypatch.setattr(run_compare_ai_service, "get_cached_report", _none)
    monkeypatch.setattr(run_compare_ai_service, "mark_queued", _none)
    monkeypatch.setattr(run_compare_ai_service, "_get_row", _none)
    monkeypatch.setattr("app.services.llm_cost_budget.check_and_apply_cap", _cap)
    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _none)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_none))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_none))
    monkeypatch.setattr(cost, "reserve", _reserve)
    monkeypatch.setattr(cost, "settle", _none)
    monkeypatch.setattr("app.services.llm_factory.get_llm", _get_llm)
    return reserved


@pytest.mark.asyncio
async def test_through_the_real_wrapper_a_viewer_makes_no_model_call(real_generation):
    result = await _latest(_user("VIEWER"))
    assert result["ai_report"] is None
    assert real_generation["n"] == 0, "no call reached the budget wrapper"


@pytest.mark.asyncio
async def test_through_the_real_wrapper_a_qa_engineer_makes_one_model_call(real_generation):
    result = await _latest(_user("QA_ENGINEER"))
    assert result["ai_report"]["executive_summary"] == "One test newly fails."
    assert real_generation["n"] == 1, "the generation went through the budget wrapper"
