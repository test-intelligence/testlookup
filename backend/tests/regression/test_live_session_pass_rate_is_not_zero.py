"""Live's session outcome: no pass rate is not 0%, and a running run is running.

Each session-state builder defaulted a missing pass rate to 0.0, so Live's
sessions table printed a "0.0%" outcome for a run at 10 passed, 1 failed (its
stored rate is NULL until it finishes), and for a session whose 42 tests had
not reported a result. The test-run fallback also called every run
"completed", a run still in progress included (the UX redesign's browser E2E
pass, 2026-10-08).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import stream_service  # noqa: E402

pytestmark = pytest.mark.regression


def _run(status: str, *, passed: int, failed: int, pass_rate=None):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="viz-3044", status=status,
        total_tests=passed + failed, passed_tests=passed, failed_tests=failed,
        skipped_tests=0, broken_tests=0, pass_rate=pass_rate,
        start_time=datetime(2026, 10, 7, 20, 16, tzinfo=timezone.utc), end_time=None,
        primary_suite_name="CheckoutSuite",
    )


def test_a_run_in_progress_is_running_with_its_rate_so_far():
    state = stream_service.build_test_run_fallback_state(_run("IN_PROGRESS", passed=10, failed=1))
    assert state.status == "running"
    assert state.pass_rate == pytest.approx(90.91)


def test_a_finished_run_keeps_its_stored_rate_and_status():
    state = stream_service.build_test_run_fallback_state(_run("FAILED", passed=29, failed=12, pass_rate=70.7))
    assert state.status == "completed"
    assert state.pass_rate == 70.7


def test_a_measured_zero_is_still_zero():
    state = stream_service.build_test_run_fallback_state(_run("FAILED", passed=0, failed=4))
    assert state.pass_rate == 0.0


def test_a_session_with_no_result_yet_has_no_rate():
    state = stream_service.build_live_session_state(
        {"run_id": "build-live-demo", "project_id": "p", "build_number": "b", "total": 42}
    )
    assert state.pass_rate is None


def test_a_live_payload_without_a_rate_gets_one_from_its_counts():
    state = stream_service.build_live_session_state(
        {"run_id": "r", "project_id": "p", "build_number": "b", "total": 4, "passed": 3, "failed": 1}
    )
    assert state.pass_rate == 75.0


def test_a_completed_session_without_a_stored_rate_has_none():
    session = SimpleNamespace(
        run_id="build-live-demo", id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="b",
        total_tests=42, extra_metadata={"final_state": {"total": 42}},
        started_at=None, completed_at=None, client_name=None, release_name=None, launch_name=None,
    )
    assert stream_service.build_completed_session_state(session).pass_rate is None
