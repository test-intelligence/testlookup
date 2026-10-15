"""Regression guard: a skipped stage says WHY, unambiguously.

The problem
-----------
Four specialist stages recorded the same rationale when they were not planned::

    "Contract Agent is disabled or the run has no failed tests"

Two different answers to the operational question, joined by "or". Only one of
them is actionable: if the flag is off, turning it on makes the stage run; if
the run was all-green, there was simply nothing to do and no configuration
change would alter that.

This was not academic. Establishing why four capabilities had never executed on
a live deployment took five queries, because the plan would not say which case
it was. The rest of the planner is already precise —
``"AIQ gap/refinement feature flag is off for this project"`` (W04 AGT-16),
``"no analyses meet confidence threshold 0.8 for defect triage"`` — so these
four were the exception, not the convention.

What is guarded
---------------
* a flag-disabled stage says the flag is off;
* an all-green run says there were no failed tests;
* the two never collapse into one ambiguous string again;
* an enabled stage on a failing run is still planned.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.services.agent_planner import build_workflow_plan  # noqa: E402

_STAGES = {
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


def _plan(*, failed_test_ids, **flags):
    enabled = {flag: False for flag in _STAGES.values()}
    enabled.update(flags)
    return build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=failed_test_ids,
        analyses={},
        **enabled,
    )


# ── The flag being off is stated as such ─────────────────────────────────────


@pytest.mark.parametrize("stage,flag", list(_STAGES.items()))
def test_a_disabled_stage_says_the_flag_is_off(stage, flag):
    """The actionable case: turning the flag on would make this run."""
    entry = _entry(_plan(failed_test_ids=["t1"], **{flag: False}), stage)

    assert entry["planned"] is False
    assert "flag is off" in entry["rationale"], entry["rationale"]
    assert " or " not in entry["rationale"], (
        "the rationale must name one cause, not offer two"
    )


# ── An all-green run is stated as such ───────────────────────────────────────


@pytest.mark.parametrize("stage,flag", list(_STAGES.items()))
def test_an_all_green_run_says_there_were_no_failures(stage, flag):
    """The non-actionable case: no flag change would alter this."""
    entry = _entry(_plan(failed_test_ids=[], **{flag: True}), stage)

    assert entry["planned"] is False
    assert "no failed tests" in entry["rationale"], entry["rationale"]
    assert "flag is off" not in entry["rationale"], (
        "an enabled stage must not be reported as disabled"
    )


# ── The two causes never collapse again ──────────────────────────────────────


@pytest.mark.parametrize("stage,flag", list(_STAGES.items()))
def test_the_two_causes_are_distinguishable(stage, flag):
    disabled = _entry(_plan(failed_test_ids=["t1"], **{flag: False}), stage)
    all_green = _entry(_plan(failed_test_ids=[], **{flag: True}), stage)

    assert disabled["rationale"] != all_green["rationale"], (
        "a reader cannot act on a reason that covers both cases"
    )


@pytest.mark.parametrize("stage,flag", list(_STAGES.items()))
def test_no_rationale_offers_a_disjunction(stage, flag):
    """'X is disabled or the run has no failed tests' is the shape being
    removed; it answers the question with a question."""
    for entry in _plan(failed_test_ids=["t1"], **{flag: False})["stages"]:
        rationale = str(entry.get("rationale") or "")
        assert "disabled or" not in rationale, rationale


# ── The enabled path still works ─────────────────────────────────────────────


@pytest.mark.parametrize("stage,flag", list(_STAGES.items()))
def test_an_enabled_stage_on_a_failing_run_is_planned(stage, flag):
    entry = _entry(_plan(failed_test_ids=["t1"], **{flag: True}), stage)

    assert entry["planned"] is True
    assert "enabled" in entry["rationale"]


# ── W04 AGT-16: the gap/refinement stages follow the same convention ─────────


@pytest.mark.parametrize("stage", ["gap_detection", "report_refinement"])
def test_the_gap_refinement_stages_say_the_project_flag_is_off(stage):
    """Off -> one actionable cause naming the project flag; on -> planned."""
    off = _entry(_plan(failed_test_ids=["t1"], gap_refinement_enabled=False), stage)
    on = _entry(_plan(failed_test_ids=["t1"], gap_refinement_enabled=True), stage)

    assert off["planned"] is False
    assert off["rationale"] == "AIQ gap/refinement feature flag is off for this project"
    assert "feature flag is off for this project" in off["rationale"]
    assert " or " not in off["rationale"]
    assert on["planned"] is True
    assert on["rationale"] == "AIQ-P4 gap/refinement stage enabled by feature flag"


def test_gap_refinement_enabled_must_be_a_boolean():
    with pytest.raises(ValueError, match="gap_refinement_enabled must be a boolean"):
        _plan(failed_test_ids=["t1"], gap_refinement_enabled="yes")
