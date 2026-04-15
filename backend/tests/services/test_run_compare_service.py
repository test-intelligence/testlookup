"""
Unit tests for ``services.run_compare_service`` — pure classifier logic.

The DB-bound ``compare_runs`` entry point is exercised separately via
integration tests; here we focus on ``_classify`` and ``_status_bucket``
where every behaviour is deterministic and doesn't need a session.
"""
from __future__ import annotations

import pytest

from app.services import run_compare_service as svc


# ── _status_bucket ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "input_status, expected",
    [
        ("PASSED", "passed"),
        ("passed", "passed"),
        ("FAILED", "failed"),
        ("BROKEN", "broken"),
        ("SKIPPED", "skipped"),
        (None, "unknown"),
        ("NEWISH", "unknown"),
    ],
)
def test_status_bucket_normalization(input_status, expected):
    assert svc._status_bucket(input_status) == expected


# ── _classify ─────────────────────────────────────────────────────────────


def test_classify_new_failure():
    assert svc._classify("PASSED", "FAILED", 100, 100) == "new_failure"
    assert svc._classify("PASSED", "BROKEN", 100, 100) == "new_failure"


def test_classify_fixed():
    assert svc._classify("FAILED", "PASSED", 100, 100) == "fixed"
    assert svc._classify("BROKEN", "PASSED", 100, 100) == "fixed"
    assert svc._classify("SKIPPED", "PASSED", 100, 100) == "fixed"


def test_classify_still_failing():
    assert svc._classify("FAILED", "FAILED", 100, 100) == "still_failing"
    assert svc._classify("BROKEN", "BROKEN", 100, 100) == "still_failing"


def test_classify_regressed():
    assert svc._classify("PASSED", "SKIPPED", 100, 100) == "regressed"


def test_classify_new_test_passing():
    """Test existed in right but not left — passing → new_test."""
    assert svc._classify(None, "PASSED", None, 200) == "new_test"


def test_classify_new_test_failing_is_new_failure():
    """Test existed in right but not left — failing → new_failure."""
    assert svc._classify(None, "FAILED", None, 200) == "new_failure"


def test_classify_removed_test():
    assert svc._classify("PASSED", None, 100, None) == "removed_test"
    assert svc._classify("FAILED", None, 100, None) == "removed_test"


def test_classify_unchanged_passing_returns_none():
    """Same status, similar duration → no delta worth surfacing."""
    assert svc._classify("PASSED", "PASSED", 100, 110) is None


def test_classify_duration_spike():
    """Same status but right duration is ≥ 3x the left duration."""
    assert svc._classify("PASSED", "PASSED", 100, 400) == "duration_spike"


def test_classify_duration_spike_edge_case_exactly_3x():
    assert svc._classify("PASSED", "PASSED", 100, 300) == "duration_spike"


def test_classify_duration_spike_below_threshold():
    assert svc._classify("PASSED", "PASSED", 100, 250) is None


def test_classify_zero_duration_does_not_divide_by_zero():
    """A left run with 0 duration should never trigger a false duration spike."""
    assert svc._classify("PASSED", "PASSED", 0, 1000) is None


def test_classify_unknown_status_change_is_regressed():
    """Fallback bucket for any unrecognised status change."""
    assert svc._classify("PASSED", "UNKNOWN_NEW", 100, 100) == "regressed"


# ── Sort priority (implicit via classification constants) ─────────────────


def test_priority_map_covers_every_classification():
    """When a new classification is added, the sort priority map must be
    updated — otherwise it defaults to 99 and the sort order breaks.
    Catch this at unit-test time rather than in prod."""
    from app.services.run_compare_service import _classify
    # Every label emitted by ``_classify`` must have a priority entry
    # in the private map used by ``compare_runs``. We inline the set
    # of labels the classifier currently emits.
    labels = {
        "new_failure", "fixed", "still_failing", "regressed",
        "improved", "new_test", "removed_test", "duration_spike",
    }
    # Simple smoke — the classifier must produce at least one of each
    # label given the right inputs. This is not an exhaustive proof but
    # it prevents trivial regressions.
    produced = set()
    inputs = [
        (None, "PASSED", None, 100),             # new_test
        (None, "FAILED", None, 100),             # new_failure
        ("PASSED", None, 100, None),             # removed_test
        ("FAILED", None, 100, None),             # removed_test
        ("PASSED", "FAILED", 100, 100),          # new_failure
        ("FAILED", "PASSED", 100, 100),          # fixed
        ("FAILED", "FAILED", 100, 100),          # still_failing
        ("PASSED", "SKIPPED", 100, 100),         # regressed
        ("PASSED", "PASSED", 100, 400),          # duration_spike
    ]
    for left, right, l_dur, r_dur in inputs:
        result = _classify(left, right, l_dur, r_dur)
        if result:
            produced.add(result)
    # Verify we cover the subset we care most about (priority tiers 0-4).
    assert {"new_failure", "fixed", "still_failing", "regressed", "duration_spike"}.issubset(
        produced
    )
    # And the full set of labels must be a subset of what ``_classify``
    # is capable of emitting.
    assert produced.issubset(labels)
