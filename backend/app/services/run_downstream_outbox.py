"""Transactional outbox for work required after a run becomes durable."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    AgentPipelineRun,
    AgentStageResult,
    NotificationLog,
    ReviewRequest,
    RunDownstreamOutbox,
    TestRun,
    WebhookDelivery,
)

logger = structlog.get_logger("services.run_downstream_outbox")

MAX_DISPATCH_ATTEMPTS = 8
MAX_EXECUTION_ATTEMPTS = 3
MAX_FINALIZATION_RECOVERY_ATTEMPTS = 8
DEFAULT_RELAY_BATCH_SIZE = 200
DEFAULT_RELAY_MAX_BATCHES = 10
EXECUTION_LEASE_SECONDS = 2100
# The ordinary finalize task has a 31-minute hard limit. Recovery must not race
# a healthy slow finalizer, so the first claim starts only after that worker can
# no longer still be executing.
FINALIZATION_RECOVERY_GRACE_SECONDS = 2100
FINALIZATION_RECOVERY_LEASE_SECONDS = 2100
_OPERATIONS = frozenset(
    {
        "persist_live_session",
        "run_notifications",
        "transition_notifications",
        "agent_pipeline",
        "ai_summary_notifications",
        "suite_comparison",
        "run_completed_webhook",
    }
)


def operation_input_version(operation: str, payload: dict[str, Any]) -> str:
    """Return a stable version for one operation's business inputs."""
    encoded = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def stage_downstream_operation(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    operation: str,
    input_version: str,
    payload: dict[str, Any],
    queue: str,
    priority: int = 5,
    initial_status: str = "pending",
) -> bool:
    """Insert an intent without turning a concurrent duplicate into an error."""
    if operation not in _OPERATIONS:
        raise ValueError(f"unsupported_downstream_operation:{operation}")
    if len(input_version) != 64 or any(c not in "0123456789abcdef" for c in input_version):
        raise ValueError("input_version_must_be_sha256")
    if not queue or len(queue) > 80:
        raise ValueError("invalid_downstream_queue")
    if not 0 <= int(priority) <= 9:
        raise ValueError("invalid_downstream_priority")
    if initial_status not in {"waiting", "pending"}:
        raise ValueError("invalid_downstream_initial_status")

    initial_next_attempt_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=FINALIZATION_RECOVERY_GRACE_SECONDS)
        if initial_status == "waiting"
        else None
    )
    statement = (
        pg_insert(RunDownstreamOutbox)
        .values(
            id=uuid.uuid4(),
            run_id=run_id,
            project_id=project_id,
            operation=operation,
            input_version=input_version,
            payload=payload,
            queue=queue,
            priority=int(priority),
            status=initial_status,
            attempts=0,
            next_attempt_at=initial_next_attempt_at,
        )
        .on_conflict_do_nothing(
            constraint="uq_run_downstream_operation_version"
        )
    )
    result = await db.execute(statement)
    return bool(getattr(result, "rowcount", 0))


async def stage_ai_summary_notification_operation(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    build_number: str,
    pipeline_run_id: uuid.UUID | None = None,
    evidence_bundle_sha256: str | None = None,
    source_executive_summary: str | None = None,
    source_executive_panel: dict[str, Any] | None = None,
    source_summary_is_ai: bool | None = None,
) -> bool:
    """Stage the stable AI-summary publication on the caller's transaction."""
    payload = {
        "test_run_id": str(run_id),
        "project_id": str(project_id),
        "build_number": str(build_number),
        "pipeline_run_id": str(pipeline_run_id) if pipeline_run_id is not None else None,
        "evidence_bundle_sha256": evidence_bundle_sha256,
        "source_executive_summary": source_executive_summary,
        "source_executive_panel": source_executive_panel,
        "source_summary_is_ai": source_summary_is_ai,
    }
    return await stage_downstream_operation(
        db,
        run_id=run_id,
        project_id=project_id,
        operation="ai_summary_notifications",
        input_version=operation_input_version("ai_summary_notifications", payload),
        payload=payload,
        queue="default",
        priority=5,
    )


