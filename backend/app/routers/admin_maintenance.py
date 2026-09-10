"""Admin-only maintenance endpoints — manually fire Celery beat tasks.

Used immediately after a deploy when the operator doesn't want to wait
for the next scheduled tick. The endpoints are thin shims around the
existing Celery tasks: same code path, same idempotency guarantees,
just triggered on demand.

All endpoints require ``UserRole.ADMIN`` (instance-level). Project-
scoped admins are NOT sufficient — these tasks walk every project.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_active_user, require_role
from app.models.postgres import User, UserRole

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/maintenance",
    tags=["Admin Maintenance"],
)


@router.post(
    "/backfill-placeholder-test-cases",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
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
    dependencies=[Depends(require_role(UserRole.ADMIN))],
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
    dependencies=[Depends(require_role(UserRole.ADMIN))],
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
    dependencies=[Depends(require_role(UserRole.ADMIN))],
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
