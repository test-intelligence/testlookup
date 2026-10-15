"""AIC-03: the Settings > AI confidence threshold decides every confidence route.

The stored threshold was honoured only by the confidence gate and the action
policy. Production triage routing ran on the SUMMARY agent's
``thresholds.confidence_min`` (a leaked default of 80) through the compiled
``triageable_analysis_count > 0`` edge, and recorded no decision at all; the
triage stage, gap detection, the low-confidence counter, the ML review flag and
the validation fallback read the env value, one of them frozen at import.

Now a pipeline freezes the threshold once (``ai_config_snapshot``) and every
reader takes that value. These tests drive:

* the REAL compiled built-in graphs (``compile_workflow`` over the built-in
  definitions, the real ``summary_node``; the other stages are recording stubs)
  at thresholds 95 and 40, offline and deep;
* the plan hash (part of the replay authority) per threshold;
* each stage reader (triage gate, gap detection, low-confidence counter,
  ML review flag, validation fallback);
* one LLM-mode analysis through the REAL ``BudgetedLLM`` (scripted fake model),
  whose recorded ``threshold_check`` carries the frozen value;
* the manual-retry decision when the stored threshold moved since the freeze.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents import workflow as wf
from app.agents.workflow_compiler import compile_workflow
from app.core.config import settings
from app.services import workflow_definition_service as definitions
from app.services.agent_planner import build_workflow_plan
from app.services.pipeline_ai_scope import build_ai_config_snapshot, pipeline_ai_scope


def _snapshot(threshold: int, *, timeout: int = 300) -> dict:
    return dict(build_ai_config_snapshot({
        "confidence_threshold": threshold,
        "confidence_threshold_source": "ai_config",
        "timeout_seconds": timeout,
        "timeout_seconds_source": "ai_config",
    }))


# ── the compiled built-in graph routes on the frozen threshold ──────────────


def _analyses() -> dict[str, dict]:
    """Three failures at 70-90: below 95, all at or above 40."""
    return {
        "tc-70": {"confidence_score": 70, "is_flaky": False},
        "tc-85": {"confidence_score": 85, "is_flaky": False},
        "tc-90": {"confidence_score": 90, "is_flaky": False},
    }


def _graph(workflow_id: str, ran: list[str]):
    body = definitions.body_from_item(definitions.builtin(workflow_id))

    def stub(stage: str):
        async def run(_state):
            ran.append(stage)
            return {"completed_stages": [stage]}
        return run

    executors: dict[str, Any] = {
        step.agent_id: stub(step.id) for step in body.steps if step.id != "summary"
    }
    # The REAL summary node: it records the branch decision after its stage.
    executors["agent.summary.v1"] = wf.summary_node
    return compile_workflow(body, node_executors=executors).graph.compile()


def _initial(workflow_type: str, threshold: int) -> dict:
    plan = build_workflow_plan(
        workflow_type=workflow_type,
        threshold=threshold,
        gap_refinement_enabled=False,
    )
    plan["workflow_id"] = workflow_type
    return {
        "pipeline_run_id": f"pipeline-{workflow_type}-{threshold}",
        "project_id": "proj-1",
        "workflow_type": workflow_type,
        "failed_test_ids": list(_analyses()),
        "analyses": _analyses(),
        "confidence_threshold": threshold,
        "ai_config_snapshot": _snapshot(threshold),
        "initial_workflow_plan": plan,
        "workflow_plan": plan,
        "_workflow_route_decisions": [],
        "completed_stages": [],
        "errors": [],
    }


@pytest.fixture
def quiet_summary(monkeypatch):
    monkeypatch.setattr(wf._summary, "run", AsyncMock(return_value={"completed_stages": ["summary"]}))
    monkeypatch.setattr(wf, "emit_event", AsyncMock())


def _summary_decisions(final: dict) -> list[dict]:
    return [
        d for d in final.get("_workflow_route_decisions") or []
        if str(d.get("decision_point", "")).startswith("route_after_summary")
    ]


@pytest.mark.asyncio
async def test_offline_at_95_skips_triage_and_says_why(quiet_summary):
    ran: list[str] = []
    final = await _graph("offline", ran).ainvoke(_initial("offline", 95))

    assert "triage" not in ran
    [decision] = _summary_decisions(final)
    assert decision["decision_point"] == "route_after_summary"
    assert decision["chosen"] == "end"
    assert decision["rationale"] == "no analyses above confidence threshold 95 — skipping triage"
    assert decision["context"] == {"analyses_count": 3, "threshold": 95, "threshold_source": "ai_config"}
    assert final["initial_workflow_plan"]["triage_threshold"] == 95


@pytest.mark.asyncio
async def test_offline_at_40_runs_triage_and_says_how_many(quiet_summary):
    ran: list[str] = []
    final = await _graph("offline", ran).ainvoke(_initial("offline", 40))

    assert "triage" in ran
    [decision] = _summary_decisions(final)
    assert decision["chosen"] == "triage"
    assert decision["rationale"] == "3 analyses meet confidence ≥ 40"


def test_one_triageable_analysis_reads_in_the_singular():
    from app.agents.workflow import _meet_confidence_text

    assert _meet_confidence_text(1, 40) == "1 analysis meets confidence ≥ 40"
    assert _meet_confidence_text(3, 40) == "3 analyses meet confidence ≥ 40"


@pytest.mark.asyncio
async def test_the_summary_steps_own_confidence_min_no_longer_routes(quiet_summary):
    """At 85 the old router (summary agent default ``confidence_min`` 80)
    would have triaged two analyses; the frozen 95 triages none. And a
    flaky analysis never counts, whatever its confidence."""
    ran: list[str] = []
    state = _initial("offline", 95)
    state["analyses"]["tc-99"] = {"confidence_score": 99, "is_flaky": True}
    final = await _graph("offline", ran).ainvoke(state)

    assert "triage" not in ran
    assert _summary_decisions(final)[0]["context"]["analyses_count"] == 4


@pytest.mark.asyncio
async def test_deep_at_95_skips_triage_but_runs_the_specialists(quiet_summary):
    ran: list[str] = []
    final = await _graph("deep", ran).ainvoke(_initial("deep", 95))

    assert "triage" not in ran
    for stage in ("failure_clustering", "contract_validation", "gap_detection",
                  "flaky_sentinel", "release_risk", "decision_report"):
        assert stage in ran, stage
    [decision] = _summary_decisions(final)
    assert decision["decision_point"] == "route_after_summary_deep"
    assert decision["chosen"] == "failure_clustering"
    assert decision["rationale"] == (
        "no analyses above confidence threshold 95 — skipping triage, continuing to specialist stages"
    )


@pytest.mark.asyncio
async def test_deep_at_40_runs_triage_then_the_specialists(quiet_summary):
    ran: list[str] = []
    final = await _graph("deep", ran).ainvoke(_initial("deep", 40))

    assert ran.index("triage") < ran.index("failure_clustering")
    assert _summary_decisions(final)[0]["chosen"] == "triage"


@pytest.mark.asyncio
async def test_no_decision_without_a_triage_stage_or_when_the_stage_fails(monkeypatch, quiet_summary):
    state = _initial("offline", 95)
    state["initial_workflow_plan"] = {"workflow_id": "live", "stages": [{"stage": "summary"}]}
    await wf.summary_node(state)
    assert state["_workflow_route_decisions"] == []

    monkeypatch.setattr(wf._summary, "run", AsyncMock(side_effect=RuntimeError("summary failed")))
    state = _initial("offline", 95)
    with pytest.raises(RuntimeError):
        await wf.summary_node(state)
    assert state["_workflow_route_decisions"] == []


def test_the_legacy_routers_read_the_frozen_threshold():
    """``_route_after_summary(_deep)`` serve only the legacy graphs; they still
    take the state's value, else the env default."""
    analyses = {"tc": {"confidence_score": 90}}
    assert wf._route_after_summary({"analyses": analyses, "confidence_threshold": 95}) == wf.END
    assert wf._route_after_summary({"analyses": analyses, "confidence_threshold": 40}) == "triage"
    assert wf._route_after_summary_deep({"analyses": analyses, "confidence_threshold": 95}) == "flaky_sentinel"
    assert wf._route_after_summary({"analyses": analyses}) == "triage"  # env default 80


