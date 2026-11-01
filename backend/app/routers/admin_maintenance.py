"""Admin-only maintenance endpoints — manually fire Celery beat tasks.

Used immediately after a deploy when the operator doesn't want to wait
for the next scheduled tick. The endpoints are thin shims around the
existing Celery tasks: same code path, same idempotency guarantees,
just triggered on demand. It also holds the operator's readers and repair
tools for work that failed for good: the dead letters, the AI cache's
leftovers, and failed intents in the run outbox.

All endpoints require an instance admin (``require_instance_admin``):
``UserRole.ADMIN``, and not through an API key bound to one project. Project-
scoped admins are NOT sufficient — these tasks walk every project.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_instance_admin
from app.db.postgres import get_db
from app.models.postgres import FeatureFlag, Project, SuiteMembershipParityRun, User
from app.models.schemas import (
    SuiteParityCutover,
    SuiteParityCutoverResult,
    SuiteParityHistory,
    SuiteParityProjectLine,
    SuiteParityRunDetail,
    SuiteParityRunSummary,
    SuiteParitySampleRow,
)
from app.services import feature_flags
from app.services import suite_membership_cutover as suite_cutover
from app.services import suite_membership_parity as parity_service
from app.services import suite_parity_vocab as parity_vocab
from app.services.agent_authority_lock import lock_global_agent_authority

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/maintenance",
    tags=["Admin Maintenance"],
)


@router.post(
    "/backfill-placeholder-test-cases",
    dependencies=[Depends(require_instance_admin())],
)
async def trigger_placeholder_backfill(
    max_runs_per_project: int = 500,
    current_user: User = Depends(get_current_active_user),
):
    """Fire ``worker.tasks.backfill_placeholder_test_cases`` immediately.

    Synthesizes placeholder ``TestCase`` rows for every historical
    ``TestRun`` where ``failed_tests + broken_tests > 0`` but no
    ``test_cases`` exist (the Phase 4.5 live-stream buffer-eviction
    scenario). Beat schedule fires this hourly at :10 — use this
    endpoint right after a deploy if you don't want to wait.

    Idempotent — subsequent calls produce zero rows once the first run
    has filled the gaps. Returns the Celery task id so the caller can
    correlate with worker logs.
    """
    try:
        from app.worker.tasks import backfill_placeholder_test_cases
        result = backfill_placeholder_test_cases.apply_async(
            kwargs={"max_runs_per_project": max_runs_per_project},
        )
        logger.info(
            "admin_triggered_placeholder_backfill",
            actor_user_id=str(current_user.id),
            task_id=result.id,
            max_runs_per_project=max_runs_per_project,
        )
        return {
            "queued": True,
            "task_id": result.id,
            "task_name": "backfill_placeholder_test_cases",
            "actor_user_id": str(current_user.id),
            "note": (
                "Synthesizing placeholder rows for runs whose live-stream "
                "buffer was evicted before persistence. Re-run "
                "``backfill-unassigned-failures`` (also under this prefix) "
                "once this finishes to assign the new rows to QA Leads."
            ),
        }
    except Exception as exc:
        logger.error(
            "admin_placeholder_backfill_dispatch_failed",
            actor_user_id=str(current_user.id),
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {exc}")


@router.post(
    "/backfill-unassigned-failures",
    dependencies=[Depends(require_instance_admin())],
)
async def trigger_unassigned_failures_backfill(
    max_runs_per_project: int = 200,
    current_user: User = Depends(get_current_active_user),
):
    """Fire ``worker.tasks.backfill_unassigned_failures`` immediately.

    Pass 1 provisions a default QA-lead user on every project missing
    one (see ``default_qa_lead_service``). Pass 2 walks recent
    FAILED/BROKEN ``TestCase`` rows whose ``assigned_to_user_id`` is
    NULL and runs the suite-owner resolver across them. The result is
    that ``/my-failures`` populates without having to wait for the
    next 15-minute beat tick.

    Idempotent — only writes to NULL rows, only provisions missing
    leads, never over-writes a manual reassignment.
    """
    try:
        from app.worker.tasks import backfill_unassigned_failures
        result = backfill_unassigned_failures.apply_async(
            kwargs={"max_runs_per_project": max_runs_per_project},
        )
        logger.info(
            "admin_triggered_failures_backfill",
            actor_user_id=str(current_user.id),
            task_id=result.id,
            max_runs_per_project=max_runs_per_project,
        )
        return {
            "queued": True,
            "task_id": result.id,
            "task_name": "backfill_unassigned_failures",
            "actor_user_id": str(current_user.id),
        }
    except Exception as exc:
        logger.error(
            "admin_failures_backfill_dispatch_failed",
            actor_user_id=str(current_user.id),
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {exc}")


@router.post(
    "/drain-active-live-sessions",
    dependencies=[Depends(require_instance_admin())],
)
async def trigger_drain_active_sessions(
    current_user: User = Depends(get_current_active_user),
):
    """Fire ``worker.tasks.drain_active_live_sessions`` immediately.

    Normally runs every 30s. Useful when an operator wants to confirm
    the drain is functional after a deploy without waiting for the
    next tick.
    """
    try:
        from app.worker.tasks import drain_active_live_sessions
        result = drain_active_live_sessions.apply_async()
        logger.info(
            "admin_triggered_drain",
            actor_user_id=str(current_user.id),
            task_id=result.id,
        )
        return {
            "queued": True,
            "task_id": result.id,
            "task_name": "drain_active_live_sessions",
            "actor_user_id": str(current_user.id),
        }
    except Exception as exc:
        logger.error(
            "admin_drain_dispatch_failed",
            actor_user_id=str(current_user.id),
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {exc}")


@router.get(
    "/dlq",
    dependencies=[Depends(require_instance_admin())],
)
async def read_dead_letters(
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_active_user),
):
    """Newest entries of both dead-letter stores (re-audit M2).

    Both stores were write-only: ``/health/ingestion`` counted one and nothing
    counted the other, so an operator could learn that work had permanently
    failed, but never what. Instance admins only -- the entries are
    cross-tenant (run and project ids, error text, sanitized event payloads).
    A store that cannot be read is a 503, never an empty list.
    """
    from app.services.ingestion_dlq import (
        DLQUnavailable,
        get_dlq_count,
        get_stream_dlq_count,
        list_recent_failures,
        list_recent_stream_failures,
    )

    try:
        persist = await list_recent_failures("persist_live_session", limit=limit)
        stream = await list_recent_stream_failures(limit=limit)
    except DLQUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The dead-letter stores could not be read (Redis unavailable). "
                "That is not the same as having no failures."
            ),
        ) from exc

    logger.info(
        "admin_read_dead_letters",
        actor_user_id=str(current_user.id),
        persist_live_session=len(persist),
        stream=len(stream),
    )
    return {
        "limit": limit,
        "sources": {
            "persist_live_session": {
                "count": await get_dlq_count("persist_live_session"),
                "entries": persist,
            },
            "stream": {"count": await get_stream_dlq_count(), "entries": stream},
        },
    }


@router.get(
    "/ai-cache",
    dependencies=[Depends(require_instance_admin())],
)
async def read_ai_cache_stats(
    current_user: User = Depends(get_current_active_user),
):
    """The AI similarity cache's health, and what the pre-M15 shared collection holds.

    Re-audit M15, code review: ``legacy_unscoped_documents`` -- entries written
    to the old collection every tenant shared, which may belong to any tenant --
    was computed by ``get_semantic_cache_stats`` and returned by nothing, so
    the signal to purge that collection could not be seen. Instance admins
    only: the counts span tenants. A cache that cannot be read is a 503, never
    a page of zeros.
    """
    from app.services.semantic_cache import get_semantic_cache_stats

    stats = await get_semantic_cache_stats()
    if stats.get("status") != "healthy":
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "The AI similarity cache could not be read. That is not the "
                    "same as an empty cache."
                ),
                # A deployment without ChromaDB gets this answer too. Say so,
                # rather than leave an operator hunting for an outage (code
                # review and QA of the M15 follow-up).
                "hint": (
                    "ChromaDB is optional. If this deployment does not run it, "
                    "there is no AI similarity cache and nothing to purge."
                ),
                **stats,
            },
        )
    logger.info(
        "admin_read_ai_cache_stats",
        actor_user_id=str(current_user.id),
        legacy_unscoped_documents=stats.get("legacy_unscoped_documents"),
    )
    return stats


async def _check_run_scope(
    db: AsyncSession, current_user: User, run_id: uuid.UUID
) -> uuid.UUID:
    """A run named in the body is checked like any scoped id (code review round 4).

    The route is for instance admins, who pass by role; the call is what the
    authorization scan reads, and a run that does not exist is a 404 rather
    than an empty requeue. Returns the run's project, which the audit row
    carries.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.core.deps import resolve_project_scope  # noqa: PLC0415
    from app.models.postgres import TestRun  # noqa: PLC0415

    project_id = (
        await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    if project_id is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    await resolve_project_scope(db, current_user, str(project_id))
    return project_id


class OutboxRequeueRequest(BaseModel):
    """Which failed run-outbox intents to put back."""

    operation: str = Field(
        ..., description="The post-ingestion operation, for example agent_pipeline."
    )
    last_error: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Only intents that failed with exactly this error, for example "
            "broker_TypeError. Required: a requeue without it re-ran every failed "
            "intent of the operation, including notifications and webhooks that "
            "had executed and failed for their own reasons (code review round 3)."
        ),
    )
    run_id: uuid.UUID | None = Field(None, description="Only this run's intents.")
    limit: int = Field(100, ge=1, le=500, description="At most this many, oldest first.")
    dry_run: bool = Field(
        True, description="List what would be requeued, and change nothing. The default."
    )


