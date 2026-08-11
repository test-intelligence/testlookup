"""Run compare re-implemented two shared rules by hand, and both had drifted.

Found by sweeping for the class F-075 exposed: a shared helper with hand-rolled
inline copies that no longer agree with it. Both defects below were reproduced
against the live homelab on real data (Checkout Service runs 101 and 102).

**1. Suite scoping over-matched.**

``_load_test_rows`` applied its run-level fallback unconditionally::

    or_(lower(trim(TestCase.suite_name)) == suite_key, run_level_match)

with a comment asserting "if this run's ``primary_suite_name`` matches, ALL test
cases for the run belong to that suite". That premise is false for a file upload
carrying several ``<testsuite>`` blocks: run 101 has ``trigger_source='api'``,
``ingestion_source='upload'``, ``primary_suite_name='api'``, and authoritative
per-row suites ``api`` / ``regression`` / ``smoke``. Scoping to ``api`` returned
**all 12** rows instead of 5, and the diff for suite "api" listed:

    test_inventory_sync       -> actually suite 'regression', status FAILED
    test_discount_stacking    -> actually suite 'regression'

``smoke`` (3) and ``regression`` (4) scoped correctly, so the same page behaved
differently depending on which suite the user picked.

The canonical answer to "which suite does this test belong to" is
``analytics_service._effective_suite_sql()``: the run-level label wins **for
live_stream runs** (those SDKs stamp the test class name on every per-event
``suite_name``, so per-row is unreliable), per-row wins for everything else.
Keying the fallback to ``live_stream`` restores that, and costs nothing: on the
deployment every NULL/blank per-row suite belongs to a live_stream run
(8 rows / 8 runs), while 1,493 api+upload and api+sdk runs have none.

**2. The pass rate used a different denominator than the rest of the product.**

``_summary_dict`` has two branches. Unscoped returns the stored
``run.pass_rate`` — computed canonically as ``passed / (passed + failed +
broken)``. The suite-scoped branch recomputed ``passed / total``, which includes
skips. So the same comparison page reported two bases: run 101 (10 passed /
1 failed / 1 skipped) showed **90.91%** unscoped and **83.33%** the moment a
suite was selected, with no pass or fail having changed.

``metrics_service._evaluated``'s docstring states the rule outright, and
``test_pass_rate_excludes_skipped`` already pins it for the ingest path.
"""
from __future__ import annotations

import inspect
import re
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import run_compare_service as rcs  # noqa: E402

pytestmark = pytest.mark.regression


def _case(name, suite, status, duration=1):
    return SimpleNamespace(
        test_fingerprint=name,
        test_name=name,
        suite_name=suite,
        status=status,
        duration_ms=duration,
    )


def _run(**kw):
    base = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="101",
        branch="main",
        commit_hash=None,
        status="PASSED",
        total_tests=12,
        passed_tests=10,
        failed_tests=1,
        broken_tests=0,
        skipped_tests=1,
        unknown_tests=0,
        pass_rate=90.91,
        duration_ms=100,
        start_time=None,
        end_time=None,
        primary_suite_name="api",
        suite_names=["api", "regression", "smoke"],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# 2. pass-rate denominator
# --------------------------------------------------------------------------


def test_scoped_pass_rate_excludes_skipped():
    """The live repro: 4 passed + 1 skipped is 100%, not 80%."""
    scoped = [
        _case("t1", "api", "PASSED"),
        _case("t2", "api", "PASSED"),
        _case("t3", "api", "PASSED"),
        _case("t4", "api", "PASSED"),
        _case("t5", "api", "SKIPPED"),
    ]
    summary = rcs._summary_dict(_run(), scoped_tests=scoped, suite_name="api")

    assert summary["skipped_tests"] == 1
    assert summary["pass_rate"] == 100.0, (
        "skips are back in the denominator — a suite where every executed test "
        "passed reports less than 100%"
    )


