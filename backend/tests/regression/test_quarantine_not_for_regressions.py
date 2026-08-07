"""A persistently-broken test must never be recommended for quarantine.

Found by exploratory testing against the live homelab (2026-08-07), one
iteration after the sibling ``flaky_test_count`` fix (PR #461).

``GET /api/v1/projects/{id}/flaky-coach`` returned this for the seeded project::

    test_discount_stacking  failure_rate=0.8  status_volatility=0.25
      intermittency_label = "low_volatility_flaky"
      quarantine_recommendation = "QUARANTINE"
      stabilization_actions[0] =
          "Quarantine this test immediately to stabilize the CI pipeline"

``test_discount_stacking`` fails in four consecutive builds and passed only once,
at the very start. It is a **stable regression**, and the product's top advice
was to suppress it from CI.

Root cause: ``_compute_quarantine_recommendation()`` took ``failure_rate`` and
nothing else. Quarantine is the correct response to an intermittent flake and
the wrong response to a broken test -- and a rate-only rule recommends it *most*
strongly exactly when it is *most* harmful, because a permanently-failing
regression has the highest failure rate there is.

The service already computed the signals that distinguish the two
(``status_volatility``, ``intermittency_label``) and displayed them on the very
same card -- it just did not feed them into the decision. The card therefore
contradicted itself, telling the reader both "Quarantine this test immediately"
and "gather more runs to confirm the flake versus an emerging regression".

Fix: a test that flips fewer than ``_MIN_FLIPS_FOR_QUARANTINE`` times in its
window is routed to INVESTIGATE instead, with advice that names it a regression.
The verdict vocabulary is unchanged (QUARANTINE / INVESTIGATE / MONITOR /
HEALTHY) -- adding a new value would risk this repo's documented "strict Pydantic
enum over a String(N) column" silent-422 class.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.test_health_coach_service import (  # noqa: E402
    _MIN_FLIPS_FOR_QUARANTINE,
    _QUARANTINE_THRESHOLD,
    _compute_quarantine_recommendation,
    _count_flips,
)


def _seq(pattern: str) -> list[str]:
    """'pffff' -> concrete status strings ('e' = BROKEN, an infra failure)."""
    return [
        {"p": "PASSED", "f": "FAILED", "e": "BROKEN", "s": "SKIPPED"}[c] for c in pattern
    ]


class TestFlipCounting:
    @pytest.mark.parametrize(
        "pattern,expected",
        [
            ("pffff", 1),   # broke once, stayed broken
            ("ppppf", 1),   # just broke
            ("ffppp", 1),   # got fixed
            ("pfpfp", 4),   # alternates every run
            ("ppeep", 2),   # BROKEN counts as a failure; recovered
            ("ppppp", 0),
            ("fffff", 0),
        ],
    )
    def test_counts_adjacent_transitions(self, pattern, expected):
        assert _count_flips(_seq(pattern)) == expected

    def test_direction_does_not_matter(self):
        """The window is newest-first in the caller; flips must be symmetric."""
        for pattern in ("pffff", "pfpfp", "ppeep", "ffppp"):
            fwd = _seq(pattern)
            assert _count_flips(fwd) == _count_flips(list(reversed(fwd)))

    def test_degenerate_windows_do_not_raise(self):
        assert _count_flips([]) == 0
        assert _count_flips(["FAILED"]) == 0


class TestRegressionsAreNotQuarantined:
    @pytest.mark.parametrize(
        "pattern,rate,why",
        [
            ("pffff", 0.8, "broken for four straight builds — a bug, not noise"),
            ("ppfff", 0.6, "broke and stayed broken"),
            ("fffff", 1.0, "never passed in the window at all"),
        ],
    )
    def test_high_failure_rate_without_flips_is_investigate(self, pattern, rate, why):
        verdict = _compute_quarantine_recommendation(rate, _count_flips(_seq(pattern)))
        assert verdict != "QUARANTINE", (
            f"{pattern} was recommended for QUARANTINE: {why}. Quarantining it "
            "hides a reproducible failure from CI."
        )
        assert verdict == "INVESTIGATE"

    def test_the_exact_case_found_on_the_homelab(self):
        """test_discount_stacking: p f f f f, failure_rate 0.8."""
        assert _compute_quarantine_recommendation(0.8, 1) == "INVESTIGATE"


class TestRealFlakesAreStillQuarantined:
    @pytest.mark.parametrize(
        "pattern,rate",
        [
            ("fpfpf", 0.6),
            ("ffpff", 0.8),
            ("pfpff", 0.6),
            # Boundary case, deliberately pinned: mostly broken but it DID
            # recover and break again, so it has returned to a state it had
            # left. Two flips is the line, and this sits on the flake side.
            ("fffpf", 0.8),
        ],
    )
    def test_intermittent_tests_above_the_threshold_still_quarantine(self, pattern, rate):
        flips = _count_flips(_seq(pattern))
        assert flips >= _MIN_FLIPS_FOR_QUARANTINE, "fixture must actually be intermittent"
        assert _compute_quarantine_recommendation(rate, flips) == "QUARANTINE", (
            "the fix must not stop quarantining genuine flakes"
        )


class TestUnchangedBehaviour:
    def test_lower_bands_are_untouched(self):
        """Only the QUARANTINE band is gated; INVESTIGATE/MONITOR/HEALTHY are not."""
        assert _compute_quarantine_recommendation(0.30, 0) == "INVESTIGATE"
        assert _compute_quarantine_recommendation(0.15, 0) == "MONITOR"
        assert _compute_quarantine_recommendation(0.02, 0) == "HEALTHY"

    @pytest.mark.parametrize("rate", [0.5, 0.6, 0.8, 1.0])
    def test_callers_without_an_ordered_window_keep_old_behaviour(self, rate):
        """``test_case_history_service`` computes from aggregate counts and has
        no sequence to derive flips from, so it must not be silently changed."""
        assert _compute_quarantine_recommendation(rate) == "QUARANTINE"
        assert _compute_quarantine_recommendation(rate, None) == "QUARANTINE"

    def test_threshold_constant_is_meaningful(self):
        assert _MIN_FLIPS_FOR_QUARANTINE >= 2, (
            "with a threshold of 1 every single-transition regression would be "
            "quarantined again — the original bug"
        )
        assert 0 < _QUARANTINE_THRESHOLD <= 1
