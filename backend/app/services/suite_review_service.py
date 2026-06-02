"""Suite owner resolution + per-(run, suite) review overlay (migration 0076).

Non-gating layer on top of AI analysis. The pipeline still finalizes runs
without waiting for a review; these records just capture the suite owner's
verdict for downstream display + audit.

Resolution rule for *who owns a suite* (since migration 0079):

    explicit ``test_suite_owners`` row
      → ``Project.default_qa_lead_user_id``    (the configured default QA lead)
      → ``Project.manager_user_id``            (legacy fallback, kept for B/C)
      → None

``is_fallback=True`` whenever the resolution didn't come from the explicit
``test_suite_owners`` row, so the UI can render the distinction.

Assignment enforcement: ``set_suite_owner`` rejects non-QA_LEAD users (returns
HTTP 400) unless the candidate is an instance ADMIN. See
``assert_user_is_qa_lead_on_project`` below for the exact rule. ADMIN bypass
exists so an instance admin can backstop an empty project (no QA_LEAD members
yet) without first promoting themselves into ProjectMember.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    SUITE_REVIEW_STATES,
    Project,
    ProjectMember,
    SuiteRunReview,
    TestSuiteOwner,
    User,
    UserRole,
)

logger = structlog.get_logger(__name__)


# ── role validation ───────────────────────────────────────────────────────


async def assert_user_is_qa_lead_on_project(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Raise HTTP 400 unless ``user_id`` is permitted to own suites in
    ``project_id``.

    Allowed:
      * Instance ADMIN (``User.role = 'ADMIN'``) — backstop for empty
        projects, mirrors the existing pattern in ``require_role``.
      * Project member with ``ProjectMember.role = 'QA_LEAD'``.
      * The project's own ``default_qa_lead_user_id`` (covers the
        bootstrap case where the row was set by an admin before the
        ProjectMember row exists).

    Anything else returns 400 so the caller renders a precise error.
    Raises 400 for unknown user too — callers should not pass dangling
    references.
    """
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found",
        )

    # Instance ADMIN bypass.
    if str(user.role) == UserRole.ADMIN.value or user.role == UserRole.ADMIN:
        return

    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.user_id == user_id,
                ProjectMember.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if member and (
        member.role == UserRole.QA_LEAD
        or str(member.role) == UserRole.QA_LEAD.value
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"User must have role QA_LEAD on project {project_id} to be "
            "assigned as a suite owner. Add them as a project member with "
            "role QA_LEAD first."
        ),
    )


# ── owner resolution ──────────────────────────────────────────────────────


async def resolve_suite_owner(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: str,
) -> tuple[Optional[User], bool]:
    """Return ``(owner_user, is_fallback)``.

    Resolution order (migration 0079):
      1. explicit ``test_suite_owners`` row → ``is_fallback=False``
      2. ``Project.default_qa_lead_user_id`` → ``is_fallback=True``
      3. ``Project.manager_user_id`` (legacy) → ``is_fallback=True``
      4. (None, False)
    """
    explicit = (
        await db.execute(
            select(TestSuiteOwner).where(
                TestSuiteOwner.project_id == project_id,
                TestSuiteOwner.suite_name == suite_name,
            )
        )
    ).scalar_one_or_none()

    if explicit and explicit.owner_user_id:
        user = (
            await db.execute(select(User).where(User.id == explicit.owner_user_id))
        ).scalar_one_or_none()
        if user:
            return user, False

    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        return None, False

    # Default QA lead is the preferred fallback (post-0079).
    if project.default_qa_lead_user_id:
        user = (
            await db.execute(select(User).where(User.id == project.default_qa_lead_user_id))
        ).scalar_one_or_none()
        if user:
            return user, True

    # Legacy manager_user_id fallback — kept so existing deployments don't
    # lose their resolved owners when default_qa_lead_user_id is still NULL.
    if project.manager_user_id:
        user = (
            await db.execute(select(User).where(User.id == project.manager_user_id))
        ).scalar_one_or_none()
        if user:
            return user, True

    return None, False


