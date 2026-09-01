"""TestSuite + CanonicalTestCase service.

Phase 2 of the test-case <-> test-suite linking feature. Handles:

  * ``get_or_create_default_suite`` — lazy creation of the per-project default
    suite (``Default Suite ({project.name})``). The schema enforces at most
    one ``is_default=true`` row per project (partial unique index from
    migration 0075), so a SELECT-then-INSERT race rejects the duplicate
    rather than corrupting state.
  * ``get_or_create_suite_by_name`` — used during ingestion to materialise
    ``test_suites`` rows for any suite_name that appears on a new TestCase.
  * ``sync_canonical_test_cases`` — batched reconciliation of
    ``canonical_test_cases`` against the TestCases in a finalised run. Runs
    in ``finalize_run`` alongside the legacy ``sync_suite_membership`` — both
    write paths execute during the Phase-2 dual-write window so a rollback
    to suite_memberships remains an option.

Deletion detection is deliberately out of scope for Phase 2. The legacy
``suite_memberships.<suite>-deleted`` bucket continues to handle that
side-channel; ``canonical_test_cases.status`` only transitions ``deleted ->
active`` here (restore on re-sighting). A later phase, after the UI ships
and the deletion UX is settled, will implement explicit lifecycle.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import HTTPException, status
from sqlalchemy import bindparam, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    ManagedTestCase,
    Project,
    TestCase,
    TestCaseLifecycleState,
    TestRun,
    TestSuite,
    TestSuiteOwner,
    User,
)
from app.core.metrics import automation_cases_orphaned
from app.services.test_management_audit_service import audit_event
from app.services.test_management_metrics_service import (
    stage_orphan_gauge_refresh,
    stage_test_management_counter,
)
from app.services.test_case_lifecycle_service import stage_test_case_snapshot

logger = structlog.get_logger("services.test_suite")


def default_suite_name_for(project_name: str) -> str:
    """Canonical name for the default suite of a given project."""
    return f"Default Suite ({project_name})"


async def get_or_create_default_suite(
    db: AsyncSession,
    project: Project,
) -> TestSuite:
    """Return the project's default suite, creating it on first access.

    Migration 0075 backfills one per project, but a project created after
    migration runs still needs this lazy path. The partial unique index
    means concurrent callers race safely: the loser gets an IntegrityError
    and we re-select.
    """
    result = await db.execute(
        select(TestSuite).where(
            TestSuite.project_id == project.id,
            TestSuite.is_default.is_(True),
        )
    )
    suite = result.scalar_one_or_none()
    if suite is not None:
        return suite

    suite = TestSuite(
        project_id=project.id,
        name=default_suite_name_for(project.name),
        is_default=True,
        description="Auto-created. New test cases without an explicit suite land here.",
    )
    # SAVEPOINT, not a bare db.rollback(): this is an injected session whose
    # caller owns the transaction. A full rollback here would discard the
    # caller's pending work; begin_nested() unwinds only the failed insert
    # when the partial unique index rejects a concurrent duplicate.
    try:
        async with db.begin_nested():
            db.add(suite)
            await db.flush()
    except IntegrityError:
        # Re-select after the partial unique index rejected our insert.
        result = await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == project.id,
                TestSuite.is_default.is_(True),
            )
        )
        suite = result.scalar_one()
    else:
        await _maybe_seed_default_owner(db, project.id, suite.name)
    return suite


async def _maybe_seed_default_owner(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: str,
) -> None:
    """Write a ``TestSuiteOwner`` row pointing at the project's default QA
    lead, if one is configured (migration 0079).

    No-ops when:
      * ``Project.default_qa_lead_user_id`` is NULL
      * A ``TestSuiteOwner`` row already exists for this (project, suite)
        — i.e. a human (or an earlier ingest) already assigned the owner;
        ingest should never silently overwrite human intent.

    Failures are swallowed and logged: the suite was already inserted +
    flushed, so a failure here must not roll back ingest. Worst-case the
    suite resolves via the read-time fallback chain — a degraded but
    correct outcome.
    """
    try:
        project = (
            await db.execute(
                select(Project.default_qa_lead_user_id).where(Project.id == project_id)
            )
        ).first()
        default_qa_lead_id = project[0] if project else None
        if default_qa_lead_id is None:
            return

        existing = (
            await db.execute(
                select(TestSuiteOwner.id).where(
                    TestSuiteOwner.project_id == project_id,
                    TestSuiteOwner.suite_name == suite_name,
                )
            )
        ).first()
        if existing is not None:
            return

        # SAVEPOINT around the insert: TestSuiteOwner has a unique
        # (project_id, suite_name) constraint, so the check-then-insert above
        # races with concurrent ingests. A failed flush on a bare injected
        # session leaves it in a rollback-required state and poisons the
        # caller's transaction; begin_nested() unwinds only this insert.
        try:
            async with db.begin_nested():
                db.add(TestSuiteOwner(
                    project_id=project_id,
                    suite_name=suite_name,
                    owner_user_id=default_qa_lead_id,
                ))
                await db.flush()
        except IntegrityError:
            # A concurrent seed won the race — the owner row already exists.
            return
        logger.info(
            "suite_default_owner_seeded",
            project_id=str(project_id),
            suite_name=suite_name,
            owner_user_id=str(default_qa_lead_id),
        )
    except Exception as exc:
        logger.warning(
            "suite_default_owner_seed_failed",
            project_id=str(project_id),
            suite_name=suite_name,
            error=str(exc),
        )


async def get_or_create_suite_by_name(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
) -> TestSuite:
    """Resolve a TestSuite for a (project, name) pair, creating if needed.

    When a new row is inserted and the project has a ``default_qa_lead_user_id``
    configured (migration 0079), a corresponding ``TestSuiteOwner`` row is
    also written so ingest-created suites have explicit ownership from day
    one instead of relying on the read-time fallback chain. The role check
    is intentionally skipped on this path — the user was already validated
    when ``Project.default_qa_lead_user_id`` was set, and re-running the
    check inside the ingest hot path would add a per-suite DB round trip.
    """
    result = await db.execute(
        select(TestSuite).where(
            TestSuite.project_id == project_id,
            TestSuite.name == name,
        )
    )
    suite = result.scalar_one_or_none()
    if suite is not None:
        return suite

    suite = TestSuite(project_id=project_id, name=name, is_default=False)
    # SAVEPOINT, not a bare db.rollback() — see get_or_create_default_suite.
    # This path runs inside a loop (sync_canonical_test_cases) on an injected
    # session, so a full rollback would discard sibling suites already created
    # in the same transaction. begin_nested() unwinds only the racing insert.
    try:
        async with db.begin_nested():
            db.add(suite)
            await db.flush()
    except IntegrityError:
        result = await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == project_id,
                TestSuite.name == name,
            )
        )
        suite = result.scalar_one()
    else:
        await _maybe_seed_default_owner(db, project_id, name)
    return suite


async def get_or_create_canonical(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    test_fingerprint: str,
    test_name: str,
    class_name: Optional[str],
    suite_name: Optional[str],
) -> CanonicalTestCase:
    """Resolve (and create if absent) the project-scoped CanonicalTestCase for a
    ``(project_id, test_fingerprint)`` — the LATEST-RUN-ONLY snapshot anchor for
    granular steps/attachments.

    Called from ``_upsert_test_case`` during ingestion so the step snapshot has
    a stable anchor *before* the later ``sync_canonical_test_cases`` pass runs.
    That pass re-selects by fingerprint and updates idempotently, so pre-creating
    the row here is safe — it just wins the get-or-create and the sync step finds
    and updates it. Uses the same SAVEPOINT race pattern as the sibling helpers.
    """
    existing = (
        await db.execute(
            select(CanonicalTestCase).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_fingerprint == test_fingerprint,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_seen_run_id = run_id
        return existing

    # Resolve the owning suite: named suite (materialise if missing) or the
    # project's default suite for NULL/empty suite_name.
    if suite_name and suite_name.strip():
        target_suite = await get_or_create_suite_by_name(db, project_id, suite_name)
    else:
        project = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            raise ValueError(f"Project not found: {project_id}")
        target_suite = await get_or_create_default_suite(db, project)

    canonical = CanonicalTestCase(
        project_id=project_id,
        test_suite_id=target_suite.id,
        test_fingerprint=test_fingerprint,
        test_name=test_name or "Unknown",
        class_name=class_name,
        status="active",
        source="execution",
        first_seen_run_id=run_id,
        last_seen_run_id=run_id,
    )
    try:
        async with db.begin_nested():
            db.add(canonical)
            await db.flush()
    except IntegrityError:
        canonical = (
            await db.execute(
                select(CanonicalTestCase).where(
                    CanonicalTestCase.project_id == project_id,
                    CanonicalTestCase.test_fingerprint == test_fingerprint,
                )
            )
        ).scalar_one()
        canonical.last_seen_run_id = run_id
    return canonical


async def sync_canonical_test_cases(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[str, int]:
    """Reconcile canonical_test_cases against a finalised run's TestCases.

    Idempotent: re-running for the same run produces no duplicate rows. For
    each test case in the run:

      * Materialise its ``TestSuite`` row if missing.
      * Upsert a ``CanonicalTestCase`` keyed by (project_id, fingerprint).
        - New fingerprint -> insert, ``first_seen_run_id = last_seen_run_id = run_id``.
        - Existing fingerprint -> bump ``last_seen_run_id``, restore from
          ``deleted``/``needs_review`` to ``active``, refresh test_name /
          class_name if the payload changed them.
      * Link ``TestCase.canonical_test_case_id``.

    Returns counts for observability.
    """
    counts = {"added": 0, "updated": 0, "linked": 0, "skipped": 0}

    cases_result = await db.execute(
        select(TestCase).where(TestCase.test_run_id == run_id)
    )
    run_cases = list(cases_result.scalars().all())
    if not run_cases:
        # Live-stream gap fallback: a run can land in ``test_runs`` with a
        # populated ``primary_suite_name`` aggregate but zero ``test_cases``
        # rows when the Redis event buffer was evicted before
        # ``persist_live_session`` read it (or when an SDK only sends
        # heartbeats without per-test events). Without this branch, the
        # finalize pipeline early-returns and ``TestSuite`` never gets
        # created — surfacing as "suite missing on /suites and /test-management"
        # bug reports. Read-side fallbacks (router union queries, list_test_suites
        # backfill) work around the gap, but the right place to close it
        # is here: materialise the TestSuite row at write time so all
        # catalog consumers see a consistent view.
        run_row = await db.execute(
            select(TestRun).where(TestRun.id == run_id)
        )
        test_run = run_row.scalar_one_or_none()
        suite_name = (
            (test_run.primary_suite_name or "").strip() if test_run else ""
        )
        if suite_name:
            await get_or_create_suite_by_name(db, project_id, suite_name)
            counts["added"] = 1
            logger.info(
                "canonical_sync_aggregate_only",
                project_id=str(project_id),
                run_id=str(run_id),
                suite_name=suite_name,
            )
        return counts

    # ── Resolve TestSuite for every suite_name in the run ────────
    suite_names = {tc.suite_name for tc in run_cases if tc.suite_name}
    suite_by_name: dict[str, TestSuite] = {}
    if suite_names:
        suites_result = await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == project_id,
                TestSuite.name.in_(suite_names),
            )
        )
        suite_by_name = {s.name: s for s in suites_result.scalars().all()}

    missing_names = suite_names - set(suite_by_name)
    for name in missing_names:
        suite_by_name[name] = await get_or_create_suite_by_name(db, project_id, name)

    # Default suite fallback for any run case that arrived with NULL suite_name.
    # Should be rare after Phase 2 pre-fills suite_name during ingest_test_results,
    # but stays as a safety net for older ingestion paths.
    default_suite: Optional[TestSuite] = None
    if any(tc.suite_name is None or tc.suite_name == "" for tc in run_cases):
        project_result = await db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        if project is not None:
            default_suite = await get_or_create_default_suite(db, project)

    # ── Batch-fetch existing CanonicalTestCase rows by fingerprint ──
    fingerprints = [tc.test_fingerprint for tc in run_cases if tc.test_fingerprint]
    canonical_by_fp: dict[str, CanonicalTestCase] = {}
    if fingerprints:
        existing_result = await db.execute(
            select(CanonicalTestCase).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_fingerprint.in_(fingerprints),
            )
        )
        canonical_by_fp = {c.test_fingerprint: c for c in existing_result.scalars().all()}

    # ── Upsert per case ──────────────────────────────────────────
    for tc in run_cases:
        if not tc.test_fingerprint:
            counts["skipped"] += 1
            continue

        target_suite = suite_by_name.get(tc.suite_name) if tc.suite_name else default_suite
        if target_suite is None:
            counts["skipped"] += 1
            continue

        canonical = canonical_by_fp.get(tc.test_fingerprint)
        if canonical is None:
            canonical = CanonicalTestCase(
                project_id=project_id,
                test_suite_id=target_suite.id,
                test_fingerprint=tc.test_fingerprint,
                test_name=tc.test_name or "Unknown",
                class_name=tc.class_name,
                status="active",
                source="execution",
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
            )
            # SAVEPOINT around the per-case flush: a bare db.rollback() here
            # would discard EVERY canonical + TestCase link already added in
            # this batch (the function runs inside ingestion_pipeline's
            # _run_isolated session, which commits once at the end), silently
            # truncating the run's catalog. begin_nested() unwinds only the
            # row that lost the concurrent-upsert race.
            try:
                async with db.begin_nested():
                    db.add(canonical)
                    await db.flush()
            except IntegrityError:
                # Lost the race to a concurrent upsert — re-select the winner.
                refetch = await db.execute(
                    select(CanonicalTestCase).where(
                        CanonicalTestCase.project_id == project_id,
                        CanonicalTestCase.test_fingerprint == tc.test_fingerprint,
                    )
                )
                canonical = refetch.scalar_one()
            canonical_by_fp[tc.test_fingerprint] = canonical
            counts["added"] += 1
        else:
            canonical.last_seen_run_id = run_id
            if canonical.status in ("deleted", "needs_review"):
                canonical.status = "active"
                canonical.deleted_at_run_id = None
                canonical.deleted_observed_at = None
                canonical.retirement_confirmed_at = None
                canonical.retirement_confirmed_by_id = None
                canonical.retirement_reason = None
            if tc.test_name and canonical.test_name != tc.test_name:
                canonical.test_name = tc.test_name
            if canonical.class_name != tc.class_name:
                canonical.class_name = tc.class_name
            counts["updated"] += 1

        if tc.canonical_test_case_id != canonical.id:
            tc.canonical_test_case_id = canonical.id
            counts["linked"] += 1

    stage_orphan_gauge_refresh(db, project_id)
    logger.info(
        "canonical_sync_complete",
        project_id=str(project_id),
        run_id=str(run_id),
        **counts,
    )
    return counts


# ── CRUD service functions (used by routers/suites.py) ─────────────────────


async def get_suite_or_404(db: AsyncSession, suite_id: uuid.UUID) -> TestSuite:
    suite = await db.get(TestSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test suite not found")
    return suite


async def get_canonical_or_404(db: AsyncSession, canonical_id: uuid.UUID) -> CanonicalTestCase:
    canonical = await db.get(CanonicalTestCase, canonical_id)
    if canonical is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canonical test case not found")
    return canonical


async def list_test_suites(
    db: AsyncSession,
    project_ids: Optional[list[uuid.UUID]],
    *,
    include_counts: bool = True,
) -> list[dict]:
    """Return suites for the given projects (or all if ``project_ids is None``)
    with an optional per-suite canonical_test_cases count.

    Auto-backfills missing ``TestSuite`` rows for suites whose runs landed
    aggregates on ``test_runs.primary_suite_name`` but never triggered
    ``ingestion_pipeline.finalize_run`` (the live-stream gap from
    CLAUDE.md pitfall #15). Without the backfill these suites are
    invisible on /suites even though they're visible on /runs, /coverage
    and /test-management. The backfill creates a regular non-default
    ``TestSuite`` row so the navigate-to-id flow keeps working.
    """
    stmt = select(TestSuite)
    # Soft-deleted projects excluded unconditionally; the membership / pinned
    # restriction below applies on top. ``project_ids`` is None for an ADMIN,
    # so before this the unscoped list applied no project filter at all —
    # measured live at **103 suites returned, 93 of them on deleted projects**.
    stmt = stmt.where(
        TestSuite.project_id.in_(
            select(Project.id).where(Project.is_active.is_(True))
        )
    )
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(TestSuite.project_id.in_(project_ids))
    stmt = stmt.order_by(TestSuite.is_default.desc(), TestSuite.name.asc())
    suites = list((await db.execute(stmt)).scalars().all())

    # ── Live-stream gap backfill ─────────────────────────────────────────
    # Read-on-write is normally a smell, but here the alternatives are
    # worse: render virtual entries breaks navigate-to-id, and asking the
    # user to wait for an out-of-band reaper is the opposite of helpful
    # for "the suite is missing from the list" support requests. The
    # backfill is idempotent (unique constraint catches concurrent
    # writes) and only fires when there's a real gap.
    if project_ids is not None:
        from sqlalchemy import text as sa_text

        # ``IN :pids`` must use ``expanding=True`` so SQLAlchemy expands
        # the list into one placeholder per element (``IN ($1, $2, ...)``)
        # at execute time. Without it, asyncpg receives a single ``$1``
        # placeholder and the Python tuple gets bound as a single value
        # — PostgreSQL then can't match a UUID column to a tuple and
        # the query 500s. Incident 2026-05-18: a single-project caller
        # (project_id query param on /suites) triggered the bad binding
        # and the surrounding ``except Exception: await db.rollback()``
        # then aborted the request's transaction, blowing up the entire
        # handler instead of degrading the backfill gracefully.
        missing_query = sa_text("""
            SELECT
                tr.project_id,
                NULLIF(TRIM(tr.primary_suite_name), '') AS suite_name
            FROM test_runs tr
            WHERE tr.project_id IN :pids
              AND tr.primary_suite_name IS NOT NULL
              AND TRIM(tr.primary_suite_name) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM test_suites ts
                  WHERE ts.project_id = tr.project_id
                    AND ts.name = NULLIF(TRIM(tr.primary_suite_name), '')
              )
            GROUP BY tr.project_id, NULLIF(TRIM(tr.primary_suite_name), '')
        """).bindparams(bindparam("pids", expanding=True))
        try:
            missing_rows = (
                await db.execute(missing_query, {"pids": list(project_ids)})
            ).fetchall()
        except Exception as exc:
            # Degrade: a backfill probe failure shouldn't 500 /suites,
            # AND must not roll back the caller's transaction (this is
            # an injected session — caller owns commit/rollback per
            # backend/CLAUDE.md "Commit responsibility (single-owner
            # rule)"). Swallow + log + continue with whatever suites
            # we already fetched.
            logger.warning(
                "test_suites backfill probe failed, skipping",
                error=str(exc),
            )
            missing_rows = []

        if missing_rows:
            existing_keys = {(s.project_id, s.name) for s in suites}
            backfilled = False
            for row in missing_rows:
                if row.suite_name is None:
                    continue
                if (row.project_id, row.suite_name) in existing_keys:
                    continue
                try:
                    suite = TestSuite(
                        project_id=row.project_id,
                        name=row.suite_name,
                        description=None,
                        tags=None,
                        is_default=False,
                    )
                    # SAVEPOINT: a bare db.rollback() would unwind the suites
                    # backfilled earlier in this loop and leave them as
                    # expired/detached objects in ``suites`` — serializing
                    # those in the response (below) then 500s the GET.
                    # begin_nested() unwinds only this insert.
                    async with db.begin_nested():
                        db.add(suite)
                        await db.flush()
                    suites.append(suite)
                    existing_keys.add((row.project_id, row.suite_name))
                    backfilled = True
                    logger.info(
                        "backfilled_missing_test_suite",
                        project_id=str(row.project_id),
                        suite_name=row.suite_name,
                    )
                except IntegrityError:
                    # Concurrent backfill from another request — the savepoint
                    # already unwound the failed insert; refetch the now-present
                    # row so the caller still sees it in this response.
                    refetch_q = await db.execute(
                        select(TestSuite).where(
                            TestSuite.project_id == row.project_id,
                            TestSuite.name == row.suite_name,
                        )
                    )
                    existing = refetch_q.scalar_one_or_none()
                    if existing is not None:
                        suites.append(existing)
                        existing_keys.add((row.project_id, row.suite_name))
            if backfilled:
                # Re-sort so the response order is stable and matches the
                # original ``ORDER BY is_default DESC, name ASC`` contract.
                suites.sort(key=lambda s: (not s.is_default, s.name.lower()))

    counts_by_suite: dict[uuid.UUID, int] = {}
    if include_counts and suites:
        count_q = await db.execute(
            select(CanonicalTestCase.test_suite_id, func.count(CanonicalTestCase.id))
            .where(CanonicalTestCase.test_suite_id.in_([s.id for s in suites]))
            .group_by(CanonicalTestCase.test_suite_id)
        )
        counts_by_suite = {row[0]: int(row[1]) for row in count_q.all()}

    return [
        {
            "id": s.id,
            "project_id": s.project_id,
            "name": s.name,
            "description": s.description,
            "is_default": s.is_default,
            "tags": s.tags,
            "test_case_count": counts_by_suite.get(s.id, 0),
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in suites
    ]


async def create_test_suite(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> TestSuite:
    """Create a non-default suite. ``is_default`` is intentionally never
    settable on create — promote one via ``set_default_suite`` instead so
    the partial unique index can't fire on a concurrent insert.
    """
    suite = TestSuite(
        project_id=project_id,
        name=name.strip(),
        description=description,
        tags=tags,
        is_default=False,
    )
    # SAVEPOINT instead of an injected-session db.rollback(): the failed
    # insert unwinds inside the savepoint and the caller's transaction stays
    # clean for its own rollback-on-exception handling.
    try:
        async with db.begin_nested():
            db.add(suite)
            await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A suite named '{name}' already exists in this project",
        ) from exc
    return suite


async def update_test_suite(
    db: AsyncSession,
    suite: TestSuite,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> TestSuite:
    if name is not None:
        suite.name = name.strip()
    if description is not None:
        suite.description = description
    if tags is not None:
        suite.tags = tags
    # SAVEPOINT instead of an injected-session db.rollback() (see create_test_suite).
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another suite in this project already uses that name",
        ) from exc
    return suite


async def delete_test_suite(db: AsyncSession, suite: TestSuite) -> None:
    """Delete a suite — refuses when the partial RESTRICT FK on
    ``canonical_test_cases.test_suite_id`` would orphan rows. The default
    suite can't be deleted at all (operators rename it; auto-creation would
    re-add it on next ingest anyway).
    """
    if suite.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The default suite cannot be deleted; rename or move cases first",
        )
    count_q = await db.execute(
        select(func.count(CanonicalTestCase.id)).where(
            CanonicalTestCase.test_suite_id == suite.id
        )
    )
    count = int(count_q.scalar() or 0)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Suite still has {count} test case(s); move or delete them first",
        )
    await db.delete(suite)
    await db.flush()


async def set_default_suite(db: AsyncSession, suite: TestSuite) -> TestSuite:
    """Promote ``suite`` to default within its project, demoting the prior one.
    Done in two flushes inside a single transaction so the partial unique index
    can't reject the new default (the old one is cleared first).
    """
    # Demote any existing default in this project.
    existing_q = await db.execute(
        select(TestSuite).where(
            TestSuite.project_id == suite.project_id,
            TestSuite.is_default.is_(True),
            TestSuite.id != suite.id,
        )
    )
    for prior in existing_q.scalars().all():
        prior.is_default = False
    await db.flush()

    suite.is_default = True
    await db.flush()
    return suite


async def list_canonical_test_cases(
    db: AsyncSession,
    *,
    project_ids: Optional[list[uuid.UUID]],
    suite_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = None,
) -> list[CanonicalTestCase]:
    stmt = select(CanonicalTestCase)
    # Soft-deleted projects are excluded unconditionally; the membership /
    # pinned-project restriction below is applied on top.
    #
    # ``project_ids`` is None for an ADMIN (no membership confinement), so
    # before this the unscoped list applied no project filter at all. Measured
    # live: **1,237 rows returned, 1,205 of them across 36 deleted projects** —
    # 97% of the Test Management canonical-case list was tests belonging to
    # projects the user cannot open.
    #
    # Ninth surface in this family (#535 runs, #538 dashboard, #539 analytics,
    # #541 ROI, #547 trends, #549 defect KPI, #550 releases, #551 live page).
    stmt = stmt.where(
        CanonicalTestCase.project_id.in_(
            select(Project.id).where(Project.is_active.is_(True))
        )
    )
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(CanonicalTestCase.project_id.in_(project_ids))
    if suite_id is not None:
        stmt = stmt.where(CanonicalTestCase.test_suite_id == suite_id)
    if status_filter is not None:
        stmt = stmt.where(CanonicalTestCase.status == status_filter)
    stmt = stmt.order_by(CanonicalTestCase.test_name.asc())
    return list((await db.execute(stmt)).scalars().all())


def legacy_suite_membership_clause(suite_key: str):
    """Which ``test_cases`` rows belong to a suite, for the legacy fallback.

    Named and module-level rather than inlined so the regression test can
    exercise THIS predicate. Built inline, a test could only re-declare an
    identical copy, and a copy keeps passing after the original drifts —
    which is the failure mode this whole helper-duplication sweep exists to
    find.

    Per-row ``suite_name`` is authoritative when present. The run-level
    ``primary_suite_name`` arm is restricted to ``live_stream`` runs: that is
    the only case where every case in a run belongs to the run's suite (the
    SDK stamps it once at session-create and per-event suite stays NULL).
    Unrestricted, a multi-``<testsuite>`` upload listed its whole run under
    whichever single suite the run-level label named.
    """
    from sqlalchemy import and_, or_

    return or_(
        func.lower(func.trim(TestCase.suite_name)) == suite_key,
        and_(
            TestRun.trigger_source == "live_stream",
            func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, ""))) == suite_key,
        ),
    )


async def list_legacy_suite_test_cases(
    db: AsyncSession,
    suite: TestSuite,
) -> list[dict]:
    """Synthesize ``CanonicalTestCaseResponse``-shaped rows for a suite by
    deriving them from ``test_cases`` joined on ``suite_name``.

    Used as a fallback when ``canonical_test_cases`` has no rows for the
    suite — pre-migration-0075 data, or runs where ``sync_canonical_test_cases``
    didn't end up linking the run's cases to this particular suite (e.g. the
    suite name on TestCase rows doesn't match the TestSuite.name). The
    response uses ``TestCase.id`` as the row id (no canonical row exists)
    and reports ``status='active'`` since the test was observed in a real
    run. One row per ``test_fingerprint`` — the most recent run wins for
    last_seen_run_id / class_name.
    """
    # Cases belong to a suite when EITHER the per-row ``tc.suite_name``
    # OR the run-level ``tr.primary_suite_name`` matches. SDK live-
    # stream paths only stamp the run-level value (per-row stays NULL);
    # legacy ingests only set the per-row value. The OR covers both.
    # See ``feedback_live_stream_suite_name_nulls``.
    #
    # The run-level arm is restricted to ``live_stream`` runs, because that is
    # the only case where "every case in this run belongs to
    # ``primary_suite_name``" is true. A multi-``<testsuite>`` upload has an
    # authoritative per-row suite AND a run-level label naming just one of
    # them, so an unrestricted OR listed the whole run under that one suite.
    # Measured on project 2aefa4fa scoped to ``api``: 12 distinct tests
    # returned where 5 belong to the suite — the other 7 are ``regression``
    # and ``smoke`` tests presented as members of ``api``.
    #
    # Same shape as ``metrics_service._suite_match_clause``,
    # ``run_compare_service`` (#559) and ``test_management_service`` (#560).
    suite_key = (suite.name or "").strip().lower()
    base = (
        select(
            TestCase.id,
            TestCase.test_fingerprint,
            TestCase.test_name,
            TestCase.class_name,
            TestCase.test_run_id,
            TestRun.created_at,
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(
            TestRun.project_id == suite.project_id,
            legacy_suite_membership_clause(suite_key),
        )
    )
    base_sq = base.subquery()
    latest = (
        select(base_sq)
        .distinct(base_sq.c.test_fingerprint)
        .order_by(base_sq.c.test_fingerprint, base_sq.c.created_at.desc())
        .subquery()
    )
    counts = (
        select(
            base_sq.c.test_fingerprint.label("fp"),
            func.count().label("run_count"),
        )
        .group_by(base_sq.c.test_fingerprint)
        .subquery()
    )
    stmt = (
        select(
            latest.c.id,
            latest.c.test_fingerprint,
            latest.c.test_name,
            latest.c.class_name,
            latest.c.test_run_id,
            latest.c.created_at,
            counts.c.run_count,
        )
        .join(counts, latest.c.test_fingerprint == counts.c.fp)
        .order_by(latest.c.test_name.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": row.id,
            "project_id": suite.project_id,
            "test_suite_id": suite.id,
            "test_suite_name": suite.name,
            "test_fingerprint": row.test_fingerprint,
            "test_name": row.test_name,
            "class_name": row.class_name,
            "status": "active",
            "source": "automation",
            "first_seen_run_id": None,
            "last_seen_run_id": row.test_run_id,
            # Legacy fallback rows use the per-run TestCase.id as the row id,
            # so the deep-link target is the same id. Lets the UI navigate to
            # ``/runs/<run>/tests/<case>`` for both fallback and canonical
            # paths without branching.
            "last_seen_test_case_id": row.id,
            "deleted_at_run_id": None,
            "managed_test_case_id": None,
            "review_tag": None,
            "tags": None,
            "run_count": int(row.run_count or 0),
            "created_at": row.created_at,
            "updated_at": row.created_at,
        }
        for row in rows
    ]


async def link_canonical_to_suite(
    db: AsyncSession,
    canonical: CanonicalTestCase,
    target_suite: TestSuite,
) -> CanonicalTestCase:
    if target_suite.project_id != canonical.project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move a test case to a suite in a different project",
        )
    canonical.test_suite_id = target_suite.id
    await db.flush()
    return canonical


async def promote_canonical_test_case(
    db: AsyncSession,
    canonical_id: uuid.UUID,
    actor: User,
) -> tuple[CanonicalTestCase, ManagedTestCase]:
    """Create and link one draft authored case under a canonical row lock."""
    canonical = (
        await db.execute(
            select(CanonicalTestCase)
            .where(CanonicalTestCase.id == canonical_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if canonical is None:
        raise HTTPException(status_code=404, detail="Canonical test case not found")
    if canonical.managed_test_case_id is not None:
        raise HTTPException(status_code=409, detail="Canonical test case is already promoted")
    if not canonical.test_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Canonical test case has no governable fingerprint identity",
        )

    same_fingerprint = list(
        (
            await db.execute(
                select(ManagedTestCase)
                .where(
                    ManagedTestCase.project_id == canonical.project_id,
                    ManagedTestCase.test_fingerprint == canonical.test_fingerprint,
                )
                .order_by(ManagedTestCase.created_at.asc(), ManagedTestCase.id.asc())
                .limit(2)
                .with_for_update()
            )
        ).scalars().all()
    )
    if len(same_fingerprint) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple managed test cases already use this fingerprint; resolve the conflict before promotion",
        )
    if same_fingerprint:
        managed = same_fingerprint[0]
        if managed.status in {
            TestCaseLifecycleState.DEPRECATED.value,
            TestCaseLifecycleState.ARCHIVED.value,
        }:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The matching managed test case is terminal; reinstate or "
                    "resolve it before promotion"
                ),
            )
        existing_link = (
            await db.execute(
                select(CanonicalTestCase.id)
                .where(
                    CanonicalTestCase.managed_test_case_id == managed.id,
                    CanonicalTestCase.id != canonical.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_link is not None:
            raise HTTPException(
                status_code=409,
                detail="The matching managed test case is already linked to another canonical case",
            )
        canonical.managed_test_case_id = managed.id
        canonical.source = "linked"
        await audit_event(
            db,
            "automation_case",
            canonical.id,
            canonical.project_id,
            "promote",
            actor,
            old_values={"managed_test_case_id": None, "source": "execution"},
            new_values={"managed_test_case_id": str(managed.id), "source": "linked"},
            policy_snapshot={"target_state": managed.status, "fingerprint_match": "existing"},
        )
        stage_test_management_counter(db, "promotion", (str(canonical.project_id),))
        await db.flush()
        return canonical, managed

    suite = await db.get(TestSuite, canonical.test_suite_id)
    managed = ManagedTestCase(
        project_id=canonical.project_id,
        title=canonical.test_name,
        feature_area=canonical.class_name,
        suite_name=suite.name if suite is not None else None,
        test_suite_id=canonical.test_suite_id,
        test_type="automation",
        status="draft",
        version=1,
        author_id=actor.id,
        is_automated=True,
        automation_status="automated",
        # Identity copy is deliberately verbatim. Recomputing from display
        # fields would break merged-list dedup for class-qualified tests.
        test_fingerprint=canonical.test_fingerprint,
    )
    db.add(managed)
    await db.flush()
    stage_test_case_snapshot(
        db,
        managed,
        actor_id=actor.id,
        change_type="promoted",
        change_summary="Promoted from automation evidence",
        changed_fields=[
            "title", "feature_area", "suite_name", "test_suite_id",
            "test_type", "status", "is_automated", "automation_status",
            "test_fingerprint",
        ],
    )
    canonical.managed_test_case_id = managed.id
    canonical.source = "linked"
    await audit_event(
        db,
        "automation_case",
        canonical.id,
        canonical.project_id,
        "promote",
        actor,
        old_values={"managed_test_case_id": None, "source": "execution"},
        new_values={"managed_test_case_id": str(managed.id), "source": "linked"},
        policy_snapshot={"target_state": "draft", "fingerprint_copy": "verbatim"},
    )
    await audit_event(
        db,
        "test_case",
        managed.id,
        managed.project_id,
        "created",
        actor,
        details=f"Promoted from canonical test case {canonical.id}",
    )
    stage_test_management_counter(db, "promotion", (str(canonical.project_id),))
    await db.flush()
    return canonical, managed


async def unlink_canonical_managed_case(
    db: AsyncSession,
    canonical_id: uuid.UUID,
    actor: User,
    *,
    reason: str,
) -> CanonicalTestCase:
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason must not be blank")
    if len(reason) > 500:
        raise HTTPException(status_code=422, detail="reason must be at most 500 characters")
    canonical = (
        await db.execute(
            select(CanonicalTestCase)
            .where(CanonicalTestCase.id == canonical_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if canonical is None:
        raise HTTPException(status_code=404, detail="Canonical test case not found")
    if canonical.managed_test_case_id is None:
        raise HTTPException(status_code=409, detail="Canonical test case has no managed link")
    old_managed_id = canonical.managed_test_case_id
    canonical.managed_test_case_id = None
    canonical.source = "execution"
    await audit_event(
        db,
        "automation_case",
        canonical.id,
        canonical.project_id,
        "unlink_managed_case",
        actor,
        old_values={"managed_test_case_id": str(old_managed_id), "source": "linked"},
        new_values={"managed_test_case_id": None, "source": "execution"},
        reason=reason,
    )
    await db.flush()
    return canonical


async def confirm_canonical_retirement(
    db: AsyncSession,
    canonical_id: uuid.UUID,
    actor: User,
    *,
    reason: str,
) -> CanonicalTestCase:
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason must not be blank")
    if len(reason) > 500:
        raise HTTPException(status_code=422, detail="reason must be at most 500 characters")
    canonical = (
        await db.execute(
            select(CanonicalTestCase)
            .where(CanonicalTestCase.id == canonical_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if canonical is None:
        raise HTTPException(status_code=404, detail="Canonical test case not found")
    if canonical.status != "deleted":
        raise HTTPException(status_code=409, detail="Only a deleted canonical case can be retired")
    if canonical.retirement_confirmed_at is not None:
        raise HTTPException(status_code=409, detail="Retirement is already confirmed")
    canonical.retirement_confirmed_at = datetime.now(timezone.utc)
    canonical.retirement_confirmed_by_id = actor.id
    canonical.retirement_reason = reason
    await audit_event(
        db,
        "automation_case",
        canonical.id,
        canonical.project_id,
        "confirm_retirement",
        actor,
        reason=reason,
        old_values={"retirement_confirmed_at": None},
        new_values={"retirement_confirmed_at": canonical.retirement_confirmed_at.isoformat()},
    )
    stage_orphan_gauge_refresh(db, canonical.project_id)
    await db.flush()
    return canonical


async def list_orphaned_canonical_cases(
    db: AsyncSession,
    project_ids: Optional[list[uuid.UUID]],
    *,
    page: int = 1,
    size: int = 25,
) -> tuple[list[CanonicalTestCase], int]:
    stmt = select(CanonicalTestCase).where(
        CanonicalTestCase.status == "deleted",
        CanonicalTestCase.retirement_confirmed_at.is_(None),
    )
    if project_ids is not None:
        if not project_ids:
            return [], 0
        stmt = stmt.where(CanonicalTestCase.project_id.in_(project_ids))
    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    rows = list(
        (
            await db.execute(
                stmt.order_by(
                    CanonicalTestCase.deleted_observed_at.asc().nullsfirst(),
                    CanonicalTestCase.id.asc(),
                ).offset((page - 1) * size).limit(size)
            )
        ).scalars().all()
    )
    count_stmt = select(
        CanonicalTestCase.project_id,
        func.count(CanonicalTestCase.id),
    ).where(
        CanonicalTestCase.status == "deleted",
        CanonicalTestCase.retirement_confirmed_at.is_(None),
    )
    if project_ids is not None:
        count_stmt = count_stmt.where(CanonicalTestCase.project_id.in_(project_ids))
    count_rows = await db.execute(count_stmt.group_by(CanonicalTestCase.project_id))
    counts = {project_id: int(count) for project_id, count in count_rows.all()}
    # Explicitly reset scoped projects with no rows; otherwise a previous
    # non-zero gauge remains stale forever after the queue is cleared.
    if project_ids is None:
        all_project_rows = await db.execute(select(Project.id))
        metric_project_ids = list(all_project_rows.scalars().all())
    else:
        metric_project_ids = project_ids
    for project_id in metric_project_ids:
        try:
            automation_cases_orphaned.labels(str(project_id)).set(counts.get(project_id, 0))
        except Exception:
            logger.warning("automation_orphan_metric_failed", project_id=str(project_id))
    return rows, total


async def list_test_case_evidence_gaps(
    db: AsyncSession,
    project_ids: Optional[list[uuid.UUID]],
    *,
    kind: str,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict], int]:
    if kind not in {"never_executed", "automation_vanished"}:
        raise HTTPException(status_code=422, detail="Unknown evidence gap kind")
    stmt = (
        select(ManagedTestCase, CanonicalTestCase)
        .outerjoin(
            CanonicalTestCase,
            CanonicalTestCase.managed_test_case_id == ManagedTestCase.id,
        )
    )
    if project_ids is not None:
        if not project_ids:
            return [], 0
        stmt = stmt.where(ManagedTestCase.project_id.in_(project_ids))
    if kind == "never_executed":
        stmt = stmt.where(
            CanonicalTestCase.id.is_(None),
            ManagedTestCase.last_executed_at.is_(None),
        )
    else:
        stmt = stmt.where(CanonicalTestCase.status == "deleted")
    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    rows = (
        await db.execute(
            stmt.order_by(ManagedTestCase.created_at.desc(), ManagedTestCase.id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    items = [
        {
            "id": managed.id,
            "project_id": managed.project_id,
            "title": managed.title,
            "status": managed.status,
            "canonical_test_case_id": canonical.id if canonical is not None else None,
            "canonical_status": canonical.status if canonical is not None else None,
            "deleted_observed_at": canonical.deleted_observed_at if canonical is not None else None,
            "last_executed_at": managed.last_executed_at,
        }
        for managed, canonical in rows
    ]
    return items, total


# Maximum canonical_ids per bulk-link request. 200 covers the realistic
# multi-select case (a SuiteCasesPage typically renders a few-dozen rows
# per scroll viewport) and keeps the worst-case round-trip bounded.
BULK_LINK_MAX_IDS = 200


async def bulk_link_canonicals_to_suite(
    db: AsyncSession,
    target_suite: TestSuite,
    canonical_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """Move a batch of canonical test cases to ``target_suite``.

    Single-canonical analogue: ``link_canonical_to_suite``. The bulk
    variant exists so the UI can move 50 cases in one round trip instead
    of N — and so we can reject the *entire* batch atomically when even
    one id belongs to a different project, matching the "cross-project
    move refused" semantic the single-move path enforces.

    Contract:

      * Empty ``canonical_ids`` → ``moved=0, missing=0`` and no DB write.
      * Any id resolving to a canonical whose ``project_id`` differs from
        ``target_suite.project_id`` → ``HTTPException(400)`` BEFORE any
        write. The whole batch fails closed; no partial moves.
      * Ids that don't resolve at all are surfaced in ``missing_ids`` so
        the caller can decide whether to retry, show a toast, etc. They
        do NOT block the batch.
      * Successfully resolved canonicals get ``test_suite_id`` flipped to
        ``target_suite.id`` and a single ``db.flush()`` is issued at the
        end — the caller still owns ``commit()``.

    Returns: ``{"moved": int, "skipped_already_in_target": int,
    "missing_ids": [uuid, ...]}``.
    """
    if not canonical_ids:
        return {"moved": 0, "skipped_already_in_target": 0, "missing_ids": []}

    if len(canonical_ids) > BULK_LINK_MAX_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Too many test cases in one batch ({len(canonical_ids)}). "
                f"Bulk-link is capped at {BULK_LINK_MAX_IDS}; "
                "split the selection or apply in chunks."
            ),
        )

    # De-dup the input. Callers (UI multi-select) sometimes send the same
    # id twice — we don't want that to double-count "moved". ``dict.fromkeys``
    # de-dups while preserving order so ``missing_ids`` is deterministic.
    unique_ids = list(dict.fromkeys(canonical_ids))

    result = await db.execute(
        select(CanonicalTestCase).where(CanonicalTestCase.id.in_(unique_ids))
    )
    found = list(result.scalars().all())
    found_by_id = {c.id: c for c in found}
    missing_ids = [cid for cid in unique_ids if cid not in found_by_id]

    # Cross-project guard fires BEFORE any mutation: an attacker shouldn't
    # be able to move 1 valid + 1 cross-project case and have the valid
    # one silently land. Validate-then-write.
    cross_project = [
        c for c in found if c.project_id != target_suite.project_id
    ]
    if cross_project:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot move test cases to a suite in a different project "
                f"({len(cross_project)} of {len(found)} ids are from other projects)"
            ),
        )

    moved = 0
    skipped = 0
    for canonical in found:
        if canonical.test_suite_id == target_suite.id:
            skipped += 1
            continue
        canonical.test_suite_id = target_suite.id
        moved += 1

    if moved:
        await db.flush()

    logger.info(
        "canonical_bulk_link",
        target_suite_id=str(target_suite.id),
        project_id=str(target_suite.project_id),
        requested=len(unique_ids),
        moved=moved,
        skipped_already_in_target=skipped,
        missing=len(missing_ids),
    )

    return {
        "moved": moved,
        "skipped_already_in_target": skipped,
        "missing_ids": missing_ids,
    }


async def reconcile_canonical_deletions(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    window_runs: Optional[int] = None,
) -> dict[str, int]:
    """Mark active canonicals as ``deleted`` when their fingerprint hasn't
    appeared in any of the project's last ``window_runs`` test runs.

    Counterpart to ``sync_canonical_test_cases``: that function handles
    *appearance* (insert new + restore previously-deleted on re-sighting);
    this one handles *disappearance*. Together they close the canonical
    lifecycle so Phase 2b can drop ``suite_memberships`` (the legacy
    ``<suite>-deleted`` bucket lived in that table).

    Why a multi-run window instead of "absent from the latest run":
    single-run absence is noisy — a run scoped to a tag filter, partial
    suite, or a developer's ad-hoc selection routinely omits tests that
    are very much still part of the codebase. Marking those ``deleted``
    on the spot would spam the catalog with false positives that have
    to be restored on the very next full run. N=5 (the default; tune via
    ``CANONICAL_DELETION_WINDOW_RUNS``) means a test has to be missing
    from five consecutive runs before we trust the absence.

    Returns counts for observability: ``deleted`` (newly marked),
    ``unchanged`` (still active, present in the window), ``window_size``
    (actual runs considered — may be less than the requested window
    when the project has fewer recent runs).

    Setting ``window_runs=0`` (or the env default ``CANONICAL_DELETION_WINDOW_RUNS=0``)
    short-circuits the reconciler — useful during the Phase 2b cutover
    while comparing canonical vs legacy suite_memberships row counts.
    """
    from app.core.config import settings

    stage_orphan_gauge_refresh(db, project_id)
    effective_window = (
        window_runs if window_runs is not None
        else settings.CANONICAL_DELETION_WINDOW_RUNS
    )
    if effective_window <= 0:
        return {"deleted": 0, "unchanged": 0, "window_size": 0}

    # Fetch the project's most recent N runs (newest first). We need
    # both the run ids (for the deleted_at pointer) and the union of
    # fingerprints seen across those runs.
    recent_runs_q = await db.execute(
        select(TestRun.id)
        .where(TestRun.project_id == project_id)
        .order_by(TestRun.created_at.desc())
        .limit(effective_window)
    )
    recent_run_ids = [row[0] for row in recent_runs_q.all()]
    if not recent_run_ids:
        # No runs at all → nothing to compare against. Don't touch any
        # canonicals; an empty project shouldn't have its catalog wiped
        # on the first reconciler tick after migration.
        return {"deleted": 0, "unchanged": 0, "window_size": 0}

    # Fingerprints observed across the window. NULL/empty fingerprints
    # are filtered — they'd never match anyway.
    fp_q = await db.execute(
        select(TestCase.test_fingerprint)
        .distinct()
        .where(
            TestCase.test_run_id.in_(recent_run_ids),
            TestCase.test_fingerprint.is_not(None),
        )
    )
    seen_fingerprints = {row[0] for row in fp_q.all() if row[0]}

    # Now sweep active canonicals for this project. We only flip
    # ``status='active'`` rows — never touch ``deleted`` (already done),
    # ``needs_review`` (a human has to clear that), or any other lifecycle
    # value a future migration adds.
    active_q = await db.execute(
        select(CanonicalTestCase).where(
            CanonicalTestCase.project_id == project_id,
            CanonicalTestCase.status == "active",
        )
    )
    active_canonicals = list(active_q.scalars().all())

    # The "most recent run that didn't include this fingerprint" pointer
    # is just the newest run in the window — the canonical was absent
    # from all N runs, so the latest one is the one we point at.
    deleted_at_run_id = recent_run_ids[0]

    counts = {"deleted": 0, "unchanged": 0, "window_size": len(recent_run_ids)}
    for canonical in active_canonicals:
        if canonical.test_fingerprint in seen_fingerprints:
            counts["unchanged"] += 1
            continue
        canonical.status = "deleted"
        canonical.deleted_at_run_id = deleted_at_run_id
        canonical.deleted_observed_at = datetime.now(timezone.utc)
        # A new disappearance is fresh evidence. A confirmation from any
        # prior disappearance cannot retire this new observation.
        canonical.retirement_confirmed_at = None
        canonical.retirement_confirmed_by_id = None
        canonical.retirement_reason = None
        counts["deleted"] += 1

    if counts["deleted"]:
        logger.info(
            "canonical_deletion_reconciler",
            project_id=str(project_id),
            window_runs=counts["window_size"],
            deleted=counts["deleted"],
            unchanged=counts["unchanged"],
            deleted_at_run_id=str(deleted_at_run_id),
        )

    return counts


async def list_runs_for_canonical(
    db: AsyncSession,
    canonical: CanonicalTestCase,
) -> list[tuple[TestCase, "TestRun"]]:
    """Per-run rows for a canonical, newest run first, WITH their run.

    Returns ``(TestCase, TestRun)`` pairs rather than bare test cases: the
    per-test history timeline (roadmap Phase 1) has to answer "unstable *where*
    and *on what*", which needs the run's environment, branch and build — and
    the practitioner ask this view is built from is explicitly a history
    annotated with environment metadata, not a bare list of outcomes.

    The join was already here to order by run time; this just stops discarding
    the joined row.
    """
    from app.models.postgres import TestRun  # local import to avoid cycle

    stmt = (
        select(TestCase, TestRun)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.canonical_test_case_id == canonical.id)
        .order_by(TestRun.created_at.desc())
    )
    return [(row[0], row[1]) for row in (await db.execute(stmt)).all()]
