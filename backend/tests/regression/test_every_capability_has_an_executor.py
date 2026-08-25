"""Regression guard: a declared capability is one something actually runs.

The defect
----------
``agent_capability_registry`` is the canonical description of what the agent
layer can do. ``defect_commander`` sat in it looking exactly like a pipeline
stage::

    _capability("defect_commander", inputs="AnalysisAgentOutput",
                output="DefectCommanderOutput",
                dependencies=("root_cause_analysis",),
                evidence=("analysis_findings",),
                concurrency_class="action_proposal", permission="mutating")

Dependencies on ``root_cause_analysis``, a mutating permission, an
action-proposal concurrency class — and **no workflow's stage order contains
it**. On the deployment it had never written a single ``agent_stage_results``
row in the entire history of the table.

Nothing is wrong with the agent. It runs on demand via
``POST /api/v1/agents/defect-command``. What was wrong was the *record*, which
described a mutating pipeline stage that nothing plans — the same
documentation-drifts-from-behaviour shape as the two "not wired into any
workflow node" comments corrected in #844, which were stale in the other
direction.

What is guarded
---------------
Both directions of the rule, because a one-way check is half a rule:

* a capability declaring ``execution="planned"`` appears in a stage order;
* a capability appearing in a stage order does not claim to be
  ``on_demand`` / ``child_spawned`` / ``runtime``.

Plus the vacuity case: the guard reads the planner's stage tuples by name, so a
renamed tuple must fail loudly rather than silently shrink the planned set.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.services import agent_planner as planner  # noqa: E402
from app.services.agent_capability_registry import _SPECS  # noqa: E402

NON_PLANNED = {"child_spawned", "on_demand", "runtime"}

# What each non-planned capability is executed BY. Named so that deleting the
# executor and leaving the declaration behind is a test failure, not a silent
# return to the original defect.
EXECUTORS = {
    "defect_commander": "on_demand",       # POST /api/v1/agents/defect-command
    "cluster_investigation": "child_spawned",  # dispatched per cluster
    "workflow": "runtime",                 # terminal-failure bookkeeping
}


def _planned_stages() -> set[str]:
    return (
        set(planner._DEEP_STAGES)
        | set(planner._OFFLINE_STAGES)
        | set(planner._LIVE_STAGES)
        | set(planner._INVESTIGATION_STAGES)
    )


def test_a_planned_capability_is_actually_planned():
    planned = _planned_stages()
    orphans = sorted(
        spec.stage_name for spec in _SPECS
        if spec.execution == "planned" and spec.stage_name not in planned
    )
    assert not orphans, (
        "these describe a pipeline stage that no workflow runs — give them an "
        f"executor or declare how they really run: {orphans}"
    )


def test_a_non_planned_capability_is_not_secretly_planned():
    """The other direction: the declaration must not contradict the planner."""
    planned = _planned_stages()
    contradictions = sorted(
        spec.stage_name for spec in _SPECS
        if spec.execution in NON_PLANNED and spec.stage_name in planned
    )
    assert not contradictions, (
        f"declared non-planned but the planner plans them: {contradictions}"
    )


@pytest.mark.parametrize("stage,execution", sorted(EXECUTORS.items()))
def test_the_known_non_planned_capabilities_keep_their_declaration(stage, execution):
    """`defect_commander` is the one this was written for.

    It is `permission="mutating"` and creates Jira tickets, so it must not be
    quietly folded into the deep pipeline: that would start filing real tickets
    on every run. Pinning the declaration makes such a change deliberate.
    """
    spec = next((s for s in _SPECS if s.stage_name == stage), None)
    assert spec is not None, f"{stage} disappeared from the registry"
    assert spec.execution == execution, (
        f"{stage} changed executor to {spec.execution!r}; if that is intended, "
        "update EXECUTORS here and say why in the CHANGELOG"
    )


def test_defect_commander_is_reachable_through_its_endpoint():
    """`on_demand` has to name a real door, or it is just a nicer excuse.

    Declaring an executor that does not exist would be the same defect wearing
    a different label.
    """
    from app.routers import agents as agents_router

    routes = {
        getattr(route, "path", "") for route in agents_router.router.routes
    }
    assert any(path.endswith("/defect-command") for path in routes), (
        "defect_commander declares execution='on_demand' but no endpoint serves it"
    )


def test_the_planner_still_exposes_the_tuples_the_guard_reads():
    """Vacuity: the quality gate finds these by name in the planner source."""
    for name in ("_OFFLINE_STAGES", "_LIVE_STAGES", "_INVESTIGATION_STAGES", "_DEEP_STAGES"):
        assert getattr(planner, name, None), (
            f"{name} is gone or empty — the capability guard reads it by name and "
            "would silently stop covering that workflow type"
        )
    assert len(_planned_stages()) >= 19, (
        "the planned-stage set collapsed; the guard above would pass vacuously"
    )
