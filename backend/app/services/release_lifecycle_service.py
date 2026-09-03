"""The active-release invariant: every project has exactly one, at every moment.

Why this exists
---------------
Before S0, a run that arrived with no release name from the client fell into a
per-project ``is_default`` row that never rotated. It accumulated runs across
every release cycle, so its membership carried no information — a 2.1 run and a
2.6 run sat in the same bucket, indistinguishable.

The active release replaces it with a *rotating* pointer. It moves as releases
ship, so an unlabelled run lands in whatever release was genuinely in flight.
That makes the fallback informative rather than decorative — but only if the
pointer is always there, which is what this module guarantees.

Two properties, and the difference matters
------------------------------------------
``at most one`` is enforced by the database: the partial unique index
``ix_releases_project_active`` (migration 0150) makes two actives impossible,
so the swap in :func:`activate_release` needs no lock.

``at least one`` cannot be a constraint — SQL has no way to say "this table has
a row for every row of that one". It is upheld by covering every path that
could leave a project without one, and then *measured* by a reconciliation
sweep. The enforcement points are:

  * project creation (same transaction as the project)
  * :func:`activate_release` / rotation, which never clears a flag without setting one
  * release deletion, which refuses to orphan a project
  * project reset, which re-provisions
  * ingest, defensively, via :func:`get_or_create_active_release`
  * the reconciliation beat, which repairs *and reports*

Resolving as of a moment, not now
---------------------------------
:func:`resolve_active_release_at` exists because reading the ``is_active`` flag
at ingest time is not the same as knowing which release was underway when the
run *executed*. A JUnit archive uploaded after a rotation would otherwise be
attributed to the release that came next. Every activation writes
``activated_at`` / ``deactivated_at``, so the interval is queryable after the
fact.

Nothing here commits. Callers own their transaction, matching
``release_linker`` and the rest of the service layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, Release

logger = structlog.get_logger(__name__)

#: Name given to a release nobody has named yet. Deliberately not a version
#: number: guessing "2.5.0" after 2.4.0 produces an authoritative-looking wrong
#: name when the team's next release is really 2.4.1, and runs then accrete
#: under a version that will never exist. A placeholder is honest about being
#: one, and ``is_auto_named`` drives the prompt to replace it.
AUTO_RELEASE_NAME = "Unreleased"

#: Statuses that mean a release is finished and must not hold the active flag.
TERMINAL_STATUSES = ("released", "cancelled", "archived")


async def _current_active(
    db: AsyncSession, project_id: uuid.UUID
) -> Optional[Release]:
    return (
        await db.execute(
            select(Release).where(
                Release.project_id == project_id,
                Release.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


async def resolve_active_release_at(
    db: AsyncSession,
    project_id: uuid.UUID,
    at: Optional[datetime] = None,
) -> Optional[Release]:
    """Return the release that was active for *project_id* at *at*.

    This is the rung the attribution ladder actually uses. It reads the
    activation interval rather than the ``is_active`` flag, so a run ingested
    after a rotation is still attributed to the release that was underway when
    it ran.

    ``at=None`` means "now" and short-circuits to the flag, which is both
    cheaper and correct for the live path.

    Falls back to the currently-active release when *at* predates every recorded
    activation — that is the common case for the first rotation after S0 ships,
    where the migrated release has ``activated_at = created_at`` but the runs
    beneath it are older. Attributing those to the current active release is the
    same answer the pre-S0 default bucket gave, so the fallback never makes an
    existing attribution worse.
    """
    if at is None:
        return await _current_active(db, project_id)

    row = (
        await db.execute(
            select(Release)
            .where(
                Release.project_id == project_id,
                Release.activated_at.isnot(None),
                Release.activated_at <= at,
            )
            # A NULL deactivated_at means "still active", so it contains any
            # `at` at or after activation.
            .where(
                (Release.deactivated_at.is_(None)) | (Release.deactivated_at > at)
            )
            .order_by(Release.activated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if row is not None:
        return row
    return await _current_active(db, project_id)



async def _next_free_auto_name(db: AsyncSession, project_id: uuid.UUID) -> str:
    """First unused auto name for the project.

    ``uq_releases_project_lower_name`` is case-insensitive, so the comparison
    here is too — otherwise "unreleased" typed by a user would look free and
    the INSERT would still collide.
    """
    taken = {
        (n or "").strip().lower()
        for n in (
            await db.execute(
                select(Release.name).where(Release.project_id == project_id)
            )
        ).scalars().all()
    }
    if AUTO_RELEASE_NAME.lower() not in taken:
        return AUTO_RELEASE_NAME
    for n in range(2, 1000):
        candidate = f"{AUTO_RELEASE_NAME} ({n})"
        if candidate.lower() not in taken:
            return candidate
    # A project with 999 placeholders is broken in a way a suffix will not fix.
    raise RuntimeError(
        f"project {project_id} has exhausted auto release names — "
        "something is minting placeholders in a loop"
    )


async def _create_auto_release(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    reason: str,
    activate: bool,
) -> Release:
    """Return an auto-named release for the project, reusing or minting one.

    **Why this is not a plain INSERT.** Migration 0059 created
    ``uq_releases_project_lower_name`` — UNIQUE on ``(project_id, lower(name))``
    — so a project can hold exactly ONE release called "Unreleased", ever,
    including a long-finished one. Minting a second unconditionally makes every
    later rotation raise: mark a release shipped on a project whose only other
    release is the original placeholder, and the INSERT collides, the handler
    re-raises, and ``PUT /releases/{id}`` 500s without shipping anything.

    So: reuse a non-terminal placeholder if one exists, and otherwise pick the
    first free name. Reuse is preferred over always-suffixing because a project
    that rotates often would otherwise accumulate ``Unreleased (2..n)`` rows
    that mean nothing to anyone.

    The two unique indexes in play fail for different reasons and are handled
    separately — conflating them is what made the original version unable to
    recover: a name collision needs a different name, an active-flag collision
    needs the winner's row.
    """
    now = datetime.now(timezone.utc)

    # Reuse: a placeholder nobody named, that has not finished, is exactly the
    # row a rotation wants. Ordered so the reuse is deterministic.
    reusable = (
        await db.execute(
            select(Release)
            .where(
                Release.project_id == project_id,
                Release.is_auto_named.is_(True),
                Release.status.notin_(TERMINAL_STATUSES),
                Release.is_active.is_(False),
            )
            .order_by(Release.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if reusable is not None:
        logger.info(
            "release_auto_reused",
            project_id=str(project_id),
            release_id=str(reusable.id),
            reason=reason,
        )
        if activate:
            await activate_release(db, reusable, reason=reason)
        return reusable

    name = await _next_free_auto_name(db, project_id)
    from app.services.release_sort_key import compute_sort_key

    release = Release(
        project_id=project_id,
        name=name,
        status="in_progress" if activate else "planning",
        # Placeholders land in the sort_key text band, which is where they
        # belong — after every real version. Leaving it NULL instead would file
        # them by NULLS LAST, which is the same position by accident rather
        # than by design, and would break the moment the ordering changed.
        sort_key=compute_sort_key(None, name),
        is_active=activate,
        is_auto_named=True,
        activated_at=now if activate else None,
        description=(
            "Auto-created so this project always has a release to attribute "
            "runs to. Rename it when you know what you are shipping."
        ),
    )
    try:
        async with db.begin_nested():
            db.add(release)
            await db.flush()
    except IntegrityError:
        # Only the active-flag race is recoverable here, and only by reading
        # the winner. A name collision at this point means another transaction
        # took the name between the lookup and the flush; re-raising lets the
        # caller retry rather than looping.
        if not activate:
            raise
        winner = await _current_active(db, project_id)
        if winner is None:
            raise
        logger.info(
            "active_release_create_race",
            project_id=str(project_id),
            winner_id=str(winner.id),
        )
        return winner

    logger.info(
        "release_auto_created",
        project_id=str(project_id),
        release_id=str(release.id),
        reason=reason,
        activated=activate,
    )
    return release


async def get_or_create_active_release(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    reason: str = "ingest_defensive",
) -> Release:
    """Return the project's active release, creating one if the invariant broke.

    The defensive enforcement point. Every other path is supposed to keep the
    invariant true; this one makes ingest correct even when one of them did not
    — including paths that bypass the router entirely, such as the raw-SQL
    project seeding in CI.
    """
    existing = await _current_active(db, project_id)
    if existing is not None:
        return existing

    logger.warning(
        "active_release_missing_repaired",
        project_id=str(project_id),
        reason=reason,
    )
    return await _create_auto_release(
        db, project_id, reason=reason, activate=True
    )


async def ensure_active_release_for_new_project(
    db: AsyncSession, project: Project
) -> Release:
    """Create the initial active release for a freshly created project.

    Called inside the project-creation transaction. If this fails, project
    creation fails — which is the only way the invariant can be true from t=0
    rather than true-after-the-first-repair.
    """
    return await _create_auto_release(
        db, project.id, reason="project_create", activate=True
    )


async def _next_successor(
    db: AsyncSession, project_id: uuid.UUID, exclude_id: uuid.UUID
) -> Optional[Release]:
    """Pick the release that should become active next, or None.

    Restricted to releases a human created or a sync produced. Ingest
    auto-creates a ``planning`` release for every unrecognised ``release_name``
    a client sends, so the planning pool is *not* a curated queue — promoting
    from it unfiltered would activate whatever string some CI job happened to
    pass. ``is_auto_named`` is exactly the "nobody chose this" signal.

    Ordered by ``created_at`` for now. When S1 lands ``sort_key`` this becomes
    version order; created_at is the honest stand-in until a total order over
    version strings actually exists.
    """
    return (
        await db.execute(
            select(Release)
            .where(
                Release.project_id == project_id,
                Release.id != exclude_id,
                Release.status == "planning",
                Release.is_auto_named.is_(False),
            )
            .order_by(Release.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def activate_release(
    db: AsyncSession,
    release: Release,
    *,
    reason: str = "manual",
) -> Optional[Release]:
    """Make *release* the active one, deactivating the incumbent.

    Returns the release that was deactivated, or None if there wasn't one.

    Both writes happen in the caller's transaction, so there is never a
    committed state with two actives or zero. The partial unique index would
    reject two anyway; doing it in one transaction is what avoids the zero.
    """
    now = datetime.now(timezone.utc)
    previous = await _current_active(db, release.project_id)

    # Identity first, and only then ids. ``Release.id`` carries a Python-side
    # ``default=uuid.uuid4`` that is applied at INSERT, so an object that has
    # not been flushed yet has ``id is None`` — and ``None == None`` would make
    # any two unflushed releases compare equal, silently turning a real
    # activation into a no-op.
    if previous is not None and (
        previous is release
        or (previous.id is not None and previous.id == release.id)
    ):
        return None

    if previous is not None:
        previous.is_active = False
        previous.deactivated_at = now
        # FLUSH HERE, and do not remove it.
        #
        # Staging both writes and letting one flush handle them does NOT work.
        # SQLAlchemy's unit of work sorts persistent UPDATEs by PRIMARY KEY,
        # not by assignment order, and ``Release.id`` is a random uuid4 — so
        # which statement reaches Postgres first is a coin flip. On the half of
        # orderings where the promote goes first, ``ix_releases_project_active``
        # sees two rows with is_active and rejects it: a partial unique INDEX
        # is checked per statement and cannot be deferred.
        #
        # Flushing the demotion releases the slot before the promotion claims
        # it. Both writes still share the caller's transaction, so there is
        # still never a COMMITTED state with zero or two actives — which is the
        # property that actually matters.
        await db.flush()

    release.is_active = True
    release.activated_at = now
    release.deactivated_at = None
    if release.status == "planning":
        release.status = "in_progress"

    logger.info(
        "release_activated",
        project_id=str(release.project_id),
        release_id=str(release.id),
        previous_id=str(previous.id) if previous else None,
        reason=reason,
    )
    return previous


async def rotate_on_close(
    db: AsyncSession,
    release: Release,
    *,
    reason: str = "released",
) -> Release:
    """Hand the active flag on when *release* finishes. Never blocks.

    Promotes the next human-created ``planning`` release if there is one, and
    mints an auto-named placeholder otherwise. A bookkeeping invariant that
    made shipping harder would be routed around — teams would simply leave
    releases ``in_progress`` forever — so this always succeeds.

    Returns whichever release is now active.
    """
    successor = await _next_successor(db, release.project_id, release.id)
    if successor is None:
        successor = await _create_auto_release(
            db, release.project_id, reason=f"successor_cascade:{reason}", activate=False
        )

    await activate_release(db, successor, reason=f"successor_cascade:{reason}")
    logger.info(
        "release_rotated",
        project_id=str(release.project_id),
        closed_id=str(release.id),
        successor_id=str(successor.id),
        reason=reason,
    )
    return successor


async def should_activate_on_create(
    db: AsyncSession, project_id: uuid.UUID
) -> bool:
    """Should a newly, explicitly created release take over as active?

    Only when the incumbent is auto-named. That covers the onboarding case — a
    project has an ``Unreleased`` placeholder, a user creates ``2.4.0`` and
    reasonably expects runs to land there — without ever displacing a release
    somebody deliberately activated. Pre-planning ``2.6.0`` while shipping 2.5.0
    must not hijack attribution.
    """
    current = await _current_active(db, project_id)
    return current is None or current.is_auto_named


async def find_projects_without_active_release(
    db: AsyncSession, limit: int = 500
) -> list[uuid.UUID]:
    """Project ids currently violating the invariant. Detection only.

    Used by the reconciliation beat. Kept separate from the repair so the beat
    can count what it found *before* fixing it — a sweep that silently repairs
    reports a perfect invariant precisely because something keeps fixing it.
    """
    active_projects = select(Release.project_id).where(Release.is_active.is_(True))
    rows = (
        await db.execute(
            select(Project.id)
            .where(
                # Soft-deleted projects are not violations. Counting them would
                # make the violation metric a permanent non-zero floor that
                # nobody can drive down, and repairing them would mint releases
                # inside projects the user deleted.
                Project.is_active.is_(True),
                Project.id.notin_(active_projects),
            )
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)
