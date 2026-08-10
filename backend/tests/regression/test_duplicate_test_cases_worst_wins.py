"""A failing test must not be hidden by a later same-named passing one.

Persistence is keyed on ``(test_run_id, test_fingerprint)`` — the right
idempotency key for re-ingesting a file, and ``_upsert_test_case`` does a blind
``existing.status = status``. So when one report named the same test more than
once, the **last occurrence won** and every earlier one was discarded.

Measured on the live deployment with three same-named cases, one failing:

===========================  ==========================
report                       run verdict
===========================  ==========================
``fail, pass, pass``         **PASSED** — 0 failures
``pass, pass, fail``         FAILED  — 1 failure
===========================  ==========================

Identical inputs, opposite verdicts, decided by document order — and the run
that genuinely contained a failure was the one reported green.

**Why this shape is common, not exotic.** Retry frameworks emit the failed
attempt and the passing retry as sibling ``<testcase>`` elements with the same
name. That is precisely the fail-then-pass ordering that resolved to PASSED, so
the signal this product exists to surface — a test that failed and then passed
— was the one most reliably dropped.

The fix collapses duplicates **within one payload** before persistence, worst
outcome winning. It deliberately does *not* change ``_upsert_test_case``: a
separate re-ingest of the same run still overwrites, so a corrected report can
still flip a verdict. Only same-payload duplicates merge.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.ingestion import (  # noqa: E402
    collapse_duplicate_cases,
    make_test_fingerprint,
)

pytestmark = pytest.mark.regression


def _case(name: str, status: str, cls: str = "d.T", **extra) -> dict:
    return {"test_name": name, "class_name": cls, "status": status, **extra}


class TestOrderNoLongerDecidesTheVerdict:
    def test_failure_first_survives(self):
        """The measured leak: a failure listed first was overwritten."""
        out = collapse_duplicate_cases(
            [_case("same", "failed"), _case("same", "passed"), _case("same", "passed")]
        )
        assert len(out) == 1
        assert out[0]["status"] == "failed", (
            "a failing test was hidden by a later same-named pass — the run "
            "reported PASSED while containing a real failure"
        )

    def test_failure_last_still_survives(self):
        out = collapse_duplicate_cases(
            [_case("same", "passed"), _case("same", "passed"), _case("same", "failed")]
        )
        assert len(out) == 1
        assert out[0]["status"] == "failed"

    def test_the_verdict_is_independent_of_order(self):
        """The property, not the two examples."""
        cases = [_case("same", "failed"), _case("same", "passed")]
        forward = collapse_duplicate_cases(cases)
        backward = collapse_duplicate_cases(list(reversed(cases)))
        assert forward[0]["status"] == backward[0]["status"] == "failed"

    def test_the_retry_shape_keeps_its_failure(self):
        """fail-then-pass is what a retry framework emits."""
        out = collapse_duplicate_cases(
            [
                _case("flaky_checkout", "failed", error_message="timeout"),
                _case("flaky_checkout", "passed"),
            ]
        )
        assert out[0]["status"] == "failed"
        assert out[0]["error_message"] == "timeout", (
            "the winning row must carry the failure's own detail, not a "
            "blank inherited from the passing retry"
        )


class TestItOnlyMergesWhatItShould:
    def test_distinct_tests_all_survive(self):
        out = collapse_duplicate_cases(
            [_case("a", "passed"), _case("b", "failed"), _case("c", "skipped")]
        )
        assert len(out) == 3

    def test_same_method_name_in_different_classes_is_two_tests(self):
        """The existing fingerprint contract — must not regress to bare name."""
        out = collapse_duplicate_cases(
            [_case("test_login", "failed", cls="pkg.A"),
             _case("test_login", "passed", cls="pkg.B")]
        )
        assert len(out) == 2
        assert {c["status"] for c in out} == {"failed", "passed"}

    def test_first_appearance_order_is_preserved(self):
        out = collapse_duplicate_cases(
            [_case("z", "passed"), _case("a", "passed"), _case("z", "failed")]
        )
        assert [c["test_name"] for c in out] == ["z", "a"]

    def test_empty_input(self):
        assert collapse_duplicate_cases([]) == []

    def test_the_input_is_not_mutated(self):
        cases = [_case("same", "failed"), _case("same", "passed")]
        before = [dict(c) for c in cases]
        collapse_duplicate_cases(cases)
        assert cases == before


class TestPrecedence:
    @pytest.mark.parametrize(
        "other", ["passed", "skipped", "broken", "unknown"],
    )
    def test_failed_beats_everything(self, other: str):
        out = collapse_duplicate_cases([_case("same", other), _case("same", "failed")])
        assert out[0]["status"] == "failed"
        out = collapse_duplicate_cases([_case("same", "failed"), _case("same", other)])
        assert out[0]["status"] == "failed"

    def test_broken_beats_passed(self):
        """BROKEN is an infra failure — not something a pass should mask."""
        out = collapse_duplicate_cases([_case("same", "broken"), _case("same", "passed")])
        assert out[0]["status"] == "broken"

    def test_skipped_beats_passed(self):
        """Reporting a skip as a pass claims coverage that never ran."""
        out = collapse_duplicate_cases([_case("same", "skipped"), _case("same", "passed")])
        assert out[0]["status"] == "skipped"

    def test_equal_ranks_keep_the_later_entry(self):
        """Ties preserve the prior last-one-wins behaviour."""
        out = collapse_duplicate_cases(
            [_case("same", "passed", duration_ms=1), _case("same", "passed", duration_ms=2)]
        )
        assert out[0]["duration_ms"] == 2


def test_the_collapse_key_matches_the_persistence_key():
    """Collapsing on a different key than the DB would reintroduce the loss."""
    a = _case("same", "failed")
    fp = make_test_fingerprint(a["test_name"], a["class_name"])
    out = collapse_duplicate_cases([a, _case("same", "passed")])
    assert make_test_fingerprint(out[0]["test_name"], out[0]["class_name"]) == fp
