"""
Transition-based notification engine — PMF US-7.1 / US-7.2.

The alert-fatigue fix: instead of a message on every run, notify only when
a test CHANGES state. Evaluated at run finalization (the same
post-ingestion orchestration point that enqueues the per-run fan-out, with
its own try/except isolation) via the ``dispatch_transition_notifications``
Celery task.

Transition events (see ``NotificationEventType``):

* ``test.newly_failing``  — the fingerprint FAILED/BROKEN in >= N
  consecutive completed runs (N = per-project threshold, default 2) and
  was not already in the confirmed-failing state.
* ``test.recovered``      — was confirmed-failing, now PASSED.
* ``test.newly_flaky``    — entered the known-flaky set this run. The
  known-flaky definition is REUSED from the PR-comment work
  (``github_pr_comment_service._flaky_fingerprints``): active quarantine
  union the flaky-coach result cache.
* ``test.quarantined`` / ``test.unquarantined`` — quarantine workflow
  transitions. These are event-driven: ``flaky_quarantine_service`` calls
  :func:`dispatch_quarantine_transitions` at its own hook points
  (approve / release / recheck-release) instead of this engine polling.

State store: one ``NotificationTestState`` row per (project, fingerprint).
``last_run_id`` makes evaluation idempotent per (fingerprint, run) — a
re-finalized run advances nothing and therefore fires nothing.

Cluster dedup (US-7.2): newly-failing tests that share a ``FailureCluster``
of the run collapse into ONE cluster line ("Cluster '<label>' — N tests,
M suites"); clusterless tests fall back to per-test lines, capped at
``MAX_TRANSITION_LINES`` with a "+K more" overflow line. Note: clusters
are produced by the async AI pipeline, so on first finalization they may
not exist yet — the engine uses whatever clusters exist at evaluation time
and falls back to (capped) per-test lines otherwise.

Batching: a run IS the batch. All transitions detected in one run
finalization render into a single message per channel — no timer window
needed.

Transaction model: Celery-task-owned — the engine opens its own
``AsyncSessionLocal`` and commits the state-store update itself (see the
allowlist entry in ``tests/test_architectural_transaction_boundaries.py``).
Delivery reuses ``notification.manager._load_and_notify`` (channel fan-out,
NotificationLog audit rows, per-user routing via the existing
``NotificationPreference.events`` lists).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    FailureCluster,
    NotificationEventType,
    NotificationTestState,
    NotificationTransitionPolicy,
    TestCase,
    TestRun,
    TestStatus,
)

logger = structlog.get_logger("services.notification_transitions")


# All five transition event values, in render order.
TRANSITION_EVENT_VALUES: tuple[str, ...] = (
    NotificationEventType.TEST_NEWLY_FAILING.value,
    NotificationEventType.TEST_RECOVERED.value,
    NotificationEventType.TEST_NEWLY_FLAKY.value,
    NotificationEventType.TEST_QUARANTINED.value,
    NotificationEventType.TEST_UNQUARANTINED.value,
)

DEFAULT_CONSECUTIVE_FAILURE_THRESHOLD = 2

# US-7.2 cap: at most this many transition lines per run message (a cluster
# group counts as one line); the rest collapse into a "+K more" line.
MAX_TRANSITION_LINES = 10

_FAILING = "failing"
_PASSING = "passing"

_FAILED_STATUSES = frozenset({TestStatus.FAILED.value, TestStatus.BROKEN.value})


# ── Per-project policy ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class EffectivePolicy:
    """Resolved per-project transition-notification policy.

    A MISSING ``NotificationTransitionPolicy`` row resolves to the
    new-project default (transitions ON, per-run spam OFF). Migration 0103
    backfilled an explicit row (transitions OFF, per-run ON) for every
    project that existed at upgrade time, so existing projects keep their
    old behaviour until someone edits the policy.
    """
    transitions_enabled: bool = True
    per_run_events_enabled: bool = False
    enabled_events: tuple[str, ...] = TRANSITION_EVENT_VALUES
    consecutive_failure_threshold: int = DEFAULT_CONSECUTIVE_FAILURE_THRESHOLD


async def get_effective_policy(
    db: AsyncSession, project_id: uuid.UUID,
) -> EffectivePolicy:
    """Load the project's policy row, resolving a missing row to the
    new-project default. Read-only."""
    result = await db.execute(
        select(NotificationTransitionPolicy).where(
            NotificationTransitionPolicy.project_id == project_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return EffectivePolicy()
    return EffectivePolicy(
        transitions_enabled=bool(row.transitions_enabled),
        per_run_events_enabled=bool(row.per_run_events_enabled),
        enabled_events=tuple(row.enabled_events or ()),
        consecutive_failure_threshold=int(
            row.consecutive_failure_threshold or DEFAULT_CONSECUTIVE_FAILURE_THRESHOLD
        ),
    )


async def get_policy_row(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[NotificationTransitionPolicy]:
    """The explicit policy row for a project, or ``None`` (= new-project
    defaults apply). Read-only."""
    result = await db.execute(
        select(NotificationTransitionPolicy).where(
            NotificationTransitionPolicy.project_id == project_id
        )
    )
    return result.scalar_one_or_none()


async def upsert_policy(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    transitions_enabled: bool,
    per_run_events_enabled: bool,
    enabled_events: list[str],
    consecutive_failure_threshold: int,
) -> NotificationTransitionPolicy:
    """Create or update the project's policy row. Stage-only — the router
    handler owns ``db.commit()`` (transaction-boundary discipline)."""
    row = await get_policy_row(db, project_id)
    if row is None:
        row = NotificationTransitionPolicy(project_id=project_id)
        db.add(row)
    row.transitions_enabled = transitions_enabled
    row.per_run_events_enabled = per_run_events_enabled
    row.enabled_events = list(enabled_events)
    row.consecutive_failure_threshold = consecutive_failure_threshold
    await db.flush()
    return row


# ── Pure state machine (unit-tested directly) ───────────────────────────────


@dataclass
class TransitionEvent:
    """One detected transition, pre-rendering."""
    event: str                    # NotificationEventType value
    fingerprint: str
    test_name: str
    suite_name: Optional[str] = None
    test_case_id: Optional[uuid.UUID] = None
    consecutive_failures: int = 0


@dataclass
class CaseOutcome:
    """Minimal view of one TestCase row of the finalized run."""
    fingerprint: str
    status: str                   # TestStatus value
    test_name: str
    suite_name: Optional[str] = None
    test_case_id: Optional[uuid.UUID] = None


def evaluate_case_transitions(
    run_id: uuid.UUID,
    cases: Sequence[CaseOutcome],
    states_by_fp: dict[str, Any],
    flaky_fps: set[str],
    threshold: int,
) -> list[TransitionEvent]:
    """Advance the state machine for every case of the run and return the
    transitions that fired.

    ``states_by_fp`` maps fingerprint → state object (ORM row or test
    double) exposing the ``NotificationTestState`` attributes; every case
    fingerprint MUST already have an entry (the orchestrator creates
    missing rows first, seeding ``is_known_flaky`` from the current flaky
    set so a pre-existing flaky backlog doesn't flood on first evaluation).

    Mutates the state objects in place. Idempotent per (fingerprint, run):
    a row already stamped with ``last_run_id == run_id`` is skipped
    entirely, so re-finalizing a run emits nothing.

    The matrix this implements (threshold N, default 2):

    * pass → fail×N          → ``test.newly_failing`` fires once, at N.
    * confirmed-failing → pass → ``test.recovered``.
    * repeats in either state → nothing.
    * fail streak shorter than N broken by a pass → nothing at all.
    * in flaky set now, wasn't before → ``test.newly_flaky``;
      leaving the set is silent.
    * SKIPPED / UNKNOWN outcomes are no-signal: the row is left untouched
      (streaks neither grow nor reset, and the run doesn't consume the
      idempotency stamp for that fingerprint).
    """
    events: list[TransitionEvent] = []
    seen: set[str] = set()

    for case in cases:
        if case.fingerprint in seen:
            # Duplicate fingerprints inside one run (retries) — first wins.
            continue
        seen.add(case.fingerprint)

        state = states_by_fp.get(case.fingerprint)
        if state is None:
            continue
        if state.last_run_id == run_id:
            continue  # already evaluated for this run — idempotent re-finalize

        if case.status in _FAILED_STATUSES:
            outcome = "fail"
        elif case.status == TestStatus.PASSED.value:
            outcome = "pass"
        else:
            continue  # SKIPPED / UNKNOWN — no signal

        state.last_run_id = run_id

        if outcome == "fail":
            state.consecutive_failures = int(state.consecutive_failures or 0) + 1
            if state.consecutive_failures >= threshold and state.state != _FAILING:
                state.state = _FAILING
                state.last_notified_state = _FAILING
                events.append(TransitionEvent(
                    event=NotificationEventType.TEST_NEWLY_FAILING.value,
                    fingerprint=case.fingerprint,
                    test_name=case.test_name,
                    suite_name=case.suite_name,
                    test_case_id=case.test_case_id,
                    consecutive_failures=state.consecutive_failures,
                ))
        else:  # pass
            was_failing = state.state == _FAILING
            state.consecutive_failures = 0
            state.state = _PASSING
            if was_failing:
                state.last_notified_state = _PASSING
                events.append(TransitionEvent(
                    event=NotificationEventType.TEST_RECOVERED.value,
                    fingerprint=case.fingerprint,
                    test_name=case.test_name,
                    suite_name=case.suite_name,
                    test_case_id=case.test_case_id,
                ))

        # Known-flaky membership transition (False → True fires; True →
        # False is silent — recovery from flakiness is covered by the
        # quarantine release event when quarantine is in play).
        in_flaky_set = case.fingerprint in flaky_fps
        if in_flaky_set and not state.is_known_flaky:
            state.is_known_flaky = True
            events.append(TransitionEvent(
                event=NotificationEventType.TEST_NEWLY_FLAKY.value,
                fingerprint=case.fingerprint,
                test_name=case.test_name,
                suite_name=case.suite_name,
                test_case_id=case.test_case_id,
            ))
        elif not in_flaky_set and state.is_known_flaky:
            state.is_known_flaky = False

    return events


# ── Pure cluster dedup + rendering (US-7.2) ─────────────────────────────────


@dataclass
class ClusterGroup:
    """Newly-failing tests of one run that share a FailureCluster."""
    cluster_id: str
    label: str
    tests: list[TransitionEvent] = field(default_factory=list)

    @property
    def suite_count(self) -> int:
        return len({t.suite_name or "" for t in self.tests})


def group_newly_failing(
    newly_failing: Sequence[TransitionEvent],
    cluster_by_test_case_id: dict[uuid.UUID, tuple[str, str]],
) -> tuple[list[ClusterGroup], list[TransitionEvent]]:
    """Split newly-failing transitions into cluster groups and singles.

    ``cluster_by_test_case_id`` maps TestCase id → (cluster_id, label). A
    cluster group is only formed when >= 2 newly-failing tests share the
    cluster — a singleton stays a per-test line (a "cluster of one" reads
    worse than the test name).
    """
    by_cluster: dict[str, ClusterGroup] = {}
    singles: list[TransitionEvent] = []

    for ev in newly_failing:
        mapping = (
            cluster_by_test_case_id.get(ev.test_case_id)
            if ev.test_case_id is not None else None
        )
        if mapping is None:
            singles.append(ev)
            continue
        cluster_id, label = mapping
        group = by_cluster.setdefault(
            cluster_id, ClusterGroup(cluster_id=cluster_id, label=label)
        )
        group.tests.append(ev)

    groups: list[ClusterGroup] = []
    for group in by_cluster.values():
        if len(group.tests) >= 2:
            groups.append(group)
        else:
            singles.extend(group.tests)

    groups.sort(key=lambda g: (-len(g.tests), g.cluster_id))
    return groups, singles


def render_transition_message(
    project_name: str,
    build_number: str,
    groups: Sequence[ClusterGroup],
    single_failing: Sequence[TransitionEvent],
    recovered: Sequence[TransitionEvent],
    newly_flaky: Sequence[TransitionEvent],
    dashboard_url: str,
    recent_failed_builds: Optional[dict[str, list[str]]] = None,
    max_lines: int = MAX_TRANSITION_LINES,
) -> tuple[str, str]:
    """Compose the single per-run transition message: (title, body).

    Every line states the transition and its evidence; the deep link to the
    run/cluster rides in ``dashboard_url`` (rendered as the channel's
    action button) and on each cluster line.
    """
    recent_failed_builds = recent_failed_builds or {}
    lines: list[str] = []

    for group in groups:
        lines.append(
            f"🧩 Cluster '{group.label}' — {len(group.tests)} tests newly failing, "
            f"{group.suite_count} suite{'s' if group.suite_count != 1 else ''} "
            f"→ {dashboard_url}?cluster={group.cluster_id}"
        )

    for ev in single_failing:
        builds = recent_failed_builds.get(ev.fingerprint) or []
        if builds:
            evidence = (
                f"failed {ev.consecutive_failures} consecutive runs: "
                + ", ".join(builds)
            )
        else:
            evidence = f"failed {ev.consecutive_failures} consecutive runs"
        lines.append(f"🔴 Newly failing: {ev.test_name} — {evidence}")

    for ev in recovered:
        lines.append(
            f"🟢 Recovered: {ev.test_name} — passed in build {build_number} "
            f"after a confirmed failing streak"
        )

    for ev in newly_flaky:
        lines.append(
            f"🟡 Newly flaky: {ev.test_name} — entered the known-flaky set "
            f"this run"
        )

    total = len(lines)
    if total > max_lines:
        shown = lines[:max_lines]
        shown.append(f"…and {total - max_lines} more transitions")
        lines = shown

    count = len(groups) + len(single_failing) + len(recovered) + len(newly_flaky)
    title = (
        f"🔀 {count} test transition{'s' if count != 1 else ''} — "
        f"{project_name} build #{build_number}"
    )
    return title, "\n".join(lines)


# ── Orchestrator (Celery entry point) ───────────────────────────────────────


async def evaluate_run_transitions(run_id: uuid.UUID) -> dict[str, Any]:
    """Evaluate transition events for a finalized run and dispatch the
    batched notification. Called from the
    ``dispatch_transition_notifications`` Celery task.

    Returns a small summary dict for task logging. Never intended to be
    called with an injected request session — it owns its transaction
    (Celery-task-owned, see module docstring).
    """
    from app.core.config import settings
    from app.db.postgres import AsyncSessionLocal

    events: list[TransitionEvent] = []
    project_id: Optional[uuid.UUID] = None
    project_name = ""
    build_number = ""
    recent_failed_builds: dict[str, list[str]] = {}
    cluster_by_tc: dict[uuid.UUID, tuple[str, str]] = {}

    async with AsyncSessionLocal() as db:
        run = (
            await db.execute(select(TestRun).where(TestRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            logger.warning("transition_eval_run_not_found", run_id=str(run_id))
            return {"skipped": "run_not_found"}

        project_id = run.project_id
        build_number = run.build_number or ""

        policy = await get_effective_policy(db, project_id)
        if not policy.transitions_enabled:
            return {"skipped": "transitions_disabled"}

        from app.models.postgres import Project
        project = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        project_name = project.name if project else str(project_id)

        case_rows = (
            await db.execute(
                select(
                    TestCase.id,
                    TestCase.test_fingerprint,
                    TestCase.status,
                    TestCase.test_name,
                    TestCase.suite_name,
                ).where(TestCase.test_run_id == run_id)
            )
        ).all()
        if not case_rows:
            return {"skipped": "no_test_cases"}

        cases = [
            CaseOutcome(
                fingerprint=row.test_fingerprint,
                status=row.status if isinstance(row.status, str) else str(row.status),
                test_name=row.test_name,
                suite_name=row.suite_name,
                test_case_id=row.id,
            )
            for row in case_rows
            if row.test_fingerprint
        ]
        fingerprints = {c.fingerprint for c in cases}

        # Known-flaky set — REUSE the PR-comment definition (active
        # quarantine ∪ flaky-coach cache). Failure to resolve it must not
        # block failing/recovered transitions.
        try:
            from app.services.github_pr_comment_service import _flaky_fingerprints
            flaky_fps = await _flaky_fingerprints(db, project_id)
        except Exception as exc:
            logger.warning(
                "transition_eval_flaky_lookup_failed",
                run_id=str(run_id), error=str(exc),
            )
            flaky_fps = set()

        # Load existing state rows (project-scoped), create missing ones.
        state_rows = (
            await db.execute(
                select(NotificationTestState).where(
                    NotificationTestState.project_id == project_id,
                    NotificationTestState.test_fingerprint.in_(fingerprints),
                )
            )
        ).scalars().all()
        states_by_fp: dict[str, Any] = {s.test_fingerprint: s for s in state_rows}
        for fp in fingerprints:
            if fp not in states_by_fp:
                row = NotificationTestState(
                    project_id=project_id,
                    test_fingerprint=fp,
                    state=_PASSING,
                    consecutive_failures=0,
                    # Seed silently: a test that was ALREADY flaky before we
                    # started tracking it must not fire test.newly_flaky on
                    # the first evaluated run.
                    is_known_flaky=fp in flaky_fps,
                )
                db.add(row)
                states_by_fp[fp] = row

        events = evaluate_case_transitions(
            run_id=run_id,
            cases=cases,
            states_by_fp=states_by_fp,
            flaky_fps=flaky_fps,
            threshold=policy.consecutive_failure_threshold,
        )

        # Policy filter: state always advances; emission is per-project
        # configurable.
        enabled = set(policy.enabled_events)
        events = [e for e in events if e.event in enabled]

        # Evidence: recent failing build numbers per newly-failing
        # fingerprint ("failed 2 consecutive runs: b-41, b-42").
        failing_fps = [
            e.fingerprint for e in events
            if e.event == NotificationEventType.TEST_NEWLY_FAILING.value
        ]
        if failing_fps:
            evidence_rows = (
                await db.execute(
                    select(
                        TestCase.test_fingerprint,
                        TestRun.build_number,
                        TestRun.created_at,
                    )
                    .join(TestRun, TestRun.id == TestCase.test_run_id)
                    .where(
                        TestRun.project_id == project_id,
                        TestCase.test_fingerprint.in_(failing_fps),
                        TestCase.status.in_(list(_FAILED_STATUSES)),
                    )
                    .order_by(TestRun.created_at.desc())
                    .limit(200)
                )
            ).all()
            newest_first: dict[str, list[str]] = {}
            for row in evidence_rows:
                newest_first.setdefault(row.test_fingerprint, []).append(
                    row.build_number or "?"
                )
            by_fp_count = {
                e.fingerprint: max(e.consecutive_failures, 1) for e in events
                if e.event == NotificationEventType.TEST_NEWLY_FAILING.value
            }
            for fp, builds in newest_first.items():
                keep = by_fp_count.get(fp, 1)
                recent_failed_builds[fp] = list(reversed(builds[:keep]))

            # Cluster dedup (US-7.2) — whatever FailureClusters exist for
            # this run at evaluation time (the AI pipeline writes them
            # asynchronously; absent clusters degrade to per-test lines).
            cluster_rows = (
                await db.execute(
                    select(FailureCluster).where(
                        FailureCluster.test_run_id == run_id
                    )
                )
            ).scalars().all()
            for cluster in cluster_rows:
                for member_id in cluster.member_test_ids or []:
                    try:
                        cluster_by_tc[uuid.UUID(str(member_id))] = (
                            cluster.cluster_id, cluster.label,
                        )
                    except (ValueError, TypeError):
                        continue

        # Persist the state-store advance regardless of whether anything
        # fires — the stamp is what makes re-finalization idempotent.
        await db.commit()

    if not events:
        return {"events": 0}

    dashboard_url = f"{settings.public_base_url}/runs/{run_id}"

    newly_failing = [
        e for e in events
        if e.event == NotificationEventType.TEST_NEWLY_FAILING.value
    ]
    recovered = [
        e for e in events
        if e.event == NotificationEventType.TEST_RECOVERED.value
    ]
    newly_flaky = [
        e for e in events
        if e.event == NotificationEventType.TEST_NEWLY_FLAKY.value
    ]

    groups, singles = group_newly_failing(newly_failing, cluster_by_tc)
    title, body = render_transition_message(
        project_name=project_name,
        build_number=build_number,
        groups=groups,
        single_failing=singles,
        recovered=recovered,
        newly_flaky=newly_flaky,
        dashboard_url=dashboard_url,
        recent_failed_builds=recent_failed_builds,
    )

    # One batched message per channel per run: subscribers of ANY present
    # transition event receive the whole summary once.
    present_events = sorted({e.event for e in events})
    event_members = [NotificationEventType(v) for v in present_events]
    metadata = {
        "project_name": project_name,
        "build_number": build_number,
        "dashboard_url": dashboard_url,
        "transition_events": present_events,
        "transition_count": len(events),
    }

    from app.services.notification.manager import _load_and_notify
    await _load_and_notify(
        project_id, run_id, event_members,
        lambda _event: (title, body),
        metadata,
    )
    logger.info(
        "transition_notifications_dispatched",
        run_id=str(run_id),
        project_id=str(project_id),
        events=len(events),
        clusters=len(groups),
    )
    return {"events": len(events), "clusters": len(groups)}


# ── Quarantine workflow hook (event-driven, not run-batched) ────────────────


async def dispatch_quarantine_transitions(
    project_id: uuid.UUID,
    event: NotificationEventType,
    tests: list[dict[str, Any]],
) -> None:
    """Send ``test.quarantined`` / ``test.unquarantined`` notifications.

    Called from ``flaky_quarantine_service`` at its state-machine hook
    points (approve → quarantined, release / recheck-release →
    unquarantined). NEVER raises — a notification outage must not break
    the quarantine workflow. ``tests`` items carry ``test_name`` and
    optionally ``suite_name`` / ``detail``.
    """
    try:
        from app.core.config import settings
        from app.db.postgres import AsyncSessionLocal

        if event not in (
            NotificationEventType.TEST_QUARANTINED,
            NotificationEventType.TEST_UNQUARANTINED,
        ) or not tests:
            return

        async with AsyncSessionLocal() as db:
            policy = await get_effective_policy(db, project_id)
            if not policy.transitions_enabled or event.value not in policy.enabled_events:
                return
            from app.models.postgres import Project
            project = (
                await db.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            project_name = project.name if project else str(project_id)

        verb = (
            "quarantined" if event == NotificationEventType.TEST_QUARANTINED
            else "released from quarantine"
        )
        icon = "🔒" if event == NotificationEventType.TEST_QUARANTINED else "🔓"
        dashboard_url = f"{settings.public_base_url}/flaky-tests"

        lines = []
        for t in tests[:MAX_TRANSITION_LINES]:
            name = t.get("test_name") or t.get("test_fingerprint") or "unknown test"
            detail = t.get("detail")
            lines.append(f"{icon} {name}{f' — {detail}' if detail else ''}")
        if len(tests) > MAX_TRANSITION_LINES:
            lines.append(f"…and {len(tests) - MAX_TRANSITION_LINES} more")

        count = len(tests)
        title = (
            f"{icon} {count} test{'s' if count != 1 else ''} {verb} — "
            f"{project_name}"
        )
        metadata = {
            "project_name": project_name,
            "dashboard_url": dashboard_url,
        }

        from app.services.notification.manager import _load_and_notify
        await _load_and_notify(
            project_id, None, [event],
            lambda _event: (title, "\n".join(lines)),
            metadata,
        )
        logger.info(
            "quarantine_transition_dispatched",
            project_id=str(project_id),
            event_type=event.value,
            tests=count,
        )
    except Exception as exc:  # noqa: BLE001 — hook must never raise
        logger.warning(
            "quarantine_transition_dispatch_failed",
            project_id=str(project_id),
            event_type=getattr(event, "value", str(event)),
            error=str(exc),
        )