async def repair_terminal_ai_summary_operation(
    db: AsyncSession,
    *,
    pipeline_run_id: uuid.UUID,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    build_number: str,
) -> dict[str, Any] | None:
    """Repair a terminal-success replay from durable summary-stage evidence.

    This is stage-only. The tracked worker commits the repaired child before it
    returns, so its parent outbox operation cannot complete first.
    """
    terminal = (
        await db.execute(
            select(AgentPipelineRun.status)
            .join(TestRun, TestRun.id == AgentPipelineRun.test_run_id)
            .where(
                AgentPipelineRun.id == pipeline_run_id,
                AgentPipelineRun.test_run_id == run_id,
                TestRun.project_id == project_id,
                # E7.1: ``partial`` no longer exists; degraded runs are ``completed``.
                AgentPipelineRun.status.in_({"completed", "passed"}),
            )
        )
    ).scalar_one_or_none()
    if terminal is None:
        return None

    summary_completed = (
        await db.execute(
            select(AgentStageResult.id)
            .where(
                AgentStageResult.pipeline_run_id == pipeline_run_id,
                AgentStageResult.stage_name == "summary",
                AgentStageResult.status == "completed",
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    inserted = False
    if summary_completed:
        evidence_bundle_sha256 = (
            await db.execute(
                select(ReviewRequest.evidence_bundle_sha256)
                .where(
                    ReviewRequest.pipeline_run_id == pipeline_run_id,
                    ReviewRequest.kind == "report",
                )
                .order_by(ReviewRequest.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        inserted = await stage_ai_summary_notification_operation(
            db,
            run_id=run_id,
            project_id=project_id,
            build_number=build_number,
            pipeline_run_id=pipeline_run_id,
            evidence_bundle_sha256=evidence_bundle_sha256,
        )
    return {
        "pipeline_status": str(terminal),
        "summary_completed": summary_completed,
        "child_inserted": inserted,
    }


async def stage_finalize_operations(
    db: AsyncSession,
    *,
    run: Any,
    project: Any,
    run_ai: bool,
    ready: bool = False,
) -> int:
    """Stage all required post-finalization publications in the caller's transaction."""
    common = {
        "run_id": str(run.id),
        "project_id": str(run.project_id),
        "build_number": str(run.build_number),
    }
    run_status = getattr(run, "status", "UNKNOWN")
    specs: list[tuple[str, dict[str, Any], str, int]] = [
        (
            "run_notifications",
            {
                **common,
                "pass_rate": float(run.pass_rate or 0),
                "total_tests": int(run.total_tests or 0),
                "failed_tests": int(run.failed_tests or 0),
                "project_name": str(project.name),
            },
            "default",
            5,
        ),
        ("transition_notifications", {"run_id": str(run.id)}, "default", 5),
        (
            "run_completed_webhook",
            {
                "project_id": str(run.project_id),
                "payload": {
                    "run_id": str(run.id),
                    "project_id": str(run.project_id),
                    "build_number": str(run.build_number),
                    "branch": getattr(run, "branch", None),
                    "commit_hash": getattr(run, "commit_hash", None),
                    "total_tests": int(run.total_tests or 0),
                    "passed_tests": int(getattr(run, "passed_tests", 0) or 0),
                    "failed_tests": int(run.failed_tests or 0),
                    "broken_tests": int(getattr(run, "broken_tests", 0) or 0),
                    "skipped_tests": int(getattr(run, "skipped_tests", 0) or 0),
                    "pass_rate": float(run.pass_rate or 0.0),
                    "status": str(getattr(run_status, "value", run_status)),
                    "start_time": (
                        run.start_time.isoformat()
                        if getattr(run, "start_time", None)
                        else None
                    ),
                    "end_time": (
                        run.end_time.isoformat()
                        if getattr(run, "end_time", None)
                        else None
                    ),
                },
            },
            "default",
            5,
        ),
    ]
    if run_ai:
        specs.insert(
            2,
            (
                "suite_comparison",
                {"test_run_id": str(run.id), "project_id": str(run.project_id)},
                "ai_analysis",
                4,
            ),
        )
        specs.append(
            (
                "agent_pipeline",
                # run_agent_pipeline's parameter is test_run_id. Staged as
                # ``{**common}`` it carried run_id instead, and Celery refuses a
                # call with arguments its task does not take before publishing:
                # every attempt failed with TypeError and was retried forever,
                # so no finished run's analysis ever started (found by the
                # re-audit's homelab check of N14).
                {
                    "test_run_id": str(run.id),
                    "project_id": str(run.project_id),
                    "build_number": str(run.build_number),
                    "workflow_type": "offline",
                },
                "ai_analysis",
                6,
            )
        )

    inserted = 0
    for operation, payload, queue, priority in specs:
        input_version = (
            hashlib.sha256(b"run_completed_webhook:v1").hexdigest()
            if operation == "run_completed_webhook"
            else operation_input_version(operation, payload)
        )
        inserted += int(
            await stage_downstream_operation(
                db,
                run_id=run.id,
                project_id=run.project_id,
                operation=operation,
                input_version=input_version,
                payload=payload,
                queue=queue,
                priority=priority,
                initial_status="pending" if ready else "waiting",
            )
        )
    return inserted


async def activate_finalize_operations(
    db: AsyncSession, *, run_id: uuid.UUID
) -> int:
    """Make a run's children dispatchable after finalization has finished."""
    result = await db.execute(
        update(RunDownstreamOutbox)
        .where(
            RunDownstreamOutbox.run_id == run_id,
            RunDownstreamOutbox.status == "waiting",
        )
        .values(
            status="pending",
            attempts=0,
            next_attempt_at=None,
            lease_expires_at=None,
            dispatch_token=None,
            last_error=None,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def claim_waiting_finalizations(
    db: AsyncSession,
    *,
    limit: int = 5,
    lease_seconds: int = FINALIZATION_RECOVERY_LEASE_SECONDS,
) -> list[dict[str, Any]]:
    """Lease stale finalization gates so a lost worker cannot strand them.

    The run row is the per-finalization mutex. All of that run's waiting child
    rows receive one token and deadline in the same transaction. The token
    fences a failed stale recovery from rescheduling rows that a newer worker
    already activated or reclaimed.
    """
    now = datetime.now(timezone.utc)
    bounded_limit = max(1, min(int(limit), 25))
    due_waiting = (
        select(RunDownstreamOutbox.id)
        .where(
            RunDownstreamOutbox.run_id == TestRun.id,
            RunDownstreamOutbox.status == "waiting",
            or_(
                RunDownstreamOutbox.next_attempt_at.is_(None),
                RunDownstreamOutbox.next_attempt_at <= now,
            ),
        )
        .exists()
    )
    # Rank projects before applying the global recovery limit. Otherwise a
    # tenant with a large, older set of interrupted finalizations can occupy
    # every minute's recovery batch and delay a peer by the full backlog.
    ranked_due_runs = (
        select(
            TestRun.id.label("id"),
            func.row_number()
            .over(
                partition_by=TestRun.project_id,
                order_by=(TestRun.created_at, TestRun.id),
            )
            .label("tenant_rank"),
        )
        .where(due_waiting)
        .cte("ranked_waiting_finalizations")
    )
    result = await db.execute(
        select(TestRun)
        .join(ranked_due_runs, ranked_due_runs.c.id == TestRun.id)
        .order_by(
            ranked_due_runs.c.tenant_rank,
            TestRun.created_at,
            TestRun.id,
        )
        .with_for_update(skip_locked=True, of=TestRun)
        .limit(bounded_limit)
    )

    claims: list[dict[str, Any]] = []
    for run in result.scalars().all():
        children_result = await db.execute(
            select(RunDownstreamOutbox.operation, RunDownstreamOutbox.attempts)
            .where(
                RunDownstreamOutbox.run_id == run.id,
                RunDownstreamOutbox.status == "waiting",
                or_(
                    RunDownstreamOutbox.next_attempt_at.is_(None),
                    RunDownstreamOutbox.next_attempt_at <= now,
                ),
            )
        )
        children = list(children_result.all())
        if not children:
            continue
        attempts = max(int(row.attempts or 0) for row in children)
        if attempts >= MAX_FINALIZATION_RECOVERY_ATTEMPTS:
            await db.execute(
                update(RunDownstreamOutbox)
                .where(
                    RunDownstreamOutbox.run_id == run.id,
                    RunDownstreamOutbox.status == "waiting",
                )
                .values(
                    status="failed",
                    next_attempt_at=None,
                    dispatch_token=None,
                    last_error="finalization_recovery_attempts_exhausted",
                )
            )
            continue

        token = uuid.uuid4()
        lease_until = now + timedelta(
            seconds=max(60, min(int(lease_seconds), 3600))
        )
        await db.execute(
            update(RunDownstreamOutbox)
            .where(
                RunDownstreamOutbox.run_id == run.id,
                RunDownstreamOutbox.status == "waiting",
            )
            .values(
                attempts=attempts + 1,
                dispatch_token=token,
                next_attempt_at=lease_until,
                last_error=None,
            )
        )
        operations = {str(row.operation) for row in children}
        claims.append(
            {
                "run_id": run.id,
                "project_id": run.project_id,
                "build_number": str(run.build_number),
                "run_ai": bool(operations & {"agent_pipeline", "suite_comparison"}),
                "recovery_token": token,
                "attempt": attempts + 1,
            }
        )
    return claims


async def mark_waiting_finalization_recovery_failed(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    recovery_token: uuid.UUID,
    attempt: int,
    error_code: str,
) -> bool:
    """Release one recovery lease without overwriting a newer owner."""
    result = await db.execute(
        update(RunDownstreamOutbox)
        .where(
            RunDownstreamOutbox.run_id == run_id,
            RunDownstreamOutbox.status == "waiting",
            RunDownstreamOutbox.dispatch_token == recovery_token,
        )
        .values(
            next_attempt_at=datetime.now(timezone.utc)
            + timedelta(seconds=min(900, 2 ** min(max(int(attempt), 1), 9))),
            dispatch_token=None,
            last_error=str(error_code)[:200],
        )
    )
    return bool(getattr(result, "rowcount", 0))


async def recover_waiting_finalizations(*, limit: int = 5) -> dict[str, int]:
    """Resume finalization gates left behind by a lost ingestion worker."""
    async with AsyncSessionLocal() as db:
        async with db.begin():
            claims = await claim_waiting_finalizations(db, limit=limit)

    completed = retrying = 0
    from app.services.ingestion_pipeline import finalize_run

    for claim in claims:
        try:
            await finalize_run(
                run_id=str(claim["run_id"]),
                project_id=str(claim["project_id"]),
                build_number=str(claim["build_number"]),
                run_ai=bool(claim["run_ai"]),
            )
            completed += 1
        except Exception as exc:  # noqa: BLE001
            retrying += 1
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    await mark_waiting_finalization_recovery_failed(
                        db,
                        run_id=claim["run_id"],
                        recovery_token=claim["recovery_token"],
                        attempt=int(claim["attempt"]),
                        error_code=f"finalization_{type(exc).__name__}",
                    )
            logger.warning(
                "run_finalization_recovery_failed",
                run_id=str(claim["run_id"]),
                attempt=int(claim["attempt"]),
                error_type=type(exc).__name__,
            )
    return {"claimed": len(claims), "completed": completed, "retrying": retrying}


async def stage_live_persist_operation(
    db: AsyncSession,
    *,
    canonical_run_id: uuid.UUID,
    session: Any,
    final_state: dict[str, Any],
) -> bool:
    """Stage live persistence alongside the session-close transaction."""
    from app.worker.ingestion_routing import queue_for_project

    payload = {
        # The worker drains Redis by the internal run identity.  Keep the
        # client-supplied display slug on LiveSession; it is not a durable key
        # and may not equal the canonical TestRun UUID.
        "run_id": str(canonical_run_id),
        "project_id": str(session.project_id),
        "build_number": str(session.build_number or session.id),
        "client_name": str(session.client_name or ""),
        "framework": str(session.framework or ""),
        "branch": str(session.branch or ""),
        "commit_hash": str(session.commit_hash or ""),
        "final_state": final_state,
        "suite_name": session.suite_name or None,
        "completed_at": (
            session.completed_at.isoformat()
            if getattr(session, "completed_at", None) is not None
            else None
        ),
    }
    operation = "persist_live_session"
    # The row's unique key already contains canonical_run_id.  Keep volatile
    # close snapshots in the payload, but use a fixed schema generation for
    # idempotency so repeated/concurrent closes converge on one intent.
    input_version = hashlib.sha256(b"persist_live_session:v1").hexdigest()
    return await stage_downstream_operation(
        db,
        run_id=canonical_run_id,
        project_id=session.project_id,
        operation=operation,
        input_version=input_version,
        payload=payload,
        queue=queue_for_project(str(session.project_id)),
        priority=7,
    )


async def claim_downstream_dispatches(
    db: AsyncSession, *, limit: int = DEFAULT_RELAY_BATCH_SIZE, lease_seconds: int = 120
) -> list[RunDownstreamOutbox]:
    """Lease due intents fairly, recovering dead publishers and workers."""
    now = datetime.now(timezone.utc)
    bounded_limit = max(1, min(int(limit), 500))
    due_filter = or_(
        and_(
            RunDownstreamOutbox.status == "pending",
            or_(
                RunDownstreamOutbox.next_attempt_at.is_(None),
                RunDownstreamOutbox.next_attempt_at <= now,
            ),
        ),
        and_(
            RunDownstreamOutbox.status == "sending",
            RunDownstreamOutbox.lease_expires_at <= now,
        ),
        and_(
            RunDownstreamOutbox.status.in_({"published", "processing"}),
            RunDownstreamOutbox.lease_expires_at <= now,
        ),
    )
    # Rank before applying the global batch limit. Limiting the globally
    # oldest rows first lets one tenant with a sufficiently large old backlog
    # exclude every peer from the candidate set forever.
    ranked_due = (
        select(
            RunDownstreamOutbox.id.label("id"),
            func.row_number()
            .over(
                partition_by=RunDownstreamOutbox.project_id,
                order_by=(
                    RunDownstreamOutbox.created_at,
                    RunDownstreamOutbox.id,
                ),
            )
            .label("tenant_rank"),
        )
        .where(due_filter)
        .cte("ranked_run_downstream_due")
    )
    result = await db.execute(
        select(RunDownstreamOutbox)
        .join(ranked_due, ranked_due.c.id == RunDownstreamOutbox.id)
        .order_by(
            ranked_due.c.tenant_rank,
            RunDownstreamOutbox.created_at,
            RunDownstreamOutbox.id,
        )
        .with_for_update(skip_locked=True, of=RunDownstreamOutbox)
        .limit(bounded_limit)
    )
    claimed: list[RunDownstreamOutbox] = []
    for row in result.scalars().all():
        # Re-check after locking because another relay may have changed the row
        # between CTE evaluation and lock acquisition.
        due = row.next_attempt_at is None or row.next_attempt_at <= now
        expired = row.lease_expires_at is not None and row.lease_expires_at <= now
        if not ((row.status == "pending" and due) or (row.status in {"sending", "published", "processing"} and expired)):
            continue
        if (
            row.status == "pending"
            and int(row.dispatch_failures or 0) >= MAX_DISPATCH_ATTEMPTS
        ):
            row.status = "failed"
            row.lease_expires_at = None
            row.next_attempt_at = None
            row.last_error = row.last_error or "dispatch_attempts_exhausted"
            continue
        previous_status = row.status
        row.status = "sending"
        row.attempts = int(row.attempts or 0) + 1
        # A sending/published lease can expire while the accepted Celery
        # message is merely waiting in its queue. Reuse that publication's
        # token so either the original or the republished copy can claim the
        # operation; begin_downstream_execution fences every later copy once
        # one reaches processing. A dead processing worker gets a fresh token.
        if previous_status == "processing" or row.dispatch_token is None:
            row.dispatch_token = uuid.uuid4()
        row.processing_task_id = None
        row.lease_expires_at = now + timedelta(
            seconds=max(10, min(int(lease_seconds), 900))
        )
        row.next_attempt_at = row.lease_expires_at
        row.last_error = None
        claimed.append(row)
    return claimed
def _fair_candidate_ids(rows: list[Any], *, limit: int) -> list[uuid.UUID]:
    """Round-robin due work so an old high-volume tenant cannot starve peers."""
    by_project: dict[uuid.UUID, deque[uuid.UUID]] = {}
    for row in rows:
        by_project.setdefault(row.project_id, deque()).append(row.id)
    selected: list[uuid.UUID] = []
    active = deque(by_project)
    while active and len(selected) < limit:
        project_id = active.popleft()
        bucket = by_project[project_id]
        selected.append(bucket.popleft())
        if bucket:
            active.append(project_id)
    return selected


async def mark_downstream_dispatch_published(
    db: AsyncSession, *, outbox_id: uuid.UUID, dispatch_token: uuid.UUID
) -> bool:
    result = await db.execute(
        select(RunDownstreamOutbox)
        .where(RunDownstreamOutbox.id == outbox_id)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None or row.dispatch_token != dispatch_token:
        return False
    if row.status in {"processing", "completed"}:
        return True
    if row.status != "sending":
        return False
    now = datetime.now(timezone.utc)
    row.status = "published"
    row.published_at = now
    row.lease_expires_at = now + timedelta(seconds=300)
    row.next_attempt_at = row.lease_expires_at
    return True


async def mark_downstream_dispatch_failed(
    db: AsyncSession,
    *,
    outbox_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    error_code: str,
) -> bool:
    result = await db.execute(
        select(RunDownstreamOutbox)
        .where(RunDownstreamOutbox.id == outbox_id)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if (
        row is None
        or row.status != "sending"
        or row.dispatch_token != dispatch_token
    ):
        return False
    row.dispatch_failures = int(row.dispatch_failures or 0) + 1
    permanent = row.dispatch_failures >= MAX_DISPATCH_ATTEMPTS
    row.status = "failed" if permanent else "pending"
    row.last_error = str(error_code)[:200]
    row.lease_expires_at = None
    row.next_attempt_at = None if permanent else datetime.now(timezone.utc) + timedelta(
        seconds=min(300, 2 ** min(int(row.attempts or 0), 8))
    )
    return True


async def begin_downstream_execution(
    *, outbox_id: uuid.UUID, dispatch_token: uuid.UUID, task_id: str
) -> bool:
    """Fence duplicate deliveries before a consumer performs business work."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RunDownstreamOutbox)
            .where(RunDownstreamOutbox.id == outbox_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or row.dispatch_token != dispatch_token:
            await db.rollback()
            return False
        if row.status == "completed":
            await db.rollback()
            return False
        if row.status not in {"sending", "published"}:
            await db.rollback()
            return False
        attempts = int(row.execution_attempts or 0) + 1
        if attempts > MAX_EXECUTION_ATTEMPTS:
            row.status = "failed"
            row.last_error = "execution_attempts_exhausted"
            row.lease_expires_at = None
            row.next_attempt_at = None
        else:
            now = datetime.now(timezone.utc)
            row.status = "processing"
            row.execution_attempts = attempts
            row.processing_task_id = str(task_id)[:80]
            row.processing_started_at = now
            row.lease_expires_at = now + timedelta(seconds=EXECUTION_LEASE_SECONDS)
            row.next_attempt_at = row.lease_expires_at
        await db.commit()
        return attempts <= MAX_EXECUTION_ATTEMPTS


async def complete_downstream_execution(
    *,
    outbox_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    detail: str = "completed",
) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RunDownstreamOutbox)
            .where(RunDownstreamOutbox.id == outbox_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or row.dispatch_token != dispatch_token:
            await db.rollback()
            return False
        if row.status == "completed":
            await db.rollback()
            return True
        if row.status != "processing":
            await db.rollback()
            return False
        row.status = "completed"
        row.completed_at = datetime.now(timezone.utc)
        row.completion_detail = str(detail)[:200]
        row.lease_expires_at = None
        row.next_attempt_at = None
        row.last_error = None
        await db.commit()
        return True


async def fail_downstream_execution(
    *, outbox_id: uuid.UUID, dispatch_token: uuid.UUID, error_code: str
) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RunDownstreamOutbox)
            .where(RunDownstreamOutbox.id == outbox_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if (
            row is None
            or row.status != "processing"
            or row.dispatch_token != dispatch_token
        ):
            await db.rollback()
            return False
        permanent = int(row.execution_attempts or 0) >= MAX_EXECUTION_ATTEMPTS
        row.status = "failed" if permanent else "pending"
        row.last_error = str(error_code)[:200]
        row.lease_expires_at = None
        row.next_attempt_at = None if permanent else datetime.now(timezone.utc) + timedelta(
            seconds=min(300, 2 ** min(int(row.execution_attempts or 0), 8))
        )
        await db.commit()
        return True


async def defer_downstream_execution(
    *,
    outbox_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    reason: str,
    delay_seconds: int = 5,
) -> bool:
    """Return an ordering-blocked operation to the relay without spending a try."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RunDownstreamOutbox)
            .where(RunDownstreamOutbox.id == outbox_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if (
            row is None
            or row.status != "processing"
            or row.dispatch_token != dispatch_token
        ):
            await db.rollback()
            return False
        row.status = "pending"
        row.execution_attempts = max(0, int(row.execution_attempts or 0) - 1)
        row.last_error = str(reason)[:200]
        row.lease_expires_at = None
        row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(1, min(int(delay_seconds), 300))
        )
        await db.commit()
        return True


#: How many failed intents one requeue may put back. The relay publishes at its
#: own pace, but a backlog should go back in slices an operator can watch, not
#: as one burst onto the workers.
REQUEUE_MAX_ROWS = 500


async def requeue_failed_downstream_operations(
    db: AsyncSession,
    *,
    operation: str,
    last_error: str | None = None,
    run_id: uuid.UUID | None = None,
    limit: int = 100,
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    """Put permanently failed intents back in the queue, oldest first. Never commits.

    An intent is marked ``failed`` after MAX_DISPATCH_ATTEMPTS publish failures
    or MAX_EXECUTION_ATTEMPTS executions, and nothing retries it after that.
    That is right while the cause is unknown, and wrong once it is fixed: until
    re-audit N23 every finished run's ``agent_pipeline`` intent failed with
    ``broker_TypeError``, and no upgrade would ever have published one of them.
    This is the operator's way back. ``dry_run`` lists what would be requeued
    and changes nothing.

    Returns each matched row as it was before the requeue.
    """
    if operation not in _OPERATIONS:
        raise ValueError(f"unknown downstream operation: {operation!r}")
    query = (
        select(RunDownstreamOutbox)
        .where(
            RunDownstreamOutbox.status == "failed",
            RunDownstreamOutbox.operation == operation,
        )
        .order_by(RunDownstreamOutbox.created_at, RunDownstreamOutbox.id)
        .limit(max(1, min(int(limit), REQUEUE_MAX_ROWS)))
    )
    if last_error is not None:
        query = query.where(RunDownstreamOutbox.last_error == last_error)
    if run_id is not None:
        query = query.where(RunDownstreamOutbox.run_id == run_id)
    if not dry_run:
        # Rows another admin is requeueing at this moment are skipped rather
        # than waited on: the second caller gets fewer rows, never the same one.
        query = query.with_for_update(skip_locked=True)
    rows = list((await db.execute(query)).scalars().all())
    before = [
        {
            "id": str(row.id),
            "run_id": str(row.run_id),
            "project_id": str(row.project_id),
            "last_error": row.last_error,
            "dispatch_failures": int(row.dispatch_failures or 0),
            "execution_attempts": int(row.execution_attempts or 0),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    if not dry_run:
        for row in rows:
            row.status = "pending"
            row.attempts = 0
            row.dispatch_failures = 0
            row.execution_attempts = 0
            row.next_attempt_at = None
            row.lease_expires_at = None
            row.processing_task_id = None
            # The claim mints a fresh token, which fences any copy of the
            # failed publication still sitting in a queue.
            row.dispatch_token = None
            row.last_error = None
    return before


def downstream_tasks() -> dict[str, Any]:
    """The Celery task each whitelisted operation is published to."""
    from app.worker import tasks

    return {
        "persist_live_session": tasks.persist_live_session,
        "run_notifications": tasks.dispatch_run_notifications,
        "transition_notifications": tasks.dispatch_transition_notifications,
        "agent_pipeline": tasks.run_agent_pipeline,
        "ai_summary_notifications": tasks.dispatch_ai_summary_email,
        "suite_comparison": tasks.precompute_suite_comparisons_for_run,
        "run_completed_webhook": tasks.dispatch_run_completed_webhook,
    }


def _publish_downstream(row: RunDownstreamOutbox) -> None:
    """Publish a whitelisted task with a stable Celery delivery identity."""
    task = downstream_tasks().get(row.operation)
    if task is None:
        raise ValueError(f"unsupported_downstream_operation:{row.operation}")
    kwargs = dict(row.payload)
    if row.operation == "agent_pipeline" and "run_id" in kwargs and "test_run_id" not in kwargs:
        # An intent staged before the payload used the task's own parameter
        # name. Translate it, so a run stuck on broker_TypeError gets its
        # analysis once this ships instead of retrying forever.
        kwargs["test_run_id"] = kwargs.pop("run_id")
    task.apply_async(
        kwargs=kwargs,
        queue=row.queue,
        priority=int(row.priority),
        task_id=f"run-downstream-{row.id}",
        headers={
            "downstream_outbox_id": str(row.id),
            "downstream_dispatch_token": str(row.dispatch_token),
        },
    )


async def relay_downstream_outbox(
    *,
    limit: int = DEFAULT_RELAY_BATCH_SIZE,
    max_batches: int = DEFAULT_RELAY_MAX_BATCHES,
) -> dict[str, int]:
    """Publish due intents; broker failures remain retryable in PostgreSQL."""
    claimed = published = failed = 0
    for _batch in range(max(1, min(int(max_batches), 20))):
        async with AsyncSessionLocal() as db:
            rows = await claim_downstream_dispatches(db, limit=limit)
            await db.commit()
        if not rows:
            break
        claimed += len(rows)
        for row in rows:
            token = row.dispatch_token
            if token is None:
                failed += 1
                continue
            try:
                _publish_downstream(row)
                async with AsyncSessionLocal() as db:
                    if await mark_downstream_dispatch_published(
                        db, outbox_id=row.id, dispatch_token=token
                    ):
                        await db.commit()
                        published += 1
                    else:
                        await db.rollback()
                        failed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                async with AsyncSessionLocal() as db:
                    changed = await mark_downstream_dispatch_failed(
                        db,
                        outbox_id=row.id,
                        dispatch_token=token,
                        error_code=f"broker_{type(exc).__name__}",
                    )
                    if changed:
                        await db.commit()
                logger.warning(
                    "run_downstream_publish_failed",
                    outbox_id=str(row.id),
                    operation=row.operation,
                    attempt=int(row.attempts or 0),
                    permanent=(
                        int(getattr(row, "dispatch_failures", 0) or 0) + 1
                        >= MAX_DISPATCH_ATTEMPTS
                    ),
                    error_type=type(exc).__name__,
                )
        if len(rows) < max(1, min(int(limit), 500)):
            break
    return {"claimed": claimed, "published": published, "failed": failed}


async def downstream_status_for_run(
    db: AsyncSession, *, run_id: uuid.UUID
) -> dict[str, Any]:
    result = await db.execute(
        select(RunDownstreamOutbox)
        .where(RunDownstreamOutbox.run_id == run_id)
        .order_by(RunDownstreamOutbox.created_at, RunDownstreamOutbox.operation)
    )
    rows = list(result.scalars().all())
    notification_result = await db.execute(
        select(NotificationLog.status, NotificationLog.delivery_attempts).where(
            NotificationLog.run_id == run_id,
            NotificationLog.delivery_key.is_not(None),
        )
    )
    webhook_result = await db.execute(
        select(
            WebhookDelivery.status,
            WebhookDelivery.attempt_count,
            WebhookDelivery.dispatch_failures,
        ).where(
            WebhookDelivery.event_type == "run.completed",
            WebhookDelivery.delivery_key.is_not(None),
            WebhookDelivery.run_id == run_id,
        )
    )

    notification_counts = {
        "pending": 0,
        "retrying": 0,
        "failed": 0,
        "completed": 0,
    }
    for raw_status, attempts in notification_result.all():
        status = str(raw_status).lower()
        if status == "failed":
            notification_counts["failed"] += 1
        elif status in {"pending", "sending"}:
            key = (
                "retrying"
                if status == "pending" and int(attempts or 0) > 0
                else "pending"
            )
            notification_counts[key] += 1
        else:
            notification_counts["completed"] += 1

    webhook_counts = {
        "pending": 0,
        "retrying": 0,
        "failed": 0,
        "completed": 0,
    }
    for raw_status, attempts, dispatch_failures in webhook_result.all():
        status = str(raw_status).upper()
        if status in {"FAILED", "DLQ"}:
            webhook_counts["failed"] += 1
        elif status in {"PENDING", "SENDING", "PROCESSING"}:
            key = (
                "retrying"
                if status == "PENDING"
                and (int(attempts or 0) > 0 or int(dispatch_failures or 0) > 0)
                else "pending"
            )
            webhook_counts[key] += 1
        else:
            webhook_counts["completed"] += 1

    statuses = {row.status for row in rows}
    child_failed = notification_counts["failed"] + webhook_counts["failed"]
    child_active = sum(
        counts[status]
        for counts in (notification_counts, webhook_counts)
        for status in ("pending", "retrying")
    )
    if "failed" in statuses or child_failed:
        overall = "failed"
    elif "waiting" in statuses:
        overall = "finalizing"
    elif statuses & {"pending", "sending", "published", "processing"} or child_active:
        overall = "pending"
    elif rows:
        overall = "completed"
    else:
        overall = "not_scheduled"
    return {
        "run_id": str(run_id),
        "status": overall,
        "child_deliveries": {
            "notifications": notification_counts,
            "webhooks": webhook_counts,
        },
        "operations": [
            {
                "operation": row.operation,
                "status": row.status,
                "attempts": int(row.attempts or 0),
                "dispatch_failures": int(row.dispatch_failures or 0),
                "execution_attempts": int(row.execution_attempts or 0),
                "last_error": row.last_error,
            }
            for row in rows
        ],
    }
