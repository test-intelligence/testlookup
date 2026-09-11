"""Re-audit N34: the release consistency check replays the gate's own mapping.

On the homelab, 76 of 215 release decisions carried
``consistency_check_failed:recommendation_vs_score``. Replaying the gate's
real mapping over the 56 of them that have a stored release decision explained
every one: 50 were NO_GO from the pass-rate hard floor, 6 were CONDITIONAL_GO
from a pass rate under the minimum. The check compared the recommendation with
a score-only band -- two modules, one rule. The ROWS below are real homelab
values (score, pass rate, thresholds, recommendation).
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from app.agents.consistency import check_release_consistency
from app.services.policy_evaluator_service import escalate_recommendation, evaluate_policy

DEFAULT_THRESHOLDS = {
    "go_threshold": 20, "no_go_threshold": 55,
    "pass_rate_minimum": 90.0, "pass_rate_hard_floor_factor": 0.7,
}

#: (recommendation, composite, pass_rate) from the homelab export; every one
#: was flagged by the old check.
ROWS = [
    ("NO_GO", 41.8, 44.44),           # hard floor: 44.44 < 63
    ("CONDITIONAL_GO", 17.8, 75.0),   # GO band, pass rate under 90
    ("NO_GO", 29.7, 55.56),           # hard floor
    ("NO_GO", 22.4, 50.0),            # hard floor at the bottom of the CG band
    ("CONDITIONAL_GO", 9.3, 85.0),    # GO band, pass rate under 90
]


def _decision(recommendation, composite, pass_rate, *, thresholds=None, rules=None,
              kind_applied=False, in_snapshot=True):
    decision = {
        "recommendation": recommendation,
        "risk_score": int(composite),
        "composite_risk": composite,
        "reasoning": "",
        "blocking_issues": [],
        "policy_id": None,
        "policy_evaluation": {
            "effective_composite": composite,
            "effective_thresholds": thresholds or DEFAULT_THRESHOLDS,
            "rule_evaluations": rules or [],
            "kind_rule_applied": kind_applied,
        },
    }
    if in_snapshot:
        decision["input_snapshot"] = {"pass_rate": pass_rate}
    else:
        decision["pass_rate"] = pass_rate
    return decision


def _check(report):
    return next(c for c in report.checks if c.name == "recommendation_vs_score")


@pytest.mark.parametrize("recommendation,composite,pass_rate", ROWS)
def test_real_homelab_decisions_are_consistent(recommendation, composite, pass_rate):
    report = check_release_consistency(_decision(recommendation, composite, pass_rate))
    check = _check(report)
    assert check.passed is True, check.details
    assert report.decision_suffix() == "; consistency_ok"


@pytest.mark.parametrize("recommendation,composite,pass_rate", ROWS)
def test_the_old_score_only_band_would_have_flagged_them(recommendation, composite, pass_rate):
    """The control: without the recorded inputs the legacy path still flags
    these rows, so the test above is passing because of the replay."""
    report = check_release_consistency({
        "recommendation": recommendation, "risk_score": int(composite), "reasoning": "",
    })
    assert _check(report).passed is False


def test_a_recommendation_that_disagrees_with_its_own_inputs_is_an_error():
    # GO at composite 60 with a healthy pass rate: the mapping says NO_GO.
    check = _check(check_release_consistency(_decision("GO", 60.0, 99.0)))
    assert check.passed is False
    assert check.severity == "error"
    assert "gives NO_GO" in check.details


def test_a_verdict_edited_without_its_inputs_is_an_error():
    # NO_GO recorded, but score 10 and pass rate 100 map to GO and no rule fired.
    check = _check(check_release_consistency(_decision("NO_GO", 10.0, 100.0)))
    assert check.passed is False
    assert check.severity == "error"


def test_policy_rules_and_thresholds_are_replayed():
    block = [{"action": "BLOCK", "passed": False}]
    warn = [{"action": "WARN", "passed": False}]
    assert _check(check_release_consistency(_decision("NO_GO", 5.0, 99.0, rules=block))).passed
    assert _check(check_release_consistency(_decision("CONDITIONAL_GO", 5.0, 99.0, rules=warn))).passed
    # A WARN never lifts a NO_GO and never lowers a CONDITIONAL_GO.
    assert not _check(check_release_consistency(_decision("CONDITIONAL_GO", 70.0, 99.0, rules=warn))).passed
    # A project policy with its own bands: 25 is GO under go_threshold=30.
    custom = {**DEFAULT_THRESHOLDS, "go_threshold": 30}
    assert _check(check_release_consistency(_decision("GO", 25.0, 99.0, thresholds=custom))).passed
    # Kind-budget downgrade recorded as applied: NO_GO -> CONDITIONAL_GO.
    assert _check(check_release_consistency(_decision("CONDITIONAL_GO", 60.0, 99.0, kind_applied=True))).passed
    # pass_rate recorded on the decision itself is read as well.
    assert _check(check_release_consistency(_decision("NO_GO", 41.8, 44.44, in_snapshot=False))).passed


def test_escalation_is_one_function_for_live_and_stored_evaluations():
    live = [SimpleNamespace(action="WARN", passed=False)]
    stored = [{"action": "WARN", "passed": False}]
    assert escalate_recommendation("GO", live) == escalate_recommendation("GO", stored) == ("CONDITIONAL_GO", "WARN")
    assert escalate_recommendation("CONDITIONAL_GO", [{"action": "BLOCK", "passed": False}]) == ("NO_GO", "BLOCK")
    assert escalate_recommendation("GO", [{"action": "BLOCK", "passed": True}]) == ("GO", "PASS")


@pytest.mark.asyncio
async def test_every_decision_the_gate_makes_is_accepted_by_the_check():
    """One source of truth: whatever evaluate_policy decides, over a grid of
    scores and pass rates, the check agrees with."""
    policy = SimpleNamespace(id=None, version=None, rules={"thresholds": dict(DEFAULT_THRESHOLDS), "rules": []})
    grid = itertools.product([0, 10, 19, 20, 35, 54, 55, 80, 100], [100.0, 95.0, 90.0, 89.0, 70.0, 63.0, 62.0, 30.0])
    checked = 0
    for dimension, pass_rate in grid:
        dims = {name: float(dimension) for name in (
            "regression_likely", "hist_recurrence", "blast_radius", "diagnosis_conf",
            "product_risk", "flaky_noise", "pass_rate_risk",
        )}
        result = await evaluate_policy(
            project_id=None, dim_scores=dims, pass_rate=pass_rate,
            context={}, db=None, policy_override=policy,
        )
        decision = {
            "recommendation": result.recommendation,
            "risk_score": int(result.effective_composite),
            "reasoning": "",
            "blocking_issues": [],
            "policy_id": result.policy_id,
            "policy_evaluation": result.to_dict(),
            "input_snapshot": {"pass_rate": pass_rate},
        }
        check = _check(check_release_consistency(decision))
        assert check.passed, (dimension, pass_rate, result.recommendation, check.details)
        checked += 1
    assert checked == 72
