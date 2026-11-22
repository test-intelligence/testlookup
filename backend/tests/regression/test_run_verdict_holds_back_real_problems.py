"""A run with real failures or an open blocking defect is never GO (D-04, D-83, D-95, D-146).

Seen on the default install, build ``main-1865`` (4 failed of 65, one open HIGH
defect shown as P1):

    Release gate: "RECOMMENDATION Go · Pass rate: 93.8% · 7 Risk Score"
    Release containing the run: NO_GO

and after a deep investigation of ``nightly-1864``: "Go · release cleared · Risk
19/100" for four product-bug failures and the same open P1. Three causes in the
score:

* an open defect added 2 raw points to ``user_impact`` whatever its severity
  (0.5 on the composite);
* a product bug carrying the flaky flag was dropped from ``user_impact``;
* failing tests entered only through the pass rate.

Score model 2 weighs defects by severity, counts flagged product bugs at half,
and adds a floor: an open CRITICAL/HIGH defect means NO_GO (as for the
release), an unquarantined failure not classed flaky or infrastructure means at
best CONDITIONAL_GO.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.agents.consistency import _replayed_recommendation
from app.services import criticality_service as cs
from app.services.policy_evaluator_service import POLICY_EVALUATOR_VERSION, evaluate_policy
from app.services.run_release_signals import is_blocking_failure, load_run_release_signals


def _dims(analyses, open_defects, pass_rate=93.8):
    return cs.compute_dimension_scores(
        analyses=analyses, anomalies=[], pass_rate=pass_rate, is_regression=False,
        regression_tests=[], failure_clusters=[], open_defects=open_defects,
    )


# ── The score ────────────────────────────────────────────────────────────


def test_an_open_high_defect_weighs_more_than_a_low_one():
    assert _dims({}, {"HIGH": 1})["user_impact"] == 20.0
    assert _dims({}, {"LOW": 1})["user_impact"] == 2.0
    assert _dims({}, {"CRITICAL": 1, "MEDIUM": 2})["user_impact"] == 50.0


def test_a_bare_defect_count_keeps_two_points_each():
    """Stored snapshots and older callers pass a count."""
    assert _dims({}, 3)["user_impact"] == 6.0


def test_a_product_bug_marked_flaky_still_counts():
    analyses = {
        f"tc-{i}": {"failure_category": "PRODUCT_BUG", "is_flaky": True, "confidence_score": 48}
        for i in range(4)
    }
    # 4 x 0.5 / 4 x 80 = 40, plus one HIGH defect = 60. It was 2.
    assert _dims(analyses, {"HIGH": 1})["user_impact"] == 60.0


def test_the_description_no_longer_says_multiplied():
    text = cs._DIMENSION_DESCRIPTIONS["user_impact"]
    assert "multiplied" not in text
    assert "severity" in text


def test_the_score_model_version_moved():
    assert cs.SCORE_MODEL_VERSION == 2
    assert POLICY_EVALUATOR_VERSION == "policy-evaluator:v3"


# ── The floor ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("before", "failures", "defects", "after"),
    [
        ("GO", 0, 0, "GO"),
        ("GO", 4, 0, "CONDITIONAL_GO"),
        ("GO", 0, 1, "NO_GO"),
        ("GO", 4, 1, "NO_GO"),
        ("CONDITIONAL_GO", 4, 0, "CONDITIONAL_GO"),
        ("CONDITIONAL_GO", 0, 1, "NO_GO"),
        ("NO_GO", 0, 0, "NO_GO"),
    ],
)
def test_the_floor_only_lowers(before, failures, defects, after):
    verdict, reasons = cs.apply_verdict_floor(
        before, blocking_failures=failures, blocking_defects=defects
    )
    assert verdict == after
    assert bool(reasons) == (verdict != before)


def test_the_floor_names_its_reason():
    _verdict, reasons = cs.apply_verdict_floor("GO", blocking_defects=1)
    assert reasons == ["1 open critical or high defect"]


# ── The stored decision path (policy evaluator + replay) ─────────────────


def _frozen_default():
    return SimpleNamespace(
        id=None,
        version=None,
        rules={
            "thresholds": {
                "go_threshold": 20.0,
                "no_go_threshold": 55.0,
                "pass_rate_minimum": 90.0,
                "pass_rate_hard_floor_factor": cs.HARD_FLOOR_FACTOR,
            },
            "dimension_weights": cs._weights(),
            "rules": [],
        },
    )


@pytest.mark.asyncio
async def test_main_1865_reads_no_go_with_an_open_p1():
    dims = _dims({}, {"HIGH": 1})
    result = await evaluate_policy(
        project_id=None, dim_scores=dims, pass_rate=93.8,
        context={"blocking_failures": 4, "blocking_defects": 1},
        db=None, policy_override=_frozen_default(),
    )

    assert result.recommendation == "NO_GO"
    assert result.verdict_floor["blocking_defects"] == 1
    assert result.verdict_floor["reasons"] == ["1 open critical or high defect"]


@pytest.mark.asyncio
async def test_real_failures_without_a_blocking_defect_read_conditional():
    result = await evaluate_policy(
        project_id=None, dim_scores=_dims({}, {"LOW": 1}), pass_rate=93.8,
        context={"blocking_failures": 4, "blocking_defects": 0},
        db=None, policy_override=_frozen_default(),
    )
    assert result.recommendation == "CONDITIONAL_GO"


@pytest.mark.asyncio
async def test_the_consistency_replay_applies_the_same_floor():
    result = await evaluate_policy(
        project_id=None, dim_scores=_dims({}, {"HIGH": 1}), pass_rate=93.8,
        context={"blocking_failures": 4, "blocking_defects": 1},
        db=None, policy_override=_frozen_default(),
    )
    decision = {"policy_evaluation": result.to_dict(), "pass_rate": 93.8}

    assert _replayed_recommendation(decision) == "NO_GO"


# ── The run's signals ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("category", "tags", "blocking"),
    [
        ("PRODUCT_BUG", None, True),
        (None, None, True),
        ("AUTOMATION_DEFECT", [], True),
        ("FLAKY", None, False),
        ("INFRASTRUCTURE", None, False),
        ("PRODUCT_BUG", ["quarantined"], False),
    ],
)
def test_which_failures_hold_a_release(category, tags, blocking):
    assert is_blocking_failure(category, tags) is blocking


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    def __init__(self, failures, defects):
        self._answers = [_Rows(failures), _Rows(defects)]

    async def execute(self, _statement):
        return self._answers.pop(0)


@pytest.mark.asyncio
async def test_signals_count_blocking_failures_and_defects_by_severity():
    db = _DB(
        failures=[("PRODUCT_BUG", None), ("FLAKY", None), (None, ["quarantined"]), (None, None)],
        defects=[("HIGH", 1), ("low", 2), (None, 1)],
    )

    signals = await load_run_release_signals(db, uuid.uuid4(), uuid.uuid4())

    assert signals == {
        "blocking_failures": 2,
        "open_defects_by_severity": {"HIGH": 1, "LOW": 2, "UNSET": 1},
        "open_defects": 4,
        "blocking_defects": 1,
    }


# ── A retry's fallback text keeps the model's reasoning (D-147) ──────────


def test_a_fallback_retry_keeps_the_models_reasoning_for_the_same_scores():
    from app.agents.release_risk_agent import REASONING_FALLBACK, _keeps_stored_reasoning

    scores = {"user_impact": 60.0}
    record = SimpleNamespace(
        reasoning="The coupon rounding change breaks four checkout tests.", dimension_scores=scores
    )
    fallback = {"reasoning_source": REASONING_FALLBACK, "dimension_scores": dict(scores)}

    assert _keeps_stored_reasoning(record, fallback) is True
    # New scores, a model answer, or a stored fallback: the new text is written.
    assert _keeps_stored_reasoning(record, {**fallback, "dimension_scores": {"user_impact": 70.0}}) is False
    assert _keeps_stored_reasoning(record, {**fallback, "reasoning_source": "llm"}) is False
    stored_fallback = SimpleNamespace(
        reasoning="Recommendation derived deterministically — LLM reasoning unavailable.",
        dimension_scores=scores,
    )
    assert _keeps_stored_reasoning(stored_fallback, fallback) is False


# ── One risk number (D-96) and no verdict in stored prose (D-119) ────────


def test_the_reasoning_shows_the_number_the_decision_used():
    """19.6 decided as "under 20" was printed "20/100" beside a GO."""
    import inspect

    from app.agents import release_risk_agent

    source = inspect.getsource(release_risk_agent)
    assert "{composite:.0f}/100" not in source
    assert "{composite:.1f}/100" in source


def test_the_quick_look_floors_its_integer():
    import inspect

    from app.services import release_council_service

    source = inspect.getsource(release_council_service._synthesize_release_council)
    assert "int(math.floor(composite))" in source
    assert "int(round(composite))" not in source


def test_the_rules_summary_states_no_release_signal():
    from app.services.rules_engine import RulesEngine

    for failed, rate in ((0, 100.0), (4, 93.8), (19, 70.3)):
        summary = RulesEngine.generate_summary(
            {"total_tests": 65, "failed_tests": failed, "pass_rate": rate,
             "build_number": "main-1865", "branch": "main"},
            {},
        )
        assert "release signal" not in summary["layer1_executive_summary"].lower()
