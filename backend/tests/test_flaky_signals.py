"""FLK-P1 — unit coverage for intermittency + error-signature analysis.

Exercises the pure ``compute_intermittency_signals`` scorer (discrimination,
granular retry/stack signals, normalisation, never-raise) and the service-level
wiring: ``refresh_flaky_coach`` verdict refinement + ``get_flaky_coach``
surfacing of the numeric signals.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.flaky_signals import (
    IntermittencySignals,
    compute_intermittency_signals,
)


# ── Pure scorer ───────────────────────────────────────────────────────────────
class TestComputeIntermittencySignals:
    def test_high_volatility_varied_errors_is_environmental(self):
        sig = compute_intermittency_signals([
            {"status": "FAILED", "error_message": "Timeout after 30s"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "Connection refused to host"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "DNS lookup failed for svc"},
        ])
        assert sig.status_volatility == 1.0          # flips every run
        assert sig.error_signature_diversity == 1.0  # 3 distinct errors / 3 fails
        assert sig.intermittency_label == "environmental_flaky"

    def test_low_volatility_single_error_is_persistent_regression(self):
        sig = compute_intermittency_signals(
            [{"status": "FAILED", "error_message": "AssertionError: expected 200"}] * 7
            + [{"status": "PASSED"}]
        )
        assert sig.status_volatility <= 0.2
        assert sig.error_signature_diversity < 0.5
        assert sig.intermittency_label == "persistent_regression"

    def test_in_run_retry_is_strong_flake_signal(self):
        sig = compute_intermittency_signals([
            {"status": "FAILED", "retry_count": 2, "error_message": "boom"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "boom"},
        ])
        assert sig.in_run_retry_rate > 0.0
        assert sig.intermittency_label == "intermittent_flaky"

    def test_is_flaky_run_flag_counts_as_retry_signal(self):
        sig = compute_intermittency_signals([
            {"status": "FAILED", "is_flaky_run": True, "error_message": "x"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "x"},
        ])
        assert sig.in_run_retry_rate > 0.0

    def test_stack_trace_diversity_distinguishes_race_from_bug(self):
        # Same stack every failure => one fingerprint => deterministic bug.
        same = compute_intermittency_signals(
            [{"status": "FAILED", "error_message": "e", "stack_trace": "at foo()\nat bar()"}] * 3
            + [{"status": "PASSED"}]
        )
        assert same.stack_trace_diversity == pytest.approx(1 / 3, abs=0.01)
        # Distinct stacks => environmental/race.
        varied = compute_intermittency_signals([
            {"status": "FAILED", "error_message": "e", "stack_trace": "at alpha()"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "e", "stack_trace": "at beta()"},
        ])
        assert varied.stack_trace_diversity == 1.0

    def test_error_signature_normalises_run_specific_noise(self):
        # Same error kind, different ids/timestamps => one signature.
        sig = compute_intermittency_signals([
            {"status": "FAILED", "error_message": "Timeout waiting for request 0xABCD1234"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "Timeout waiting for request 0x99FF0011"},
        ])
        assert sig.error_signature_diversity == 0.5  # 1 unique sig / 2 fails

    def test_enum_repr_status_normalised(self):
        sig = compute_intermittency_signals([
            {"status": "TestStatus.FAILED", "error_message": "e"},
            {"status": "TestStatus.PASSED"},
            {"status": "TestStatus.FAILED", "error_message": "e"},
        ])
        assert sig.fail_count == 2
        assert sig.flip_count == 2

    def test_insufficient_data_label(self):
        assert compute_intermittency_signals([]).intermittency_label == "insufficient_data"
        two = compute_intermittency_signals([{"status": "FAILED"}, {"status": "PASSED"}])
        assert two.intermittency_label == "insufficient_data"  # < 3 runs

    @pytest.mark.parametrize("records", [
        None,
        [None, "garbage", 123],
        [{"status": None}, {}],
        [{"status": "FAILED", "retry_count": "not-a-number"}],
        [{"status": "FAILED", "error_message": object()}],
    ])
    def test_never_raises_on_malformed_input(self, records):
        sig = compute_intermittency_signals(records)
        assert isinstance(sig, IntermittencySignals)

    def test_signals_serialise_to_plain_dict(self):
        d = compute_intermittency_signals([{"status": "FAILED"}]).to_dict()
        assert set(d) >= {
            "status_volatility", "error_signature_diversity",
            "stack_trace_diversity", "in_run_retry_rate", "intermittency_label",
        }


# ── Service wiring ────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self


def _now():
    return datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_refresh_downgrades_persistent_regression_off_quarantine_track():
    """A low-volatility, single-error fingerprint with a >50% failure rate would
    normally be QUARANTINE; FLK-P1 recognises it as a persistent regression and
    advises INVESTIGATE instead (advisory only — the quarantine state machine is
    untouched).
    """
    from app.services import test_health_coach_service as svc

    project_id = uuid.uuid4()
    t = [_now() - timedelta(days=d) for d in range(8)]
    candidates = _Result([SimpleNamespace(test_fingerprint="fpReg")])
    # 6 FAILED (same error) + 2 PASSED => failure_rate 0.75 (>=0.5 QUARANTINE)
    # but low volatility + single error signature => persistent_regression.
    status_rows = _Result([
        SimpleNamespace(fp="fpReg", status="FAILED", created_at=t[0],
                        error_message="AssertionError: expected 200 got 500",
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="FAILED", created_at=t[1],
                        error_message="AssertionError: expected 200 got 500",
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="FAILED", created_at=t[2],
                        error_message="AssertionError: expected 200 got 500",
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="FAILED", created_at=t[3],
                        error_message="AssertionError: expected 200 got 500",
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="FAILED", created_at=t[4],
                        error_message="AssertionError: expected 200 got 500",
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="FAILED", created_at=t[5],
                        error_message="AssertionError: expected 200 got 500",
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="PASSED", created_at=t[6],
                        error_message=None, stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpReg", status="PASSED", created_at=t[7],
                        error_message=None, stack_trace=None, retry_count=None, is_flaky_run=None),
    ])
    tc_rows = _Result([SimpleNamespace(fp="fpReg", test_name="testReg", suite_name="S")])

    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=[candidates, _Result([]), status_rows, tc_rows])
    added = []
    db.add = lambda obj: added.append(obj)

    count = await svc.refresh_flaky_coach(project_id, db, days=30)
    assert count == 1
    res = added[0]
    assert res.failure_rate == 0.75
    # Persistent-regression discrimination kicks in: NOT quarantined as flaky.
    assert res.quarantine_recommendation == "INVESTIGATE"
    assert any("real regression" in a for a in res.stabilization_actions)


@pytest.mark.asyncio
async def test_get_flaky_coach_surfaces_intermittency_signals():
    from app.services import test_health_coach_service as svc

    project_id = uuid.uuid4()
    cached = _Result([SimpleNamespace(
        test_fingerprint="fpA", test_name="testA", suite_name="S",
        failure_rate=0.5, total_runs=4, failed_runs=2,
        flaky_since=_now(), last_failure_at=_now(),
        quarantine_recommendation="INVESTIGATE",
        stabilization_actions=["existing"], impact_score=10.0,
        status_history=["FAILED", "PASSED", "FAILED", "PASSED"],
        flaky_confidence_low=0.15, flaky_confidence_high=0.85,
        is_flaky_confidence=0.72,
    )])
    signal_rows = _Result([
        SimpleNamespace(fp="fpA", status="FAILED", error_message="Timeout 0xAB",
                        stack_trace="at x()", retry_count=1, is_flaky_run=None),
        SimpleNamespace(fp="fpA", status="PASSED", error_message=None,
                        stack_trace=None, retry_count=None, is_flaky_run=None),
        SimpleNamespace(fp="fpA", status="FAILED", error_message="Connection lost",
                        stack_trace="at y()", retry_count=None, is_flaky_run=None),
    ])
    manual = _Result([])

    db = SimpleNamespace()
    # get_flaky_coach: _load_flaky_cache, _load_intermittency_signals, _load_manual_flaky_triage
    db.execute = AsyncMock(side_effect=[cached, signal_rows, manual])

    resp = await svc.get_flaky_coach(project_id, db, days=30, limit=50)
    assert resp.total_flaky == 1
    entry = resp.entries[0]
    assert entry.status_volatility is not None
    assert entry.error_signature_diversity is not None
    assert entry.in_run_retry_rate is not None
    assert entry.intermittency_label in {
        "intermittent_flaky", "environmental_flaky", "low_volatility_flaky",
        "persistent_regression", "insufficient_data",
    }
    # FLK-P2: the persisted Wilson confidence band is surfaced from the cache row.
    assert entry.flaky_confidence_low == 0.15
    assert entry.flaky_confidence_high == 0.85
