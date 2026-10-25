"""The verdict's ``structured_actions`` through the REAL ``SynthesisAgent.run`` (W15 AGT-02, §2.2 test 2).

The same known-flaky state is synthesized twice: once on the deterministic
narrative path (``AI_OFFLINE_MODE``) and once with a scripted chat model behind
the REAL ``BudgetedLLM`` wrapper (``get_llm`` returns the wrapper, as it does in
production; only the wrapper's gate stores are stubbed — never a raw model in
place of ``get_llm``). Both runs must yield the SAME typed actions: the plan
reads the bundle's ids, never the narrative, so a model can neither add nor
change an action. The verdict passes ``InvestigatorSynthesisOutput`` and the
text ``recommended_actions`` are unchanged.
"""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("sqlalchemy")

from langchain_core.language_models import FakeListChatModel  # noqa: E402

from app.agents.investigator import synthesis as syn_mod  # noqa: E402
from app.agents.investigator.persistence import BudgetReservation  # noqa: E402
from app.agents.investigator.synthesis import RECOMMENDED_ACTIONS, SynthesisAgent  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.models.agent_contracts import (  # noqa: E402
    InvestigatorSynthesisOutput,
    validate_agent_contract,
)
from app.services import llm_circuit_breaker, llm_cost_reservation as cost  # noqa: E402
from app.services.llm_cost_reservation import Reservation  # noqa: E402
from app.services.llm_factory import BudgetedLLM  # noqa: E402
from tests.test_investigator_action_plan import (  # noqa: E402
    INVESTIGATION_ID,
    OWNER,
    _flaky_bundle,
    _fp,
    _regression_bundle,
)

MODEL_NARRATIVE = "Scripted narrative: the offenders are the usual flaky checkout tests."
# What a manipulated model might try: invent actions. None of it may reach the verdict's plan.
MODEL_REPLY = json.dumps({
    "narrative": MODEL_NARRATIVE,
    "structured_actions": [{"action_type": "quarantine.apply", "target_id": "fpEVILb2C3d4"}],
})

FLAKY_HYPOTHESES = [
    {"id": "known_flaky", "status": "validated", "confidence": 88,
     "summary": "flaky coverage 100%", "confidence_basis": "heuristic_estimate"},
    {"id": "regression", "status": "invalidated", "confidence": 60,
     "summary": "no core", "confidence_basis": "heuristic_estimate"},
]


@pytest.fixture
def quiet_agent(monkeypatch):
    """Stub the BaseAgent observability surface (real DB / Mongo I/O) only."""
    async def _noop(*_args, **_kwargs):
        return None

    async def _not_cancelled(_investigation_id):
        return False

    from app.agents.base import BaseAgent

    monkeypatch.setattr(BaseAgent, "mark_stage_running", _noop)
    monkeypatch.setattr(BaseAgent, "mark_stage_done", _noop)
    monkeypatch.setattr(BaseAgent, "log_decision", _noop)
    monkeypatch.setattr(BaseAgent, "broadcast_progress", _noop)
    monkeypatch.setattr(BaseAgent, "_estimate_stage_cost", _noop)
    monkeypatch.setattr(syn_mod, "is_cancel_requested", _not_cancelled)


@pytest.fixture
def wrapper_gates(monkeypatch):
    """The BudgetedLLM's gate stores (circuit breaker, cost reservation),
    stubbed exactly like the triage wrapper regression does."""
    calls = {"reserve": 0}

    async def _available(*_args, **_kwargs):
        return None

    async def _reserve(provider, model, *, input_tokens, max_output_tokens):
        calls["reserve"] += 1
        return Reservation(key="k", project_id="p", estimated_usd=0.01, ttl_seconds=60)

    async def _settle(*_args, **_kwargs):
        return None

    async def _record(*_args, **_kwargs):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_record))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_record))
    monkeypatch.setattr(cost, "reserve", _reserve)
    monkeypatch.setattr(cost, "settle", _settle)
    return calls


def _state(bundle, *, mode="suggest"):
    return {
        "investigation_id": INVESTIGATION_ID,
        "pipeline_run_id": "22222222-2222-4222-8222-222222222222",
        "run_id": bundle["run"]["id"],
        "project_id": "44444444-4444-4444-8444-444444444444",
        "build_number": "b-7",
        "mode": mode,
        "triggered_by": "manual",
        "budget": {"max_llm_calls": 30, "max_tokens": 60000, "max_seconds": 300},
        "deadline_ts": time.monotonic() + 300.0,
        "bundle": bundle,
        "cancelled": False,
        "hypotheses": list(FLAKY_HYPOTHESES),
        "spend_llm_calls": 0,
        "spend_tokens": 0,
        "spend_cost_usd": 0.0,
        "errors": [],
        "verdict": None,
        "model_info": None,
    }


