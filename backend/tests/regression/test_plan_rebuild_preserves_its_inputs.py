"""Regression guard: the explained plan is rebuilt with the SAME inputs.

The problem
-----------
``attach_workflow_plan_and_verification`` does not reuse the plan the pipeline
actually ran under. It calls ``build_workflow_plan`` a second time to produce an
"explained" plan carrying real outcomes, then overwrites ``workflow_plan`` with
the result and verifies execution against it.

That second call forwarded only ``workflow_type``, ``failed_test_ids``,
``analyses`` and the cluster settings. Every other input fell back to its kwarg
default -- and the four specialist flags default to ``False``. So the rebuilt
plan always said::

    "RegressionWatchman feature flag is off for this project"

no matter what the flags were. Measured on the deployment 2026-08-24 with all
four flags ON: ``agent_contracts`` held contracts for ``contract_validation``,
``change_ownership`` and ``regression_watchman`` -- the agents had just run --
while the rebuilt plan called all four unplanned, the stage rows recorded them
"skipped", and the verifier warned ``unplanned_stages_not_executed``. The
decision critic then failed closed on ``completed_agent_contracts_present`` and
took every deep run to ``partial``.

A defaulted kwarg is the dangerous shape here: it does not raise, it quietly
answers a different question. The sibling guard
``test_plan_rationale_names_the_real_cause`` covers ``build_workflow_plan``
directly and passed throughout -- the bug lived in the round trip, which nothing
exercised.

What is guarded
---------------
* enabled specialist flags survive the rebuild;
* disabled ones stay disabled (the fix must not invert the default);
* the plan carries the flags, so the rebuild has something to recover;
* ``triage_threshold`` and the decision-graph budget survive too;
* a plan with different flags is a different plan (distinct ``plan_sha256``).
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.services.agent_planner import (  # noqa: E402
    attach_workflow_plan_and_verification,
    build_workflow_plan,
)

_FLAGS = {
    "contract_validation": "contract_validation_enabled",
    "log_intelligence": "log_intelligence_enabled",
    "regression_watchman": "regression_watchman_enabled",
    "change_ownership": "change_ownership_enabled",
}


def _entry(plan: dict, stage: str) -> dict:
    for item in plan.get("stages", []):
        if isinstance(item, dict) and item.get("stage") == stage:
            return item
    raise AssertionError(f"{stage} missing from the plan")


def _built(**overrides):
    kwargs = {
        "workflow_type": "deep",
        "failed_test_ids": ["t1"],
        "analyses": {},
    }
    kwargs.update({flag: True for flag in _FLAGS.values()})
    kwargs.update(overrides)
    return build_workflow_plan(**kwargs)


def _rebuilt(plan: dict) -> dict:
    state = attach_workflow_plan_and_verification(
        {"initial_workflow_plan": plan, "failed_test_ids": ["t1"], "analyses": {}},
        workflow_type="deep",
    )
    return state["workflow_plan"]


# ── The flags survive the round trip ─────────────────────────────────────────


@pytest.mark.parametrize("stage", list(_FLAGS))
def test_an_enabled_specialist_is_still_planned_after_the_rebuild(stage):
    """The exact failure: four running agents reported as "flag is off"."""
    entry = _entry(_rebuilt(_built()), stage)

    assert entry["planned"] is True, (
        f"{stage} was enabled and has failed tests, but the rebuilt plan "
        f"dropped it: {entry['rationale']}"
    )
    assert "flag is off" not in entry["rationale"], entry["rationale"]


@pytest.mark.parametrize("stage,flag", list(_FLAGS.items()))
def test_a_disabled_specialist_stays_disabled_after_the_rebuild(stage, flag):
    """The fix must recover the real value, not default the other way."""
    entry = _entry(_rebuilt(_built(**{flag: False})), stage)

    assert entry["planned"] is False
    assert "flag is off" in entry["rationale"], entry["rationale"]


# ── The plan carries what the rebuild needs ──────────────────────────────────


def test_the_plan_records_the_specialist_flags():
    """Nothing can be recovered from a plan that never stored it."""
    plan = _built(log_intelligence_enabled=False)

    # Exhaustive equality on purpose: a flag added to the plan without being
    # threaded through the REBUILD is exactly the #839 defect, and an
    # `is-subset` assertion would not notice the new key. This fired when
    # `defect_commander` was added, which is the check doing its job.
    assert plan["specialist_flags"] == {
        "contract_validation": True,
        "log_intelligence": False,
        "regression_watchman": True,
        "change_ownership": True,
        # Not passed by this test, so it records the default — off.
        "defect_commander": False,
    }


def test_the_rebuild_preserves_threshold_and_graph_budget():
    """The same defaulted-kwarg hole, in the two other dropped inputs."""
    budget = {
        "max_llm_calls": 7,
        "max_tokens": 1234,
        "max_cost_usd": 0.5,
        "max_seconds": 60,
    }
    rebuilt = _rebuilt(_built(threshold=55, decision_graph_aggregate_budget=budget))

    assert rebuilt["triage_threshold"] == 55
    assert rebuilt["decision_graph_aggregate_budget"] == budget


# ── Different flags mean a different plan ────────────────────────────────────


def test_flags_participate_in_the_plan_digest():
    """If the digest ignored the flags, two different plans would alias."""
    all_on = _built()
    one_off = _built(regression_watchman_enabled=False)

    assert all_on["plan_sha256"] != one_off["plan_sha256"]
