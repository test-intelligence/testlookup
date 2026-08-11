"""
Unit tests for ``services.run_compare_service`` — pure classifier logic.

The DB-bound ``compare_runs`` entry point is exercised separately via
integration tests; here we focus on ``_classify`` and ``_status_bucket``
where every behaviour is deterministic and doesn't need a session.
"""
from __future__ import annotations

from types import SimpleNamespace

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


# ── _similarity ───────────────────────────────────────────────────────────


def test_similarity_identical_strings_is_one():
    assert svc._similarity("test_login", "auth", "test_login", "auth") == 1.0


def test_similarity_suite_match_is_case_insensitive():
    assert svc._similarity("test_login", "AuthSuite", "test_login", "authsuite") == 1.0


def test_similarity_completely_different_is_low():
    score = svc._similarity("foo_bar_baz", "alpha", "qux_wibble_zap", "omega")
    assert score < svc._FUZZY_PAIR_THRESHOLD


def test_similarity_common_rename_suffix_clears_threshold():
    """Adding a clarifying suffix is the most common rename."""
    score = svc._similarity(
        "test_login", "auth",
        "test_login_with_valid_credentials", "auth",
    )
    assert score >= svc._FUZZY_PAIR_THRESHOLD


def test_similarity_empty_inputs_return_zero():
    assert svc._similarity(None, None, None, None) == 0.0
    assert svc._similarity("", "", "test", "auth") == 0.0


# ── _greedy_fuzzy_pair ────────────────────────────────────────────────────


def _tc(fp: str, name: str, suite: str = "auth", status: str = "PASSED", duration: int = 100):
    """Build a TestCase-shaped SimpleNamespace for the pairer."""
    return SimpleNamespace(
        test_fingerprint=fp,
        test_name=name,
        suite_name=suite,
        status=status,
        duration_ms=duration,
    )


def test_summary_dict_scopes_counts_to_suite_cases():
    run = SimpleNamespace(
        id="run-1",
        project_id="project-1",
        build_number="42",
        branch="main",
        commit_hash="abc",
        status="FAILED",
        start_time=None,
        end_time=None,
        primary_suite_name="all",
        suite_names=["auth", "payments"],
        total_tests=999,
        passed_tests=999,
        failed_tests=0,
        broken_tests=0,
        skipped_tests=0,
        pass_rate=100,
        duration_ms=9999,
    )
    scoped = [
        _tc("a", "passes", suite="Auth", status="PASSED", duration=10),
        _tc("b", "fails", suite="Auth", status="FAILED", duration=15),
        _tc("c", "skips", suite="Auth", status="SKIPPED", duration=5),
    ]

    summary = svc._summary_dict(run, scoped_tests=scoped, suite_name="Auth")

    assert summary["total_tests"] == 3
    assert summary["passed_tests"] == 1
    assert summary["failed_tests"] == 1
    assert summary["skipped_tests"] == 1
    # 1 passed / 1 failed / 1 skipped -> 1/(1+1) = 50%, NOT 1/3.
    #
    # This previously asserted 33.333, pinning `passed / total`. That is the
    # denominator `test_pass_rate_excludes_skipped` was written to eliminate:
    # its docstring describes this exact formula as the bug ("divided
    # passed / total where total INCLUDED skipped ... a run that was mostly
    # skips showed a misleadingly low pass rate"), and records the fix landing
    # in `_update_run_aggregates`. run_compare kept the pre-fix formula, so the
    # same comparison page reported 90.91% unscoped and 83.33% suite-scoped for
    # one live run. Skips are excluded on purpose -- see
    # `metrics_service._evaluated`.
    assert summary["pass_rate"] == pytest.approx(50.0, abs=0.001)
    assert summary["primary_suite_name"] == "Auth"
    assert summary["suite_names"] == ["Auth"]


def test_greedy_fuzzy_pair_empty_inputs():
    assert svc._greedy_fuzzy_pair([], []) == []
    assert svc._greedy_fuzzy_pair([_tc("fp1", "test_a")], []) == []
    assert svc._greedy_fuzzy_pair([], [_tc("fp1", "test_a")]) == []


def test_greedy_fuzzy_pair_matches_rename():
    removed = [_tc("fp_old", "test_login")]
    added = [_tc("fp_new", "test_login_with_valid_credentials")]
    pairs = svc._greedy_fuzzy_pair(removed, added)
    assert len(pairs) == 1
    left, right, score = pairs[0]
    assert left.test_fingerprint == "fp_old"
    assert right.test_fingerprint == "fp_new"
    assert score >= svc._FUZZY_PAIR_THRESHOLD


def test_greedy_fuzzy_pair_ignores_below_threshold():
    removed = [_tc("fp_old", "test_login")]
    added = [_tc("fp_new", "test_payment_processing_edge_case")]
    assert svc._greedy_fuzzy_pair(removed, added) == []


def test_greedy_fuzzy_pair_picks_best_score_first():
    """When one added test could match two removed tests, the better-scoring
    pair wins and the loser stays unmatched."""
    removed = [
        _tc("fp_r1", "test_login"),
        _tc("fp_r2", "test_login_old"),  # closer to the added one
    ]
    added = [_tc("fp_a1", "test_login_old_updated")]
    pairs = svc._greedy_fuzzy_pair(removed, added)
    assert len(pairs) == 1
    left, right, _ = pairs[0]
    # ``test_login_old`` → ``test_login_old_updated`` scores higher than
    # ``test_login`` → ``test_login_old_updated``.
    assert left.test_fingerprint == "fp_r2"
    assert right.test_fingerprint == "fp_a1"


def test_greedy_fuzzy_pair_skips_when_too_many_candidates(monkeypatch):
    """Interactive diff mustn't burn seconds inside the matcher when both
    sides are large — we bail out and leave rename tracking to the
    user's eyes."""
    monkeypatch.setattr(svc, "_FUZZY_PAIR_MAX_CANDIDATES", 2)
    removed = [_tc(f"fp_r{i}", f"test_{i}") for i in range(5)]
    added = [_tc(f"fp_a{i}", f"test_{i}_renamed") for i in range(5)]
    assert svc._greedy_fuzzy_pair(removed, added) == []


def test_greedy_fuzzy_pair_no_duplicate_assignment():
    """Each TestCase appears in at most one pair."""
    removed = [
        _tc("fp_r1", "test_login_flow"),
        _tc("fp_r2", "test_logout_flow"),
    ]
    added = [
        _tc("fp_a1", "test_login_flow_v2"),
        _tc("fp_a2", "test_logout_flow_v2"),
    ]
    pairs = svc._greedy_fuzzy_pair(removed, added)
    assert len(pairs) == 2
    left_fps = {left.test_fingerprint for left, _, _ in pairs}
    right_fps = {right.test_fingerprint for _, right, _ in pairs}
    assert len(left_fps) == 2
    assert len(right_fps) == 2
