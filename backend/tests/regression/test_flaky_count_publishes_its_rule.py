"""A flaky count must say what it measured — BUG-007.

A user read **"Flaky 0"** on the summary report beside a flaky test on Flaky
Coach and reported the pair as a contradiction. Measured live on 2026-09-21,
the two surfaces disagreed on **2 of 5** projects:

===================  ===================  =========================
project              Flaky Coach          summary ``flaky_test_count``
===================  ===================  =========================
ExploreQA 134934     1                    0
Inventory Service    5                    6
===================  ===================  =========================

Neither was computing anything wrong. They apply different rules to different
populations — Flaky Coach takes 30 days and needs 3 runs; this count takes each
test's last 10 runs and needs 5 of them. The ExploreQA test had exactly **3**
runs (FAILED/PASSED/FAILED), which clears Flaky Coach's bar and misses this one.

So the defect is that neither number carried its rule, and the fix is to publish
the rule — **not** to align the thresholds. ``_count_flaky_tests`` feeds the
``max_flaky_count`` hard cap and the readiness score, so moving it moves release
verdicts. A display problem must not be fixed by silently changing a gate, and
``test_the_counting_rule_is_unchanged`` below is what holds that line.
"""
from __future__ import annotations

import inspect

from app.models.schemas import FlakyCountCriteria, SummaryReportResponse
from app.services import metrics_service as svc


class TestTheRuleIsPublished:
    def test_the_criteria_name_every_condition_the_query_applies(self):
        c = svc.flaky_count_criteria()
        assert set(c) == {
            "window_runs",
            "min_runs",
            "min_flips",
            "min_failure_ratio",
            "max_failure_ratio",
        }

    def test_the_criteria_validate_against_the_published_schema(self):
        # The service dict and the response model are edited in different files;
        # a field added to one and not the other would ship a 500, not a typo.
        assert FlakyCountCriteria(**svc.flaky_count_criteria())

    def test_the_summary_response_carries_them(self):
        assert "flaky_criteria" in SummaryReportResponse.model_fields

    def test_the_report_populates_them_from_the_service(self):
        src = inspect.getsource(
            __import__(
                "app.services.summary_report_service", fromlist=["build_summary_report"]
            )
        )
        # The CALL, not the name: the module explains the choice in a comment
        # above it, and matching the bare name finds the prose.
        assert "flaky_count_criteria()" in src


class TestTheRuleMatchesTheQueryThatEnforcesIt:
    """Published criteria that drift from the SQL are worse than none — they
    are a confident wrong answer to "why is this 0?"."""

    def test_the_ratio_band_is_not_a_second_literal(self):
        src = inspect.getsource(svc._count_flaky_tests)
        assert "BETWEEN 0.1 AND 0.9" not in src, (
            "the band is hardcoded in the SQL again; the published criteria are "
            "now a second source of truth for the same rule"
        )
        assert "_FLAKY_MIN_FAILURE_RATIO" in src
        assert "_FLAKY_MAX_FAILURE_RATIO" in src

    def test_every_published_bound_appears_in_the_query(self):
        src = inspect.getsource(svc._count_flaky_tests)
        for name in (
            "_FLAKY_WINDOW_RUNS",
            "_FLAKY_MIN_RUNS",
            "_FLAKY_MIN_FLIPS",
            "_FLAKY_MIN_FAILURE_RATIO",
            "_FLAKY_MAX_FAILURE_RATIO",
        ):
            assert name in src, f"{name} is published but the query does not use it"


class TestTheGateDidNotMove:
    """This count is a release input, not just a tile.

    ``_evaluate_hard_caps`` trips ``max_flaky_count`` on it and ``_compute_readiness``
    scores on it. Widening the rule to agree with Flaky Coach would have been the
    obvious "fix" and would have changed GO/NO_GO on live projects without
    anyone asking for it.
    """

    def test_the_counting_rule_is_unchanged(self):
        c = svc.flaky_count_criteria()
        assert c["window_runs"] == 10
        assert c["min_runs"] == 5
        assert c["min_failure_ratio"] == 0.1
        assert c["max_failure_ratio"] == 0.9

    def test_the_flip_threshold_is_still_the_shared_one(self):
        # Three read-path detectors once re-derived flakiness independently and
        # each admitted stable regressions; flaky_signals owns the value so they
        # cannot drift again. Publishing it must not fork it.
        from app.services.flaky_signals import MIN_FLIPS_FOR_INTERMITTENCY

        assert svc.flaky_count_criteria()["min_flips"] == MIN_FLIPS_FOR_INTERMITTENCY

    def test_min_runs_was_not_lowered_to_match_flaky_coach(self):
        # The tempting fix. Flaky Coach fires at 3 runs; dropping this to 3
        # would make the ExploreQA test count here too — and would feed more
        # tests into the flaky hard cap that gates releases.
        assert svc.flaky_count_criteria()["min_runs"] > 3, (
            "min_runs was lowered toward Flaky Coach's threshold; that changes "
            "the release gate's flaky hard cap, which is not a display fix"
        )
