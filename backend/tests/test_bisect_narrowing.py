"""W23b FAIL-12: the pure bisect walk and its compare gate (contract §2.1 B-2, §6.3 G-B).

``bisect_narrowing.walk`` judges the rows of the ONE statement: the series'
runs newest first (the failing run itself first) with this test's outcome in
each, and the test's passes on ``main`` / ``master``. Every timestamp is built
inside its test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.bisect_narrowing import (
    Narrowing,
    WalkRow,
    compare_gate,
    outcome_of,
    series_key,
    walk,
)
from app.services.bisect_vocab import BISECT_MAX_RUNS, TEST_OUTCOMES, UNTESTED_LIST_MAX


def _series(outcomes, *, branch="feature/pay", commits=None, start=None):
    """Rows newest first; ``outcomes[0]`` is the failing run itself."""
    start = start or datetime.now(timezone.utc)
    commits = commits or [f"commit-sentinel-{i}" for i in range(len(outcomes))]
    return [
        WalkRow(run_id=uuid.uuid4(), build_number=f"b{len(outcomes) - i}", commit_hash=commits[i],
                branch=branch, created_at=start - timedelta(hours=i), ci_run_url=None, outcome=o)
        for i, o in enumerate(outcomes)
    ]


def _main_pass(at, branch="main", commit="main-sentinel"):
    return WalkRow(run_id=uuid.uuid4(), build_number="m1", commit_hash=commit, branch=branch,
                   created_at=at, ci_run_url=None, outcome="pass")


def test_outcome_of_buckets_every_status():
    assert [outcome_of(s) for s in ("PASSED", "FAILED", "BROKEN", "SKIPPED", "UNKNOWN", None, "")] == [
        "pass", "fail", "fail", "untested", "untested", "untested", "untested",
    ]
    assert {outcome_of(s) for s in ("PASSED", "FAILED", None)} == set(TEST_OUTCOMES)


def test_series_key_is_the_index_expression():
    assert series_key("Feature/PAY") == "feature/pay"
    assert series_key(None) == ""
    assert series_key("") == ""


def test_streak_detection_good_is_the_pass_bad_the_oldest_fail():
    rows = _series(["fail", "fail", "pass", "fail"])
    n = walk(rows, [])
    assert (n.state, n.anchor, n.fallback_branch) == ("narrowed", "same_branch", None)
    assert n.good == rows[2] and n.bad == rows[1]
    assert n.streak_length == 2
    assert (n.untested, n.untested_count) == ((), 0)
    assert (n.runs_read, n.bounded) == (4, False)


def test_untested_inside_the_streak_dropped_between_kept_newest_first():
    rows = _series(["fail", "untested", "fail", "untested", "untested", "pass"])
    n = walk(rows, [])
    assert n.bad == rows[2] and n.good == rows[5]
    assert n.streak_length == 2  # untested runs are not counted
    assert n.untested == (rows[3], rows[4])
    assert n.untested_count == 2


def test_a_pass_without_a_commit_ends_the_walk():
    rows = _series(["fail", "pass", "pass"], commits=["bad-sentinel", None, "older-sentinel"])
    n = walk(rows, [])
    assert n.state == "narrowed" and n.good == rows[1]
    assert compare_gate(n) == "no_good_commit"


def test_a_fail_without_a_commit_is_no_bad_commit():
    rows = _series(["fail", "pass"], commits=["", "good-sentinel"])
    assert compare_gate(walk(rows, [])) == "no_bad_commit"


def test_branch_falls_back_to_main_only_for_an_older_pass():
    rows = _series(["fail", "fail", "untested"])
    newer = _main_pass(rows[0].created_at + timedelta(minutes=5), commit="newer-sentinel")
    between = _main_pass(rows[0].created_at - timedelta(minutes=30), commit="between-sentinel")
    older = _main_pass(rows[1].created_at - timedelta(minutes=30), branch="MASTER", commit="older-sentinel")
    n = walk(rows, [newer, between, older])
    # "between" is newer than bad (rows[1]); only a pass older than bad anchors.
    assert (n.state, n.anchor, n.fallback_branch) == ("narrowed", "main_fallback", "master")
    assert n.good == older and n.bad == rows[1]
    assert n.untested == () and n.untested_count == 0  # another branch's runs are not "in between"
    assert compare_gate(n) is None


def test_a_run_on_main_has_no_fallback():
    rows = _series(["fail", "fail"], branch="Main")
    older = _main_pass(rows[1].created_at - timedelta(hours=1), branch="master")
    n = walk(rows, [older])
    assert n.state == "never_passed" and n.good is None and n.anchor is None


def test_a_run_with_no_branch_walks_the_empty_series():
    rows = _series(["fail", "untested", "pass"], branch=None)
    n = walk(rows, [])
    assert series_key(rows[0].branch) == ""
    assert n.state == "narrowed" and n.good == rows[2] and n.untested == (rows[1],)
    older = _main_pass(rows[1].created_at - timedelta(hours=1))
    m = walk(_series(["fail"], branch=None), [older])
    assert (m.state, m.anchor, m.fallback_branch) == ("narrowed", "main_fallback", "main")


def test_never_passed_vs_no_pass_within_bound_at_exactly_the_bound():
    short = walk(_series(["fail"] * (BISECT_MAX_RUNS - 1)), [])
    assert (short.state, short.bounded, short.runs_read) == ("never_passed", False, BISECT_MAX_RUNS - 1)
    full = walk(_series(["fail"] + ["untested"] * (BISECT_MAX_RUNS - 1)), [])
    assert (full.state, full.bounded, full.runs_read) == ("no_pass_within_bound", True, BISECT_MAX_RUNS)
    assert full.bad is not None and full.streak_length == 1
    # Rows past the bound are never read (a pass there is "older history").
    over = walk(_series(["fail"] * BISECT_MAX_RUNS + ["pass"]), [])
    assert (over.state, over.runs_read) == ("no_pass_within_bound", BISECT_MAX_RUNS)


@pytest.mark.parametrize("own", ["pass", "untested"])
def test_not_failing(own):
    n = walk(_series([own, "fail", "pass"]), [])
    assert n.state == "not_failing" and n.good is None and n.bad is None and n.streak_length == 0
    assert compare_gate(n) == "not_narrowed"


def test_same_commit_is_case_insensitive():
    rows = _series(["fail", "pass"], commits=["AbCdEf0123", "abcdef0123"])
    assert compare_gate(walk(rows, [])) == "same_commit"


def test_the_untested_list_is_capped_with_the_full_count():
    rows = _series(["fail"] + ["untested"] * (UNTESTED_LIST_MAX + 5) + ["pass"])
    n = walk(rows, [])
    assert n.untested_count == UNTESTED_LIST_MAX + 5
    assert n.untested == tuple(rows[1:UNTESTED_LIST_MAX + 1])  # the newest ones


def test_compare_gate_attempts_two_distinct_commits():
    n = walk(_series(["fail", "pass"], commits=["badsentinel002", "goodsentinel01"]), [])
    assert compare_gate(n) is None
    assert isinstance(n, Narrowing)
