"""D-142: the quick look evaluates with the project's effective policy, whole.

Before: ``_synthesize_release_council`` scored with the built-in thresholds,
hard-coded ``policy_level="hardcoded"``, and then layered the published
policy's pass-rate bands and P0 cap on top. A policy was half applied (its
bands moved the verdict, its thresholds, weights and rules did not) and the
gate footer read "Evaluated with system defaults · pass-rate bands: project
policy v1".

After: one resolution of the effective policy feeds the stored decision's own
evaluator (thresholds, weights, the rules a quick look has inputs for) and the
band floor. With no policy the verdict, score and footer fields are exactly
what they were (the identity grid below replays the old algorithm).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


class _Scalar:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v


def _run(pass_rate):
    return SimpleNamespace(
        id=uuid.uuid4(), pass_rate=pass_rate, project_id=uuid.uuid4(),
        build_number="b-142", status="FAILED",
    )


def _signals(*, by_severity=None, blocking_failures=0, blocking_defects=0):
    by_severity = dict(by_severity or {})
    return {
        "blocking_failures": blocking_failures,
        "open_defects_by_severity": by_severity,
        "open_defects": sum(by_severity.values()),
        "blocking_defects": blocking_defects,
    }


@pytest.fixture
def world(monkeypatch):
    """Patch the run's signals, the policy resolution and the P0 count; the
    session then sees exactly one read, the run itself."""
    from app.services import metrics_service, run_release_signals

    state = SimpleNamespace(signals=_signals(), policy=None, p0=0)
    monkeypatch.setattr(
        run_release_signals, "load_run_release_signals",
        AsyncMock(side_effect=lambda *a, **k: state.signals),
    )
    monkeypatch.setattr(
        metrics_service, "_resolve_active_policy_for_project",
        AsyncMock(side_effect=lambda *a, **k: state.policy),
    )
    monkeypatch.setattr(
        metrics_service, "count_open_critical_defects",
        AsyncMock(side_effect=lambda *a, **k: state.p0),
    )
    return state


async def _quick_look(run):
    from app.services.release_council_service import _synthesize_release_council

    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Scalar(run)]))
    return await _synthesize_release_council(run.id, db)


def _old_quick_look(pass_rate, signals):
    """The pre-D-142 algorithm, verbatim, for the no-policy identity check."""
    from app.core.config import settings
    from app.services.criticality_service import (
        HARD_FLOOR_FACTOR,
        apply_verdict_floor,
        compute_composite,
        compute_dimension_scores,
        score_to_recommendation,
    )

    threshold = float(settings.RELEASE_PASS_RATE_THRESHOLD)
    dims = compute_dimension_scores(
        analyses={}, anomalies=[], pass_rate=pass_rate, is_regression=False,
        regression_tests=[], failure_clusters=[],
        open_defects=signals["open_defects_by_severity"] or int(signals["open_defects"]),
    )
    composite = compute_composite(dims)
    if pass_rate < threshold * HARD_FLOOR_FACTOR:
        composite = max(composite, 60.0)
    rec = score_to_recommendation(composite, pass_rate, threshold)
    rec, reasons = apply_verdict_floor(
        rec,
        blocking_failures=int(signals["blocking_failures"]),
        blocking_defects=int(signals["blocking_defects"]),
    )
    return rec, composite, reasons, threshold


_GRID_SIGNALS = [
    _signals(),
    _signals(by_severity={"MEDIUM": 3}),
    _signals(by_severity={"HIGH": 1}, blocking_defects=1),
    _signals(by_severity={"LOW": 2}, blocking_failures=2),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("pass_rate", [100.0, 95.0, 93.8, 89.0, 70.0, 64.0, 62.0, 50.0, 0.0])
@pytest.mark.parametrize("signals", _GRID_SIGNALS)
async def test_no_policy_leaves_verdict_score_and_footer_exactly_as_before(world, pass_rate, signals):
    from app.services.release_council_service import _quick_look_conditions

    world.signals = signals
    world.policy = None
    result = await _quick_look(_run(pass_rate))

    rec, composite, reasons, threshold = _old_quick_look(pass_rate, signals)
    assert result.recommendation == rec
    assert result.composite_risk == composite
    assert result.risk_score == int(composite // 1)
    # The footer's fields: "Evaluated with system defaults", no band policy.
    assert result.policy_level == "hardcoded"
    assert result.policy_id is None and result.policy_version is None
    assert result.band_policy_level is None and result.release_readiness_band is None
    assert result.rule_evaluations == [] and result.blocking_issues == []
    assert result.conditions_for_go == (
        [f"Resolve: {r}" for r in reasons] + _quick_look_conditions(rec, pass_rate, threshold)
    )
    assert result.input_snapshot["threshold"] == threshold
    assert result.input_snapshot["no_go_floor_pct"] == round(threshold * 0.7, 2)


def _policy(document, *, version=1, level="project"):
    return (document, "pol-142", version, level)


GREEN_BANDS = {"green_min": 90.0, "yellow_min": 85.0, "orange_min": 80.0}


@pytest.mark.asyncio
async def test_a_policy_pass_rate_minimum_applies_and_the_level_is_true(world):
    """The finding's own case: minimum 95, a 93.8% run. Built-in 90 said GO."""
    world.policy = _policy({"thresholds": {"pass_rate_minimum": 95.0}, "pass_rate_bands": GREEN_BANDS})
    result = await _quick_look(_run(93.8))

    assert result.recommendation == "CONDITIONAL_GO"
    assert result.policy_level == "project"
    assert result.policy_id == "pol-142" and result.policy_version == 1
    assert result.band_policy_level == "project" and result.band_policy_version == 1
    assert result.input_snapshot["threshold"] == 95.0
    assert result.input_snapshot["no_go_floor_pct"] == round(95.0 * 0.7, 2)
    assert any("95.0%" in c for c in result.conditions_for_go)


