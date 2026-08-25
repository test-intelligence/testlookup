"""Regression guard: every planned stage gets a row to write into.

The defect
----------
``_create_pipeline_run`` seeds one ``AgentStageResult`` row per stage, from a
hand-maintained list in ``workflow.py``. The planner has its own list. They were
two copies of one rule, and they drifted the moment a stage was added:
``defect_commander`` went into the planner's ``_DEEP_STAGES`` and not into the
seeding list.

The consequence is quiet. ``mark_stage_running`` looks the row up and returns
without writing when it is missing, so with its flag on the stage would have
executed and recorded nothing — no status, no timestamps, no decision trail.
That is the same "a stage that ran was recorded as never having run" defect as
#840 and #844, arriving through a list nobody thought of as a rule.

It survived a green unit suite because no test exercised ``_create_pipeline_run``,
and it was caught only by reading ``agent_stage_results`` on the deployment
after a real run: zero rows for a stage the plan named.

What is guarded
---------------
The seeding list is now DERIVED from the planner, so the two cannot disagree.
This pins that, per workflow type, and fails loudly if the derivation is ever
replaced by a literal again.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.agents import workflow as wf  # noqa: E402
from app.services import agent_planner as ap  # noqa: E402

# workflow type -> (seeded rows, planner stage order)
WORKFLOWS = {
    "deep": (wf._DEEP_PIPELINE_STAGES, ap._DEEP_STAGES),
    "live": (wf._LIVE_PIPELINE_STAGES, ap._LIVE_STAGES),
    "offline": (wf._PIPELINE_STAGES, ap._OFFLINE_STAGES),
}


@pytest.mark.parametrize("workflow_type", sorted(WORKFLOWS))
def test_every_planned_stage_is_seeded_a_row(workflow_type):
    seeded, planned = WORKFLOWS[workflow_type]

    missing = sorted(set(planned) - set(seeded))
    assert not missing, (
        f"{workflow_type}: planned but never seeded a row — the stage would run "
        f"and mark_stage_running would find nothing to write to: {missing}"
    )


@pytest.mark.parametrize("workflow_type", sorted(WORKFLOWS))
def test_no_row_is_seeded_for_a_stage_the_planner_never_runs(workflow_type):
    """The other direction: a seeded row nothing fills stays `pending` forever
    and is then relabelled `skipped`, which reads as a deliberate skip."""
    seeded, planned = WORKFLOWS[workflow_type]

    extra = sorted(set(seeded) - set(planned))
    assert not extra, (
        f"{workflow_type}: seeded a row for a stage the planner never runs: {extra}"
    )


def test_the_deep_seeding_list_is_derived_not_copied():
    """A literal here is how the two drifted in the first place."""
    assert wf._DEEP_PIPELINE_STAGES == list(ap._DEEP_STAGES), (
        "the seeded deep stages are no longer the planner's list — if this is "
        "deliberate, the two-copies problem is back"
    )
    assert wf._LIVE_PIPELINE_STAGES == list(ap._LIVE_STAGES)


def test_the_guard_can_actually_see_the_stages():
    """A guard comparing two empty lists agrees with itself and proves nothing."""
    assert len(ap._DEEP_STAGES) >= 20, (
        f"only {len(ap._DEEP_STAGES)} deep stages — the comparison above would "
        "pass vacuously"
    )
    assert "defect_commander" in ap._DEEP_STAGES, (
        "the stage this guard was written for dropped out of the deep plan"
    )
