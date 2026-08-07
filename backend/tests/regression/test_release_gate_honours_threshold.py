"""A run below the configured pass-rate threshold must not be a clean GO.

Closes F-020, measured on the live homelab (2026-08-07) across nine pass-rate
levels on a throwaway project, default `synthesized` path, no ReleaseGatePolicy::

    pass_rate  100  95  89  83  70  64 | 62     50     0
    verdict    GO   GO  GO  GO  GO  GO | NO_GO  NO_GO  NO_GO

Two defects in one table:

1. **The configured threshold gated nothing.** With `RELEASE_PASS_RATE_THRESHOLD
   = 90`, only `0.7 x 90 = 63` acted as a NO_GO floor. A build with a THIRD of
   its suite failing (64%) was reported ship-ready.

2. **CONDITIONAL_GO was unreachable.** `_GO_THRESHOLD=20`, `_NO_GO_THRESHOLD=55`,
   so the conditional band is composite in [20, 55). On the synthesized path the
   analysis-driven dimensions all score 0, so observed composites ran
   5, 6, 7, 9, 11, 13 and then jumped straight to 60 via the hard-floor bump —
   clearing the band entirely. One of the product's three documented states
   could never occur.

Fix: a pass rate below the configured `threshold` (but above the hard floor)
yields CONDITIONAL_GO. This deliberately reuses the operator's OWN configured
number instead of introducing another magic constant, and it is policy-driven --
`policy_evaluator_service` passes `thresholds["pass_rate_minimum"]`.

Rule ordering is load-bearing: both NO_GO rules are evaluated first, so the new
band can never *soften* a NO_GO into a CONDITIONAL_GO.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.criticality_service import (  # noqa: E402
    HARD_FLOOR_FACTOR,
    _GO_THRESHOLD,
    _NO_GO_THRESHOLD,
    score_to_recommendation,
)

THRESHOLD = 90.0
FLOOR = THRESHOLD * HARD_FLOOR_FACTOR  # 63.0
LOW_COMPOSITE = 10.0  # what the synthesized path actually produces


class TestTheMeasuredTable:
    """Re-asserts the exact live measurements, with the corrected verdicts."""

    @pytest.mark.parametrize(
        "pass_rate,expected",
        [
            (100.0, "GO"),
            (95.0, "GO"),
            (90.0, "GO"),              # exactly at the bar is still a clean go
            (89.0, "CONDITIONAL_GO"),  # was GO
            (83.0, "CONDITIONAL_GO"),  # was GO
            (70.0, "CONDITIONAL_GO"),  # was GO
            (64.0, "CONDITIONAL_GO"),  # was GO — a third of the suite failing
            (62.0, "NO_GO"),
            (50.0, "NO_GO"),
            (0.0, "NO_GO"),
        ],
    )
    def test_verdict_for_each_measured_level(self, pass_rate, expected):
        assert score_to_recommendation(LOW_COMPOSITE, pass_rate, THRESHOLD) == expected

    def test_a_third_of_the_suite_failing_is_not_ship_ready(self):
        """The headline case, stated plainly."""
        assert score_to_recommendation(LOW_COMPOSITE, 64.0, THRESHOLD) != "GO"


class TestConditionalGoIsNowReachable:
    def test_reachable_from_a_synthesized_composite(self):
        """The whole point: composites of 5-13 could never reach the [20,55)
        band, so CONDITIONAL_GO never occurred on the default path."""
        for composite in (5.0, 6.0, 7.0, 9.0, 11.0, 13.0):
            assert composite < _GO_THRESHOLD, "fixture must model the real range"
            assert (
                score_to_recommendation(composite, 80.0, THRESHOLD) == "CONDITIONAL_GO"
            )

    def test_all_three_states_are_producible_on_the_synthesized_path(self):
        """Every documented state must be reachable with a realistic composite."""
        verdicts = {
            score_to_recommendation(LOW_COMPOSITE, pr, THRESHOLD)
            for pr in (100.0, 80.0, 10.0)
        }
        assert verdicts == {"GO", "CONDITIONAL_GO", "NO_GO"}


class TestNoGoIsNeverSoftened:
    """Rule ordering — the new band must not rescue a failing run."""

    def test_high_composite_still_no_go_even_above_threshold(self):
        assert score_to_recommendation(_NO_GO_THRESHOLD, 100.0, THRESHOLD) == "NO_GO"

    def test_high_composite_still_no_go_below_threshold(self):
        """Below the bar AND high risk must stay NO_GO, not become CONDITIONAL."""
        assert score_to_recommendation(70.0, 80.0, THRESHOLD) == "NO_GO"

    def test_below_hard_floor_still_no_go_regardless_of_composite(self):
        for composite in (0.0, 10.0, 54.0):
            assert score_to_recommendation(composite, 50.0, THRESHOLD) == "NO_GO"

    def test_the_floor_boundary_is_exact(self):
        assert score_to_recommendation(LOW_COMPOSITE, FLOOR - 0.1, THRESHOLD) == "NO_GO"
        assert score_to_recommendation(LOW_COMPOSITE, FLOOR, THRESHOLD) != "NO_GO"


class TestPreExistingContractsHold:
    """The four assertions already in test_criticality_service.py."""

    def test_low_risk_high_pass_rate_is_go(self):
        assert score_to_recommendation(15.0, 95.0, 90.0) == "GO"

    def test_medium_risk_is_conditional_go(self):
        assert score_to_recommendation(35.0, 85.0, 90.0) == "CONDITIONAL_GO"

    def test_high_risk_is_no_go(self):
        assert score_to_recommendation(70.0, 85.0, 90.0) == "NO_GO"

    def test_critically_low_pass_rate_forces_no_go(self):
        assert score_to_recommendation(10.0, 50.0, 90.0) == "NO_GO"


class TestPolicyDriven:
    """The band must follow a project's configured threshold, not a constant."""

    @pytest.mark.parametrize("threshold,pass_rate,expected", [
        (75.0, 80.0, "GO"),              # 80 >= a lenient 75 bar
        (95.0, 92.0, "CONDITIONAL_GO"),  # 92 < a strict 95 bar
        (60.0, 62.0, "GO"),              # lenient bar, above it
    ])
    def test_band_follows_the_configured_threshold(self, threshold, pass_rate, expected):
        assert score_to_recommendation(LOW_COMPOSITE, pass_rate, threshold) == expected

    def test_hard_floor_factor_override_is_respected(self):
        """A stricter floor makes an otherwise-CONDITIONAL run a NO_GO."""
        assert score_to_recommendation(
            LOW_COMPOSITE, 80.0, 90.0, hard_floor_factor=0.95,
        ) == "NO_GO"


def test_snapshot_names_the_new_band():
    """The verdict must stay explicable from its own payload."""
    import inspect

    from app.services import release_council_service as svc

    src = inspect.getsource(svc._synthesize_release_council)
    assert "pass_rate_below_threshold" in src, (
        "verdict_driver must distinguish 'your configured threshold held this "
        "back' from 'the risk model held this back'"
    )
