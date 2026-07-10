"""
Quarantine lifecycle tests — PMF US-5.4 (owner / SLA / staleness) and
US-5.5 (auto-promotion out of quarantine).

Hermetic: every DB touch goes through fake sessions / patched
``AsyncSessionLocal``; no live Postgres required.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.postgres import (
    FlakyQuarantineRequest,
    FlakyQuarantineStatus,
    NotificationEventType,
)
from app.models.postgres import TestStatus as _TS  # alias: dodge pytest collection
from app.services import flaky_quarantine_service as svc


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, *, scalars_list=None, scalar_one=None, rows=None):
        self._scalars_list = scalars_list or []
        self._scalar_one = scalar_one
        self._rows = rows or []

    def scalars(self):
        items = list(self._scalars_list)
        return SimpleNamespace(
            all=lambda: items,
            first=lambda: items[0] if items else None,
        )

    def scalar_one_or_none(self):
        return self._scalar_one

    def all(self):
        return list(self._rows)


class FakeSession:
    """Serves pre-programmed results in execute() call order."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return self._results.pop(0)

    async def commit(self):
        self.commits += 1

    def add(self, obj):
        self.added.append(obj)


def _quarantined_row(
    project_id=None,
    *,
    consecutive_passes=0,
    ready_to_promote=False,
    last_stability_run_id=None,
    status=FlakyQuarantineStatus.QUARANTINED.value,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        test_fingerprint="fp-abc",
        test_name="test_login",
        suite_name="auth",
        status=status,
        detection_method="flaky_sentinel_agent",
        flip_rate=0.3,
        flip_window_size=20,
        pass_count=14,
        fail_count=6,
        quarantine_start=datetime.now(timezone.utc),
        quarantine_expires_at=None,
        approved_by_user_id=None,
        rejected_by_user_id=None,
        owner_user_id=None,
        sla_days=14,
        stale_at=None,
        stale_notified_at=None,
        consecutive_passes=consecutive_passes,
        last_stability_run_id=last_stability_run_id,
        ready_to_promote=ready_to_promote,
        ready_notified_at=None,
        updated_at=datetime.now(timezone.utc),
    )


# ── advance_consecutive_passes (pure) ───────────────────────────────────────


def test_pass_increments_counter():
    assert svc.advance_consecutive_passes(0, [_TS.PASSED.value]) == 1
    assert svc.advance_consecutive_passes(7, [_TS.PASSED.value]) == 8


def test_single_fail_resets_counter_to_zero():
    """REGRESSION (US-5.5 acceptance): one failure resets the whole streak
    — even at 19/20, even when the run also contains passes (retries)."""
    assert svc.advance_consecutive_passes(19, [_TS.FAILED.value]) == 0
    assert svc.advance_consecutive_passes(19, [_TS.BROKEN.value]) == 0
    # A retried run with pass AND fail still counts as a failure.
    assert svc.advance_consecutive_passes(
        19, [_TS.PASSED.value, _TS.FAILED.value]
    ) == 0


def test_skipped_only_run_is_no_signal():
    assert svc.advance_consecutive_passes(5, [_TS.SKIPPED.value]) == 5
    assert svc.advance_consecutive_passes(5, []) == 5


def test_retried_all_pass_run_counts_once():
    # Multiple PASSED entries (retries) in one run advance by exactly one.
    assert svc.advance_consecutive_passes(
        3, [_TS.PASSED.value, _TS.PASSED.value]
    ) == 4


# ── Lifecycle policy resolution ─────────────────────────────────────────────


def test_lifecycle_policy_defaults_match_backlog():
    policy = svc.EffectiveLifecyclePolicy()
    assert policy.sla_days == 14
    assert policy.auto_create_defect is False
    assert policy.auto_promote is False
    assert policy.promote_after_passes == 20
    assert policy.detection_flip_rate_threshold == 0.20
    assert policy.detection_min_runs == 10


@pytest.mark.asyncio
async def test_missing_policy_row_resolves_to_defaults():
    db = FakeSession([FakeResult(scalar_one=None)])
    policy = await svc.get_lifecycle_policy(db, uuid.uuid4())
    assert policy == svc.EffectiveLifecyclePolicy()


@pytest.mark.asyncio
async def test_explicit_policy_row_maps_through():
    row = SimpleNamespace(
        sla_days=7,
        auto_create_defect=True,
        auto_promote=True,
        promote_after_passes=5,
        detection_flip_rate_threshold=0.5,
        detection_min_runs=25,
    )
    db = FakeSession([FakeResult(scalar_one=row)])
    policy = await svc.get_lifecycle_policy(db, uuid.uuid4())
    assert policy.sla_days == 7
    assert policy.auto_create_defect is True
    assert policy.auto_promote is True
    assert policy.promote_after_passes == 5
    assert policy.detection_flip_rate_threshold == 0.5
    assert policy.detection_min_runs == 25