def test_scoped_pass_rate_matches_the_unscoped_basis():
    """Selecting a suite must not change the *basis*, only the population.

    Same 12 tests either way, so the two branches must agree; they differed by
    7.6 points on live data.
    """
    scoped = (
        [_case(f"p{i}", "api", "PASSED") for i in range(10)]
        + [_case("f1", "api", "FAILED")]
        + [_case("s1", "api", "SKIPPED")]
    )
    run = _run()
    scoped_summary = rcs._summary_dict(run, scoped_tests=scoped, suite_name="api")
    unscoped_summary = rcs._summary_dict(run)

    assert scoped_summary["pass_rate"] == pytest.approx(
        unscoped_summary["pass_rate"], abs=0.01
    ), (
        f"scoped {scoped_summary['pass_rate']} vs unscoped "
        f"{unscoped_summary['pass_rate']} over the identical test set"
    )


def test_broken_stays_in_the_denominator():
    """``_evaluated``'s docstring is explicit: BROKEN must count, or a run of
    10 passed / 2 broken reports 100%."""
    scoped = [_case(f"p{i}", "api", "PASSED") for i in range(10)] + [
        _case("b1", "api", "BROKEN"),
        _case("b2", "api", "BROKEN"),
    ]
    summary = rcs._summary_dict(_run(), scoped_tests=scoped, suite_name="api")
    assert summary["pass_rate"] == pytest.approx(83.333, abs=0.01)


def test_an_all_skipped_suite_does_not_divide_by_zero():
    scoped = [_case("s1", "api", "SKIPPED"), _case("s2", "api", "SKIPPED")]
    summary = rcs._summary_dict(_run(), scoped_tests=scoped, suite_name="api")
    assert summary["pass_rate"] == 0.0


def test_scoped_summary_reports_unknown():
    """Consistency with #558 — an uninterpretable result must not sit inside
    ``total_tests`` with no bucket accounting for it."""
    scoped = [
        _case("p1", "api", "PASSED"),
        _case("u1", "api", "WEIRD"),
    ]
    summary = rcs._summary_dict(_run(), scoped_tests=scoped, suite_name="api")
    assert summary["unknown_tests"] == 1
    buckets = (
        summary["passed_tests"]
        + summary["failed_tests"]
        + summary["broken_tests"]
        + summary["skipped_tests"]
        + summary["unknown_tests"]
    )
    assert buckets == summary["total_tests"] == 2


def test_unscoped_summary_reports_unknown_too():
    summary = rcs._summary_dict(_run(unknown_tests=3))
    assert summary["unknown_tests"] == 3


# --------------------------------------------------------------------------
# 1. suite scoping
# --------------------------------------------------------------------------


def test_run_level_suite_fallback_is_restricted_to_live_stream():
    """The fallback must not fire for uploads, which have authoritative
    per-row suite names."""
    src = inspect.getsource(rcs._load_test_rows)
    match = re.search(
        r"run_level_match\s*=\s*\((?:.|\n)*?\)\s*\n\s*stmt = stmt\.where", src
    )
    assert match, "could not locate the run-level match predicate"
    predicate = match.group(0)
    assert 'TestRun.trigger_source == "live_stream"' in predicate, (
        "the run-level fallback still applies to upload/sdk runs, so scoping "
        "to one suite returns the whole run"
    )


def test_the_per_row_suite_filter_survives():
    """Losing the per-row arm would break scoping entirely."""
    src = inspect.getsource(rcs._load_test_rows)
    assert "func.lower(func.trim(TestCase.suite_name)) == suite_key" in src


def test_the_fallback_itself_survives():
    """It exists for live_stream runs whose per-event suite_name is NULL —
    8 rows on the deployment. Deleting it would regress that."""
    src = inspect.getsource(rcs._load_test_rows)
    assert "TestRun.primary_suite_name" in src
    assert "or_(" in src