# ── the plan ────────────────────────────────────────────────────────────────


def test_the_plan_hash_follows_the_threshold():
    at_95 = build_workflow_plan(workflow_type="offline", threshold=95)
    at_40 = build_workflow_plan(workflow_type="offline", threshold=40)
    again = build_workflow_plan(workflow_type="offline", threshold=95)

    assert at_95["triage_threshold"] == 95
    assert at_95["plan_sha256"] != at_40["plan_sha256"]
    assert at_95["plan_sha256"] == again["plan_sha256"]


def test_the_default_plan_threshold_is_the_env_default():
    assert build_workflow_plan(workflow_type="offline")["triage_threshold"] == int(
        settings.AI_CONFIDENCE_THRESHOLD
    )


# ── the stage readers ───────────────────────────────────────────────────────


def _triage_agent(monkeypatch):
    from app.agents.triage_agent import DefectTriageAgent

    agent = DefectTriageAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    agent._triage_one = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda tc_id, *_a, **_k: {"test_case_id": tc_id, "action": "created"}
    )
    monkeypatch.setattr(settings, "JIRA_ENABLED", False)
    return agent


@pytest.mark.parametrize("threshold,created", [(95, 0), (80, 1)])
@pytest.mark.asyncio
async def test_triage_gates_on_the_frozen_threshold(monkeypatch, threshold, created):
    agent = _triage_agent(monkeypatch)
    state = {
        "pipeline_run_id": "p-1",
        "project_id": "proj-1",
        "analyses": {"tc-1": {"confidence_score": 90, "failure_category": "TEST_DATA"}},
        "confidence_threshold": threshold,
        "ai_config_snapshot": _snapshot(threshold),
    }

    result = await agent.run(state)

    assert agent._triage_one.await_count == created
    gate = next(
        call.kwargs for call in agent.log_decision.await_args_list
        if call.kwargs.get("decision_point") == "triage_gate"
    )
    assert gate["context"]["confidence_threshold"] == threshold
    assert gate["context"]["confidence_threshold_source"] == "ai_config"
    assert f"gate is confidence >= {threshold}" in gate["rationale"]
    assert [r["action"] for r in result["triage_results"]] == (["created"] if created else ["skipped"])


