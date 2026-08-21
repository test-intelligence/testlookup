"""The denominators behind the headline numbers must be right, and stated.

Two long-standing findings, resolved together because they share one question:
*what population is this percentage over?*

**F-080** — the dashboard's suite selector was inert. `/metrics/summary` and
`/metrics/trends` returned identical numbers for every suite, equal to the
unscoped figure: 60 executions for `api`, `regression` and `smoke` alike, when
the truth was 25/20/15, with the same pass rate for each. The selector looked
like it worked, the API documented the parameter and returned 200, and it
answered a different question than the one asked.

**F-067** — the dashboard reported 81.0% while the Summary Report reported
83.3% for the same window. Both were correct: the first counts every execution,
the second counts each unique test once. Neither said so, which left a user to
conclude one screen was lying.

Neither number was deleted. The unique-test basis is what makes the Summary
Report agree with Coverage, and the execution basis is what "how did CI behave"
means. What was wrong was shipping them unlabelled.

This matters beyond the two screens: the roadmap's attribution verdict is built
on these numbers, and a verdict resting on figures users already distrust
inherits that distrust.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.services import metrics_service, summary_report_service
from app.services.metrics_service import (
    PASS_RATE_BASIS_EXECUTIONS,
    PASS_RATE_BASIS_LABELS,
    PASS_RATE_BASIS_UNIQUE_TESTS,
)


# ── F-080: the suite filter must actually scope ──────────────────────────────

def test_suite_branch_counts_per_test_rows_not_whole_run_aggregates():
    """THE F-080 bug. ``TestRun.passed_tests``/``total_tests`` are whole-run
    totals; summing them for every run that *touches* a suite reports all of
    that run's other suites under this one."""
    src = inspect.getsource(metrics_service._period_stats)
    assert "func.count(TestCase.id).label(\"total\")" in src, (
        "suite numbers must come from per-test rows"
    )
    assert "sum_total = int(case_row.total or 0) + int(pending_row.total or 0)" in src


def test_suite_branch_still_counts_runs_with_no_per_test_rows():
    """The property the OLD implementation existed to protect.

    Live-stream runs persist run aggregates before their per-test rows, so an
    implementation that counted only ``test_cases`` returned 0 for a populated
    suite and blanked the dashboard. The fix must not reintroduce that: runs
    with no rows yet still contribute their run-level totals.
    """
    src = inspect.getsource(metrics_service._period_stats)
    # Assert the PROPERTY, not one spelling of it. This used to pin the exact
    # string ``~exists().where(TestCase.test_run_id == TestRun.id)``, which
    # broke the moment that EXISTS had to be rewritten with an explicit FROM
    # and ``.correlate(TestRun)`` to stop it auto-correlating itself into
    # nothing (it was 500-ing every suite-filtered request). The fallback was
    # entirely intact; only the spelling changed. A guard that fails on a
    # faithful refactor trains people to edit the guard.
    assert "no_rows" in src, (
        "the no-rows-yet fallback is gone; mid-ingest live-stream runs, which "
        "persist run aggregates before per-test rows, would drop out of the "
        "suite numbers and blank the dashboard again"
    )
    assert re.search(r"no_rows\s*=\s*~", src), (
        "no_rows is no longer a NEGATED exists, so it selects the wrong set "
        "of runs"
    )
    assert "TestCase.test_run_id == TestRun.id" in src, (
        "the fallback no longer correlates test_cases to the run"
    )
    assert "pending_row" in src


def test_suite_branch_uses_the_effective_suite_rule():
    """A live-stream run's per-case ``suite_name`` is often the test class, so
    the run-level label wins there. Bucketing by the raw per-case name alone
    would scatter one logical suite across dozens of fake ones."""
    src = inspect.getsource(metrics_service._period_stats)
    assert 'TestRun.trigger_source == "live_stream"' in src
    assert "primary_suite_name" in src


def test_run_count_and_duration_stay_run_level_facts():
    """A run either is or is not in this suite's window — there is nothing to
    apportion, so these must not be derived from per-test rows."""
    src = inspect.getsource(metrics_service._period_stats)
    assert "run_row.total_runs" in src
    assert "run_row.avg_duration_ms" in src


# ── F-067: every pass rate declares its population ───────────────────────────

def test_the_two_bases_are_named_once_and_shared():
    """One vocabulary, imported by both surfaces, so they cannot drift into
    differently-worded descriptions of the same thing."""
    assert PASS_RATE_BASIS_EXECUTIONS == "executions"
    assert PASS_RATE_BASIS_UNIQUE_TESTS == "unique_tests"
    assert set(PASS_RATE_BASIS_LABELS) == {
        PASS_RATE_BASIS_EXECUTIONS,
        PASS_RATE_BASIS_UNIQUE_TESTS,
    }
    assert summary_report_service.PASS_RATE_BASIS_UNIQUE_TESTS is PASS_RATE_BASIS_UNIQUE_TESTS


def test_dashboard_pass_rate_declares_the_executions_basis():
    src = inspect.getsource(metrics_service.get_dashboard_summary)
    assert '"basis": PASS_RATE_BASIS_EXECUTIONS' in src
    assert '"basis_label"' in src


def test_summary_report_pass_rate_declares_the_unique_tests_basis():
    src = inspect.getsource(summary_report_service)
    assert '"pass_rate_basis": PASS_RATE_BASIS_UNIQUE_TESTS' in src
    assert '"pass_rate_basis_label"' in src


def test_the_bases_are_different_which_is_the_whole_point():
    """If these ever collapse to one value the labelling is decorative and the
    two screens are silently disagreeing again."""
    assert PASS_RATE_BASIS_EXECUTIONS != PASS_RATE_BASIS_UNIQUE_TESTS
    assert (
        PASS_RATE_BASIS_LABELS[PASS_RATE_BASIS_EXECUTIONS]
        != PASS_RATE_BASIS_LABELS[PASS_RATE_BASIS_UNIQUE_TESTS]
    )


@pytest.mark.parametrize("basis", [PASS_RATE_BASIS_EXECUTIONS, PASS_RATE_BASIS_UNIQUE_TESTS])
def test_every_basis_has_a_human_label(basis):
    """A machine token alone puts the burden of knowing what it means back on
    the reader — which is the state F-067 was in."""
    label = PASS_RATE_BASIS_LABELS[basis]
    assert label and label != basis
