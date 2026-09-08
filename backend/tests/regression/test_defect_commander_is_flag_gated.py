"""Regression guard: the deep pipeline's only mutating stage stays off by default.

``defect_commander`` writes a ``Defect`` row and, when ``JIRA_ENABLED`` is set,
files a real ticket. It is now a planned stage in ``_DEEP_STAGES`` rather than
an endpoint-only capability, which means a defaulting bug here does not produce
a wrong number on a dashboard — it files tickets in someone's tracker on every
deep run.

What is guarded
---------------
* **Off unless explicitly enabled.** The plan does not schedule it, and the
  node returns a skip WITHOUT touching the stage lifecycle, so "off" cannot be
  mistaken for "ran and promoted nothing".
* **The flag survives the plan rebuild.** ``attach_workflow_plan_and_verification``
  re-derives the plan from the stored one; a kwarg left to its default there
  silently rewrites it, and the verifier then checks execution against a plan
  that never applied. That is exactly #839 — four agents ran while every record
  said "flag off" — and this stage is the one where that failure mode would
  mutate external state.
* **Off and nothing-to-promote stay distinguishable**, in the plan rationale
  and in the recorded decision.
* **Cluster selection is deterministic.** ``_promote`` acts on one cluster
  while the deep state carries a list; a non-deterministic pick would file a
  different defect on each re-run of identical evidence.
* **The fan-out depth is preserved.** ``gap_detection``'s predecessors must all
  sit one hop from ``cluster_investigation_join``; a predecessor at a different
  depth makes the join fire twice and re-runs everything downstream.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents import workflow as wf  # noqa: E402
from app.agents.defect_commander import DefectCommander  # noqa: E402
from app.services import agent_planner as ap  # noqa: E402
from app.services.agent_capability_registry import get_capability  # noqa: E402


def _stage(plan: dict) -> dict:
    return next(s for s in plan["stages"] if s["stage"] == "defect_commander")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget_state",
    [
        {"_cost_budget_mode_override": "ml", "_cost_budget_block": False},
        {"_cost_budget_mode_override": "rules", "_cost_budget_block": False},
        {"_cost_budget_mode_override": None, "_cost_budget_block": True},
    ],
)
async def test_cost_budget_state_never_constructs_llm_for_jira_content(
    monkeypatch,
    budget_state,
):
    from types import SimpleNamespace

    from app.services import defect_promotion_service

    get_llm = AsyncMock(side_effect=AssertionError("LLM must remain gated"))
    monkeypatch.setattr(defect_promotion_service, "get_llm", get_llm)
    cluster = SimpleNamespace(
        label="checkout timeout",
        size=3,
        representative_error="request timed out",
    )

    content = await DefectCommander._jira_content_for_state(
        budget_state,
        cluster,
        [],
    )

    get_llm.assert_not_awaited()
    assert content["title"] == "checkout timeout"
    assert content["component"] == "Unknown"


# ── Default off ──────────────────────────────────────────────────────────────


def test_the_plan_does_not_schedule_it_by_default():
    plan = ap.build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"])

    assert _stage(plan)["planned"] is False, "a mutating stage must not default to on"
    assert plan["specialist_flags"]["defect_commander"] is False
    assert "flag is off" in _stage(plan)["rationale"]


def test_enabling_the_flag_schedules_it():
    plan = ap.build_workflow_plan(
        workflow_type="deep", failed_test_ids=["t1"], defect_commander_enabled=True
    )

    assert _stage(plan)["planned"] is True
    assert plan["specialist_flags"]["defect_commander"] is True


def test_off_and_nothing_to_promote_are_different_answers():
    """Only one of them is fixable by changing a flag."""
    off = ap.build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"])
    green = ap.build_workflow_plan(
        workflow_type="deep", failed_test_ids=[], defect_commander_enabled=True
    )

    assert _stage(off)["rationale"] != _stage(green)["rationale"]
    assert "flag is off" in _stage(off)["rationale"]
    assert "no failed tests" in _stage(green)["rationale"]


@pytest.mark.parametrize("enabled", [False, True])
def test_the_flag_survives_the_plan_rebuild(enabled):
    """#839: a defaulted kwarg in the rebuild rewrites the plan silently."""
    original = ap.build_workflow_plan(
        workflow_type="deep", failed_test_ids=["t1"], defect_commander_enabled=enabled
    )
    rebuilt = ap.attach_workflow_plan_and_verification(
        {"initial_workflow_plan": original, "failed_test_ids": ["t1"], "analyses": {}},
        workflow_type="deep",
    )["workflow_plan"]

    assert rebuilt["specialist_flags"]["defect_commander"] is enabled, (
        "the rebuild lost the flag — the verifier would check execution against "
        "a plan that never applied"
    )
    assert _stage(rebuilt)["planned"] is enabled


