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
from datetime import datetime, timedelta, timezone
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


@pytest.mark.asyncio
async def test_newer_run_waits_when_older_transition_is_still_in_flight():
    from sqlalchemy.dialects import postgresql

    older_operation_id = uuid.uuid4()

    class _OrderDB:
        def __init__(self, result):
            self.result = result
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalar_one_or_none=lambda: self.result)

    project_id = uuid.uuid4()
    base = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    older_run = SimpleNamespace(
        id=RUN_A,
        project_id=project_id,
        end_time=base,
        created_at=base,
    )
    newer_run = SimpleNamespace(
        id=RUN_B,
        project_id=project_id,
        end_time=base + timedelta(seconds=1),
        created_at=base + timedelta(seconds=1),
    )

    newer_db = _OrderDB(older_operation_id)
    with pytest.raises(nt.TransitionOrderPending, match="older_transition_run_pending"):
        await nt._ensure_transition_order(newer_db, newer_run)

    sql = str(newer_db.statement.compile(dialect=postgresql.dialect()))
    assert "run_downstream_outbox" in sql
    assert "test_runs" in sql
    compiled_params = str(newer_db.statement.compile().params)
    assert "transition_notifications" in compiled_params
    for status in {
        "waiting",
        "pending",
        "sending",
        "published",
        "processing",
        "failed",
    }:
        assert status in compiled_params

    # Once no earlier live intent remains, the older run and the retried newer
    # run are both admitted in chronological order.
    await nt._ensure_transition_order(_OrderDB(None), older_run)
    await nt._ensure_transition_order(_OrderDB(None), newer_run)


@pytest.mark.asyncio
async def test_transition_state_rows_are_selected_for_update():
    from sqlalchemy.dialects import postgresql

    state = _state("fp-lock")

    class _LockDB:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [state]))

    db = _LockDB()
    result = await nt._load_or_seed_states(
        db, uuid.uuid4(), ["fp-lock"], set()
    )

    sql = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert result == {"fp-lock": state}


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


# ── Durable transition delivery ordering (H12) ────────────────────────────


@pytest.mark.asyncio
async def test_transition_default_fanout_uses_stable_delivery_scope(monkeypatch):
    stage = AsyncMock()
    monkeypatch.setattr(mgr, "stage_scoped_preference_deliveries", stage)
    db = SimpleNamespace()

    event = nt.TransitionEvent(
        event=NotificationEventType.TEST_RECOVERED.value,
        fingerprint="fp1",
        test_name="test_recovered",
    )
    result = await nt._dispatch_transition_events(
        db=db,
        run_id=RUN_A,
        project_id=uuid.uuid4(),
        project_name="acme",
        build_number="42",
        events=[event],
        cluster_by_tc={},
        recent_failed_builds={},
        team_channels={},
        ownership_rules=[],
        owner_map=None,
        delivery_scope=f"transition-run:{RUN_A}",
    )

    assert result == {
        "events": 1,
        "clusters": 0,
        "team_batches": 0,
        "default_events": 1,
    }
    stage.assert_awaited_once()
    assert stage.await_args.args == (db,)
    assert stage.await_args.kwargs["delivery_scope"] == f"transition-run:{RUN_A}"