async def list_suite_owners(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_names: list[str],
) -> dict[str, dict]:
    """Bulk-resolve owners for a list of suites in one project.

    Returns ``{suite_name: {owner_user_id, owner_email, owner_full_name,
    is_fallback}}``. Suites not in the result dict have no resolved owner.
    """
    if not suite_names:
        return {}

    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()

    explicit_rows = (
        await db.execute(
            select(TestSuiteOwner).where(
                TestSuiteOwner.project_id == project_id,
                TestSuiteOwner.suite_name.in_(suite_names),
            )
        )
    ).scalars().all()
    explicit_by_suite = {row.suite_name: row.owner_user_id for row in explicit_rows}

    needed_user_ids: set[uuid.UUID] = set()
    for owner_id in explicit_by_suite.values():
        if owner_id:
            needed_user_ids.add(owner_id)
    # 0079: prefer the configured default QA lead; legacy manager_user_id is
    # the second-tier fallback so older deployments don't regress.
    fallback_id = None
    if project:
        fallback_id = project.default_qa_lead_user_id or project.manager_user_id
    if fallback_id:
        needed_user_ids.add(fallback_id)

    users_by_id: dict[uuid.UUID, User] = {}
    if needed_user_ids:
        user_rows = (
            await db.execute(select(User).where(User.id.in_(needed_user_ids)))
        ).scalars().all()
        users_by_id = {u.id: u for u in user_rows}

    result: dict[str, dict] = {}
    for suite_name in suite_names:
        explicit_owner_id = explicit_by_suite.get(suite_name)
        owner_id = explicit_owner_id or fallback_id
        is_fallback = explicit_owner_id is None and fallback_id is not None
        user = users_by_id.get(owner_id) if owner_id else None
        result[suite_name] = {
            "owner_user_id": owner_id,
            "owner_email": user.email if user else None,
            "owner_full_name": user.full_name if user else None,
            "is_fallback": is_fallback,
        }
    return result


async def set_suite_owner(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: str,
    owner_user_id: Optional[uuid.UUID],
) -> TestSuiteOwner:
    """Upsert the explicit owner row. ``owner_user_id=None`` clears it.

    If the caller passes None and a row exists, the row is deleted so the
    default-QA-lead / manager fallback kicks in cleanly.

    When ``owner_user_id`` is non-None, the candidate is validated against
    the project's QA_LEAD members (or instance ADMIN) before the write.
    Non-QA_LEAD assignment raises HTTP 400.
    """
    if owner_user_id is not None:
        await assert_user_is_qa_lead_on_project(db, owner_user_id, project_id)
    existing = (
        await db.execute(
            select(TestSuiteOwner).where(
                TestSuiteOwner.project_id == project_id,
                TestSuiteOwner.suite_name == suite_name,
            )
        )
    ).scalar_one_or_none()

    # Stage-only: ``get_db`` commits at request-scope exit, so the service
    # only ``add`` / ``delete`` / mutate. A ``flush`` is needed so the PK +
    # defaults are populated on the returned row (the caller may serialize
    # it before commit happens).
    if owner_user_id is None:
        if existing:
            await db.delete(existing)
            await db.flush()
            logger.info(
                "suite_owner_cleared",
                project_id=str(project_id),
                suite_name=suite_name,
            )
        return existing  # type: ignore[return-value]

    if existing:
        existing.owner_user_id = owner_user_id
        await db.flush()
        return existing

    row = TestSuiteOwner(
        project_id=project_id,
        suite_name=suite_name,
        owner_user_id=owner_user_id,
    )
    db.add(row)
    await db.flush()
    logger.info(
        "suite_owner_assigned",
        project_id=str(project_id),
        suite_name=suite_name,
        owner_user_id=str(owner_user_id),
    )
    return row


# ── reviews ───────────────────────────────────────────────────────────────


