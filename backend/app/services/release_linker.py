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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Release, ReleaseTestRunLink

logger = logging.getLogger(__name__)


async def resolve_or_create_release(
    db: AsyncSession,
    project_id: uuid.UUID,
    release_name: str,
) -> Tuple[Release, bool]:
    """
    Return the release matching *release_name* for *project_id*.
    If none is found, create a new one in ``planning`` status.

    Returns
    -------
    (release, created)
        created=True when a brand-new release was inserted.
    """
    # Case-insensitive lookup
    stmt = select(Release).where(
        Release.project_id == project_id,
        Release.name.ilike(release_name.strip()),
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing, False

    release = Release(
        project_id=project_id,
        name=release_name.strip(),
        status="planning",
        description="Auto-created from test run metadata.",
    )
    db.add(release)
    await db.flush()   # assign PK without committing yet
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
    db.add(link)
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
