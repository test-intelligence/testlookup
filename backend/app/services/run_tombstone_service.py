"""Records that a run was deliberately deleted, so nothing brings it back.

Five code paths create a ``TestRun`` from a **caller-supplied id** on a SELECT
miss: the stream stub, ``persist_live_session``, the live-session drainer, the
live-persist Celery task, and ``ingestion_pipeline`` when a ``run_id`` is
passed. Every one of them is correct on its own terms — they exist so a live
session that races the drainer still lands in ``test_runs``.

They are also, collectively, the reason a per-run delete could not be exposed.
Delete a run while any of them is in flight and the row reappears seconds
later, with its Mongo events, MinIO objects and event archive already gone: a
run that looks real, reads as passing or in-progress, and has nothing behind
it. Refusing ``IN_PROGRESS`` at the endpoint narrows the window but does not
close it — a Celery task already holding the id does not re-read the status.

So the delete leaves a tombstone and the creators consult it. The check is one
indexed primary-key lookup on a table with one row per deleted run.

**Fails open, deliberately.** If the lookup itself errors, the run is treated
as NOT tombstoned and ingestion proceeds. The alternative — dropping live
results because a bookkeeping query failed — trades a rare, visible,
recoverable problem (a resurrected row an operator can delete again) for a
silent, unrecoverable one (test results discarded).
"""
from __future__ import annotations

import uuid
from typing import Iterable, Optional

import structlog
from sqlalchemy import select

from app.models.postgres import RunTombstone

logger = structlog.get_logger(__name__)


async def run_is_tombstoned(db, run_id: uuid.UUID | str) -> bool:
    """True when this run id was deliberately deleted and must not return.

    ``db`` is the caller's session: this is a read, so it takes no transaction
    of its own and follows the stage-only rule the rest of the services do.
    """
    try:
        run_uuid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
    except (ValueError, AttributeError, TypeError):
        # A slug that is not a UUID was never a TestRun.id, so it cannot name
        # a tombstone. Callers convert via canonical_test_run_uuid first.
        return False

    try:
        found = (
            await db.execute(
                select(RunTombstone.run_id).where(RunTombstone.run_id == run_uuid)
            )
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — see module docstring: fails open
        logger.warning(
            "run_tombstone_lookup_failed", run_id=str(run_uuid), error=str(exc)
        )
        return False

    # Compare the value, not just its presence. The query selects the primary
    # key it filtered on, so a genuine hit returns exactly ``run_uuid``;
    # anything else did not answer the question that was asked and is treated
    # as "not tombstoned", consistent with the fail-open stance above.
    tombstoned = found == run_uuid
    if tombstoned:
        logger.info("run_resurrection_refused", run_id=str(run_uuid))
    return tombstoned


async def tombstoned_run_ids(
    db, run_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """The subset of ``run_ids`` that are tombstoned. One query, not N."""
    ids = [r for r in run_ids if isinstance(r, uuid.UUID)]
    if not ids:
        return set()
    try:
        rows = (
            await db.execute(
                select(RunTombstone.run_id).where(RunTombstone.run_id.in_(ids))
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001 — fails open, as above
        logger.warning("run_tombstone_bulk_lookup_failed", error=str(exc))
        return set()
    return set(rows)


def stage_tombstone(
    db,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    reason: Optional[str] = None,
    deleted_by_id: Optional[uuid.UUID] = None,
    deletion_job_id: Optional[uuid.UUID] = None,
) -> RunTombstone:
    """Stage the tombstone on the caller's session.

    Stage-only: the caller commits. The tombstone must land in the SAME
    transaction as the Postgres row deletion — a tombstone committed
    separately either guards a run that still exists (blocking legitimate
    live ingestion into a row that is right there) or, in the other order,
    leaves a window in which the run is gone and nothing stops it returning.
    """
    tombstone = RunTombstone(
        run_id=run_id,
        project_id=project_id,
        reason=(reason or None),
        deleted_by_id=deleted_by_id,
        deletion_job_id=deletion_job_id,
    )
    db.add(tombstone)
    return tombstone
