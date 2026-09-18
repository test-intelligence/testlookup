"""Operational records of deletions actually running (S2b).

``settings_audit_log`` already carries the compliance evidence for a purge, and
it is never purged. But it is written *after* the purge commits, so it can only
describe work that finished. Nothing could see that a destructive job was
running, and nothing could record that one failed.

Outcome writes open their own session. A ``failed`` or ``partial`` status
written on the caller's session is erased by the rollback that produced the
failure — the only outcomes a same-session writer can record are the
successful ones, which is precisely the wrong half. The purge-audit row is
written after its commit for the same reason.

The preview -> queued -> running hand-off is deliberately different. Those
transitions run on the request/worker transaction while holding the job row
``FOR UPDATE``. That makes a double submit wait for the first claim and then
observe ``queued`` instead of dispatching the same destructive job twice.

That independence is also why these functions swallow their own errors: a
bookkeeping failure must not turn a completed purge into a reported failure, or
mask a real one. When the record cannot be written the job simply has no row,
and the audit log still has the truth.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import structlog
from sqlalchemy import select, update

from app.models.postgres import DeletionJob

logger = structlog.get_logger(__name__)

#: States something can actually reach. ``cancelled`` is still deliberately
#: absent: nothing can cancel a job, and a state nothing writes is a filter
#: option that returns nothing forever.
#:
#: ``previewed`` was withheld in S2b for exactly that reason and is added here
#: because S5's preview endpoint now writes it — the freeze that lets execute
#: replay a candidate set instead of re-resolving it.
QUEUED = "queued"
PREVIEWED = "previewed"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
PARTIAL = "partial"

REACHABLE_STATUSES: frozenset[str] = frozenset(
    {QUEUED, PREVIEWED, RUNNING, COMPLETED, FAILED, PARTIAL}
)

#: Jobs that have stopped. Retention purges only these: a row still marked
#: ``running`` past the audit window is a crashed sweep, and deleting it on a
#: clock would erase the only evidence that it hung.
#: ``previewed`` IS terminal for retention: a preview nobody executed is
#: abandoned, not in flight, and holding those forever would make the table
#: grow with every dry run an operator ever cancelled.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {COMPLETED, FAILED, PARTIAL, PREVIEWED}
)

#: Job kinds. Only ``scheduled`` has a producer in this slice; the rest arrive
#: with S2c and S5. Listed so the vocabulary lives in one place rather than
#: being re-invented per caller.
KIND_SCHEDULED = "scheduled"
KIND_SINGLE_ENTITY = "single_entity"
KIND_CRITERIA = "criteria"


def candidate_hash(run_ids: Iterable[Any]) -> str:
    """Stable fingerprint of a candidate set.

    Order-independent, so two resolutions of the same runs agree regardless of
    query plan. Criteria deletion (S5) compares this between preview and
    execute to refuse a job whose world moved underneath it.
    """
    joined = ",".join(sorted(str(r) for r in run_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


async def open_job(
    *,
    project_id: uuid.UUID,
    job_kind: str,
    criteria: Optional[dict] = None,
    requested_by_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """Record that a deletion has started. Returns the job id, or None.

    None means the bookkeeping failed, not that the deletion did — the caller
    proceeds and simply has no job row. Never raises into the caller's path.
    """
    from app.db.postgres import AsyncSessionLocal

    job_id = uuid.uuid4()
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                DeletionJob(
                    id=job_id,
                    project_id=project_id,
                    job_kind=job_kind,
                    status=RUNNING,
                    criteria=criteria,
                    requested_by_id=requested_by_id,
                    started_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
        return job_id
    except Exception as exc:  # noqa: BLE001 — bookkeeping must not break the job
        logger.warning(
            "deletion_job_open_failed",
            project_id=str(project_id),
            job_kind=job_kind,
            error=str(exc),
        )
        return None


async def close_job(
    job_id: Optional[uuid.UUID],
    *,
    status: str,
    counts: Optional[dict] = None,
    resolved_run_ids: Optional[list[str]] = None,
    bytes_reclaimed: Optional[int] = None,
    error: Optional[str] = None,
    expected_status: Optional[str] = None,
) -> bool:
    """Finish a job record, on its own session.

    ``job_id`` may be None when :func:`open_job` could not write — the call is
    then a no-op rather than an error, so a caller never has to branch.

    ``bytes_reclaimed`` stays NULL when not supplied. It is not defaulted to 0:
    "reclaimed nothing" and "nobody measured" are opposite findings, and this
    column is read by the surface that tells an operator whether a purge was
    worth running.
    """
    if job_id is None:
        return False
    if status not in REACHABLE_STATUSES:
        # A typo'd status is a row that no filter will ever match — the same
        # silent-forever failure as a mismatched enum vocabulary.
        logger.warning(
            "deletion_job_unknown_status", job_id=str(job_id), status=status
        )
        return False

    from app.db.postgres import AsyncSessionLocal

    values: dict[str, Any] = {
        "status": status,
        "finished_at": datetime.now(timezone.utc),
    }
    if counts is not None:
        values["counts"] = counts
    if resolved_run_ids is not None:
        values["resolved_run_ids"] = resolved_run_ids
        values["candidate_hash"] = candidate_hash(resolved_run_ids)
    if bytes_reclaimed is not None:
        values["bytes_reclaimed"] = bytes_reclaimed
    if error is not None:
        # Bounded: a stack trace from a cross-store failure can be enormous,
        # and this column is rendered in a list view.
        values["error"] = error[:2000]

    try:
        async with AsyncSessionLocal() as db:
            statement = update(DeletionJob).where(DeletionJob.id == job_id)
            if expected_status is not None:
                statement = statement.where(DeletionJob.status == expected_status)
            result = await db.execute(statement.values(**values))
            await db.commit()
            updated = result.rowcount == 1
            if expected_status is not None and not updated:
                logger.warning(
                    "deletion_job_stale_close_refused",
                    job_id=str(job_id),
                    expected_status=expected_status,
                    requested_status=status,
                )
            return updated
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "deletion_job_close_failed", job_id=str(job_id), error=str(exc)
        )
        return False


async def get_job(
    db, job_id: uuid.UUID, *, for_update: bool = False
) -> Optional[DeletionJob]:
    """Read one job, optionally locking it for a caller-owned transition."""
    statement = select(DeletionJob).where(DeletionJob.id == job_id)
    if for_update:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def list_jobs(db, project_id: uuid.UUID, *, limit: int = 50) -> list[DeletionJob]:
    """Most recent jobs for a project, newest first."""
    result = await db.execute(
        select(DeletionJob)
        .where(DeletionJob.project_id == project_id)
        .order_by(DeletionJob.requested_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def stage_frozen_candidate_set(
    db,
    *,
    project_id: uuid.UUID,
    run_ids: list[uuid.UUID],
    criteria: dict,
    requested_by_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Stage a ``previewed`` job holding the materialized ids and their hash.

    This is the freeze half of N4. Execute replays this set rather than
    re-running the resolver, because criteria read columns other code rewrites
    while the job sits queued — ``TestRun.status`` by ``_update_run_aggregates``
    and by live-session close, ``primary_suite_name`` at session close. Without
    the freeze, the set executed is not the set an ADMIN reviewed.

    **Stage-only, unlike the status writers above, and the difference is the
    point.** ``open_job`` and ``close_job`` take their own sessions because
    they record whether a deletion FAILED, and a status written on the failing
    transaction dies with it. This records no outcome: it is an ordinary write
    on the success path of a request the router already owns a transaction
    for. Giving it a private session would mean a preview that errored after
    this point still left a job an operator could execute.
    """
    job_id = uuid.uuid4()
    resolved = [str(r) for r in run_ids]
    db.add(
        DeletionJob(
            id=job_id,
            project_id=project_id,
            job_kind=KIND_CRITERIA,
            status=PREVIEWED,
            criteria=criteria,
            resolved_run_ids=resolved,
            candidate_hash=candidate_hash(resolved),
            requested_by_id=requested_by_id,
        )
    )
    return job_id


