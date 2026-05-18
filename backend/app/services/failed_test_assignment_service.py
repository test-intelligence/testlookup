"""Auto-assign failed test cases to suite owners at ingest time (migration 0080).

When a test run is finalised, every TestCase whose status is ``FAILED`` or
``BROKEN`` is assigned to the resolved owner of its suite. The resolution
chain mirrors ``suite_review_service.resolve_suite_owner``:

    TestSuiteOwner row for the suite
      → ``Project.default_qa_lead_user_id`` (the default QA lead)
      → ``Project.manager_user_id``        (legacy fallback)
      → NULL                                (unassigned; admin fix needed)

Why a separate module: the assignment runs as one of several isolated steps
in ``finalize_run`` (see ``ingestion_pipeline._run_isolated``). Keeping the
batch resolution + UPDATE logic out of ``suite_review_service`` keeps that
service focused on per-request owner mutations and reviews. This module is
read-mostly + one batch UPDATE per run.

Idempotency: only writes to ``TestCase.assigned_to_user_id`` rows where the
value is currently NULL. A re-run of ``finalize_run`` (e.g. live-stream
recovery) will not overwrite a human reassignment that happened between
runs. The ingest pipeline runs each step in its own session and swallows
failures, so a hiccup here cannot break the broader run finalisation.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Project,
    ProjectMember,
    TestCase,
    TestRun,
    TestStatus,
    TestSuiteOwner,
    UserRole,
)

logger = structlog.get_logger(__name__)

# Statuses considered "needs action" — assign these to the suite owner.
# UNKNOWN deliberately excluded: it's the ingestion default for cases the
# parser couldn't classify; treating it as "failed" would generate spurious
# action items.
ACTIONABLE_STATUSES = ("FAILED", "BROKEN")

# Project-member fallback role priority. When neither
# ``Project.default_qa_lead_user_id`` nor ``Project.manager_user_id`` is
# configured, pick from project members with these roles. QA_LEAD first
# (the role authorised to triage); ADMIN only as the last resort so a
# project with no QA Lead at all still gets failures into someone's
# inbox.
_FALLBACK_MEMBER_ROLES = (UserRole.QA_LEAD.value, UserRole.ADMIN.value)


async def _resolve_qa_lead_pool(
    db: AsyncSession, project_id: uuid.UUID,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Return ``(qa_leads, admins)`` for the project — both lists ordered
    by ``ProjectMember.created_at ASC`` so the hash-mod-N distribution
    below is deterministic across runs.

    Used as the SECONDARY assignment surface when neither an explicit
    ``TestSuiteOwner`` row nor the suite's configured owner matches.
    The user's stated requirement is "failures should be assigned to
    all users with QA Lead roles for the project" — i.e. spread, don't
    concentrate.
    """
    result = await db.execute(
        select(ProjectMember.user_id, ProjectMember.role)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.role.in_(_FALLBACK_MEMBER_ROLES),
        )
        .order_by(ProjectMember.created_at.asc())
    )
    rows = list(result.all())
    qa_leads = [r.user_id for r in rows if r.role == UserRole.QA_LEAD.value]
    admins = [r.user_id for r in rows if r.role == UserRole.ADMIN.value]
    return qa_leads, admins


def _pick_pool_member(
    pool: list[uuid.UUID],
    fingerprint: Optional[str],
    salt: str = "",
) -> Optional[uuid.UUID]:
    """Deterministically pick one user from ``pool`` based on the test's
    fingerprint. Same fingerprint always lands with the same person so
    repeat failures don't get spread across multiple inboxes.

    ``salt`` lets the caller bias the bucket selection (e.g. project_id)
    so the same fingerprint in two different projects still distributes
    independently.
    """
    if not pool:
        return None
    if not fingerprint:
        # Pre-fingerprint legacy rows — fall back to the first member.
        return pool[0]
    digest = hashlib.md5(f"{salt}:{fingerprint}".encode()).hexdigest()
    idx = int(digest, 16) % len(pool)
    return pool[idx]


