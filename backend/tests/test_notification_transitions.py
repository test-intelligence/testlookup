"""Transition-based notifications (PMF US-7.1 / US-7.2).

Covers:

* The state machine matrix — pass→fail→pass emits exactly
  ``test.newly_failing`` (at the threshold) then ``test.recovered``;
  repeats emit nothing; threshold is configurable.
* Idempotent re-finalization — re-evaluating the same run advances
  nothing and fires nothing (per-(fingerprint, run) stamp).
* Known-flaky membership — entering the set fires ``test.newly_flaky``
  once; leaving it is silent; rows created already-flaky are seeded
  silently (orchestrator contract, exercised here via the seed flag).
* Cluster dedup (US-7.2) — shared-FailureCluster tests collapse into one
  cluster line; singleton clusters stay per-test; the 10-line cap emits
  a "+K more" overflow line.
* Policy resolution — a MISSING policy row = new-project defaults
  (transitions ON, per-run spam OFF); an explicit row maps through.
* Per-run spam gate — ``dispatch_run_notifications`` sends nothing when
  the project policy has ``per_run_events_enabled=False`` (new-project
  default) and keeps sending when True (the migration-backfilled value
  for every pre-existing project — no surprise change).
* Quarantine hook dispatch gating — disabled policy/event → no send.
"""
from __future__ import annotations

import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

# aiosmtplib is optional locally; the manager package imports it via
# email_service. Stub before import (same pattern as the other
# notification regression tests).
if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.models.postgres import (  # noqa: E402
    NotificationChannel,
    NotificationEventType,
)
from app.services import notification_transitions as nt  # noqa: E402
from app.services.notification import manager as mgr  # noqa: E402


RUN_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
RUN_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
RUN_C = uuid.UUID("00000000-0000-0000-0000-00000000000c")
RUN_D = uuid.UUID("00000000-0000-0000-0000-00000000000d")


