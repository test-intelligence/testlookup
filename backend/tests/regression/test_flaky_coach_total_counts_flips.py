"""``total_flaky`` must count tests that actually oscillate.

Closes F-010, the last surface using the order-blind definition.

Three surfaces already require pass<->fail transitions:
``metrics_service.flaky_test_count``, ``analytics_service.flaky_tests`` (behind
the /failures verdict), and this module's quarantine *recommendation*. The
coach's headline still counted every row, so for one project at one moment:

    dashboard KPI            flaky_test_count      = 2
    /failures verdict        analytics/flaky-tests = 2
    flaky coach              total_flaky           = 5     <-- this

**Why it stopped being cosmetic.** ``value_metrics_service`` counts
``FlakyCoachResult`` rows into ``flaky_tests_identified``, so the looser
definition was being reported as a **stakeholder-facing ROI figure**.

**What this deliberately does NOT do.** The first attempt filtered persistent
regressions out of the coach *list*. That was wrong, and an existing test caught
it: ``test_flaky_signals::test_refresh_downgrades_persistent_regression_off_
quarantine_track`` pins FLK-P1 behaviour where a regression IS surfaced with a
downgraded recommendation and "treat as a regression, not a flake" advice —
guidance added by the quarantine-recommendation fix. Dropping those rows would
have deleted useful triage information and made that copy unreachable.

So the list keeps them; only the COUNT is corrected. It is computed from the
stored ``status_history``, so no migration is needed. Manual-triage rows carry a
sentinel history and are always counted — a human's call outranks the heuristic.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import test_health_coach_service as coach  # noqa: E402
from app.services.flaky_signals import MIN_FLIPS_FOR_INTERMITTENCY  # noqa: E402


def _reader_src() -> str:
    """Source of ``get_flaky_coach`` — the function that builds the response."""
    return inspect.getsource(coach.get_flaky_coach)


class TestTheHeadlineCountIsGatedOnFlips:
    def test_total_flaky_is_not_just_len_entries(self):
        assert "total_flaky=len(entries)" not in _reader_src(), (
            "total_flaky counts every row, including persistent regressions, so "
            "it disagrees with every other surface — and feeds the ROI figure"
        )

    def test_it_counts_via_a_flip_predicate(self):
        src = _reader_src()
        assert "_entry_is_intermittent" in src
        assert re.search(
            r"_count_flips\(history\)\s*>=\s*_MIN_FLIPS_FOR_QUARANTINE", src
        ), "the count predicate does not require flips"

    def test_manual_triage_rows_are_always_counted(self):
        """A human's FLAKY_TEST call outranks the heuristic."""
        assert "FLAKY (manual triage)" in _reader_src(), (
            "manual-triage rows must be counted regardless of flips"
        )


class TestTheListItselfIsUntouched:
    """Pinned because the first attempt at F-010 broke exactly this."""

    def test_entries_are_not_filtered_by_flips(self):
        src = inspect.getsource(coach.refresh_flaky_coach)
        assert not re.search(
            r"_count_flips\(statuses\)\s*<\s*_MIN_FLIPS_FOR_QUARANTINE:\s*\n\s*continue",
            src,
        ), (
            "entries are being dropped from the coach list; FLK-P1 surfaces "
            "persistent regressions with a downgraded recommendation and that "
            "guidance must stay reachable"
        )

    def test_pass_and_fail_are_both_still_required(self):
        src = inspect.getsource(coach.refresh_flaky_coach)
        assert "if failed == 0 or passed == 0" in src, (
            "the always-green / always-broken exclusion was lost"
        )

    def test_minimum_history_still_required(self):
        assert "if total < 3" in inspect.getsource(coach.refresh_flaky_coach)


class TestAllFourSurfacesShareOneConstant:
    """The drift guard. Four independent re-derivations is how this started."""

    def test_coach_threshold_is_the_canonical_one(self):
        assert coach._MIN_FLIPS_FOR_QUARANTINE is MIN_FLIPS_FOR_INTERMITTENCY

    def test_metrics_and_analytics_agree(self):
        from app.services.analytics_service import _FLAKY_MIN_FLIPS as analytics_flips
        from app.services.metrics_service import _FLAKY_MIN_FLIPS as metrics_flips

        assert metrics_flips is MIN_FLIPS_FOR_INTERMITTENCY
        assert analytics_flips is MIN_FLIPS_FOR_INTERMITTENCY


def test_the_roi_metric_reads_this_table():
    """Anchors WHY this matters: the ROI figure counts these rows.

    If value_metrics stops sourcing from FlakyCoachResult, this failing is the
    signal to revisit the justification above.
    """
    from app.services import value_metrics_service

    assert "FlakyCoachResult" in inspect.getsource(value_metrics_service), (
        "value_metrics no longer counts FlakyCoachResult — re-check whether the "
        "coach still feeds flaky_tests_identified"
    )