class FrozenSetRejected(Exception):
    """The frozen set cannot be executed. Carries the HTTP status to use."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def claim_frozen_set(
    db,
    *,
    job_id: uuid.UUID,
    project_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Atomically claim a previewed job for queueing and return its run ids.

    Refuses, with the status the caller should return:

    * **404** — no such job, or it belongs to another project. ``job_id`` is
      not a guarded path param, so this ownership check is the whole defence;
      the two cases are indistinguishable so the endpoint is not an existence
      oracle.
    * **409** — the job is not ``previewed``. A job already executing or
      finished must not run twice: this endpoint is the only thing standing
      between a double-submitted form and a second deletion.
    * **409** — the stored hash does not match the stored ids. That means the
      row was edited underneath the preview, and the set is no longer the one
      that was authorised.
    """
    job = await get_job(db, job_id, for_update=True)
    if job is None or job.project_id != project_id:
        raise FrozenSetRejected(404, "Deletion job not found")

    if job.status != PREVIEWED:
        raise FrozenSetRejected(
            409,
            f"job is {job.status}, not {PREVIEWED} — a previewed set can be "
            "executed once, and this one already was",
        )

    resolved = list(job.resolved_run_ids or [])
    if not resolved:
        raise FrozenSetRejected(
            409, "the previewed set is empty; nothing to execute"
        )

    if job.candidate_hash != candidate_hash(resolved):
        raise FrozenSetRejected(
            409,
            "the frozen candidate set no longer matches its hash — re-run the "
            "preview rather than executing a set that changed underneath it",
        )

    # The caller commits this transition before dispatch. Because the row is
    # locked until then, a concurrent execute request cannot also observe
    # PREVIEWED and queue a second destructive task.
    job.status = QUEUED
    return [uuid.UUID(str(r)) for r in resolved]


