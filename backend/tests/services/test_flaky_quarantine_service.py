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


def test_active_states_are_the_enforced_subset_of_live_states():
    """US-5.1 — the CI manifest must reflect *currently effective*
    quarantines only: un-reviewed signals (DETECTED/PROPOSED) and staged
    approvals (APPROVED) may never suppress a CI failure, and terminal
    states are over."""
    for active in (
        FlakyQuarantineStatus.QUARANTINED,
        FlakyQuarantineStatus.RECHECK_SCHEDULED,
        FlakyQuarantineStatus.RE_QUARANTINED,
    ):
        assert active.value in svc._ACTIVE_QUARANTINE_STATES
    for excluded in (
        FlakyQuarantineStatus.DETECTED,
        FlakyQuarantineStatus.PROPOSED,
        FlakyQuarantineStatus.APPROVED,
        FlakyQuarantineStatus.RELEASED,
        FlakyQuarantineStatus.REJECTED,
        FlakyQuarantineStatus.EXPIRED,
    ):
        assert excluded.value not in svc._ACTIVE_QUARANTINE_STATES
    # Sanity: every active state is also a live state.
    assert set(svc._ACTIVE_QUARANTINE_STATES) <= set(svc._LIVE_STATES)


def _manifest_row(fp: str, status: str = "QUARANTINED", updated=None):
    from datetime import datetime, timezone
    return SimpleNamespace(
        test_fingerprint=fp,
        status=status,
        updated_at=updated or datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_manifest_etag_stable_across_identical_state_and_row_order():
    a = [_manifest_row("fp-a"), _manifest_row("fp-b")]
    b = [_manifest_row("fp-b"), _manifest_row("fp-a")]  # same set, reordered
    assert svc.manifest_etag(a) == svc.manifest_etag(b)
    assert len(svc.manifest_etag(a)) == 64  # sha256 hex


def test_manifest_etag_changes_when_state_changes():
    from datetime import datetime, timezone
    base = [_manifest_row("fp-a")]
    assert svc.manifest_etag(base) != svc.manifest_etag([])  # entry added
    assert svc.manifest_etag(base) != svc.manifest_etag(
        [_manifest_row("fp-a", status="RE_QUARANTINED")]
    )
    assert svc.manifest_etag(base) != svc.manifest_etag(
        [_manifest_row("fp-a", updated=datetime(2026, 7, 2, tzinfo=timezone.utc))]
    )


def test_active_quarantine_entries_empty_when_flag_off():
    import asyncio
    import uuid as _uuid
    from unittest.mock import AsyncMock, patch

    db = AsyncMock()
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=False)):
        result = asyncio.run(svc.active_quarantine_entries(db, _uuid.uuid4()))
    assert result == []
    db.execute.assert_not_called()


def test_active_quarantine_entries_returns_row_classname_pairs():
    import asyncio
    import uuid as _uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    row = _manifest_row("fp-a")
    exec_result = MagicMock()
    exec_result.all.return_value = [(row, "com.acme.LoginTest")]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)):
        result = asyncio.run(svc.active_quarantine_entries(db, _uuid.uuid4()))
    assert result == [(row, "com.acme.LoginTest")]


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
