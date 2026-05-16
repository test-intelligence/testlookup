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
from typing import Optional

import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    Project,
    TestCase,
    TestRun,
    TestSuite,
    TestSuiteOwner,
)

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
    db.add(suite)
    try:
        await db.flush()
        await _maybe_seed_default_owner(db, project.id, suite.name)
    except Exception:
        await db.rollback()
        # Re-select after the partial unique index rejected our insert.
        result = await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == project.id,
                TestSuite.is_default.is_(True),
            )
        )
        suite = result.scalar_one()
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

        db.add(TestSuiteOwner(
            project_id=project_id,
            suite_name=suite_name,
            owner_user_id=default_qa_lead_id,
        ))
        await db.flush()
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
    db.add(suite)
    try:
        await db.flush()
        await _maybe_seed_default_owner(db, project_id, name)
    except Exception:
        await db.rollback()
        result = await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == project_id,
                TestSuite.name == name,
            )
        )
        suite = result.scalar_one()
    return suite


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
            db.add(canonical)
            try:
                await db.flush()
            except Exception:
                await db.rollback()
                # Lost the race to a concurrent upsert — re-select.
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
            if tc.test_name and canonical.test_name != tc.test_name:
                canonical.test_name = tc.test_name
            if canonical.class_name != tc.class_name:
                canonical.class_name = tc.class_name
            counts["updated"] += 1

        if tc.canonical_test_case_id != canonical.id:
            tc.canonical_test_case_id = canonical.id
            counts["linked"] += 1

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
        """).bindparams(pids=tuple(project_ids))
        try:
            missing_rows = (await db.execute(missing_query)).fetchall()
        except Exception as exc:
            # An older deployment / migration mismatch shouldn't 500
            # /suites — degrade by skipping the backfill.
            logger.warning("test_suites backfill probe failed, skipping",
                           error=str(exc))
            await db.rollback()
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
                    # Concurrent backfill from another request — rollback
                    # the pending insert and refetch the now-present row
                    # so the caller still sees it in this response.
                    await db.rollback()
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
    db.add(suite)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
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
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
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
            TestCase.suite_name == suite.name,
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


async def list_runs_for_canonical(
    db: AsyncSession,
    canonical: CanonicalTestCase,
) -> list[TestCase]:
    """All per-run TestCase rows linked to a canonical, newest run first."""
    from app.models.postgres import TestRun  # local import to avoid cycle

    stmt = (
        select(TestCase)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.canonical_test_case_id == canonical.id)
        .order_by(TestRun.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())