@pytest.mark.asyncio
async def test_triage_keeps_the_high_priority_arm(monkeypatch):
    """A PRODUCT_BUG at >= 50 is still triaged under 95 (unchanged rule)."""
    agent = _triage_agent(monkeypatch)
    state = {
        "pipeline_run_id": "p-1",
        "project_id": "proj-1",
        "analyses": {"tc-1": {"confidence_score": 60, "failure_category": "PRODUCT_BUG"}},
        "confidence_threshold": 95,
    }
    await agent.run(state)
    assert agent._triage_one.await_count == 1


@pytest.mark.asyncio
async def test_gap_detection_reports_a_confidence_90_analysis_as_low_at_95(monkeypatch):
    from app.agents.gap_detection_agent import GapDetectionAgent

    agent = GapDetectionAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    analyses = {"a": {
        "failure_category": "PRODUCT_BUG",
        "evidence_references": [{"source": "log"}],
        "confidence_score": 90,
    }}

    def low(result: dict) -> list:
        report = result["gap_report"]
        return [g for g in report["gaps"] if "confidence_below_threshold" in str(g.get("detail"))]

    at_95 = await agent.run({"failed_test_ids": ["a"], "analyses": analyses, "confidence_threshold": 95})
    at_80 = await agent.run({"failed_test_ids": ["a"], "analyses": analyses, "confidence_threshold": 80})

    assert [g["detail"] for g in low(at_95)] == ["confidence_below_threshold:95"]
    assert low(at_80) == []


