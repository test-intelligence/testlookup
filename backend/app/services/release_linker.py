"""
Shared helper: resolve a release by name (or create it) and link a test run to it.

Used by:
  - ingestion.py  (webhook-based Allure/TestNG uploads)
  - stream.py     (live execution sessions)
  - runs.py       (manual UI/API linking)

Behaviour
---------
- Lookup is case-insensitive and scoped to the project.
- If the release name is not found a new release is auto-created in "planning"
  status so QA leads can promote it later without data loss.
- The link is idempotent — calling twice with the same (release_id, run_id)
  does nothing on the second call.
"""
import logging
import uuid
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Release, ReleaseTestRunLink

logger = logging.getLogger(__name__)


async def _lookup_release(
    db: AsyncSession, project_id: uuid.UUID, normalized_name: str,
) -> Optional[Release]:
    """Case-insensitive lookup by (project_id, lower(name))."""
    stmt = select(Release).where(
        Release.project_id == project_id,
        func.lower(Release.name) == normalized_name.lower(),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def resolve_or_create_release(
    db: AsyncSession,
    project_id: uuid.UUID,
    release_name: str,
) -> Tuple[Release, bool]:
    """
    Return the release matching *release_name* for *project_id*.
    If none is found, create a new one in ``planning`` status.

    Race-safe: two concurrent ingestion requests for the same release name
    are serialized by the ``uq_releases_project_lower_name`` unique functional
    index (migration 0059). The loser of the race catches ``IntegrityError``
    on flush, rolls back its pending insert, and re-reads the winner's row.

    The case-insensitive comparison uses ``lower()`` (not ``ILIKE``) because
    the input is untrusted user data — ``ILIKE`` would interpret any ``_``
    or ``%`` in the release name as a wildcard, causing it to match a
    different existing release and silently mis-link the run.

    Returns
    -------
    (release, created)
        created=True when a brand-new release was inserted by *this* call.
    """
    normalized = release_name.strip()

    existing = await _lookup_release(db, project_id, normalized)
    if existing:
        return existing, False

    # Wrap the INSERT in a SAVEPOINT so a unique-violation rolls back ONLY
    # the failed insert — not the caller's outer transaction (which may
    # already hold the ingested TestRun + TestCase rows).
    release = Release(
        project_id=project_id,
        name=normalized,
        status="planning",
        description="Auto-created from test run metadata.",
    )
    try:
        async with db.begin_nested():
            db.add(release)
            await db.flush()   # assign PK without committing outer txn
    except IntegrityError:
        # Another transaction inserted the same (project_id, lower(name))
        # between our lookup and flush. The SAVEPOINT is already rolled back;
        # fetch the winner's row in the still-live outer transaction.
        winner = await _lookup_release(db, project_id, normalized)
        if winner is None:
            # Extremely unlikely: the unique-index violation fired but the
            # winning row is not yet visible to our snapshot (possible under
            # REPEATABLE READ with an uncommitted concurrent txn). Re-raise
            # so the caller can retry the whole operation.
            raise
        logger.info(
            "Race on release '%s' project %s — using existing id=%s",
            release_name, project_id, winner.id,
        )
        return winner, False

    logger.info(
        "Auto-created release '%s' (id=%s) for project %s",
        release_name, release.id, project_id,
    )
    return release, True


async def link_run_to_release(
    db: AsyncSession,
    release_id: uuid.UUID,
    test_run_id: uuid.UUID,
    phase_id: Optional[uuid.UUID] = None,
) -> bool:
    """
    Create a ReleaseTestRunLink if one does not already exist.

    Returns True when a new link was created, False when it already existed.
    Race-safe: the ``uq_release_test_run`` constraint blocks duplicate
    inserts; the SAVEPOINT keeps a loser's rollback local to the INSERT.
    """
    existing = (await db.execute(
        select(ReleaseTestRunLink).where(
            ReleaseTestRunLink.release_id == release_id,
            ReleaseTestRunLink.test_run_id == test_run_id,
        )
    )).scalar_one_or_none()

    if existing:
        return False

    link = ReleaseTestRunLink(
        release_id=release_id,
        test_run_id=test_run_id,
        phase_id=phase_id,
    )
    try:
        async with db.begin_nested():
            db.add(link)
            await db.flush()
    except IntegrityError:
        # Concurrent link creation won the race. The SAVEPOINT rolled back
        # the failed INSERT; the link already exists, so we're done.
        return False
    return True


async def auto_link_release(
    db: AsyncSession,
    project_id: uuid.UUID,
    release_name: str,
    test_run_id: uuid.UUID,
    phase_id: Optional[uuid.UUID] = None,
) -> Tuple[Release, bool]:
    """
    Convenience wrapper: resolve (or create) a release by name, then link the run.

    Returns (release, release_was_created).
    Caller is responsible for committing the session.
    """
    release, created = await resolve_or_create_release(db, project_id, release_name)
    await link_run_to_release(db, release.id, test_run_id, phase_id)
    return release, created