@pytest.mark.asyncio
async def test_team_transition_stages_durable_route_snapshot(monkeypatch):
    from app.services import notification_routing as routing

    event = nt.TransitionEvent(
        event=NotificationEventType.TEST_RECOVERED.value,
        fingerprint="fp1",
        test_name="test_recovered",
    )
    team_batch = routing.TeamBatch(
        recovered=[event],
        channel=routing.TeamChannelInfo(
            team_name="payments",
            channel_type=NotificationChannel.SLACK.value,
            target="https://hooks.slack.test/team-secret",
        ),
    )
    monkeypatch.setattr(
        routing,
        "partition_transitions",
        lambda *args: ([team_batch], routing.TransitionBatch()),
    )
    direct_send = AsyncMock()
    monkeypatch.setattr(routing, "send_to_team_channel", direct_send)
    stage = AsyncMock()
    monkeypatch.setattr(mgr, "stage_team_notification_deliveries", stage)
    load = AsyncMock()
    monkeypatch.setattr(mgr, "_load_and_notify", load)

    result = await nt._dispatch_transition_events(
        db=SimpleNamespace(),
        run_id=RUN_A,
        project_id=uuid.uuid4(),
        project_name="acme",
        build_number="42",
        events=[event],
        cluster_by_tc={},
        recent_failed_builds={},
        team_channels={"payments": team_batch.channel},
        ownership_rules=[SimpleNamespace()],
        owner_map=None,
        delivery_scope=f"transition-run:{RUN_A}",
    )

    assert result["team_batches"] == 1
    direct_send.assert_not_awaited()
    load.assert_not_awaited()
    stage.assert_awaited_once()
    assert stage.await_args.kwargs["delivery_scope"] == f"transition-run:{RUN_A}"
    delivery = stage.await_args.kwargs["deliveries"][0]
    assert delivery["team_name"] == "payments"
    assert delivery["channel_type"] == NotificationChannel.SLACK.value
    assert delivery["target"] == "https://hooks.slack.test/team-secret"
    assert delivery["fallback"]["delivery_scope"] == (
        f"transition-run:{RUN_A}:team-fallback:payments"
    )
    assert delivery["fallback"]["events"] == [
        NotificationEventType.TEST_RECOVERED.value
    ]


class _TransitionRunDB:
    def __init__(self, order: list[str]):
        project_id = uuid.uuid4()
        self._results = iter([
            SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(
                    project_id=project_id,
                    build_number="42",
                )
            ),
            SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(
                    name="acme",
                    component_owner_map=None,
                )
            ),
            SimpleNamespace(
                all=lambda: [SimpleNamespace(
                    id=uuid.uuid4(),
                    test_fingerprint="fp1",
                    status="PASSED",
                    test_name="test_recovered",
                    suite_name="suite",
                )]
            ),
        ])
        self.order = order

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args, **kwargs):
        return next(self._results)

    async def commit(self):
        self.order.append("state_commit")


def _patch_transition_evaluation(monkeypatch, db, dispatch):
    import app.db.postgres as postgres
    import app.services.github_pr_comment_service as github_pr_comment
    import app.services.notification_routing as notification_routing
    import app.services.ownership_resolver_service as ownership_resolver

    event = nt.TransitionEvent(
        event=NotificationEventType.TEST_RECOVERED.value,
        fingerprint="fp1",
        test_name="test_recovered",
    )
    policy = SimpleNamespace(
        transitions_enabled=True,
        enabled_events=[NotificationEventType.TEST_RECOVERED.value],
        consecutive_failure_threshold=2,
    )
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(nt, "get_effective_policy", AsyncMock(return_value=policy))
    monkeypatch.setattr(nt, "_load_or_seed_states", AsyncMock(return_value={}))
    monkeypatch.setattr(nt, "evaluate_case_transitions", lambda **kwargs: [event])
    monkeypatch.setattr(
        github_pr_comment,
        "_flaky_fingerprints",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        notification_routing,
        "load_team_channels",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        ownership_resolver,
        "load_rules_for_project",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(nt, "_dispatch_transition_events", dispatch)


@pytest.mark.asyncio
async def test_transition_state_commits_only_after_delivery_intent_staging(monkeypatch):
    order: list[str] = []
    db = _TransitionRunDB(order)

    async def _stage(**kwargs):
        order.append("child_staged")
        assert kwargs["delivery_scope"] == f"transition-run:{RUN_A}"
        return {"events": 1}

    dispatch = AsyncMock(side_effect=_stage)
    _patch_transition_evaluation(monkeypatch, db, dispatch)

    assert await nt.evaluate_run_transitions(RUN_A) == {"events": 1}
    assert order == ["child_staged", "state_commit"]


@pytest.mark.asyncio
async def test_transition_state_is_not_committed_when_child_staging_fails(monkeypatch):
    order: list[str] = []
    db = _TransitionRunDB(order)
    dispatch = AsyncMock(side_effect=RuntimeError("child staging failed"))
    _patch_transition_evaluation(monkeypatch, db, dispatch)

    with pytest.raises(RuntimeError, match="child staging failed"):
        await nt.evaluate_run_transitions(RUN_A)

    assert order == []


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