@pytest.mark.asyncio
async def test_the_low_confidence_counter_uses_the_frozen_threshold():
    from app.agents.analysis_agent import AnalysisAgent

    agent = AnalysisAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    agent._fetch_test_metadata = AsyncMock(return_value={"tc-1": {"test_name": "t"}})  # type: ignore[method-assign]
    agent._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
    agent._resolve_adaptive_concurrency = AsyncMock(  # type: ignore[method-assign]
        return_value={"concurrency": 1, "rationale": "test"}
    )
    agent._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
    agent._record_latency_feedback = MagicMock()  # type: ignore[method-assign]

    async def _analyse(*_a, **_k):
        return {"root_cause_summary": "x" * 40, "confidence_score": 90, "_audit": {}}

    agent._analyse_with_retry = _analyse  # type: ignore[method-assign]
    base = {
        "pipeline_run_id": "p-1", "project_id": "symbolic", "failed_test_ids": ["tc-1"],
        "_cost_budget_prechecked": True, "_cost_budget_action": "ALLOW",
    }

    at_95 = await agent.run({**base, "confidence_threshold": 95})
    at_80 = await agent.run({**base, "confidence_threshold": 80})

    assert at_95["low_confidence_count"] == 1
    assert at_80["low_confidence_count"] == 0


def test_the_ml_review_flag_takes_the_frozen_threshold(monkeypatch):
    from app.services.ml import classifier as ml

    model = SimpleNamespace(
        predict=lambda rows: [ml.CATEGORY_LABELS.index("PRODUCT_BUG")],
        predict_proba=lambda rows: [[0.9 if i == ml.CATEGORY_LABELS.index("PRODUCT_BUG") else 0.1 / (len(ml.CATEGORY_LABELS) - 1)
                                     for i in range(len(ml.CATEGORY_LABELS))]],
    )
    monkeypatch.setattr(ml, "_load_model", lambda: model)
    monkeypatch.setattr("app.services.ml.feature_extractor.features_to_array", lambda _f: [0.0])

    assert ml.MLClassifier.classify({})["confidence_score"] == 90
    assert ml.MLClassifier.classify({})["requires_human_review"] is False          # env 80
    assert ml.MLClassifier.classify({}, confidence_threshold=95)["requires_human_review"] is True
    with pipeline_ai_scope(_snapshot(95)):
        assert ml.MLClassifier.classify({})["requires_human_review"] is True


def test_the_validation_fallback_takes_the_frozen_threshold():
    """No stored ``threshold_check`` (legacy path): inside a pipeline the
    frozen value and its source, not ``env_default``."""
    from app.services.confidence_validation import validate_confidence

    def analysis() -> dict:
        return {
            "root_cause_summary": "A detailed explanation of the failure with evidence.",
            "failure_category": "PRODUCT_BUG",
            "confidence_score": 90,
            "evidence_references": [{"source": "stacktrace"}],
            "tools_used": ["fetch_stacktrace"],
            "_routing": {},
        }

    outside = validate_confidence(analysis())
    with pipeline_ai_scope(_snapshot(95)):
        inside = validate_confidence(analysis())

    assert outside["_routing"]["threshold_check"]["threshold"] == int(settings.AI_CONFIDENCE_THRESHOLD)
    assert outside["_routing"]["threshold_check"]["source"] == "env_default"
    check = inside["_routing"]["threshold_check"]
    assert (check["threshold"], check["source"]) == (95, "ai_config")
    assert inside["requires_human_review"] is (not check["passed"])


