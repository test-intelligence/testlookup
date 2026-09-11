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

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_instance_admin
from app.db.postgres import get_db
from app.models.postgres import User

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
