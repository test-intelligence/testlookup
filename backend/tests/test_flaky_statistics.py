"""FLK-P2 regression — Wilson confidence interval on the flaky failure ratio.

Pure unit tests for ``app.services.flaky_statistics`` (no DB, no stubs). Pin:
  * closed-form correctness against hand-computed Wilson values,
  * the *statistical-strength* ranking property (30/100 outranks 3/10),
  * interval monotonicity in the sample size, and
  * never-raise behaviour on degenerate / malformed inputs.
"""
from __future__ import annotations

import math

import pytest

from app.services.flaky_statistics import (
    Z_95,
    FailureRatioConfidence,
    wilson_failure_confidence,
)


def _wilson_ref(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Independent reference implementation for cross-checking."""
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return center - half, center + half


class TestClosedFormCorrectness:
    def test_matches_reference_30_of_100(self):
        c = wilson_failure_confidence(30, 100)
        lo, hi = _wilson_ref(30, 100)
        assert c.point == pytest.approx(0.30, abs=1e-9)
        assert c.low == pytest.approx(lo, abs=1e-3)
        assert c.high == pytest.approx(hi, abs=1e-3)
        # Sanity against textbook Wilson 95% for 30/100 ≈ [0.219, 0.396].
        assert c.low == pytest.approx(0.219, abs=0.01)
        assert c.high == pytest.approx(0.396, abs=0.01)

    def test_matches_reference_3_of_10(self):
        c = wilson_failure_confidence(3, 10)
        lo, hi = _wilson_ref(3, 10)
        assert c.point == pytest.approx(0.30, abs=1e-9)
        assert c.low == pytest.approx(lo, abs=1e-3)
        assert c.high == pytest.approx(hi, abs=1e-3)

    def test_point_estimate_is_failure_ratio(self):
        assert wilson_failure_confidence(7, 20).point == pytest.approx(0.35, abs=1e-9)

    def test_bounds_within_unit_interval(self):
        for k, n in [(0, 5), (5, 5), (1, 3), (50, 100), (99, 100)]:
            c = wilson_failure_confidence(k, n)
            assert 0.0 <= c.low <= c.high <= 1.0


class TestStatisticalStrengthRanking:
    def test_more_runs_outrank_fewer_at_same_point_estimate(self):
        """30/100 and 3/10 share p̂=0.30; the larger sample must have the
        higher (stronger) lower bound — the FLK-P2 ranking property."""
        big = wilson_failure_confidence(30, 100)
        small = wilson_failure_confidence(3, 10)
        assert big.point == small.point == pytest.approx(0.30, abs=1e-9)
        assert big.low > small.low

    def test_interval_narrows_as_sample_grows(self):
        widths = [
            wilson_failure_confidence(n // 2, n).high - wilson_failure_confidence(n // 2, n).low
            for n in (4, 10, 50, 200, 1000)
        ]
        # Strictly decreasing width with more evidence.
        assert all(a > b for a, b in zip(widths, widths[1:]))


class TestExtremes:
    def test_zero_failures(self):
        c = wilson_failure_confidence(0, 50)
        assert c.point == 0.0
        assert c.low == 0.0           # Wilson pins the lower bound at 0 for p̂=0
        assert 0.0 < c.high < 0.2     # but the upper bound is non-trivial

    def test_all_failures(self):
        c = wilson_failure_confidence(50, 50)
        assert c.point == 1.0
        assert c.high == 1.0
        assert 0.8 < c.low < 1.0

    def test_single_run(self):
        c = wilson_failure_confidence(1, 1)
        assert 0.0 <= c.low <= c.high <= 1.0


class TestNeverRaiseOnDegenerateInput:
    @pytest.mark.parametrize(
        "failures,total",
        [
            (0, 0),            # no runs
            (5, 0),            # failures but no runs
            (-3, 10),          # negative failures
            (3, -10),          # negative total
            (None, 10),        # non-numeric failures
            (3, None),         # non-numeric total
            ("x", "y"),        # garbage
            (3.7, 10.2),       # floats coerce
            (15, 10),          # failures > total (clamped)
        ],
    )
    def test_degenerate_inputs_never_raise(self, failures, total):
        c = wilson_failure_confidence(failures, total)
        assert isinstance(c, FailureRatioConfidence)
        assert 0.0 <= c.low <= c.high <= 1.0

    def test_no_runs_is_neutral_zero_band(self):
        c = wilson_failure_confidence(0, 0)
        assert c.total == 0
        assert c.low == 0.0 and c.high == 0.0 and c.point == 0.0

    def test_failures_exceeding_total_clamp_to_one(self):
        c = wilson_failure_confidence(15, 10)
        assert c.failures == 10 and c.total == 10
        assert c.point == 1.0

    def test_bad_z_falls_back_to_95(self):
        for bad_z in (0, -1.0, float("inf"), float("nan")):
            c = wilson_failure_confidence(30, 100, z=bad_z)
            assert c.z == Z_95
            assert 0.0 <= c.low <= c.high <= 1.0

    def test_to_dict_roundtrip(self):
        d = wilson_failure_confidence(30, 100).to_dict()
        assert set(d) == {"failures", "total", "point", "low", "high", "z"}