async def start_frozen_set(
    db,
    *,
    job_id: uuid.UUID,
    project_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Atomically move a queued frozen set to running.

    Celery is at-least-once. A duplicate delivery waits on the row lock and
    then sees ``running`` (or a final state), so only one worker can cross the
    destructive boundary.
    """
    job = await get_job(db, job_id, for_update=True)
    if job is None or job.project_id != project_id:
        raise FrozenSetRejected(404, "Deletion job not found")

    if job.status != QUEUED:
        raise FrozenSetRejected(
            409,
            f"job is {job.status}, not {QUEUED} — a queued set can be "
            "started once, and this one already was",
        )

    resolved = list(job.resolved_run_ids or [])
    if not resolved:
        raise FrozenSetRejected(409, "the queued set is empty; nothing to execute")
    if job.candidate_hash != candidate_hash(resolved):
        raise FrozenSetRejected(
            409,
            "the frozen candidate set no longer matches its hash — refusing "
            "a queued set that changed underneath the worker",
        )

    job.status = RUNNING
    job.started_at = datetime.now(timezone.utc)
    return [uuid.UUID(str(r)) for r in resolved]


async def relay_queued_criteria_deletions(*, limit: int = 100) -> dict[str, int]:
    """Publish durable queued criteria jobs; duplicate deliveries are safe.

    ``DeletionJob`` is the outbox row. The API commits QUEUED before its
    best-effort fast-path publish, while this beat-driven relay recovers a
    process or broker failure in that gap. The worker's QUEUED -> RUNNING row
    lock is the consumer-side deduplication boundary.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.worker.tasks import execute_criteria_deletion_task

    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(
                        DeletionJob.id,
                        DeletionJob.project_id,
                        DeletionJob.requested_by_id,
                    )
                    .where(
                        DeletionJob.job_kind == KIND_CRITERIA,
                        DeletionJob.status == QUEUED,
                    )
                    .order_by(DeletionJob.requested_at)
                    .limit(limit)
                )
            ).all()
        )

    published = failed = 0
    for job_id, project_id, requested_by_id in rows:
        try:
            execute_criteria_deletion_task.delay(
                str(job_id),
                str(project_id),
                str(requested_by_id) if requested_by_id else None,
            )
            published += 1
        except Exception as exc:  # noqa: BLE001 - relay retries next minute
            failed += 1
            logger.warning(
                "criteria_deletion_relay_publish_failed",
                job_id=str(job_id),
                error_type=type(exc).__name__,
            )
    return {"found": len(rows), "published": published, "failed": failed}


def outcome_status(*, requested: int, deleted: int) -> str:
    """The status a finished multi-run job should carry.

    ``partial`` is the reason this function exists: a criteria job deletes many
    runs and nothing retries it, so "some of them went" is a real outcome and
    must not be reported as either success or failure. The nightly purge
    self-heals within 24h; this does not.
    """
    if deleted == 0 and requested > 0:
        return FAILED
    if deleted < requested:
        return PARTIAL
    return COMPLETED
