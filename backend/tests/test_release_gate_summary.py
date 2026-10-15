"""W05 REL-01: the verdict a release card carries (``shape_gate_summary``).

A card shows the CURRENT recorded verdict; nothing is computed in the browser.
So the summary built from a stored decision row must say exactly what the live
scorecard says for the same counts: the same pass rate (one shared helper), the
same "measured" formula and the same "why not" sentence. These pin that parity
without a database; the one-statement batch read runs against PostgreSQL in
``tests/integration/test_release_gate_summary_postgres.py``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import release_gate_service as gate
from app.services import release_rollup_service as rollup_svc
from app.services.release_rollup_service import STATUSES, ReleaseRollup

SUMMARY_KEYS = {
    "verdict", "decided_at", "pass_rate", "denominator", "evidence_count", "run_count",
    "status_counts", "measured", "insufficient_reason", "blocking_reasons", "blocking_total",
}


def _rollup(statuses: dict[str, str], incomplete: dict[str, int] | None = None) -> ReleaseRollup:
    r = ReleaseRollup(latest_by_test=dict(statuses), incomplete_runs=dict(incomplete or {}))
    r.status_counts = {s: 0 for s in STATUSES}
    for status in r.latest_by_test.values():
        r.status_counts[status] += 1
    return r


def _summary_of(rollup: ReleaseRollup, **overrides):
    kwargs = dict(
        verdict="GO",
        decided_at=datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc),
        denominator=rollup.denominator,
        evidence_count=rollup.evidence_count,
        run_count=3,
        status_counts=rollup.status_counts,
        # What record_decision stores for this rollup.
        ingestion_complete=not bool(rollup.incomplete_runs),
        incomplete_runs=rollup.incomplete_runs,
        blocking_reasons=[],
        blocking_total=0,
    )
    kwargs.update(overrides)
    return gate.shape_gate_summary(**kwargs)


@pytest.mark.parametrize(
    "statuses",
    [
        {f"s::t{i}": "PASSED" for i in range(9)} | {"s::bad": "FAILED"},
        {"s::a": "PASSED", "s::b": "BROKEN", "s::c": "SKIPPED", "s::d": "UNKNOWN", "s::e": "FAILED"},
        {f"s::t{i}": "PASSED" for i in range(7)},
        {"s::a": "PASSED", "s::b": "FAILED", "s::c": "FAILED"},
    ],
)
def test_the_pass_rate_is_the_rollups_own(statuses):
    rollup = _rollup(statuses)
    assert _summary_of(rollup)["pass_rate"] == rollup.pass_rate()
    assert rollup_svc.summarise(rollup)["pass_rate"] == rollup.pass_rate()


def test_the_shared_helper_divides_by_evidence_and_rounds_to_two_places():
    assert rollup_svc.pass_rate_from_counts(2, 3) == 66.67
    assert rollup_svc.pass_rate_from_counts(0, 4) == 0.0, "a measured zero is a zero"
    assert rollup_svc.pass_rate_from_counts(0, 0) is None
    assert rollup_svc.pass_rate_from_counts(None, 4) is None


def test_no_evidence_and_a_legacy_row_have_no_pass_rate():
    nothing_ran = _rollup({"s::a": "SKIPPED", "s::b": "SKIPPED"})
    assert _summary_of(nothing_ran)["pass_rate"] is None
    # A legacy decision row: no status_rollup at all, so PASSED is unknown --
    # absence is not 0%.
    legacy = gate.shape_gate_summary(
        verdict="GO", decided_at=None, denominator=40, evidence_count=40, run_count=0,
        status_counts={}, ingestion_complete=None, incomplete_runs=None,
        blocking_reasons=[], blocking_total=0,
    )
    assert legacy["pass_rate"] is None
    assert legacy["status_counts"] == {}
    assert legacy["measured"] is True


@pytest.mark.parametrize(
    "statuses,incomplete",
    [
        ({"s::a": "PASSED", "s::b": "PASSED"}, None),                      # below the floor
        ({f"s::t{i}": "PASSED" for i in range(6)}, {"run-1": 2}),          # rejected rows
        ({"s::a": "PASSED"}, {"run-1": 2, "run-2": 1}),                    # both: floor first
        ({f"s::t{i}": "PASSED" for i in range(6)}, None),                  # measured
        ({f"s::t{i}": "PASSED" for i in range(5)}, None),                  # exactly the floor
    ],
)
def test_measured_and_the_reason_agree_with_summarise(statuses, incomplete):
    rollup = _rollup(statuses, incomplete)
    live = rollup_svc.summarise(rollup)
    stored = _summary_of(rollup)
    assert stored["measured"] is live["measured"]
    assert stored["insufficient_reason"] == live["insufficient_reason"]


def test_the_reason_sentences_are_the_scorecards():
    assert rollup_svc.insufficient_reason(2, 9, 0) == "2 of 9 tests carry evidence; 5 required"
    assert rollup_svc.insufficient_reason(7, 9, 2) == "2 run(s) rejected source results"
    assert rollup_svc.insufficient_reason(7, 9, 0) is None


def test_an_unmeasured_row_with_no_rejection_counts_still_says_why():
    summary = gate.shape_gate_summary(
        verdict="NOT_EVALUATED", decided_at=None, denominator=9, evidence_count=9, run_count=1,
        status_counts={"PASSED": 9}, ingestion_complete=False, incomplete_runs=None,
        blocking_reasons=[], blocking_total=0,
    )
    assert summary["measured"] is False
    assert summary["insufficient_reason"]


def test_blocking_reasons_stop_at_the_list_cap_while_the_total_is_full():
    reasons = [f"suite::fp{i:03d} is FAILED" for i in range(25)]
    summary = _summary_of(
        _rollup({"s::a": "FAILED"}), verdict="NO_GO", blocking_reasons=reasons, blocking_total=25,
    )
    assert gate.GATE_LIST_REASON_CAP == 20
    assert summary["blocking_reasons"] == reasons[:20]
    assert summary["blocking_total"] == 25


def test_the_summary_has_exactly_the_contract_keys_and_plain_types():
    summary = _summary_of(_rollup({"s::a": "PASSED"}), verdict="NOT_EVALUATED")
    assert set(summary) == SUMMARY_KEYS
    assert isinstance(summary["verdict"], str)
    assert summary["decided_at"] == "2026-10-15T09:30:00+00:00"


def test_decided_at_is_utc_iso():
    ist = timezone(timedelta(hours=5, minutes=30))
    summary = _summary_of(_rollup({}), decided_at=datetime(2026, 10, 15, 15, 0, tzinfo=ist))
    assert summary["decided_at"] == "2026-10-15T09:30:00+00:00"


# ── current_gate: additive keys, still query-free ───────────────────────────


class _NoQueries:
    async def execute(self, *a, **kw):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("current_gate must not query beyond current_decision")


def _stored(**extra):
    base = dict(
        verdict="NO_GO",
        blocking_reasons=["ui::t1 is FAILED", "ui::t2 is BROKEN"],
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        denominator=40,
        evidence_count=38,
        status_rollup={"PASSED": 36, "FAILED": 1, "BROKEN": 1},
        attribution_mix={},
        run_ids=["a", "b"],
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_current_gate_gains_pass_rate_reason_and_total(monkeypatch):
    async def _current(*a, **kw):
        return _stored()

    monkeypatch.setattr(gate.decisions, "current_decision", _current)
    result = asyncio.run(gate.current_gate(_NoQueries(), "r1"))
    assert result["blocking_total"] == 2
    assert result["scorecard"]["pass_rate"] == rollup_svc.pass_rate_from_counts(36, 38)
    assert result["scorecard"]["insufficient_reason"] is None
    # Existing keys untouched.
    assert result["blocking_reasons"] == ["ui::t1 is FAILED", "ui::t2 is BROKEN"]
    assert result["scorecard"]["run_count"] == 2
    assert result["from_snapshot"] is True


def test_current_gate_says_why_a_thin_verdict_is_unmeasured(monkeypatch):
    async def _current(*a, **kw):
        return _stored(verdict="NOT_EVALUATED", evidence_count=2, denominator=9,
                       status_rollup={"PASSED": 2}, blocking_reasons=None, run_ids=None)

    monkeypatch.setattr(gate.decisions, "current_decision", _current)
    result = asyncio.run(gate.current_gate(_NoQueries(), "r1"))
    assert result["scorecard"]["measured"] is False
    assert result["scorecard"]["insufficient_reason"] == "2 of 9 tests carry evidence; 5 required"
    assert result["blocking_total"] == 0 and result["blocking_reasons"] == []


def test_current_gate_summaries_runs_nothing_for_no_ids():
    assert asyncio.run(gate.current_gate_summaries(_NoQueries(), [])) == {}
    assert asyncio.run(gate.current_gate_summaries(_NoQueries(), ["not-a-uuid"])) == {}