# ── The node ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_disabled_node_never_touches_the_stage_lifecycle(monkeypatch):
    """A skip is not a run — especially for a stage that mutates."""
    running, done = AsyncMock(), AsyncMock()
    monkeypatch.setattr(DefectCommander, "mark_stage_running", running)
    monkeypatch.setattr(DefectCommander, "mark_stage_done", done)
    promote = AsyncMock()
    monkeypatch.setattr(DefectCommander, "_promote", promote)

    delta = await wf.defect_commander_node({"pipeline_run_id": "pipe-1", "project_id": "p1"})

    assert delta["defect_promotion"] is None
    assert "defect_commander" in delta["skipped_stages"]
    assert running.await_count == 0
    assert done.await_count == 0
    assert promote.await_count == 0, "the disabled path must not promote anything"


@pytest.mark.asyncio
async def test_an_enabled_run_with_no_cluster_records_why(monkeypatch):
    """A mutating stage that mutated nothing has to say so."""
    decisions: list[dict] = []

    async def _capture(_self, _run_id, **kwargs):
        decisions.append(kwargs)

    monkeypatch.setattr(DefectCommander, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(DefectCommander, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(DefectCommander, "broadcast_progress", AsyncMock())
    monkeypatch.setattr(DefectCommander, "log_decision", _capture)
    promote = AsyncMock()
    monkeypatch.setattr(DefectCommander, "_promote", promote)

    delta = await wf.defect_commander_node({
        "pipeline_run_id": "pipe-1",
        "project_id": "p1",
        "defect_commander_enabled": True,
        "failure_clusters": [],
    })

    assert delta["defect_promotion"] is None
    assert "defect_commander" not in (delta.get("skipped_stages") or []), (
        "it ran — calling that a skip would hide the difference from flag-off"
    )
    assert promote.await_count == 0
    assert any(d["chosen"] == "no_cluster" for d in decisions), decisions


# ── Cluster selection ────────────────────────────────────────────────────────


def test_cluster_selection_is_deterministic_and_prefers_the_largest():
    clusters = [
        {"cluster_id": "cl_b", "size": 2},
        {"cluster_id": "cl_a", "size": 9},
        {"cluster_id": "cl_c", "size": 9},
    ]
    picked = DefectCommander.select_cluster_id({"failure_clusters": clusters})

    assert picked == "cl_a", "largest first, cluster_id as tie-break"
    for _ in range(5):
        assert DefectCommander.select_cluster_id({"failure_clusters": clusters}) == picked


def test_an_explicit_cluster_id_still_wins():
    """The standalone endpoint drives this agent that way and must keep working."""
    picked = DefectCommander.select_cluster_id(
        {"cluster_id": "chosen", "failure_clusters": [{"cluster_id": "other", "size": 99}]}
    )
    assert picked == "chosen"


def test_no_clusters_selects_nothing():
    assert DefectCommander.select_cluster_id({"failure_clusters": []}) is None
    assert DefectCommander.select_cluster_id({}) is None


# ── Wiring ───────────────────────────────────────────────────────────────────


def test_it_is_a_planned_capability_that_still_declares_itself_mutating():
    capability = get_capability("defect_commander")

    assert capability.execution == "planned", "it is in _DEEP_STAGES now"
    assert capability.permission == "mutating", (
        "downgrading this would let it slip past mutating-action policy checks"
    )
    assert "defect_commander" in ap._DEEP_STAGES


def test_the_specialist_fan_out_keeps_every_member_at_one_depth():
    """A predecessor at a different depth makes gap_detection fire twice."""
    source = inspect.getsource(wf._build_deep_graph)
    tree = ast.parse(source.lstrip())

    fan_out = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.For)
        and any(
            isinstance(c, ast.Constant) and c.value == "cluster_investigation_join"
            for c in ast.walk(n)
        )
    ]
    assert fan_out, "the specialist fan-out loop is gone — re-check the graph shape"

    members = {
        c.value for c in ast.walk(fan_out[0].iter)
        if isinstance(c, ast.Constant) and isinstance(c.value, str)
    }
    assert "defect_commander" in members, (
        "defect_commander must join the SAME fan-out, not hang off a deeper edge"
    )
    assert {"contract_validation", "log_intelligence", "regression_watchman",
            "change_ownership"} <= members