# ── Owner resolution (US-5.4) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_resolved_from_suite_rule_contact():
    rule = SimpleNamespace(
        id=uuid.uuid4(),
        match_type="suite_name",
        match_pattern="auth*",
        service_name="auth-svc",
        team_name="auth-team",
        team_contact="lead@acme.dev",
        priority=10,
    )
    owner = SimpleNamespace(id=uuid.uuid4())
    db = FakeSession([
        FakeResult(scalars_list=[rule]),   # load_rules_for_project
        FakeResult(scalar_one=None),       # Project lookup (no owner map)
        FakeResult(scalars_list=[owner]),  # User lookup by contact
    ])
    resolved = await svc._resolve_owner_user_id(
        db, uuid.uuid4(), "auth.spec.ts", "test_login",
    )
    assert resolved == owner.id


@pytest.mark.asyncio
async def test_owner_none_when_no_rule_matches():
    db = FakeSession([
        FakeResult(scalars_list=[]),  # no rules
        FakeResult(scalar_one=None),  # no project owner map
    ])
    resolved = await svc._resolve_owner_user_id(
        db, uuid.uuid4(), "auth.spec.ts", "test_login",
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_activation_context_falls_back_to_actor_and_never_raises():
    """Ownership-resolution faults must never fail activation: the context
    helper swallows everything and falls back to the approving QA lead."""
    fallback = uuid.uuid4()

    def _boom():
        raise RuntimeError("db down")

    with patch.object(svc, "AsyncSessionLocal", _boom):
        policy, owner_id = await svc._lifecycle_activation_context(
            uuid.uuid4(), "auth", "test_login", fallback,
        )
    assert owner_id == fallback
    assert policy == svc.EffectiveLifecyclePolicy()


# ── stale property (US-5.4 surfacing) ───────────────────────────────────────


def _orm_row(status: str, stale_at):
    row = FlakyQuarantineRequest()
    row.status = status
    row.stale_at = stale_at
    return row


def test_stale_true_for_active_quarantine_past_sla():
    past = datetime.now(timezone.utc) - timedelta(days=2)
    assert _orm_row("QUARANTINED", past).stale is True
    assert _orm_row("RECHECK_SCHEDULED", past).stale is True
    assert _orm_row("RE_QUARANTINED", past).stale is True


def test_stale_false_within_sla_or_without_deadline():
    future = datetime.now(timezone.utc) + timedelta(days=2)
    assert _orm_row("QUARANTINED", future).stale is False
    assert _orm_row("QUARANTINED", None).stale is False


def test_stale_false_for_non_active_states():
    past = datetime.now(timezone.utc) - timedelta(days=2)
    for status in ("PROPOSED", "APPROVED", "RELEASED", "REJECTED", "EXPIRED"):
        assert _orm_row(status, past).stale is False


# ── update_quarantine_stability (US-5.5) ────────────────────────────────────


def _stability_session(row, case_statuses, policy_row):
    """execute() order: run lookup → quarantine rows → case rows → policy."""
    return FakeSession([
        FakeResult(scalar_one=SimpleNamespace(project_id=row.project_id)),
        FakeResult(scalars_list=[row]),
        FakeResult(rows=[(row.test_fingerprint, s) for s in case_statuses]),
        FakeResult(scalar_one=policy_row),
    ])


async def _run_stability(row, case_statuses, policy_row, run_id=None):
    run_id = run_id or uuid.uuid4()
    db = _stability_session(row, case_statuses, policy_row)
    dispatch = AsyncMock()
    audit = AsyncMock()
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: db), \
         patch.object(svc, "_audit", audit), \
         patch(
             "app.services.notification_transitions.dispatch_quarantine_transitions",
             dispatch,
         ):
        result = await svc.update_quarantine_stability(run_id)
    return result, db, dispatch, audit, run_id


@pytest.mark.asyncio
async def test_stability_pass_increments_and_stamps_run():
    row = _quarantined_row(consecutive_passes=3)
    policy = None  # defaults: threshold 20, auto_promote off
    result, db, dispatch, audit, run_id = await _run_stability(
        row, [_TS.PASSED.value], policy,
    )
    assert result == {"tracked": 1, "auto_released": 0, "ready": 0}
    assert row.consecutive_passes == 4
    assert row.last_stability_run_id == run_id
    assert db.commits == 1
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_stability_single_fail_resets_and_clears_ready_flag():
    """REGRESSION (US-5.5): one failing run resets the streak to 0 AND
    withdraws a pending ready_to_promote flag."""
    row = _quarantined_row(consecutive_passes=19, ready_to_promote=True)
    row.ready_notified_at = datetime.now(timezone.utc)
    result, db, dispatch, audit, _ = await _run_stability(
        row, [_TS.FAILED.value], None,
    )
    assert result["tracked"] == 1
    assert row.consecutive_passes == 0
    assert row.ready_to_promote is False
    assert row.ready_notified_at is None  # re-armed for the next crossing
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_stability_threshold_flags_ready_when_auto_promote_off():
    row = _quarantined_row(consecutive_passes=19)
    policy_row = SimpleNamespace(
        sla_days=14, auto_create_defect=False, auto_promote=False,
        promote_after_passes=20, detection_flip_rate_threshold=0.2,
        detection_min_runs=10,
    )
    result, db, dispatch, audit, _ = await _run_stability(
        row, [_TS.PASSED.value], policy_row,
    )
    assert result == {"tracked": 1, "auto_released": 0, "ready": 1}
    assert row.status == FlakyQuarantineStatus.QUARANTINED.value  # NOT released
    assert row.ready_to_promote is True
    assert row.ready_notified_at is not None
    assert dispatch.await_count == 1
    assert dispatch.await_args.args[1] == (
        NotificationEventType.TEST_READY_TO_UNQUARANTINE
    )
    audit.assert_awaited()  # ready_to_promote audit entry