def _assert_contract(verdict):
    """The verdict passes the synthesis contract unchanged (no validation error)."""
    contracted = validate_agent_contract(
        InvestigatorSynthesisOutput, {"verdict": verdict}, agent_name="investigator.synthesis",
    )
    assert "contract_validation_error" not in contracted["agent_contracts"]["investigator.synthesis"]
    assert contracted["verdict"] == verdict


async def _deterministic(bundle, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    return await SynthesisAgent().run(_state(bundle))


async def _through_the_wrapper(bundle, monkeypatch):
    """The narrative call goes through the REAL BudgetedLLM around a scripted model."""
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    import app.services.llm_factory as llm_factory

    async def _reserve(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="structured-actions", allowed=True,
            reserved_llm_calls=1, reserved_tokens=5000,
        )

    async def _settle(*_args, **_kwargs):
        return True

    wrapper = BudgetedLLM(
        FakeListChatModel(responses=[MODEL_REPLY]), provider="openrouter", model="scripted",
    )

    async def _get_llm(*_args, **_kwargs):
        return wrapper

    monkeypatch.setattr(syn_mod, "reserve_investigation_budget", _reserve)
    monkeypatch.setattr(syn_mod, "settle_investigation_budget", _settle)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)
    return await SynthesisAgent().run(_state(bundle))


@pytest.mark.asyncio
async def test_the_offline_and_the_model_path_yield_the_same_typed_actions(
    quiet_agent, wrapper_gates, monkeypatch,
):
    offline = await _deterministic(_flaky_bundle(3), monkeypatch)
    enriched = await _through_the_wrapper(_flaky_bundle(3), monkeypatch)

    # The model path really ran through the wrapper and wrote the narrative.
    assert wrapper_gates["reserve"] == 1
    assert enriched["verdict"]["narrative"] == MODEL_NARRATIVE
    assert enriched["model_info"] is not None
    assert offline["verdict"]["narrative"] != MODEL_NARRATIVE

    actions = offline["verdict"]["structured_actions"]
    assert [a["target_id"] for a in actions] == [_fp(0), _fp(1), _fp(2)]
    assert enriched["verdict"]["structured_actions"] == actions
    assert "fpEVILb2C3d4" not in json.dumps(enriched["verdict"])
    for delta in (offline, enriched):
        verdict = delta["verdict"]
        _assert_contract(verdict)
        assert verdict["recommended_actions"] == RECOMMENDED_ACTIONS["known_flaky"]
        assert {a["payload"]["proposing_agent_mode"] for a in verdict["structured_actions"]} == {"suggest"}
        assert {a["payload"]["investigation_id"] for a in verdict["structured_actions"]} == {INVESTIGATION_ID}


@pytest.mark.asyncio
async def test_the_bundles_suite_owners_reach_the_regression_plan(quiet_agent, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    bundle = _regression_bundle()
    bundle["suite_owners"] = {"checkout": OWNER}
    state = _state(bundle)
    state["hypotheses"] = [
        {"id": "regression", "status": "validated", "confidence": 80,
         "summary": "core", "confidence_basis": "heuristic_estimate"},
    ]
    verdict = (await SynthesisAgent().run(state))["verdict"]
    assert verdict["primary_cause"] == "regression"
    types = [a["action_type"] for a in verdict["structured_actions"]]
    assert types == ["failure.reassign"] * 4 + ["defect.promote"]
    assert verdict["recommended_actions"] == RECOMMENDED_ACTIONS["regression"]


@pytest.mark.asyncio
async def test_a_failing_plan_leaves_a_valid_verdict_with_no_typed_actions(quiet_agent, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    def _broken(*_args, **_kwargs):
        raise RuntimeError("plan exploded")

    monkeypatch.setattr(syn_mod, "plan_structured_actions", _broken)
    verdict = (await SynthesisAgent().run(_state(_flaky_bundle(2))))["verdict"]
    assert verdict["structured_actions"] == []
    assert verdict["primary_cause"] == "known_flaky"
    _assert_contract(verdict)