# ── one LLM-mode analysis through the real BudgetedLLM ──────────────────────


FINAL = json.dumps({
    "root_cause_summary": "Checkout rounds the tax twice on every order total.",
    "failure_category": "PRODUCT_BUG",
    "backend_error_found": True,
    "pod_issue_found": False,
    "is_flaky": False,
    "confidence_score": 82,
    "recommended_actions": ["Fix the tax rounding"],
    "role_actions": {},
    "evidence_references": [],
})


@pytest.fixture
def scripted_llm(monkeypatch):
    """The real wrapper over a scripted fake model; only its GATE stores stubbed."""
    from langchain_core.language_models import FakeListChatModel

    import app.services.agent as triage
    from app.services import llm_circuit_breaker, llm_cost_reservation as cost
    from app.services.llm_factory import BudgetedLLM

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _none)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_none))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_none))
    monkeypatch.setattr(cost, "reserve", _none)
    monkeypatch.setattr(cost, "settle", _none)
    monkeypatch.setattr(triage, "_store_audit_trail", AsyncMock())
    monkeypatch.setattr(triage, "_emit_event", AsyncMock())
    monkeypatch.setattr(triage, "_get_tools", lambda: [])
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    llm = BudgetedLLM(
        FakeListChatModel(responses=[f"Thought: done\nFinal Answer: {FINAL}"] * 4),
        provider="openrouter", model="scripted",
    )

    async def _get_llm(*_a, **_k):
        return llm

    monkeypatch.setattr(triage, "get_llm", _get_llm)
    return llm


@pytest.mark.asyncio
async def test_an_llm_analysis_records_the_frozen_threshold_check(scripted_llm, monkeypatch):
    import asyncio

    from app.agents.analysis_agent import AnalysisAgent

    async def live_40(**_kwargs):
        """The admin moved the live setting to 40 after the pipeline started."""
        return {"confidence_threshold": 40, "confidence_threshold_source": "ai_config"}

    monkeypatch.setattr("app.services.ai_config_resolver.get_effective_ai_config", live_40)
    monkeypatch.setattr("app.agents.analysis_agent.store_artifact", AsyncMock(return_value=None))
    agent = AnalysisAgent()
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    snapshot = _snapshot(95)
    state = {
        "pipeline_run_id": "p-llm",
        "analysis_mode_requested": "llm",
        "analysis_mode_resolved": "llm",
        "confidence_threshold": 95,
        "llm_timeout_seconds": 300,
        "ai_config_snapshot": snapshot,
        "test_run_data": {},
    }

    with pipeline_ai_scope(snapshot):
        analysis = await agent._analyse_one(asyncio.Semaphore(1), "tc-1", {"test_name": "test_checkout"}, state)

    check = analysis["_audit"]["threshold_check"]
    assert check == {
        "threshold": 95,
        "observed_confidence": analysis["confidence_score"],
        "passed": analysis["confidence_score"] >= 95,
        "source": "ai_config",
    }
    assert analysis["requires_human_review"] is (not check["passed"])
    assert analysis["_audit"]["analysis_mode"] == "llm"
    # Provenance: the frozen (effective) provider/model when the snapshot has them.
    assert analysis["_audit"]["model_config_snapshot"]["provider"] == settings.LLM_PROVIDER


def test_the_model_provenance_comes_from_the_frozen_mode_snapshot():
    from app.agents.analysis_agent import _frozen_model_field

    state = {"analysis_mode_resolution": {"provider": "openrouter", "model": "stored-model"}}
    assert _frozen_model_field(state, "provider", "ollama") == "openrouter"
    assert _frozen_model_field(state, "model", "env-model") == "stored-model"
    assert _frozen_model_field({}, "provider", "ollama") == "ollama"


