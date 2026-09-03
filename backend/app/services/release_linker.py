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
  does nothing on the second call. It does NOT remove a run's other links: a
  run can legitimately belong to more than one release.
- Every link records ``link_source`` — how the attribution was decided — so a
  release scorecard can state how much of its evidence was asserted by a
  client and how much the system inferred (migration 0150).
- When the caller has no release name at all, ``link_run_or_default`` falls
  back to the release that was ACTIVE WHEN THE RUN EXECUTED, resolved from the
  activation interval rather than from the current flag. See
  ``release_lifecycle_service``.

Superseded
----------
``get_or_create_default_release`` and the ``is_default`` flag it reads are the
pre-0150 fallback: one permanent bucket per project that never rotated, so its
membership said nothing about which release was underway. Both are kept only so
migration 0150 has a downgrade path. Do not add new callers.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    LinkSource,
    Project,
    Release,
    ReleaseTestRunLink,
    TestRun,
)

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
    from app.services.release_sort_key import compute_sort_key

    release = Release(
        project_id=project_id,
        name=normalized,
        status="planning",
        # This is the dominant release creator — one row for every unrecognised
        # release_name any client sends. Two fields it must not omit:
        #
        # sort_key, because migration 0151 backfilled it for every existing row
        # and indexed it. Leaving it NULL here splits the population in two:
        # historic releases ordered, newly-ingested ones filed at one end by
        # NULLS LAST regardless of version — so a run under "2.5.0" would sort
        # after "1.0.0".
        #
        # is_auto_named, because nobody chose this name either — a CI job
        # passed a string. It is the signal rotation uses to decide what may
        # become the next active release, and without it an arbitrary
        # client-supplied string is eligible.
        sort_key=compute_sort_key(None, normalized),
        is_auto_named=True,
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
    link_source: str = LinkSource.UNKNOWN.value,
    project_id: Optional[uuid.UUID] = None,
    linked_by_id: Optional[uuid.UUID] = None,
) -> bool:
    """
    Create a ReleaseTestRunLink if one does not already exist.

    Returns True when a new link was created, False when it already existed.
    Race-safe: the ``uq_release_test_run`` constraint blocks duplicate
    inserts; the SAVEPOINT keeps a loser's rollback local to the INSERT.

    ``link_source`` records HOW the attribution was decided (migration 0150).
    It defaults to ``unknown`` rather than to a plausible-looking value on
    purpose: a caller that forgets to say produces an obviously-unlabelled row,
    not one that silently claims to be a deliberate assignment.

    Note this dedupes on ``(release_id, test_run_id)`` and does NOT remove a
    run's other links — a run genuinely can belong to more than one release
    (a hotfix build validated for both 2.3.1 and 2.4.0). Readers that need a
    single release per run must pick one deliberately rather than assuming
    there is only ever one row.
    """
    existing = (await db.execute(
        select(ReleaseTestRunLink).where(
            ReleaseTestRunLink.release_id == release_id,
            ReleaseTestRunLink.test_run_id == test_run_id,
        )
    )).scalar_one_or_none()

    if existing:
        return False

    # Is this run already attributed somewhere? If not, this link becomes the
    # primary one. If it is, the existing primary stands — an automatic link
    # arriving later must not silently displace whatever a human chose, and the
    # partial unique index would reject a second primary anyway.
    has_primary = (await db.execute(
        select(ReleaseTestRunLink.id).where(
            ReleaseTestRunLink.test_run_id == test_run_id,
            ReleaseTestRunLink.is_primary.is_(True),
        ).limit(1)
    )).scalar_one_or_none() is not None

    link = ReleaseTestRunLink(
        release_id=release_id,
        test_run_id=test_run_id,
        phase_id=phase_id,
        link_source=link_source,
        is_primary=not has_primary,
        project_id=project_id,
        linked_by_id=linked_by_id,
    )
    try:
        async with db.begin_nested():
            db.add(link)
            await db.flush()
    except IntegrityError as exc:
        # Which constraint fired matters. The original catch-all returned False
        # — "the link already existed" — for ANY violation, so a concurrent
        # writer claiming the primary slot was reported as a successful no-op
        # and the run silently ended up with no link at all.
        #
        # uq_release_test_run: another transaction created the same link. Done.
        # ix_rtr_links_primary: our has_primary probe raced a concurrent
        #   promote. The link itself is still wanted — retry it as a secondary.
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or str(exc.orig)
        if "ix_rtr_links_primary" in constraint:
            link = ReleaseTestRunLink(
                release_id=release_id,
                test_run_id=test_run_id,
                phase_id=phase_id,
                link_source=link_source,
                is_primary=False,
                project_id=project_id,
                linked_by_id=linked_by_id,
            )
            async with db.begin_nested():
                db.add(link)
                await db.flush()
            return True
        if "uq_release_test_run" in constraint:
            return False
        # Anything else is not a race we understand; swallowing it would
        # report success for a link that was never written.
        raise

    # Keep the denormalized column in step with the link that was just made
    # primary. Only when this link IS the primary one — an added secondary
    # membership must not repoint analytics away from the primary.
    if not has_primary:
        await sync_primary_release(db, test_run_id)
    return True


