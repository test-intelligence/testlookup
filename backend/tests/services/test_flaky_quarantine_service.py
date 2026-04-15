"""
Unit tests for ``app.services.flaky_quarantine_service`` — focused on the
module-level constants and the snapshot helper. The transition helpers
(``approve``/``reject``/``release``/``run_recheck_cycle``) hit the DB
and are covered by integration tests.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import flaky_quarantine_service as svc
from app.models.postgres import FlakyQuarantineStatus


def test_live_states_constant_excludes_terminal_states():
    assert FlakyQuarantineStatus.RELEASED.value not in svc._LIVE_STATES
    assert FlakyQuarantineStatus.REJECTED.value not in svc._LIVE_STATES
    assert FlakyQuarantineStatus.EXPIRED.value not in svc._LIVE_STATES
    # All five live states must be present.
    for live in (
        FlakyQuarantineStatus.DETECTED,
        FlakyQuarantineStatus.PROPOSED,
        FlakyQuarantineStatus.APPROVED,
        FlakyQuarantineStatus.QUARANTINED,
        FlakyQuarantineStatus.RECHECK_SCHEDULED,
        FlakyQuarantineStatus.RE_QUARANTINED,
    ):
        assert live.value in svc._LIVE_STATES


def test_snapshot_captures_audit_fields():
    row = SimpleNamespace(
        status="PROPOSED",
        flip_rate=0.42,
        flip_window_size=20,
        quarantine_start=None,
        quarantine_expires_at=None,
        approved_by_user_id=None,
        rejected_by_user_id=None,
    )
    snap = svc._snapshot(row)
    assert snap == {
        "status": "PROPOSED",
        "flip_rate": 0.42,
        "flip_window_size": 20,
        "quarantine_start": None,
        "quarantine_expires_at": None,
        "approved_by_user_id": None,
        "rejected_by_user_id": None,
    }


def test_recheck_release_threshold_is_conservative():
    """Tight threshold so we don't release tests that are only moderately
    stable. 10% flip rate over 10+ runs is the floor for release."""
    assert svc._RECHECK_RELEASE_THRESHOLD == 0.10


def test_proposal_ttl_days_matches_ux_plan():
    """Week-long TTL keeps the review queue readable."""
    assert svc._PROPOSAL_TTL_DAYS == 7


def test_default_quarantine_duration_days_matches_plan():
    """14 days is the documented default window size."""
    assert svc._DEFAULT_QUARANTINE_DAYS == 14