@router.post(
    "/outbox/requeue",
    dependencies=[Depends(require_instance_admin())],
)
async def requeue_failed_outbox_operations(
    body: OutboxRequeueRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Put failed post-ingestion work back in the run outbox (re-audit N23).

    A finished run's follow-up work -- notifications, the completion webhook,
    suite comparisons, the AI pipeline -- is published from a durable outbox,
    and an intent that keeps failing is marked ``failed`` and never retried.
    Until N23 every ``agent_pipeline`` intent failed that way with
    ``broker_TypeError``, so the fix alone would not have analysed one of those
    runs. List them with this call (it is a dry run unless ``dry_run`` is
    false), then requeue them in slices of at most 500, oldest first. Each
    requeued ``agent_pipeline`` intent starts one AI pipeline.

    Requeue a stranded N27 intent only after its pipeline is marked failed: a
    pipeline left ``running`` refuses the retry until the stale-pipeline reaper
    (every 10 minutes, after 30) has failed it.
    """
    from app.services.run_downstream_outbox import (
        requeue_failed_downstream_operations as _requeue,
    )

    run_project_id = None
    if body.run_id is not None:
        run_project_id = await _check_run_scope(db, current_user, body.run_id)
    try:
        rows = await _requeue(
            db,
            operation=body.operation,
            last_error=body.last_error,
            run_id=body.run_id,
            limit=body.limit,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "admin_requeued_outbox_operations",
        actor_user_id=str(current_user.id),
        operation=body.operation,
        last_error=body.last_error,
        dry_run=body.dry_run,
        count=len(rows),
        outbox_ids=[row["id"] for row in rows],
    )
    # Re-audit N30: the log line above was the only record, and logs rotate.
    # A requeue re-runs cross-tenant work (each agent_pipeline intent starts an
    # AI pipeline), so who did it, with which filters, and to how many rows is
    # written to the durable audit trail, on the request's session: it commits
    # with the requeue, and a dry run is recorded too.
    from app.services.access_audit_service import log_access_change  # noqa: PLC0415

    await log_access_change(
        db,
        "admin.outbox_requeue",
        current_user,
        project_id=run_project_id,
        after_value={
            "operation": body.operation,
            "last_error": body.last_error,
            "run_id": str(body.run_id) if body.run_id is not None else None,
            "limit": body.limit,
            "dry_run": body.dry_run,
            "matched": len(rows),
            "requeued": 0 if body.dry_run else len(rows),
            "outbox_ids": [str(row["id"]) for row in rows],
        },
    )
    return {
        "operation": body.operation,
        "dry_run": body.dry_run,
        "matched": len(rows),
        "requeued": 0 if body.dry_run else len(rows),
        "rows": rows,
    }


# ── W25a HYG-04: the suite-membership parity harness (the 7-day cutover clock) ─
#
# The two GETs read ``suite_membership_parity_runs`` (+ ``users.username`` and
# ``projects.name`` by id lists) and nothing else: they never claim, enqueue or
# write. The POST reads whether a fresh comparison is running, then enqueues the
# worker task (the comparison never runs in the API process). Instance admins
# only; project names are shown to them alone.
#
# W25d HYG-04 (the cutover): the history GET also reads the cut-over state
# (the flag ``suite_membership_legacy_writes`` + its settings_audit_log trail,
# ``suite_cutover.cutover_state``) and still writes nothing; the cutover POST is
# the ONE action that turns the flag off, refused (409) unless the same
# readiness the GET shows says ``cutover_ready``.


def _as_uuid(value: object) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _usernames(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.username).where(User.id.in_(ids)))
    return {row[0]: str(row[1]) for row in rows.all()}


async def _project_names(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(select(Project.id, Project.name).where(Project.id.in_(ids)))
    return {row[0]: str(row[1]) for row in rows.all()}


def _summary(row: Any, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    requester = row.requested_by_id
    return {
        "id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "requested_by_name": names.get(requester) if requester is not None and row.trigger == "admin" else None,
        "window_runs": row.window_runs,
        "projects_checked": row.projects_checked,
        "keys_compared": row.keys_compared,
        "blocking_diffs": row.blocking_diffs,
        "expected_divergences": row.expected_divergences,
    }


@router.get(
    "/suite-membership-parity",
    response_model=SuiteParityHistory,
    dependencies=[Depends(require_instance_admin())],
)
async def read_suite_membership_parity(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> SuiteParityHistory:
    """The comparisons started in the last 14 UTC days (newest first) and the clean-day streak.

    ``cutover_ready`` is true on the 7th consecutive UTC day whose LAST finished
    comparison is clean. A day with no finished comparison breaks the streak
    (absence is not health); today without one does not, until it is over. An
    empty ``runs`` list means no comparison has run yet — never "0 differences".

    W25d: ``cutover`` is the derived cut-over state. After a cutover the window
    ENDS at ``cutover.cut_over_at`` (the 14 UTC days ending on that date, runs
    started before it), so the evidence stays readable and the dates after the
    cutover never read as missed days. Before it the answer is today's.
    """
    from app.core.config import settings

    now = datetime.now(timezone.utc)
    state = await suite_cutover.cutover_state(db, now=now)
    cut_over_at = state["cut_over_at"]
    rows, streak, ready = await suite_cutover.readiness(db, now=cut_over_at or now, until=cut_over_at)
    names = await _usernames(db, {r.requested_by_id for r in rows if r.requested_by_id is not None})
    return SuiteParityHistory(
        runs=[SuiteParityRunSummary(**_summary(r, names)) for r in rows],
        streak_days=streak,
        required_days=parity_vocab.REQUIRED_CLEAN_DAYS,
        cutover_ready=ready,
        window_runs_now=int(settings.CANONICAL_DELETION_WINDOW_RUNS),
        cutover=SuiteParityCutover(**state),
    )


@router.get(
    "/suite-membership-parity/{parity_run_id}",
    response_model=SuiteParityRunDetail,
    dependencies=[Depends(require_instance_admin())],
)
async def read_suite_membership_parity_run(
    parity_run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> SuiteParityRunDetail:
    """One comparison: its counts per reason, the projects with differences and the sample.

    Project names are resolved at read time (None for a project deleted since).
    """
    row = await db.get(SuiteMembershipParityRun, parity_run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=parity_vocab.NOT_FOUND_SENTENCE)
    per_project = [p for p in (row.per_project or []) if isinstance(p, dict) and _as_uuid(p.get("project_id"))]
    sample = [s for s in (row.sample or []) if isinstance(s, dict) and _as_uuid(s.get("project_id"))]
    project_ids = {_as_uuid(p["project_id"]) for p in per_project} | {_as_uuid(s["project_id"]) for s in sample}
    projects = await _project_names(db, {pid for pid in project_ids if pid is not None})
    names = await _usernames(db, {row.requested_by_id} if row.requested_by_id is not None else set())
    blocking = set(parity_vocab.BLOCKING_REASONS)
    counts = parity_service.zero_counts()
    counts.update({str(k): int(v) for k, v in (row.counts or {}).items()})
    return SuiteParityRunDetail(
        **_summary(row, names),
        counts=counts,
        per_project=[
            SuiteParityProjectLine(
                project_id=pid, project_name=projects.get(pid),
                blocking=int(p.get("blocking", 0)), expected=int(p.get("expected", 0)),
            )
            for p in per_project if (pid := _as_uuid(p["project_id"])) is not None
        ],
        sample=[
            SuiteParitySampleRow(
                reason=str(s.get("reason", "")), blocking=s.get("reason") in blocking,
                project_id=pid, project_name=projects.get(pid),
                suite=str(s.get("suite", "")), fingerprint=str(s.get("fingerprint", "")),
                test_name=s.get("test_name"), legacy=str(s.get("legacy", "")), canonical=str(s.get("canonical", "")),
            )
            for s in sample if (pid := _as_uuid(s["project_id"])) is not None
        ],
        error_code=row.error_code,
    )


async def _a_comparison_is_running(db: AsyncSession) -> bool:
    """A ``running`` parity row started less than ``RUN_STALE_MINUTES`` ago (an older one is a dead worker's)."""
    fresh_since = datetime.now(timezone.utc) - timedelta(minutes=parity_vocab.RUN_STALE_MINUTES)
    running = (await db.execute(
        select(SuiteMembershipParityRun.id)
        .where(SuiteMembershipParityRun.status == "running", SuiteMembershipParityRun.started_at >= fresh_since)
        .limit(1)
    )).first()
    return running is not None


@router.post(
    "/suite-membership-parity",
    status_code=202,
    dependencies=[Depends(require_instance_admin())],
)
async def trigger_suite_membership_parity(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    """"Compare now": enqueue one read-only comparison on the worker.

    409 once cut over (W25d: the old tables are no longer written, so the
    comparison is retired — checked first). 409 while a comparison started less
    than ``RUN_STALE_MINUTES`` ago is still running (read before enqueueing;
    the runner's unique claim is the real guard). 503 when the broker refuses
    the task — its exception TYPE is logged, never echoed.
    """
    if not await suite_cutover.legacy_model_active(db):
        raise HTTPException(status_code=409, detail=suite_cutover.COMPARISON_RETIRED_SENTENCE)
    if await _a_comparison_is_running(db):
        raise HTTPException(status_code=409, detail=parity_vocab.ALREADY_RUNNING_SENTENCE)
    try:
        from app.worker.tasks import run_suite_membership_parity

        result = run_suite_membership_parity.apply_async(
            kwargs={"trigger": "admin", "requested_by_id": str(current_user.id)},
        )
    except Exception as exc:  # noqa: BLE001 - only the TYPE name is logged; the sentence is fixed
        logger.warning(
            "admin_suite_parity_dispatch_failed",
            actor_user_id=str(current_user.id),
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail=parity_vocab.DISPATCH_FAILED_SENTENCE) from None
    logger.info("admin_triggered_suite_parity", actor_user_id=str(current_user.id), task_id=result.id)
    return {"queued": True, "task_id": str(result.id), "task_name": parity_vocab.TASK_NAME}


# ── W25d HYG-04: the cutover (the card's "Cut over now") ─────────────────────


@router.post(
    "/suite-membership-cutover",
    response_model=SuiteParityCutoverResult,
    dependencies=[Depends(require_instance_admin())],
)
async def cut_over_suite_membership(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> SuiteParityCutoverResult:
    """Stop writing (and reading) the legacy suite-membership tables: turn ``suite_membership_legacy_writes`` OFF.

    Refused (409, fixed sentences, never an exception text) when it is already
    off, while a fresh comparison is running, or unless the SAME readiness the
    history GET shows says ``cutover_ready`` (seven clean UTC days). Under the
    flag service's own lock the row is re-read, so two clicks make one change.
    The flag's ``settings_audit_log`` row (actor + reason, no names in the
    reason) is the record; ONE commit; then the flag cache is dropped and every
    project's analytics epoch is bumped (the coverage map's ``needs_review``
    source changes) — best effort, a Redis error is logged by TYPE and never
    fails the POST. The body is ignored. Reversible until F-W25-2: turning the
    flag back ON at /settings/feature-flags resumes legacy reads and writes.
    """
    if not await suite_cutover.legacy_model_active(db, fresh=True):
        raise HTTPException(status_code=409, detail=suite_cutover.ALREADY_CUT_OVER_SENTENCE)
    if await _a_comparison_is_running(db):
        raise HTTPException(status_code=409, detail=suite_cutover.COMPARISON_RUNNING_SENTENCE)
    _rows, streak, ready = await suite_cutover.readiness(db, now=datetime.now(timezone.utc))
    if not ready:
        raise HTTPException(
            status_code=409,
            detail=suite_cutover.NOT_READY_SENTENCE.format(streak=streak, required=parity_vocab.REQUIRED_CLEAN_DAYS),
        )
    # Re-entrant transaction lock (the flag service takes it again). The re-read selects the COLUMN, so it sees the
    # committed value even though the ORM row is already in this session's identity map from the first read.
    await lock_global_agent_authority(db)
    current = (await db.execute(
        select(FeatureFlag.enabled_global).where(FeatureFlag.key == suite_cutover.FEATURE_FLAG)
    )).first()
    if current is None or not current[0]:
        raise HTTPException(status_code=409, detail=suite_cutover.ALREADY_CUT_OVER_SENTENCE)
    flag = await feature_flags.update_flag(
        db,
        key=suite_cutover.FEATURE_FLAG,
        updates={"enabled_global": False},
        actor=current_user,
        reason=suite_cutover.CUTOVER_REASON.format(streak=streak),
    )
    cut_over_at = flag.updated_at or datetime.now(timezone.utc)
    await db.commit()
    await feature_flags.invalidate_flag_cache(suite_cutover.FEATURE_FLAG)
    try:
        from app.services.cache_service import bump_analytics_epochs

        project_ids = (await db.execute(select(Project.id))).scalars().all()
        await bump_analytics_epochs(project_ids)
    except Exception as exc:  # noqa: BLE001 - the flip has committed; the cache TTL is the backstop
        logger.warning(
            "suite_membership_cutover_epoch_bump_failed",
            actor_user_id=str(current_user.id),
            error_type=type(exc).__name__,
        )
    logger.info("suite_membership_cutover", actor_user_id=str(current_user.id), streak_days=streak)
    return SuiteParityCutoverResult(cut_over_at=cut_over_at, streak_days=streak)