async def sync_primary_release(
    db: AsyncSession,
    test_run_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """Point ``test_runs.primary_release_id`` at the run's primary link.

    Returns the release id written, or None when the run has no primary link
    (a legitimate state — an in-flight live run has no link yet, and a
    swallowed linker error leaves one permanently unattributed).

    **There is no single writer to point at.** The epic originally claimed the
    linker owned this column; it does not. Four live paths change which link is
    primary — ``release_linker.link_run_to_release``,
    ``release_service.link_test_run``, ``release_service.unlink_test_run``, and
    the Run Detail control in ``routers/runs.py`` — and each must call this
    afterwards. Miss one and the denormalized column goes stale silently:
    release-scoped analytics would answer from the old attribution while
    ``/runs``, which reads the link table directly, answers correctly. Two
    surfaces disagreeing with no error anywhere is the whole failure mode this
    function exists to make preventable.

    A drift sweep (``reconcile_primary_releases``) repairs anything that slips
    through and counts it, so a missed call site is measurable rather than
    invisible.
    """
    primary = (await db.execute(
        select(ReleaseTestRunLink.release_id).where(
            ReleaseTestRunLink.test_run_id == test_run_id,
            ReleaseTestRunLink.is_primary.is_(True),
        ).limit(1)
    )).scalar_one_or_none()

    await db.execute(
        update(TestRun)
        .where(TestRun.id == test_run_id)
        .values(primary_release_id=primary)
    )
    return primary


async def auto_link_release(
    db: AsyncSession,
    project_id: uuid.UUID,
    release_name: str,
    test_run_id: uuid.UUID,
    phase_id: Optional[uuid.UUID] = None,
    link_source: str = LinkSource.EXPLICIT_CLIENT.value,
    linked_by_id: Optional[uuid.UUID] = None,
) -> Tuple[Release, bool]:
    """
    Convenience wrapper: resolve (or create) a release by name, then link the run.

    Returns (release, release_was_created).
    Caller is responsible for committing the session.

    Defaults to ``explicit_client`` because reaching this function means a
    release name was supplied — that is an assertion, not an inference. The
    manual UI path overrides it.
    """
    release, created = await resolve_or_create_release(db, project_id, release_name)
    await link_run_to_release(
        db, release.id, test_run_id, phase_id,
        link_source=link_source, project_id=project_id,
        linked_by_id=linked_by_id,
    )
    return release, created


def default_release_name_for(project_name: str) -> str:
    """Canonical name for a project's auto-created default release."""
    return f"Default Release ({project_name})"


async def get_or_create_default_release(
    db: AsyncSession,
    project: Project,
) -> Release:
    """Return the project's ``is_default=True`` release, creating one on
    first use. Mirrors ``test_suite_service.get_or_create_default_suite`` —
    the partial unique index ``ix_releases_project_default`` makes
    concurrent first-use callers race-safe (the loser catches IntegrityError
    and re-reads the winner's row).

    Migration 0077.
    """
    existing = (
        await db.execute(
            select(Release).where(
                Release.project_id == project.id,
                Release.is_default.is_(True),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    release = Release(
        project_id=project.id,
        name=default_release_name_for(project.name),
        status="planning",
        is_default=True,
        description=(
            "Auto-created. Test runs ingested without an explicit release "
            "land here so they still participate in release tracking."
        ),
    )
    try:
        async with db.begin_nested():
            db.add(release)
            await db.flush()
    except IntegrityError:
        winner = (
            await db.execute(
                select(Release).where(
                    Release.project_id == project.id,
                    Release.is_default.is_(True),
                )
            )
        ).scalar_one_or_none()
        if winner is None:
            raise
        return winner
    logger.info(
        "Auto-created default release for project %s (release_id=%s)",
        project.id, release.id,
    )
    return release


async def _link_with_phase(
    db: AsyncSession,
    *,
    release: Release,
    test_run_id: uuid.UUID,
    phase_id: Optional[uuid.UUID],
    executed_at: Optional[datetime],
    link_source: str,
    project_id: uuid.UUID,
) -> Tuple[Release, bool]:
    """Link a run to *release*, attributing a phase when one can be inferred.

    **A caller-supplied ``phase_id`` always wins.** The only writer of
    ``phase_id`` before S3a was the manual link endpoint and its Link Run modal,
    so a non-NULL value here means a person chose it. Inference must never
    overwrite that — it would silently move a hand-placed run to a different
    phase, and phase membership is what a phase gate evaluates.

    Inference only fills the gap, and returns None freely: a run in a release
    but in no phase is a normal state, not a hole to plug with a guess.
    """
    resolved_phase = phase_id
    if resolved_phase is None:
        from app.services import release_attribution

        resolved_phase = await release_attribution.match_phase(
            db, release.id, executed_at
        )

    created = await link_run_to_release(
        db,
        release.id,
        test_run_id,
        resolved_phase,
        link_source=link_source,
        project_id=project_id,
    )
    return release, created


async def link_run_or_default(
    db: AsyncSession,
    project_id: uuid.UUID,
    release_name: Optional[str],
    test_run_id: uuid.UUID,
    phase_id: Optional[uuid.UUID] = None,
    executed_at: Optional[datetime] = None,
) -> Optional[Tuple[Release, bool]]:
    """Link a run to the named release; if ``release_name`` is blank/None,
    fall back to the release that was ACTIVE WHEN THE RUN EXECUTED.

    Returns ``(release, created)`` on success, or ``None`` when the project
    can't be loaded (caller decides whether that's a hard error or skip).

    Why *executed*, not *now* (migration 0150)
    ------------------------------------------
    Reading the active flag at ingest time is not the same as knowing which
    release was underway when the tests ran. A JUnit archive uploaded after a
    rotation would be attributed to the release that came next, and there is no
    re-attribution path once a link exists. ``executed_at`` lets the caller pass
    the run's real start time so the fallback resolves against the activation
    interval instead.

    Callers that genuinely have no execution time pass ``None`` and get the
    currently-active release. That is the honest degradation — and it is
    recorded, because the link still carries ``link_source='active_release'``,
    which the scorecard reports as inferred either way.

    The fallback release is now guaranteed to exist: if the project has no
    active release the lifecycle service creates one rather than returning
    nothing, so this path cannot leave a run unattributed by accident.
    """
    cleaned = (release_name or "").strip()
    if cleaned:
        # Rung 1. Phase inference applies here too — naming a release does not
        # name a phase, and a client that supplies one is rare.
        resolved, _created = await resolve_or_create_release(db, project_id, cleaned)
        return await _link_with_phase(
            db,
            release=resolved,
            test_run_id=test_run_id,
            phase_id=phase_id,
            executed_at=executed_at,
            link_source=LinkSource.EXPLICIT_CLIENT.value,
            project_id=project_id,
        )

    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        return None

    from app.services import release_attribution, release_lifecycle_service

    run = (
        await db.execute(select(TestRun).where(TestRun.id == test_run_id))
    ).scalar_one_or_none()

    # ── Rung 3: a project attribution rule ───────────────────────────────────
    # Ahead of the cutoff window because a rule is an explicit statement by
    # someone who owns the project ("release/* means the 2.5 line"), whereas a
    # window is an inference from dates. An explicit rule should not lose to a
    # date range that happens to overlap.
    if run is not None:
        rule = await release_attribution.match_attribution_rule(db, project_id, run)
        if rule is not None:
            resolved, _created = await resolve_or_create_release(
                db, project_id, rule.target_release_name
            )
            return await _link_with_phase(
                db,
                release=resolved,
                test_run_id=test_run_id,
                phase_id=phase_id,
                executed_at=executed_at,
                link_source=LinkSource.RULE_MATCH.value,
                project_id=project_id,
            )

    # ── Rung 4: a release whose cutoff window contains the execution time ────
    windowed = await release_attribution.match_cutoff_window(
        db, project_id, executed_at
    )
    if windowed is not None:
        return await _link_with_phase(
            db,
            release=windowed,
            test_run_id=test_run_id,
            phase_id=phase_id,
            executed_at=executed_at,
            link_source=LinkSource.CUTOFF_WINDOW.value,
            project_id=project_id,
        )

    # ── Rung 5: the release active when the run executed ─────────────────────
    active = await release_lifecycle_service.resolve_active_release_at(
        db, project_id, executed_at
    )
    if active is None:
        active = await release_lifecycle_service.get_or_create_active_release(
            db, project_id, reason="ingest_defensive"
        )

    return await _link_with_phase(
        db,
        release=active,
        test_run_id=test_run_id,
        phase_id=phase_id,
        executed_at=executed_at,
        link_source=LinkSource.ACTIVE_RELEASE.value,
        project_id=project_id,
    )


async def find_primary_release_drift(
    db: AsyncSession, limit: int = 500
) -> list[uuid.UUID]:
    """Run ids whose ``primary_release_id`` disagrees with their primary link.

    Detection only — the repair is :func:`sync_primary_release`, and they are
    kept apart so the sweep can count what it found *before* fixing it. A sweep
    that silently repairs reports a healthy system precisely because something
    keeps repairing it, and the missed call site that caused the drift is never
    seen.

    Covers both directions: a run whose column points at the wrong release, and
    a run with a primary link whose column is NULL. ``IS DISTINCT FROM`` rather
    than ``!=`` because NULL is a legitimate value on both sides and ``!=``
    would quietly skip exactly the rows most likely to be wrong.
    """
    primary = (
        select(ReleaseTestRunLink.release_id)
        .where(
            ReleaseTestRunLink.test_run_id == TestRun.id,
            ReleaseTestRunLink.is_primary.is_(True),
        )
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(TestRun.id)
            .where(TestRun.primary_release_id.is_distinct_from(primary))
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def find_releases_missing_sort_key(
    db: AsyncSession, limit: int = 500
) -> list[uuid.UUID]:
    """Release ids whose ``sort_key`` is NULL. Detection only.

    Migration 0151 adds the column and 0153 indexes it, but neither computes
    it: an earlier draft reimplemented the encoder in SQL and got pre-releases
    wrong, putting every ``2.4.0-rc1`` in the text band — after its own GA
    instead of before it. Two implementations of one encoding is a drift this
    codebase has paid for before, and the SQL copy is the one that cannot be
    unit-tested, so it was deleted rather than fixed.

    That leaves the column NULL until this sweep fills it, which is safe only
    because nothing reads ``sort_key`` yet. When the first reader lands, a NULL
    would sort every affected release to one end regardless of its version —
    so this must be running before then.
    """
    from app.models.postgres import Release

    rows = (
        await db.execute(
            select(Release.id).where(Release.sort_key.is_(None)).limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def sync_release_sort_key(db: AsyncSession, release_id: uuid.UUID) -> Optional[str]:
    """Recompute one release's ``sort_key`` from the single Python encoder."""
    from app.models.postgres import Release
    from app.services.release_sort_key import compute_sort_key

    release = (
        await db.execute(select(Release).where(Release.id == release_id))
    ).scalar_one_or_none()
    if release is None:
        return None
    release.sort_key = compute_sort_key(release.version, release.name)
    return release.sort_key