@pytest.mark.asyncio
async def test_stability_threshold_auto_releases_when_auto_promote_on():
    row = _quarantined_row(consecutive_passes=19)
    policy_row = SimpleNamespace(
        sla_days=14, auto_create_defect=False, auto_promote=True,
        promote_after_passes=20, detection_flip_rate_threshold=0.2,
        detection_min_runs=10,
    )
    result, db, dispatch, audit, _ = await _run_stability(
        row, [_TS.PASSED.value], policy_row,
    )
    assert result == {"tracked": 1, "auto_released": 1, "ready": 0}
    assert row.status == FlakyQuarantineStatus.RELEASED.value
    # Existing test.unquarantined event — same as a manual/recheck release.
    assert dispatch.await_count == 1
    assert dispatch.await_args.args[1] == NotificationEventType.TEST_UNQUARANTINED
    # Audit-logged with the dedicated action.
    assert audit.await_args.kwargs["action"] == "auto_promote_release"


@pytest.mark.asyncio
async def test_stability_idempotent_per_run():
    run_id = uuid.uuid4()
    row = _quarantined_row(consecutive_passes=5, last_stability_run_id=run_id)
    result, db, dispatch, audit, _ = await _run_stability(
        row, [_TS.PASSED.value], None, run_id=run_id,
    )
    assert result == {"tracked": 0, "auto_released": 0, "ready": 0}
    assert row.consecutive_passes == 5  # unchanged — already stamped
    assert db.commits == 0


@pytest.mark.asyncio
async def test_stability_noop_when_flag_off():
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=False)):
        result = await svc.update_quarantine_stability(uuid.uuid4())
    assert result == {"tracked": 0, "auto_released": 0, "ready": 0}


# ── mark_stale_quarantines (US-5.4) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_sweep_stamps_and_notifies_once_per_entry():
    row = _quarantined_row()
    row.stale_at = datetime.now(timezone.utc) - timedelta(days=3)
    db = FakeSession([FakeResult(scalars_list=[row])])
    dispatch = AsyncMock()
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: db), \
         patch(
             "app.services.notification_transitions.dispatch_quarantine_transitions",
             dispatch,
         ):
        flagged = await svc.mark_stale_quarantines()
    assert flagged == 1
    assert row.stale_notified_at is not None  # once-only anchor stamped
    assert db.commits == 1
    assert dispatch.await_count == 1
    assert dispatch.await_args.args[1] == NotificationEventType.TEST_QUARANTINE_STALE
    entries = dispatch.await_args.args[2]
    assert "past its 14-day SLA" in entries[0]["detail"]


@pytest.mark.asyncio
async def test_stale_sweep_noop_when_flag_off():
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=False)):
        assert await svc.mark_stale_quarantines() == 0


# ── Event plumbing ──────────────────────────────────────────────────────────


def test_quarantine_event_rendering_covers_lifecycle_events():
    from app.services.notification_transitions import _QUARANTINE_EVENT_RENDERING
    assert set(_QUARANTINE_EVENT_RENDERING) == {
        "test.quarantined",
        "test.unquarantined",
        "test.quarantine_stale",
        "test.ready_to_unquarantine",
    }


def test_transition_event_vocab_in_sync_between_service_and_schemas():
    from app.models.schemas import TRANSITION_EVENT_VALUES as schema_values
    from app.services.notification_transitions import (
        TRANSITION_EVENT_VALUES as service_values,
    )
    assert set(schema_values) == set(service_values)
    assert "test.quarantine_stale" in service_values
    assert "test.ready_to_unquarantine" in service_values


# ── Internal defect staging (US-5.4) ────────────────────────────────────────


def test_stage_internal_defect_builds_local_record():
    row = _quarantined_row()
    db = FakeSession([])
    defect_id = svc._stage_internal_defect(db, row)
    assert defect_id is not None
    assert len(db.added) == 1
    defect = db.added[0]
    assert defect.id == defect_id
    assert defect.title == "Quarantined flaky test: test_login"
    assert defect.project_id == row.project_id
    assert defect.resolution_status == "OPEN"
    assert defect.promotion_source == "quarantine_lifecycle"
    # Flake history + review link in the body.
    assert "flip rate 30%" in defect.description
    assert "/quarantine" in defect.description
    # No Jira fields — internal record only (Epic 6 does the posting).
    assert defect.jira_ticket_id is None