# ── a resume replays, a manual retry reruns ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_resume_replays_the_frozen_snapshot_not_the_live_value(monkeypatch):
    async def live_40(**_kwargs):
        return {"confidence_threshold": 40, "confidence_threshold_source": "ai_config", "timeout_seconds": 300}

    monkeypatch.setattr("app.services.ai_config_resolver.get_effective_ai_config", live_40)
    setup = {
        "ai_config_snapshot": _snapshot(95, timeout=5),
        "gap_refinement_settings": {"enabled": True},
        "initial_workflow_plan": {"stages": []},
    }

    snapshot, gap, to_persist = await wf._pipeline_ai_context(setup)
    fields = wf._frozen_ai_state(snapshot, gap)

    assert to_persist is None
    assert fields["confidence_threshold"] == 95
    assert fields["llm_timeout_seconds"] == 5
    assert fields["gap_refinement_enabled"] is True
    assert fields["ai_config_snapshot"]["origin"] == "pipeline_start"


@pytest.mark.asyncio
async def test_a_legacy_row_builds_and_persists_a_resumed_legacy_snapshot(monkeypatch):
    async def live(**kwargs):
        assert kwargs.get("fresh") is True
        return {"confidence_threshold": 40, "confidence_threshold_source": "ai_config", "timeout_seconds": 120}

    monkeypatch.setattr("app.services.ai_config_resolver.get_effective_ai_config", live)
    plan = build_workflow_plan(workflow_type="deep", failed_test_ids=["t"], gap_refinement_enabled=True)
    plan.pop("specialist_flags")  # a pre-W04 plan: the gap decision lives on the stage rows only
    snapshot, gap, to_persist = await wf._pipeline_ai_context({"initial_workflow_plan": plan})

    assert snapshot["origin"] == "resumed_legacy"
    assert snapshot["confidence_threshold"] == 40
    assert gap == {"enabled": True}
    assert to_persist == {"ai_config_snapshot": snapshot, "gap_refinement_settings": {"enabled": True}}


def _metadata(threshold: int, timeout: int, *, with_snapshot: bool = True) -> dict:
    mode = {"resolved": "rules", "provider": "ollama", "model": "m", "offline": False}
    metadata: dict[str, Any] = {
        "analysis_mode_resolution": mode,
        "initial_workflow_plan": {"stages": [{"stage": "ingestion"}]},
    }
    if with_snapshot:
        metadata["ai_config_snapshot"] = _snapshot(threshold, timeout=timeout)
    return metadata


def test_a_manual_retry_reruns_when_the_threshold_moved():
    from app.services.pipeline_retry_config import decide_retry_mode

    mode = {"resolved": "rules", "provider": "ollama", "model": "m", "offline": False}
    moved = decide_retry_mode(_metadata(95, 300), mode, _snapshot(40))
    same = decide_retry_mode(_metadata(95, 300), mode, _snapshot(95))
    timeout_moved = decide_retry_mode(_metadata(95, 5), mode, _snapshot(95, timeout=300))

    assert (moved.mode, moved.reason) == ("rerun", "config_changed")
    assert (same.mode, same.reason) == ("resume", "config_unchanged")
    assert (timeout_moved.mode, timeout_moved.reason) == ("rerun", "config_changed")


def test_a_legacy_row_without_a_snapshot_still_resumes():
    from app.services.pipeline_retry_config import config_fingerprint, decide_retry_mode

    mode = {"resolved": "rules", "provider": "ollama", "model": "m", "offline": False}
    legacy = decide_retry_mode(_metadata(95, 300, with_snapshot=False), mode, _snapshot(40))
    assert (legacy.mode, legacy.reason) == ("resume", "config_unchanged")
    # Without the live config the fingerprint is the pre-W04 four-field hash.
    plain = decide_retry_mode(_metadata(95, 300), mode)
    assert plain.frozen_fingerprint == config_fingerprint(mode)
    assert plain.mode == "resume"
