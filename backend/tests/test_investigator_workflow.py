"""Investigator node behaviour: offline determinism, budgets, cancel (AI-1).

Drives the hypothesis/synthesis agents' ``run()`` skeleton directly with
prepared state, stubbing the BaseAgent observability surface and the
row-persistence helpers (both open real DB sessions). Pins:

* offline-deterministic verdicts — with ``AI_OFFLINE_MODE`` (the default)
  NO LLM is ever constructed and ``confidence_basis`` stays
  ``heuristic_estimate``;
* the wall-clock budget path — a passed deadline yields an honest
  ``inconclusive`` leftover and the synthesis narrative carries a budget
  note while the investigation still completes;
* the cooperative cancel path — a set cancel flag skips work and produces
  no hypothesis;
* the LLM-budget gate — ``max_llm_calls=0`` blocks the weigh call even
  when an LLM would be reachable;
* graph topology — plan fans out to all five hypotheses, which fan into
  synthesis.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.investigator import hypotheses as hyp_mod  # noqa: E402
from app.agents.investigator import synthesis as syn_mod  # noqa: E402
from app.agents.investigator.hypotheses import (  # noqa: E402
    BASIS_HEURISTIC,
    InfraHypothesisAgent,
    KnownFlakyHypothesisAgent,
)
from app.agents.investigator.synthesis import SynthesisAgent  # noqa: E402
from tests.test_investigator_hypotheses import (  # noqa: E402
    all_flaky_bundle,
    pure_infra_bundle,
)


# ─────────────────────────── Stubs ───────────────────────────


@pytest.fixture
def quiet_agent(monkeypatch):
    """Stub the observability + persistence surface (real DB/Mongo I/O)."""
    persisted: list[dict] = []

    async def _noop(*args, **kwargs):
        return None

    async def _capture_persist(_investigation_id, hypothesis):
        persisted.append(hypothesis)

    async def _not_cancelled(_investigation_id):
        return False

    from app.agents.base import BaseAgent

    monkeypatch.setattr(BaseAgent, "mark_stage_running", _noop)
    monkeypatch.setattr(BaseAgent, "mark_stage_done", _noop)
    monkeypatch.setattr(BaseAgent, "log_decision", _noop)
    monkeypatch.setattr(BaseAgent, "broadcast_progress", _noop)
    monkeypatch.setattr(hyp_mod, "persist_hypothesis", _capture_persist)
    monkeypatch.setattr(hyp_mod, "is_cancel_requested", _not_cancelled)
    monkeypatch.setattr(syn_mod, "is_cancel_requested", _not_cancelled)
    return persisted


def _state(bundle, *, deadline_offset=300.0, max_llm_calls=30, cancelled=False):
    return {
        "investigation_id": "11111111-1111-1111-1111-111111111111",
        "pipeline_run_id": "22222222-2222-2222-2222-222222222222",
        "run_id": "33333333-3333-3333-3333-333333333333",
        "project_id": "44444444-4444-4444-4444-444444444444",
        "build_number": "b-100",
        "mode": "shadow",
        "triggered_by": "manual",
        "budget": {"max_llm_calls": max_llm_calls, "max_tokens": 60000, "max_seconds": 300},
        "deadline_ts": time.monotonic() + deadline_offset,
        "bundle": bundle,
        "cancelled": cancelled,
        "hypotheses": [],
        "spend_llm_calls": 0,
        "spend_tokens": 0,
        "spend_cost_usd": 0.0,
        "errors": [],
        "verdict": None,
        "model_info": None,
    }


# ─────────────────────── Offline determinism ───────────────────────


@pytest.mark.asyncio
async def test_offline_run_is_deterministic_and_never_touches_llm(
    quiet_agent, monkeypatch,
):
    """AI_OFFLINE_MODE (the default) → deterministic verdict, heuristic
    basis, and get_llm is NEVER constructed."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    def _explode(*args, **kwargs):  # pragma: no cover — must not be reached
        raise AssertionError("get_llm must not be called in offline mode")

    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(llm_factory, "get_llm", _explode)

    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle()))
    (hyp,) = delta["hypotheses"]
    assert hyp["id"] == "infra"
    assert hyp["status"] == "validated"
    assert hyp["confidence_basis"] == BASIS_HEURISTIC
    assert delta["spend_llm_calls"] == 0
    assert delta["spend_tokens"] == 0
    # Progress persisted onto the row as the node finished.
    assert quiet_agent and quiet_agent[0]["id"] == "infra"