async def assign_failed_tests_to_suite_owners(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[str, int]:
    """Assign every actionable failure in ``run_id`` to a triager.

    Resolution per failure:
      1. Explicit ``TestSuiteOwner`` row for the (project, suite_name).
      2. Otherwise distribute across the **project's QA Lead pool** via
         a deterministic hash of ``test_fingerprint`` — repeat failures
         of the same test always land with the same person, but the
         pool gets spread evenly so no single QA Lead's inbox
         monopolises every failure. The pool is built from project
         members with role ``QA_LEAD``; ``Project.default_qa_lead_user_id``
         is folded into the pool (deduplicated) so a configured
         default is still represented but never monopolises.
      3. If no QA_LEAD members exist, fall back to
         ``Project.manager_user_id`` (legacy field), then to the
         project's ADMIN pool (same hash distribution).
      4. Unassigned — left NULL.

    The function:
      * Fetches all FAILED/BROKEN test cases in the run.
      * Batch-fetches every ``TestSuiteOwner`` for the involved suites in one
        round trip.
      * Buckets failures by resolved owner so writes are batched.
      * Writes only to rows currently NULL so human reassignments are
        preserved.

    Returns ``{assigned, already_assigned, unassigned}`` for observability.
    """
    counts = {"assigned": 0, "already_assigned": 0, "unassigned": 0}

    # 1. Fetch failed/broken cases for this run. ``test_fingerprint`` is
    # the stable identity that the pool-distribution hash keys off of;
    # ``suite_name`` may be NULL for cases that landed in the default
    # suite — handle those via the default-suite fallback below.
    failures_result = await db.execute(
        select(
            TestCase.id,
            TestCase.suite_name,
            TestCase.assigned_to_user_id,
            TestCase.test_fingerprint,
        )
        .where(
            TestCase.test_run_id == run_id,
            TestCase.status.in_(ACTIONABLE_STATUSES),
        )
    )
    failures = list(failures_result.all())
    if not failures:
        return counts

    # 2. Resolve the project-level fields + QA Lead pool. Pool is built
    # from QA_LEAD members + the configured default (if any) so a
    # default QA lead who isn't yet a project member still receives
    # their share — useful during the brief window after a project is
    # created and before membership is fully provisioned.
    project_row = (
        await db.execute(
            select(Project.default_qa_lead_user_id, Project.manager_user_id)
            .where(Project.id == project_id)
        )
    ).first()
    default_qa_lead_id: Optional[uuid.UUID] = (
        project_row[0] if project_row else None
    )
    manager_id: Optional[uuid.UUID] = project_row[1] if project_row else None

    qa_lead_pool, admin_pool = await _resolve_qa_lead_pool(db, project_id)
    if default_qa_lead_id and default_qa_lead_id not in qa_lead_pool:
        # Honour the configured default even if it isn't a member row yet.
        qa_lead_pool = [default_qa_lead_id, *qa_lead_pool]

    # 3. Resolve the default-suite name so cases with NULL suite_name (rare
    # post-Phase 2 but still possible from legacy ingest paths) map to its
    # owner correctly. ``get_or_create_default_suite`` is idempotent and
    # cheap to call here.
    suite_names_in_run = {f.suite_name for f in failures if f.suite_name}
    needs_default = any(f.suite_name in (None, "") for f in failures)
    default_suite_name: Optional[str] = None
    if needs_default:
        from app.services.test_suite_service import get_or_create_default_suite
        project_obj = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project_obj is not None:
            default_suite = await get_or_create_default_suite(db, project_obj)
            default_suite_name = default_suite.name
            suite_names_in_run.add(default_suite_name)

    # 4. Batch-fetch explicit owner rows for every suite involved.
    owner_by_suite: dict[str, uuid.UUID] = {}
    if suite_names_in_run:
        owner_rows = (
            await db.execute(
                select(TestSuiteOwner.suite_name, TestSuiteOwner.owner_user_id)
                .where(
                    TestSuiteOwner.project_id == project_id,
                    TestSuiteOwner.suite_name.in_(suite_names_in_run),
                )
            )
        ).all()
        for row in owner_rows:
            if row.owner_user_id is not None:
                owner_by_suite[row.suite_name] = row.owner_user_id

    # 5. Bucket the failure ids by resolved owner. Each failure
    # independently resolves its owner via the chain documented in the
    # docstring — explicit suite-owner row > QA-Lead-pool distribution
    # > manager fallback > ADMIN-pool distribution > NULL. WRITES are
    # batched: one UPDATE per (resolved owner) bucket below.
    salt = str(project_id)
    by_owner: dict[uuid.UUID, list[uuid.UUID]] = {}
    for f in failures:
        if f.assigned_to_user_id is not None:
            counts["already_assigned"] += 1
            continue
        suite_key = f.suite_name or default_suite_name
        owner_id: Optional[uuid.UUID] = (
            owner_by_suite.get(suite_key) if suite_key else None
        )
        if owner_id is None and qa_lead_pool:
            owner_id = _pick_pool_member(qa_lead_pool, f.test_fingerprint, salt)
        if owner_id is None and manager_id is not None:
            owner_id = manager_id
        if owner_id is None and admin_pool:
            owner_id = _pick_pool_member(admin_pool, f.test_fingerprint, salt)
        if owner_id is None:
            counts["unassigned"] += 1
            continue
        by_owner.setdefault(owner_id, []).append(f.id)

    # 6. One UPDATE per owner bucket. The ``assigned_to_user_id IS NULL``
    # guard is redundant given step 5's filter but kept for safety in case
    # of concurrent writes from a parallel pipeline.
    for owner_id, ids in by_owner.items():
        await db.execute(
            update(TestCase)
            .where(
                TestCase.id.in_(ids),
                TestCase.assigned_to_user_id.is_(None),
            )
            .values(assigned_to_user_id=owner_id)
        )
        counts["assigned"] += len(ids)

    logger.info(
        "failed_tests_assigned",
        project_id=str(project_id),
        run_id=str(run_id),
        assigned=counts["assigned"],
        already_assigned=counts["already_assigned"],
        unassigned=counts["unassigned"],
        distinct_owners=len(by_owner),
        qa_lead_pool_size=len(qa_lead_pool),
        admin_pool_size=len(admin_pool),
    )
    return counts


async def backfill_unassigned_failures(
    db: AsyncSession,
    project_id: Optional[uuid.UUID] = None,
    max_runs: int = 200,
) -> dict[str, int]:
    """Retroactively assign FAILED/BROKEN TestCases left unassigned by earlier
    ingests (e.g., before the project-member fallback shipped, or before a
    project had any owner config).

    Walks the most recent ``max_runs`` runs that still contain at least one
    actionable TestCase with ``assigned_to_user_id IS NULL`` and re-runs the
    per-run resolver. Idempotent because the resolver itself only writes to
    rows currently NULL.
    """
    stmt = (
        select(TestRun.id, TestRun.project_id)
        .join(TestCase, TestCase.test_run_id == TestRun.id)
        .where(
            TestCase.assigned_to_user_id.is_(None),
            TestCase.status.in_(_actionable_status_values()),
        )
    )
    if project_id is not None:
        stmt = stmt.where(TestRun.project_id == project_id)
    stmt = stmt.group_by(TestRun.id, TestRun.project_id).order_by(
        TestRun.created_at.desc()
    ).limit(max_runs)

    pairs = list((await db.execute(stmt)).all())
    totals = {"runs": 0, "assigned": 0, "unassigned": 0}
    for run_id, pid in pairs:
        counts = await assign_failed_tests_to_suite_owners(db, pid, run_id)
        totals["runs"] += 1
        totals["assigned"] += counts.get("assigned", 0)
        totals["unassigned"] += counts.get("unassigned", 0)
    logger.info(
        "failed_tests_backfill_complete",
        project_id=str(project_id) if project_id else None,
        **totals,
    )
    return totals


def _actionable_status_values() -> tuple[str, ...]:
    """Materialise the actionable status values for ``IN (...)`` filters
    (TestCase.status is a String(20) column, not the enum). Kept as a
    helper to avoid drifting from ACTIONABLE_STATUSES.
    """
    return tuple(s.value if isinstance(s, TestStatus) else s for s in ACTIONABLE_STATUSES)
