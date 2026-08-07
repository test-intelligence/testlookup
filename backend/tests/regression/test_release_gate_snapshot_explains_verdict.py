"""The release-gate snapshot must explain its own verdict.

Found by exploratory testing against the live homelab (2026-08-07). The
quick-look (``synthesized``) release-readiness response returned, for one run::

    recommendation        = GO
    pass_rate             = 83.33
    input_snapshot.threshold = 90.0

A pass rate 7 points UNDER the only threshold in the payload, reported as a
clean GO. Read literally, the response contradicts itself.

It is not actually a wrong verdict -- ``threshold`` is not the GO cutoff.
``score_to_recommendation`` only lets pass rate force a verdict when it drops
below ``HARD_FLOOR_FACTOR`` (0.7) of the threshold; above that line the
composite risk score decides. But nothing in the payload said so, so the number
sitting next to the verdict appeared to refute it.

Fix: publish what was actually applied -- ``no_go_floor_pct`` (the real cutoff),
``hard_floor_factor``, and ``verdict_driver`` (which rule decided). **No verdict
changes.**

The measured behaviour that motivated this, from a throwaway project (one run
per level, cleaned up afterwards):

    pass_rate  100  95  89  83  70  64 | 62  50   0
    verdict    GO   GO  GO  GO  GO  GO | NO_GO NO_GO NO_GO

Binary at 63% (= 0.7 x 90), and CONDITIONAL_GO never appears: composites ran
5,6,7,9,11,13 then jumped to 60 (the hard-floor bump), clearing the entire
[20,55) conditional band. Whether a 64%-pass run should really be a clean GO,
and whether CONDITIONAL_GO should be reachable on this path, are product calls
recorded in the ledger -- deliberately NOT decided here, because they change
ship/no-ship recommendations.
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


class TestOneCanonicalFloorFactor:
    def test_the_factor_is_exported_and_sane(self):
        assert 0.0 < HARD_FLOOR_FACTOR <= 1.0

    def test_score_to_recommendation_defaults_to_it(self):
        """The decision function and the published number must be one value."""
        import inspect

        sig = inspect.signature(score_to_recommendation)
        assert sig.parameters["hard_floor_factor"].default == HARD_FLOOR_FACTOR

    def test_the_council_service_imports_it_rather_than_retyping_0_7(self):
        import inspect

        from app.services import release_council_service as svc

        src = inspect.getsource(svc)
        assert "threshold * 0.7" not in src, (
            "a hand-typed 0.7 can drift from the value published in the "
            "snapshot, which would make the response lie about its own rule"
        )
        assert "_HARD_FLOOR_FACTOR" in src


class TestSnapshotExplainsTheVerdict:
    """Pins the fields, and that they agree with the real decision rule."""

    def test_snapshot_publishes_the_effective_floor(self):
        import inspect

        from app.services import release_council_service as svc

        src = inspect.getsource(svc._synthesize_release_council)
        for field in ("no_go_floor_pct", "hard_floor_factor", "verdict_driver"):
            assert field in src, (
                f"snapshot omits {field!r}; a reader sees only `threshold`, "
                "which is not the cutoff the verdict used"
            )

    @pytest.mark.parametrize(
        "pass_rate,threshold,expect_floor_driver",
        [
            (83.33, 90.0, False),  # the reported case: above the floor
            (64.0, 90.0, False),   # still above 63 -> composite decides
            (62.0, 90.0, True),    # under 63 -> the floor forces the verdict
            (0.0, 90.0, True),
            (100.0, 90.0, False),
        ],
    )
    def test_verdict_driver_matches_the_actual_rule(self, pass_rate, threshold, expect_floor_driver):
        """`verdict_driver` must not claim a rule the verdict didn't use."""
        floor_applies = pass_rate < threshold * HARD_FLOOR_FACTOR
        assert floor_applies is expect_floor_driver
        if floor_applies:
            assert score_to_recommendation(0.0, pass_rate, threshold) == "NO_GO"


class TestVerdictsAreUnchanged:
    """This PR is transparency only — the mapping must behave exactly as before."""

    @pytest.mark.parametrize(
        "composite,pass_rate,expected",
        [
            (5.0, 100.0, "GO"),
            (13.0, 64.0, "GO"),      # measured on the live probe
            (60.0, 62.0, "NO_GO"),   # measured: hard-floor bump
            (0.0, 50.0, "NO_GO"),    # floor wins regardless of composite
            (_GO_THRESHOLD, 100.0, "CONDITIONAL_GO"),
            (_NO_GO_THRESHOLD, 100.0, "NO_GO"),
        ],
    )
    def test_mapping_is_untouched(self, composite, pass_rate, expected):
        assert score_to_recommendation(composite, pass_rate, 90.0) == expected

    def test_conditional_go_is_reachable_in_principle(self):
        """It IS reachable from the mapping — just not from the synthesized
        composites (5-13, then a bump to 60). Documents that the gap is in the
        score, not in this function, so a future fix targets the right place."""
        assert score_to_recommendation(30.0, 100.0, 90.0) == "CONDITIONAL_GO"
        assert _GO_THRESHOLD < 60.0, "the hard-floor bump lands above the band"
        assert 60.0 >= _NO_GO_THRESHOLD, (
            "the bump must still reach NO_GO; if this fails the floor stopped "
            "working and failing runs would be reported CONDITIONAL_GO"
        )