@pytest.mark.asyncio
async def test_offline_synthesis_is_deterministic(quiet_agent, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    agent = SynthesisAgent()
    state = _state(all_flaky_bundle())
    state["hypotheses"] = [
        {"id": "known_flaky", "status": "validated", "confidence": 88,
         "summary": "flaky coverage 100%", "confidence_basis": BASIS_HEURISTIC},
        {"id": "regression", "status": "invalidated", "confidence": 60,
         "summary": "no core", "confidence_basis": BASIS_HEURISTIC},
    ]
    delta = await agent.run(state)
    verdict = delta["verdict"]
    assert verdict["primary_cause"] == "known_flaky"
    assert verdict["confidence"] == 88
    assert verdict["recommended_actions"]
    assert delta["model_info"] is None  # no LLM engaged offline
    assert delta["spend_llm_calls"] == 0


# ─────────────────────── LLM budget gate ───────────────────────


@pytest.mark.asyncio
async def test_llm_call_budget_zero_blocks_weighing(quiet_agent, monkeypatch):
    """max_llm_calls=0 blocks the weigh call even when NOT offline."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("budget of 0 must block the LLM call")

    import app.services.llm_factory as llm_factory

    from app.agents.investigator.persistence import BudgetReservation

    async def _deny(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="zero-budget",
            allowed=False,
            stop_reason="llm_call_budget_exhausted",
        )

    monkeypatch.setattr(llm_factory, "get_llm", _explode)
    monkeypatch.setattr(hyp_mod, "reserve_investigation_budget", _deny)

    agent = KnownFlakyHypothesisAgent()
    delta = await agent.run(_state(all_flaky_bundle(), max_llm_calls=0))
    (hyp,) = delta["hypotheses"]
    assert hyp["confidence_basis"] == BASIS_HEURISTIC
    assert hyp["llm_enrichment_stop_reason"] == "llm_call_budget_exhausted"
    assert delta["spend_llm_calls"] == 0


@pytest.mark.asyncio
async def test_atomic_budget_denial_is_visible_on_deterministic_hypothesis(
    quiet_agent, monkeypatch,
):
    from app.agents.investigator.persistence import BudgetReservation
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _deny(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="denied",
            allowed=False,
            stop_reason="llm_call_budget_exhausted",
        )

    monkeypatch.setattr(hyp_mod, "reserve_investigation_budget", _deny)
    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle(), max_llm_calls=1))
    (hypothesis,) = delta["hypotheses"]
    assert hypothesis["status"] == "validated"
    assert hypothesis["confidence_basis"] == BASIS_HEURISTIC
    assert hypothesis["llm_enrichment_stop_reason"] == "llm_call_budget_exhausted"
    assert "budget exhausted" in hypothesis["summary"].lower()
    assert delta["spend_llm_calls"] == 0


@pytest.mark.asyncio
async def test_settlement_failure_preserves_deterministic_hypothesis_and_degrades(
    quiet_agent, monkeypatch,
):
    from app.agents.investigator.persistence import BudgetReservation
    from app.core.config import settings
    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _reserve(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="stable-hypothesis",
            allowed=True,
            reserved_llm_calls=1,
            reserved_tokens=500,
        )

    async def _settle(*_args, **_kwargs):
        raise RuntimeError("db password=SUPERSECRET")

    class _Llm:
        async def ainvoke(self, _prompt):
            return SimpleNamespace(
                content='{"status":"validated","confidence":90,"summary":"weighted"}',
                usage_metadata={"input_tokens": 20, "output_tokens": 10},
            )

    async def _get_llm(**_kwargs):
        return _Llm()

    monkeypatch.setattr(hyp_mod, "reserve_investigation_budget", _reserve)
    monkeypatch.setattr(hyp_mod, "settle_investigation_budget", _settle)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)

    delta = await InfraHypothesisAgent().run(_state(pure_infra_bundle()))
    (hypothesis,) = delta["hypotheses"]
    assert hypothesis["status"] == "validated"
    assert hypothesis["llm_enrichment_stop_reason"] == "budget_settlement_failed"
    assert "SUPERSECRET" not in str(hypothesis)


@pytest.mark.asyncio
async def test_cancel_race_after_hypothesis_reservation_skips_provider(
    quiet_agent, monkeypatch,
):
    from app.agents.investigator.persistence import BudgetReservation
    from app.core.config import settings
    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    settled: list[dict] = []

    async def _reserve(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="cancel-race-hypothesis",
            allowed=True,
            reserved_llm_calls=1,
            reserved_tokens=500,
        )

    async def _settle(*_args, **kwargs):
        settled.append(kwargs)
        return True

    async def _get_llm(**_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("cancelled child must not construct a provider")

    async def _cancelled(_investigation_id):
        return True

    monkeypatch.setattr(hyp_mod, "reserve_investigation_budget", _reserve)
    monkeypatch.setattr(hyp_mod, "settle_investigation_budget", _settle)
    monkeypatch.setattr(hyp_mod, "is_cancel_requested", _cancelled)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)

    agent = InfraHypothesisAgent()
    result = await agent._weigh_with_llm(
        agent.evaluate(pure_infra_bundle()), _state(pure_infra_bundle())
    )
    assert result[0] is None
    assert result[2] == "cancelled"
    assert settled and settled[0]["actual_llm_calls"] == 0


@pytest.mark.asyncio
async def test_cancel_race_after_synthesis_reservation_skips_provider(
    quiet_agent, monkeypatch,
):
    from app.agents.investigator.persistence import BudgetReservation
    from app.core.config import settings
    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    settled: list[dict] = []

    async def _reserve(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="cancel-race-synthesis",
            allowed=True,
            reserved_llm_calls=1,
            reserved_tokens=500,
        )

    async def _settle(*_args, **kwargs):
        settled.append(kwargs)
        return True

    async def _get_llm(**_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("cancelled child must not construct a provider")

    async def _cancelled(_investigation_id):
        return True

    monkeypatch.setattr(syn_mod, "reserve_investigation_budget", _reserve)
    monkeypatch.setattr(syn_mod, "settle_investigation_budget", _settle)
    monkeypatch.setattr(syn_mod, "is_cancel_requested", _cancelled)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)

    result = await SynthesisAgent()._narrative_with_llm(
        "infra",
        [{"id": "infra", "status": "validated", "confidence": 80, "summary": "x"}],
        _state(pure_infra_bundle()),
    )
    assert result[0] is None
    assert result[2] == "cancelled"
    assert settled and settled[0]["actual_llm_calls"] == 0


@pytest.mark.asyncio
async def test_investigator_llm_prompts_redact_external_context(
    quiet_agent, monkeypatch,
):
    from app.agents.investigator.persistence import BudgetReservation
    from app.core.config import settings
    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    captured: list[str] = []

    async def _reserve(*_args, **_kwargs):
        return BudgetReservation(
            reservation_id="privacy",
            allowed=True,
            reserved_llm_calls=1,
            reserved_tokens=5000,
        )

    async def _settle(*_args, **_kwargs):
        return True

    class _Llm:
        async def ainvoke(self, prompt):
            captured.append(prompt)
            if "hypothesis_id" in prompt:
                content = '{"status":"validated","confidence":90,"summary":"safe"}'
            else:
                content = '{"narrative":"safe"}'
            return SimpleNamespace(
                content=content,
                usage_metadata={"input_tokens": 20, "output_tokens": 10},
            )

    async def _get_llm(**_kwargs):
        return _Llm()

    monkeypatch.setattr(hyp_mod, "reserve_investigation_budget", _reserve)
    monkeypatch.setattr(hyp_mod, "settle_investigation_budget", _settle)
    monkeypatch.setattr(syn_mod, "reserve_investigation_budget", _reserve)
    monkeypatch.setattr(syn_mod, "settle_investigation_budget", _settle)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)

    bundle = pure_infra_bundle()
    canary = "user@example.com?token=sk-secret123"
    bundle["infra_rule_hits"] = [{"rule_id": "infra", "test_name": canary}]
    await InfraHypothesisAgent().run(_state(bundle))

    synthesis_state = _state({
        **bundle,
        "run": {**(bundle.get("run") or {}), "branch": canary},
    })
    await SynthesisAgent()._narrative_with_llm(
        "infra",
        [{"id": "infra", "status": "validated", "confidence": 90,
          "summary": canary}],
        synthesis_state,
    )

    assert len(captured) == 2
    assert all(canary not in prompt for prompt in captured)
    assert all("sk-secret123" not in prompt for prompt in captured)
    assert any("[REDACTED_EMAIL]" in prompt for prompt in captured)


# ─────────────────────── Budget exhaustion ───────────────────────


@pytest.mark.asyncio
async def test_deadline_exhaustion_yields_inconclusive_leftover(quiet_agent):
    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle(), deadline_offset=-1.0))
    (hyp,) = delta["hypotheses"]
    assert hyp["status"] == "inconclusive"
    assert "budget exhausted" in hyp["summary"].lower()
    assert hyp["confidence_basis"] == BASIS_HEURISTIC


@pytest.mark.asyncio
async def test_synthesis_notes_budget_exhaustion_in_narrative(quiet_agent, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    agent = SynthesisAgent()
    state = _state(pure_infra_bundle(), deadline_offset=-1.0)
    state["hypotheses"] = [
        {"id": "infra", "status": "validated", "confidence": 85,
         "summary": "infra dominates", "confidence_basis": BASIS_HEURISTIC},
        {"id": "commit", "status": "inconclusive", "confidence": 0,
         "summary": "Wall-clock budget exhausted before this hypothesis ran.",
         "confidence_basis": BASIS_HEURISTIC},
    ]
    delta = await agent.run(state)
    verdict = delta["verdict"]
    # Budget exhaustion is honest: verdict still lands (status completed at
    # the runner level) with the leftovers named in the narrative.
    assert verdict["primary_cause"] == "infra"
    assert "budget" in verdict["narrative"].lower()
    assert "commit" in verdict["narrative"]
    assert verdict["degradation"]["budget_exhausted"] is True
    assert verdict["degradation"]["hypotheses_with_stops"] == ["commit"]


# ─────────────────────── Cooperative cancel ───────────────────────


@pytest.mark.asyncio
async def test_cancel_flag_skips_hypothesis_work(quiet_agent, monkeypatch):
    async def _cancelled(_investigation_id):
        return True

    monkeypatch.setattr(hyp_mod, "is_cancel_requested", _cancelled)
    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle()))
    assert delta["hypotheses"] == []
    assert quiet_agent == []  # nothing persisted


@pytest.mark.asyncio
async def test_cancel_flag_skips_synthesis(quiet_agent, monkeypatch):
    async def _cancelled(_investigation_id):
        return True

    monkeypatch.setattr(syn_mod, "is_cancel_requested", _cancelled)
    agent = SynthesisAgent()
    state = _state(pure_infra_bundle())
    state["hypotheses"] = [
        {"id": "infra", "status": "validated", "confidence": 85, "summary": "x"},
    ]
    delta = await agent.run(state)
    assert delta["verdict"] is None


# ─────────────────────── Graph topology + finalize helpers ───────────────────


def test_graph_topology_fans_out_to_all_hypotheses():
    from app.agents.investigator.workflow import _build_graph

    graph = _build_graph()
    edges = {(e[0], e[1]) for e in graph.edges}
    for hyp in (
        "hypothesis_infra",
        "hypothesis_commit",
        "hypothesis_environment",
        "hypothesis_known_flaky",
        "hypothesis_regression",
    ):
        assert ("investigator_plan", hyp) in edges
        assert (hyp, "investigator_synthesis") in edges


def test_merge_hypotheses_keeps_pending_placeholders():
    from app.agents.investigator.workflow import _merge_hypotheses
    from app.services.agent_investigation_service import HYPOTHESIS_IDS

    seeded = [{"id": h, "status": "pending"} for h in HYPOTHESIS_IDS]
    produced = [{"id": "infra", "status": "validated", "confidence": 80}]
    merged = _merge_hypotheses(seeded, produced)
    assert [m["id"] for m in merged] == list(HYPOTHESIS_IDS)
    assert merged[0]["status"] == "validated"
    assert all(m["status"] == "pending" for m in merged[1:])


def test_verdict_summary_line_shapes():
    from app.agents.investigator.workflow import (
        _safe_investigation_error,
        _terminal_status,
        _verdict_summary_line,
    )

    assert "cancelled" in _verdict_summary_line(None, "cancelled")
    assert "failed" in _verdict_summary_line(None, "failed")
    line = _verdict_summary_line(
        {"primary_cause": "infra", "confidence": 85, "narrative": "n" * 500},
        "completed",
    )
    assert "primary_cause=infra" in line and len(line) < 300
    assert _terminal_status(error=None, cancel_requested=True) == "cancelled"
    assert _terminal_status(error="failed", cancel_requested=True) == "failed"
    safe, correlation_id = _safe_investigation_error(
        RuntimeError("db password=SUPERSECRET and token=short-secret")
    )
    assert "SUPERSECRET" not in safe
    assert "short-secret" not in safe
    assert correlation_id in safe


@pytest.mark.asyncio
async def test_finalize_is_idempotent_and_cancellation_updates_pipeline_atomically(
    monkeypatch,
):
    from app.agents.investigator import workflow as workflow_mod
    from app.services import agent_investigation_service as investigation_service

    investigation_id = str(uuid.uuid4())
    pipeline_id = str(uuid.uuid4())
    project_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.UUID(investigation_id),
        project_id=project_id,
        run_id=uuid.uuid4(),
        status="running",
        mode="shadow",
        triggered_by="manual",
        cancel_requested=True,
        hypotheses=[],
        verdict=None,
        model_info=None,
        prompt_versions=None,
        error=None,
        completed_at=None,
        spend={"ledger_version": 2, "llm_calls": 0, "tokens": 0},
    )
    pipeline = SimpleNamespace(
        id=pipeline_id,
        status="running",
        completed_at=None,
        error=None,
        execution_metadata={},
    )
    policy = SimpleNamespace(shadow_runs_completed=0)
    recorded: list[str] = []

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            sql = str(statement)
            if "agent_investigations" in sql:
                return _Result(row)
            if "agent_pipeline_runs" in sql:
                return _Result(pipeline)
            if "agent_policies" in sql:
                return _Result(policy)
            raise AssertionError(sql)

        def add(self, _value):
            return None

        async def commit(self):
            return None

    async def _record(*_args, **_kwargs):
        recorded.append("recorded")

    monkeypatch.setattr(workflow_mod, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(investigation_service, "record_agent_run", _record)
    monkeypatch.setattr(workflow_mod, "_prompt_versions", lambda: {})

    kwargs = {
        "pipeline_run_id": pipeline_id,
        "final_state": {"hypotheses": [], "verdict": None},
        "error": None,
        "wall_seconds": 2.0,
    }
    await workflow_mod._finalize(investigation_id, **kwargs)
    await workflow_mod._finalize(investigation_id, **kwargs)

    assert row.status == "cancelled"
    # E7.1: the pipeline vocabulary has no ``cancelled``; the state machine maps
    # it onto ``failed`` and keeps the reason in the error (public: failed).
    assert pipeline.status == "failed"
    assert pipeline.error.startswith("cancelled: ")
    assert recorded == ["recorded"]
    assert policy.shadow_runs_completed == 0


@pytest.mark.asyncio
async def test_stale_finalizer_reconciles_outbox_in_same_commit(monkeypatch):
    from app.agents.investigator import workflow as workflow_mod
    from app.services import agent_investigation_service as investigation_service

    investigation_id = str(uuid.uuid4())
    row = SimpleNamespace(
        id=uuid.UUID(investigation_id), project_id=uuid.uuid4(),
        run_id=uuid.uuid4(), status="running", mode="shadow",
        triggered_by="auto:cluster_child", cancel_requested=False,
        hypotheses=[], verdict=None, model_info=None, prompt_versions=None,
        error=None, completed_at=None,
        spend={"ledger_version": 2, "llm_calls": 0, "tokens": 0},
    )
    outbox = SimpleNamespace(
        status="sent", next_attempt_at=None, last_error=None
    )
    commits: list[str] = []

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return list(self.value)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            sql = str(statement)
            if "agent_investigations" in sql:
                return _Result(row)
            if "agent_child_dispatch_outbox" in sql:
                return _Result([outbox])
            if "agent_pipeline_runs" in sql:
                return _Result(None)
            raise AssertionError(sql)

        def add(self, _value):
            return None

        async def commit(self):
            assert row.status == "failed"
            assert outbox.status == "failed"
            commits.append("atomic")

    async def _record(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow_mod, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(investigation_service, "record_agent_run", _record)
    monkeypatch.setattr(workflow_mod, "_prompt_versions", lambda: {})

    await workflow_mod._finalize(
        investigation_id,
        pipeline_run_id=str(uuid.uuid4()),
        final_state=None,
        error="Investigation execution lease expired.",
        wall_seconds=600.0,
        outbox_failure_reason="investigation_execution_lease_expired",
    )

    assert commits == ["atomic"]
    assert outbox.next_attempt_at is None
    assert outbox.last_error == "investigation_execution_lease_expired"


@pytest.mark.asyncio
async def test_cluster_bundle_fails_closed_when_db_membership_is_stale(
    monkeypatch,
):
    from app.agents.investigator import workflow as workflow_mod
    from app.services.agent_planner import compute_cluster_scope_sha256
    import app.services.github_pr_comment_service as baseline_service

    run_id, project_id, parent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cluster_id, planned_member = uuid.uuid4(), uuid.uuid4()
    cluster = SimpleNamespace(
        id=cluster_id, test_run_id=run_id, pipeline_run_id=parent_id,
        cluster_id="cl_scope", member_test_ids=[str(planned_member)],
    )
    run = SimpleNamespace(
        id=run_id, project_id=project_id, build_number="b1", branch="main",
        commit_hash=None, ci_repo=None, pr_number=None, ci_run_url=None,
        duration_ms=1, total_tests=1, failed_tests=1, broken_tests=0,
        ocp_namespace=None, ocp_node=None, suite_names=[],
    )
    investigation = SimpleNamespace(
        run_id=run_id, project_id=project_id, scope_type="failure_cluster",
        failure_cluster_id=cluster_id, parent_pipeline_run_id=parent_id,
        cluster_member_test_ids=[str(planned_member)],
        cluster_scope_sha256=compute_cluster_scope_sha256(
            project_id, run_id, parent_id, cluster_id, [planned_member]
        ),
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def all(self):
            return list(self.value)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result(run), _Result(cluster), _Result([]),
    ])
    class _Context:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(workflow_mod, "AsyncSessionLocal", _Context)
    monkeypatch.setattr(
        baseline_service, "_select_baseline", AsyncMock(return_value=None)
    )

    with pytest.raises(ValueError, match="cluster_member_authority_invalid"):
        await workflow_mod._gather_bundle(investigation)


@pytest.mark.asyncio
async def test_completed_hypothesis_is_not_replayed_on_child_resume(
    quiet_agent, monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    def _explode(*_args, **_kwargs):
        raise AssertionError("completed hypothesis must not invoke the provider")

    import app.services.llm_factory as llm_factory
    monkeypatch.setattr(llm_factory, "get_llm", _explode)

    state = _state(pure_infra_bundle())
    state["resume_completed_hypotheses"] = {"infra"}
    delta = await InfraHypothesisAgent().run(state)

    assert delta == {"hypotheses": [], "errors": []}
    assert quiet_agent == []


@pytest.mark.asyncio
async def test_resume_investigation_resets_only_incomplete_stage_and_reuses_identity(
    monkeypatch,
):
    from app.agents.investigator import workflow as workflow_mod

    investigation_id = uuid.uuid4()
    pipeline_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}"
    )
    completed = SimpleNamespace(
        stage_name="hypothesis_infra", status="completed", attempt=1,
        started_at=object(), completed_at=object(), error=None,
        stop_reason=None, skipped_reason=None, execution_path="executed",
        idempotency_key="a" * 64, result_data={"hypothesis": "infra"},
    )
    pending = SimpleNamespace(
        stage_name="hypothesis_commit", status="failed", attempt=1,
        started_at=object(), completed_at=object(), error="old",
        stop_reason="provider_error", skipped_reason="old",
        execution_path="executed", idempotency_key="b" * 64,
        result_data={"old": True},
    )
    row = SimpleNamespace(
        id=investigation_id, scope_type="failure_cluster", status="failed",
        spend={"reservations": {}}, hypotheses=[
            {"id": "infra", "status": "validated"},
        ], verdict=None, started_at=object(), completed_at=object(),
        error="old", cancel_requested=True, cancelled_by="timeout",
    )
    pipeline = SimpleNamespace(
        id=pipeline_id, status="failed", started_at=object(),
        completed_at=object(), error="old",
    )

    class _Result:
        def __init__(self, value):
            self.value = value
        def scalar_one_or_none(self):
            return self.value
        def scalars(self):
            return self
        def all(self):
            return list(self.value)

    class _Session:
        def __init__(self):
            self.results = iter((_Result(row), _Result([completed, pending]), _Result(pipeline)))
            self.commits = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return False
        async def execute(self, _statement):
            return next(self.results)
        async def commit(self):
            self.commits += 1

    session = _Session()
    monkeypatch.setattr(workflow_mod, "AsyncSessionLocal", lambda: session)
    captured = {}

    async def _run(investigation_id, **kwargs):
        captured["id"] = investigation_id
        captured["kwargs"] = kwargs
        return {"resumed": True}

    monkeypatch.setattr(workflow_mod, "run_investigation", _run)
    result = await workflow_mod.resume_investigation(str(investigation_id))

    assert result == {"resumed": True}
    assert captured["id"] == str(investigation_id)
    assert captured["kwargs"]["resume"] is True
    assert completed.status == "completed"
    assert pending.status == "pending"
    assert pending.attempt == 2
    assert pending.idempotency_key is None
    assert row.status == "queued"
    assert pipeline.status == "pending"
    assert session.commits == 1