def _state(fp: str = "fp1", **overrides):
    """A NotificationTestState-shaped test double."""
    defaults = dict(
        test_fingerprint=fp,
        state="passing",
        consecutive_failures=0,
        last_notified_state=None,
        is_known_flaky=False,
        last_run_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _case(fp: str = "fp1", status: str = "FAILED", name: str = "test_x", **kw):
    return nt.CaseOutcome(
        fingerprint=fp, status=status, test_name=name,
        suite_name=kw.get("suite_name"), test_case_id=kw.get("test_case_id"),
    )


def _run(run_id, fp, status, states, flaky=frozenset(), threshold=2):
    return nt.evaluate_case_transitions(
        run_id=run_id,
        cases=[_case(fp, status)],
        states_by_fp=states,
        flaky_fps=set(flaky),
        threshold=threshold,
    )


# ── State machine matrix ────────────────────────────────────────────────────


def test_pass_fail_fail_pass_emits_newly_failing_then_recovered():
    states = {"fp1": _state()}
    assert _run(RUN_A, "fp1", "PASSED", states) == []
    assert _run(RUN_B, "fp1", "FAILED", states) == []          # 1 < threshold 2
    events = _run(RUN_C, "fp1", "FAILED", states)
    assert [e.event for e in events] == ["test.newly_failing"]
    assert events[0].consecutive_failures == 2
    events = _run(RUN_D, "fp1", "PASSED", states)
    assert [e.event for e in events] == ["test.recovered"]
    assert states["fp1"].state == "passing"
    assert states["fp1"].consecutive_failures == 0


def test_repeats_emit_nothing():
    states = {"fp1": _state(state="failing", consecutive_failures=5,
                            last_notified_state="failing")}
    # Still failing → nothing (already confirmed-failing).
    assert _run(RUN_A, "fp1", "FAILED", states) == []
    # Recover, then keep passing → exactly one recovered, then nothing.
    assert [e.event for e in _run(RUN_B, "fp1", "PASSED", states)] == ["test.recovered"]
    assert _run(RUN_C, "fp1", "PASSED", states) == []


def test_short_fail_streak_broken_by_pass_emits_nothing_at_all():
    states = {"fp1": _state()}
    assert _run(RUN_A, "fp1", "FAILED", states) == []
    assert _run(RUN_B, "fp1", "PASSED", states) == []  # never hit threshold
    assert states["fp1"].consecutive_failures == 0


def test_threshold_is_configurable():
    states = {"fp1": _state()}
    events = _run(RUN_A, "fp1", "FAILED", states, threshold=1)
    assert [e.event for e in events] == ["test.newly_failing"]
    # threshold=3: two failures are not enough
    states3 = {"fp1": _state()}
    assert _run(RUN_A, "fp1", "FAILED", states3, threshold=3) == []
    assert _run(RUN_B, "fp1", "FAILED", states3, threshold=3) == []
    assert [e.event for e in _run(RUN_C, "fp1", "FAILED", states3, threshold=3)] == [
        "test.newly_failing"
    ]


def test_broken_counts_as_failing():
    states = {"fp1": _state(consecutive_failures=1)}
    events = _run(RUN_A, "fp1", "BROKEN", states)
    assert [e.event for e in events] == ["test.newly_failing"]


def test_skipped_is_no_signal():
    states = {"fp1": _state(consecutive_failures=1)}
    assert _run(RUN_A, "fp1", "SKIPPED", states) == []
    # Streak neither grew nor reset, and the run didn't consume the stamp.
    assert states["fp1"].consecutive_failures == 1
    assert states["fp1"].last_run_id is None


def test_refinalizing_the_same_run_is_idempotent():
    states = {"fp1": _state(consecutive_failures=1)}
    first = _run(RUN_A, "fp1", "FAILED", states)
    assert [e.event for e in first] == ["test.newly_failing"]
    # Same run again — nothing advances, nothing fires.
    assert _run(RUN_A, "fp1", "FAILED", states) == []
    assert states["fp1"].consecutive_failures == 2


def test_duplicate_fingerprint_within_one_run_counts_once():
    states = {"fp1": _state(consecutive_failures=1)}
    events = nt.evaluate_case_transitions(
        run_id=RUN_A,
        cases=[_case("fp1", "FAILED"), _case("fp1", "FAILED")],
        states_by_fp=states,
        flaky_fps=set(),
        threshold=2,
    )
    assert len(events) == 1
    assert states["fp1"].consecutive_failures == 2


# ── Known-flaky membership ──────────────────────────────────────────────────


def test_entering_flaky_set_fires_once_and_leaving_is_silent():
    states = {"fp1": _state()}
    events = _run(RUN_A, "fp1", "PASSED", states, flaky={"fp1"})
    assert [e.event for e in events] == ["test.newly_flaky"]
    assert states["fp1"].is_known_flaky is True
    # Still flaky next run → nothing.
    assert _run(RUN_B, "fp1", "PASSED", states, flaky={"fp1"}) == []
    # Leaves the set → silent, flag cleared.
    assert _run(RUN_C, "fp1", "PASSED", states, flaky=set()) == []
    assert states["fp1"].is_known_flaky is False


def test_rows_seeded_already_flaky_do_not_fire():
    # The orchestrator seeds new rows with is_known_flaky = (fp in set); a
    # long-flaky backlog therefore never floods on first evaluation.
    states = {"fp1": _state(is_known_flaky=True)}
    assert _run(RUN_A, "fp1", "PASSED", states, flaky={"fp1"}) == []


# ── Cluster dedup + rendering (US-7.2) ──────────────────────────────────────


def _failing_event(fp, name, tc_id=None, consecutive=2, suite=None):
    return nt.TransitionEvent(
        event=NotificationEventType.TEST_NEWLY_FAILING.value,
        fingerprint=fp, test_name=name, suite_name=suite,
        test_case_id=tc_id, consecutive_failures=consecutive,
    )


def test_shared_cluster_collapses_to_one_group():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    events = [
        _failing_event("f1", "t1", a, suite="s1"),
        _failing_event("f2", "t2", b, suite="s2"),
        _failing_event("f3", "t3", c, suite="s1"),
    ]
    cluster_map = {a: ("cl_001", "DB timeout"), b: ("cl_001", "DB timeout")}
    groups, singles = nt.group_newly_failing(events, cluster_map)
    assert len(groups) == 1
    assert groups[0].cluster_id == "cl_001"
    assert len(groups[0].tests) == 2
    assert groups[0].suite_count == 2
    assert [s.test_name for s in singles] == ["t3"]


def test_singleton_cluster_falls_back_to_per_test():
    a = uuid.uuid4()
    events = [_failing_event("f1", "t1", a)]
    groups, singles = nt.group_newly_failing(events, {a: ("cl_9", "x")})
    assert groups == []
    assert len(singles) == 1


def test_clusterless_tests_are_singles():
    events = [_failing_event("f1", "t1", uuid.uuid4())]
    groups, singles = nt.group_newly_failing(events, {})
    assert groups == []
    assert len(singles) == 1


def test_render_includes_evidence_and_cluster_line():
    a, b = uuid.uuid4(), uuid.uuid4()
    group = nt.ClusterGroup(cluster_id="cl_001", label="DB timeout")
    group.tests = [
        _failing_event("f1", "t1", a, suite="s1"),
        _failing_event("f2", "t2", b, suite="s2"),
    ]
    title, body = nt.render_transition_message(
        project_name="acme",
        build_number="b-42",
        groups=[group],
        single_failing=[_failing_event("f3", "t3", consecutive=2)],
        recovered=[],
        newly_flaky=[],
        dashboard_url="http://x/runs/r1",
        recent_failed_builds={"f3": ["b-41", "b-42"]},
    )
    assert "acme" in title and "b-42" in title
    assert "Cluster 'DB timeout' — 2 tests newly failing, 2 suites" in body
    assert "cluster=cl_001" in body                      # deep link
    assert "failed 2 consecutive runs: b-41, b-42" in body


def test_render_caps_lines_with_overflow():
    singles = [_failing_event(f"f{i}", f"t{i}") for i in range(14)]
    _, body = nt.render_transition_message(
        project_name="acme", build_number="1",
        groups=[], single_failing=singles, recovered=[], newly_flaky=[],
        dashboard_url="#",
    )
    lines = body.splitlines()
    assert len(lines) == nt.MAX_TRANSITION_LINES + 1
    assert lines[-1] == "…and 4 more transitions"


# ── Policy resolution ───────────────────────────────────────────────────────


class _PolicyDB:
    """Fake session: execute() → result whose scalar_one_or_none() returns
    the configured policy row."""

    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return SimpleNamespace(scalar_one_or_none=lambda: self._row)


@pytest.mark.asyncio
async def test_missing_policy_row_resolves_to_new_project_defaults():
    policy = await nt.get_effective_policy(_PolicyDB(None), uuid.uuid4())
    assert policy.transitions_enabled is True
    assert policy.per_run_events_enabled is False
    assert set(policy.enabled_events) == set(nt.TRANSITION_EVENT_VALUES)
    assert policy.consecutive_failure_threshold == 2


@pytest.mark.asyncio
async def test_explicit_policy_row_maps_through():
    row = SimpleNamespace(
        transitions_enabled=False,
        per_run_events_enabled=True,
        enabled_events=["test.newly_failing"],
        consecutive_failure_threshold=3,
    )
    policy = await nt.get_effective_policy(_PolicyDB(row), uuid.uuid4())
    assert policy.transitions_enabled is False
    assert policy.per_run_events_enabled is True          # legacy behaviour
    assert policy.enabled_events == ("test.newly_failing",)
    assert policy.consecutive_failure_threshold == 3


# ── Per-run spam gate on the legacy dispatch ────────────────────────────────


class _ManagerDB:
    """Fake manager session serving BOTH the policy lookup
    (scalar_one_or_none) and the preference load (.all)."""

    def __init__(self, policy_row, prefs_rows):
        self._policy = policy_row
        self._prefs = prefs_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        statement = str(a[0]) if a else ""
        if "app_settings" in statement or "secret_refs" in statement:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        if "notification_transition_policies" in statement:
            return SimpleNamespace(scalar_one_or_none=lambda: self._policy)
        return SimpleNamespace(
            all=lambda: self._prefs,
        )

    def add(self, obj):
        return None

    async def commit(self):
        return None


def _pref():
    return SimpleNamespace(
        enabled=True, project_id=uuid.uuid4(), user_id=uuid.uuid4(),
        channel=NotificationChannel.SLACK,
        events=[NotificationEventType.RUN_FAILED.value],
        failure_rate_threshold=80.0,
        slack_webhook_url="https://hooks.slack test",
        teams_webhook_url=None, email_override=None,
    )


def _policy_row(per_run: bool):
    return SimpleNamespace(
        transitions_enabled=True,
        per_run_events_enabled=per_run,
        enabled_events=list(nt.TRANSITION_EVENT_VALUES),
        consecutive_failure_threshold=2,
    )


async def _dispatch_with_policy(per_run: bool):
    sent = AsyncMock(return_value=("sent", None))
    db = _ManagerDB(_policy_row(per_run), [(_pref(), "u@x.com")])
    with patch.object(mgr, "AsyncSessionLocal", lambda: db), \
         patch.object(mgr, "_dispatch_to_channel", sent):
        await mgr.dispatch_run_notifications(
            project_id=uuid.uuid4(), run_id=uuid.uuid4(), build_number="7",
            pass_rate=50.0, total_tests=10, failed_tests=5,
            project_name="acme",
        )
    return sent


@pytest.mark.asyncio
async def test_per_run_spam_off_suppresses_run_notifications():
    # New-project default: per-run fan-out is silenced entirely.
    sent = await _dispatch_with_policy(per_run=False)
    assert sent.await_args_list == []


@pytest.mark.asyncio
async def test_per_run_enabled_keeps_existing_behaviour():
    # Migration 0103 backfills per_run_events_enabled=True for every
    # existing project — the legacy fan-out must keep working unchanged.
    sent = await _dispatch_with_policy(per_run=True)
    assert len(sent.await_args_list) == 1
    assert sent.await_args_list[0].args[4] == NotificationEventType.RUN_FAILED


# ── Quarantine hook dispatch gating ─────────────────────────────────────────


def _patch_session_factory(monkeypatch, policy_row):
    import app.db.postgres as _pg
    monkeypatch.setattr(
        _pg, "get_session_factory",
        lambda: (lambda: _PolicyDB(policy_row)),
    )


@pytest.mark.asyncio
async def test_quarantine_dispatch_skips_when_transitions_disabled(monkeypatch):
    _patch_session_factory(monkeypatch, SimpleNamespace(
        transitions_enabled=False,
        per_run_events_enabled=True,
        enabled_events=list(nt.TRANSITION_EVENT_VALUES),
        consecutive_failure_threshold=2,
    ))
    load = AsyncMock()
    monkeypatch.setattr(mgr, "_load_and_notify", load)
    await nt.dispatch_quarantine_transitions(
        uuid.uuid4(), NotificationEventType.TEST_QUARANTINED,
        [{"test_name": "t1"}],
    )
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_quarantine_dispatch_skips_when_event_not_enabled(monkeypatch):
    _patch_session_factory(monkeypatch, SimpleNamespace(
        transitions_enabled=True,
        per_run_events_enabled=False,
        enabled_events=["test.newly_failing"],   # quarantine events off
        consecutive_failure_threshold=2,
    ))
    load = AsyncMock()
    monkeypatch.setattr(mgr, "_load_and_notify", load)
    await nt.dispatch_quarantine_transitions(
        uuid.uuid4(), NotificationEventType.TEST_QUARANTINED,
        [{"test_name": "t1"}],
    )
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_quarantine_dispatch_never_raises(monkeypatch):
    import app.db.postgres as _pg

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(_pg, "get_session_factory", _boom)
    # Must swallow — quarantine workflow may never break on notify errors.
    await nt.dispatch_quarantine_transitions(
        uuid.uuid4(), NotificationEventType.TEST_UNQUARANTINED,
        [{"test_name": "t1"}],
    )
