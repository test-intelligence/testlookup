"""Operational records of deletions actually running (S2b).

``settings_audit_log`` already carries the compliance evidence for a purge, and
it is never purged. But it is written *after* the purge commits, so it can only
describe work that finished. Nothing could see that a destructive job was
running, and nothing could record that one failed.

**Every write here opens its own session, and that is the whole design.** A
``failed`` or ``partial`` status written on the caller's session is erased by
the rollback that produced the failure — the only outcomes a same-session
writer can record are the successful ones, which is precisely the wrong half.
The purge-audit row is written after its commit for the same reason.

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

#: States this slice can actually reach. ``previewed`` arrives with criteria
#: deletion's freeze; ``cancelled`` is deliberately absent until something can
#: produce it.
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
PARTIAL = "partial"

REACHABLE_STATUSES: frozenset[str] = frozenset(
    {QUEUED, RUNNING, COMPLETED, FAILED, PARTIAL}
)

#: Jobs that have stopped. Retention purges only these: a row still marked
#: ``running`` past the audit window is a crashed sweep, and deleting it on a
#: clock would erase the only evidence that it hung.
TERMINAL_STATUSES: frozenset[str] = frozenset({COMPLETED, FAILED, PARTIAL})

#: Job kinds. Only ``scheduled`` has a producer in this slice; the rest arrive
#: with S2c and S3b. Listed so the vocabulary lives in one place rather than
#: being re-invented per caller.
KIND_SCHEDULED = "scheduled"
KIND_SINGLE_ENTITY = "single_entity"
KIND_CRITERIA = "criteria"


def candidate_hash(run_ids: Iterable[Any]) -> str:
    """Stable fingerprint of a candidate set.

    Order-independent, so two resolutions of the same runs agree regardless of
    query plan. Criteria deletion (S3b) compares this between preview and
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
) -> None:
    """Finish a job record, on its own session.

    ``job_id`` may be None when :func:`open_job` could not write — the call is
    then a no-op rather than an error, so a caller never has to branch.

    ``bytes_reclaimed`` stays NULL when not supplied. It is not defaulted to 0:
    "reclaimed nothing" and "nobody measured" are opposite findings, and this
    column is read by the surface that tells an operator whether a purge was
    worth running.
    """
    if job_id is None:
        return
    if status not in REACHABLE_STATUSES:
        # A typo'd status is a row that no filter will ever match — the same
        # silent-forever failure as a mismatched enum vocabulary.
        logger.warning(
            "deletion_job_unknown_status", job_id=str(job_id), status=status
        )
        return

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
            await db.execute(
                update(DeletionJob).where(DeletionJob.id == job_id).values(**values)
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "deletion_job_close_failed", job_id=str(job_id), error=str(exc)
        )


async def get_job(db, job_id: uuid.UUID) -> Optional[DeletionJob]:
    """Read one job. Read-only; the caller owns its session."""
    return (
        await db.execute(select(DeletionJob).where(DeletionJob.id == job_id))
    ).scalar_one_or_none()


async def list_jobs(db, project_id: uuid.UUID, *, limit: int = 50) -> list[DeletionJob]:
    """Most recent jobs for a project, newest first."""
    result = await db.execute(
        select(DeletionJob)
        .where(DeletionJob.project_id == project_id)
        .order_by(DeletionJob.requested_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