@pytest.mark.asyncio
async def test_a_policy_risk_line_applies(world):
    """A Go line of 5: the quick look's ~6 risk is Conditional, not Go."""
    world.policy = _policy({
        "thresholds": {"go_threshold": 5.0, "no_go_threshold": 40.0},
        "pass_rate_bands": GREEN_BANDS,
    }, level="system", version=7)
    result = await _quick_look(_run(100.0))

    assert result.recommendation == "CONDITIONAL_GO"
    assert result.policy_level == "system" and result.policy_version == 7
    assert result.input_snapshot["go_threshold"] == 5.0
    assert result.input_snapshot["no_go_threshold"] == 40.0


@pytest.mark.asyncio
async def test_an_open_defect_rule_the_quick_look_can_measure_is_applied(world):
    world.signals = _signals(by_severity={"MEDIUM": 3})
    world.policy = _policy({
        "pass_rate_bands": GREEN_BANDS,
        "rules": [{
            "id": "r-defects", "name": "Open defect limit", "type": "open_defect_limit",
            "params": {"max_open_defects": 1, "action": "BLOCK"},
        }],
    })
    result = await _quick_look(_run(100.0))

    assert result.recommendation == "NO_GO"
    assert [ev.rule_id for ev in result.rule_evaluations] == ["r-defects"]
    assert result.rule_evaluations[0].passed is False
    assert result.blocking_issues and result.blocking_issues[0].startswith("[Policy] ")


@pytest.mark.asyncio
async def test_rules_the_quick_look_cannot_measure_are_named_not_passed(world):
    """No flaky count on a quick look: the rule is listed as not checked, never
    evaluated against a zero that would read as a pass."""
    world.policy = _policy({
        "pass_rate_bands": GREEN_BANDS,
        "rules": [
            {"id": "r-flaky", "name": "Flaky test limit", "type": "flaky_recurrence",
             "params": {"max_flaky_tests": 0, "action": "BLOCK"}},
            {"id": "r-off", "name": "Disabled", "type": "flaky_recurrence", "enabled": False,
             "params": {}},
        ],
    })
    result = await _quick_look(_run(100.0))

    assert result.rule_evaluations == []
    assert result.input_snapshot["rules_not_checked"] == [
        {"id": "r-flaky", "name": "Flaky test limit", "type": "flaky_recurrence"},
    ]
    assert result.recommendation == "GO"