async def get_or_create_review(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: str,
    test_run_id: uuid.UUID,
) -> SuiteRunReview:
    """Return the existing review for (run, suite) or create a pending one."""
    existing = (
        await db.execute(
            select(SuiteRunReview).where(
                SuiteRunReview.test_run_id == test_run_id,
                SuiteRunReview.suite_name == suite_name,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    row = SuiteRunReview(
        project_id=project_id,
        suite_name=suite_name,
        test_run_id=test_run_id,
        state="pending",
    )
    # Two reviewers (or a double-clicked submit) can race the SELECT above for
    # the same (test_run_id, suite_name); the unique index
    # ``uq_suite_run_reviews_run_suite`` would then turn the loser's INSERT
    # into an IntegrityError that, on the injected request session, escapes to
    # ``get_db`` as a 500 and drops that verdict. Guard the INSERT in a
    # SAVEPOINT so only the failed INSERT unwinds, then re-select the winner.
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return (
            await db.execute(
                select(SuiteRunReview).where(
                    SuiteRunReview.test_run_id == test_run_id,
                    SuiteRunReview.suite_name == suite_name,
                )
            )
        ).scalar_one()
    # Materialise server-side defaults (created_at / updated_at) so callers
    # that serialize the row before the request commits don't trigger a
    # sync lazy-load on the expired columns — that path raises
    # ``sqlalchemy.exc.MissingGreenlet`` inside the async session.
    await db.refresh(row)
    return row


async def update_review(
    db: AsyncSession,
    review_id: uuid.UUID,
    state: str,
    note: Optional[str],
    reviewer_user_id: uuid.UUID,
) -> SuiteRunReview:
    if state not in SUITE_REVIEW_STATES:
        raise ValueError(f"invalid review state: {state!r}")

    review = (
        await db.execute(select(SuiteRunReview).where(SuiteRunReview.id == review_id))
    ).scalar_one_or_none()
    if not review:
        raise LookupError("review not found")

    review.state = state
    review.note = note
    review.reviewer_user_id = reviewer_user_id
    review.reviewed_at = (
        datetime.now(timezone.utc) if state != "pending" else None
    )
    await db.flush()
    # The ``updated_at`` column is recomputed server-side via
    # ``onupdate=func.now()`` and gets expired after flush. Refresh so a
    # subsequent attribute read (e.g. Pydantic response serialization
    # before the request commits) doesn't fire a sync lazy-load and raise
    # ``MissingGreenlet``.
    await db.refresh(review)
    logger.info(
        "suite_review_updated",
        review_id=str(review.id),
        suite_name=review.suite_name,
        test_run_id=str(review.test_run_id),
        state=state,
        reviewer_user_id=str(reviewer_user_id),
    )
    return review


async def list_reviews(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    suite_name: Optional[str] = None,
    state: Optional[str] = None,
    test_run_id: Optional[uuid.UUID] = None,
    reviewer_user_id: Optional[uuid.UUID] = None,
    limit: int = 200,
) -> list[SuiteRunReview]:
    stmt = select(SuiteRunReview).where(SuiteRunReview.project_id == project_id)
    if suite_name:
        stmt = stmt.where(SuiteRunReview.suite_name == suite_name)
    if state:
        stmt = stmt.where(SuiteRunReview.state == state)
    if test_run_id:
        stmt = stmt.where(SuiteRunReview.test_run_id == test_run_id)
    if reviewer_user_id:
        stmt = stmt.where(SuiteRunReview.reviewer_user_id == reviewer_user_id)
    stmt = stmt.order_by(SuiteRunReview.updated_at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()


async def enrich_reviewers(
    db: AsyncSession, reviews: list[SuiteRunReview]
) -> dict[uuid.UUID, str]:
    """Return ``{reviewer_user_id: email}`` for the reviewers in ``reviews``."""
    ids = {r.reviewer_user_id for r in reviews if r.reviewer_user_id}
    if not ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.email).where(User.id.in_(ids)))
    ).all()
    return {row.id: row.email for row in rows}


# ── Test → suite → owner resolution (notify-owner flow) ────────────────────


async def resolve_test_to_suite_owner(
    db: AsyncSession,
    project_id: uuid.UUID,
    test_name: str,
    days: int,
) -> dict:
    """For a given (project, test_name, window), return everything the
    notify-owner flow needs:

    ``{
        suite_name: str | None,
        owner: User | None,
        is_fallback: bool,
        latest_run_id: UUID | None,
        latest_run_build: str | None,
        latest_failed_test_case_id: UUID | None,
    }``

    Picks the most recent failing TestCase row whose test_name matches in the
    window, reads its suite, and resolves the owner via the
    explicit-row-then-project-manager chain. ``suite_name=None`` when the
    test didn't fail in the window (or doesn't exist).
    """
    from datetime import datetime, timedelta, timezone

    from app.models.postgres import TestCase, TestRun, TestStatus

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    stmt = (
        select(
            TestCase.id,
            TestCase.suite_name,
            TestRun.id.label("run_id"),
            TestRun.build_number,
            TestRun.created_at,
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(
            TestRun.project_id == project_id,
            TestRun.created_at >= cutoff,
            TestCase.test_name == test_name,
            TestCase.status.in_([TestStatus.FAILED.value, TestStatus.BROKEN.value]),
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return {
            "suite_name": None,
            "owner": None,
            "is_fallback": False,
            "latest_run_id": None,
            "latest_run_build": None,
            "latest_failed_test_case_id": None,
        }

    suite_name = row.suite_name or None
    owner: Optional[User] = None
    is_fallback = False
    if suite_name:
        owner, is_fallback = await resolve_suite_owner(db, project_id, suite_name)
    if owner is None:
        # No explicit suite owner — fall back to project manager directly so
        # the email still has somewhere to land for tests without suite tags.
        from app.models.postgres import Project

        project = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project and project.manager_user_id:
            owner = (
                await db.execute(
                    select(User).where(User.id == project.manager_user_id)
                )
            ).scalar_one_or_none()
            is_fallback = owner is not None

    return {
        "suite_name": suite_name,
        "owner": owner,
        "is_fallback": is_fallback,
        "latest_run_id": row.run_id,
        "latest_run_build": str(row.build_number) if row.build_number else None,
        "latest_failed_test_case_id": row.id,
    }