# ── Review F1: the bands that can floor the verdict are published ────────────
#
# Every policy saved from the editor carries the band defaults (red under 90,
# orange 90-95). A 92% run at risk ~6 is Conditional because of the orange band
# and an 85% run is No-Go because of the red band; the response must carry the
# band edges and say that the band moved the verdict, or the gate card states
# thresholds ("Go under risk 20 and at 90% or more") its own verdict contradicts.


@pytest.mark.asyncio
async def test_a_92_percent_run_is_held_back_by_the_orange_band(world):
    world.policy = _policy({"rules": []})  # no bands in the document: the classifier's defaults
    result = await _quick_look(_run(92.0))

    assert result.recommendation == "CONDITIONAL_GO"
    assert result.release_readiness_band == "orange"
    assert result.pass_rate_bands == {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}
    assert result.band_held_back is True


@pytest.mark.asyncio
async def test_an_85_percent_run_is_held_back_by_the_red_band(world):
    world.policy = _policy({"pass_rate_bands": {"orange_min": 90, "yellow_min": 95, "green_min": 99}})
    result = await _quick_look(_run(85.0))

    assert result.recommendation == "NO_GO"
    assert result.release_readiness_band == "red"
    assert result.band_held_back is True


@pytest.mark.asyncio
async def test_a_band_that_agrees_or_a_hard_cap_is_not_a_band_hold(world):
    world.policy = _policy({"pass_rate_bands": GREEN_BANDS})
    agreed = await _quick_look(_run(100.0))
    assert agreed.recommendation == "GO" and agreed.band_held_back is False
    assert agreed.pass_rate_bands == {"orange_min": 80.0, "yellow_min": 85.0, "green_min": 90.0}

    world.p0 = 1
    world.policy = _policy({"pass_rate_bands": GREEN_BANDS, "hard_caps": {"max_p0_defects": 0}})
    capped = await _quick_look(_run(100.0))
    assert capped.recommendation == "NO_GO" and capped.band_downgrades == ["p0_defects:1>0"]
    assert capped.band_held_back is False  # band_downgrades names the cap


@pytest.mark.asyncio
async def test_no_policy_publishes_no_bands(world):
    result = await _quick_look(_run(92.0))
    assert result.pass_rate_bands is None and result.band_held_back is False


class _Rows:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return SimpleNamespace(all=lambda: self._v)

    def all(self):
        return self._v


def _stored(recommendation="GO", human_override=None):
    return SimpleNamespace(
        test_run_id=uuid.uuid4(), recommendation=recommendation, risk_score=10, composite_risk=10.0,
        dimension_scores=None, blocking_issues=[], conditions_for_go=[], reasoning="stored",
        score_model_version=2, input_snapshot={}, human_override=human_override, overridden_by=None,
        original_recommendation=None, original_risk_score=None, override_audit=[], policy_id=None,
        policy_evaluation=None, created_at=None, updated_at=None,
    )


async def _stored_council(decision, run):
    from app.services.release_council_service import get_release_council

    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Rows(decision), _Rows(run), _Rows([]), _Rows([]),  # decision, run, clusters, defects by component
    ]))
    return await get_release_council(run.id, db)


@pytest.mark.asyncio
async def test_a_stored_go_held_back_by_the_band_says_so(world):
    world.policy = _policy({"rules": []})
    council = await _stored_council(_stored("GO"), _run(92.0))

    assert council.recommendation == "CONDITIONAL_GO"
    assert council.original_recommendation == "GO"
    assert council.pass_rate_bands == {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}
    assert council.band_held_back is True


@pytest.mark.asyncio
async def test_an_overridden_stored_decision_has_no_band_floor(world):
    world.policy = _policy({"rules": []})
    council = await _stored_council(_stored("GO", human_override="accepted"), _run(92.0))

    assert council.recommendation == "GO"
    assert council.pass_rate_bands is None and council.band_held_back is False


@pytest.mark.asyncio
async def test_the_band_floor_still_applies_from_the_same_policy(world):
    """The P0 cap of the one resolved policy still forces No-Go."""
    world.p0 = 2
    world.policy = _policy({"pass_rate_bands": GREEN_BANDS, "hard_caps": {"max_p0_defects": 0}})
    result = await _quick_look(_run(100.0))

    assert result.recommendation == "NO_GO"
    assert result.band_downgrades == ["p0_defects:2>0"]
    assert result.policy_level == "project"
