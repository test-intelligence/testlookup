"""
Test management service — CRUD + review workflow for managed test cases and
test plans.

Transaction model (item #2): every function in this module stages changes
only. The calling router handler owns ``await db.commit()`` so that a single
commit covers the business mutation, the version snapshot, the audit row,
and any downstream recomputed counts atomically.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ManagedTestCase,
    Project,
    TestCaseComment,
    TestCaseReview,
    TestCaseVersion,
    TestPlan,
    TestPlanItem,
    User,
    UserRole,
)
from app.models.schemas import (
    ManagedTestCaseCreate,
    ManagedTestCaseUpdate,
    ReviewActionRequest,
    TestCaseCommentCreate,
    TestPlanCreate,
    TestPlanItemCreate,
    TestPlanUpdate,
)
from app.routers.test_management_shared import apply_model_updates, audit_event, get_or_404, paginate_scalars


async def get_test_case_or_404(db: AsyncSession, case_id: uuid.UUID) -> ManagedTestCase:
    return cast(
        ManagedTestCase,
        await get_or_404(db, ManagedTestCase, case_id, "Test case not found"),
    )


async def list_managed_test_cases(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    page: int,
    size: int,
    status: Optional[str] = None,
    test_type: Optional[str] = None,
    priority: Optional[str] = None,
    feature_area: Optional[str] = None,
    ai_generated: Optional[bool] = None,
    search: Optional[str] = None,
    suite_name: Optional[str] = None,
):
    query = select(ManagedTestCase)
    if project_id:
        query = query.where(ManagedTestCase.project_id == project_id)
    if status:
        query = query.where(ManagedTestCase.status == status)
    else:
        query = query.where(ManagedTestCase.status != "deprecated")
    if test_type:
        query = query.where(ManagedTestCase.test_type == test_type)
    if priority:
        query = query.where(ManagedTestCase.priority == priority)
    if feature_area:
        query = query.where(ManagedTestCase.feature_area == feature_area)
    if ai_generated is not None:
        query = query.where(ManagedTestCase.ai_generated == ai_generated)
    if suite_name:
        query = query.where(ManagedTestCase.suite_name == suite_name)
    if search:
        from app.services.sql_utils import like_contains
        query = query.where(ManagedTestCase.title.ilike(like_contains(search), escape="\\"))
    return await paginate_scalars(db, query.order_by(ManagedTestCase.created_at.desc()), page, size)


async def list_automation_test_cases(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    search: Optional[str] = None,
    suite_name: Optional[str] = None,
    exclude_fingerprints: Optional[set[str]] = None,
) -> list[dict]:
    """Return synthesized ``ManagedTestCase``-shaped rows derived from per-run
    ``TestCase`` rows for a project.

    Backs the "include automation-ingested tests" toggle on
    /test-management. Dedupes by ``test_fingerprint`` (one row per logical
    test) and joins via the latest TestRun so ``last_executed_at`` /
    ``last_execution_status`` reflect the most recent run.

    The result rows are NOT inserted into ``managed_test_cases`` — they're
    serialised through ``ManagedTestCaseResponse`` for frontend display
    only. The router tags each row with ``source='automation'`` so the UI
    can render them differently from authored test cases.
    """
    from app.models.postgres import TestCase, TestRun
    from app.services.sql_utils import like_contains

    base = (
        select(
            TestCase.id,
            TestCase.test_fingerprint,
            TestCase.test_name,
            TestCase.class_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.failure_category,
            TestCase.tags,
            TestRun.created_at.label("run_created_at"),
            TestRun.id.label("run_id"),
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(TestRun.project_id == project_id)
        .where(TestCase.test_fingerprint.isnot(None))
    )
    if suite_name:
        base = base.where(TestCase.suite_name == suite_name)
    if search:
        pattern = like_contains(search)
        base = base.where(
            TestCase.test_name.ilike(pattern, escape="\\")
            | TestCase.class_name.ilike(pattern, escape="\\")
        )
    base_sq = base.subquery()

    latest = (
        select(base_sq)
        .distinct(base_sq.c.test_fingerprint)
        .order_by(base_sq.c.test_fingerprint, base_sq.c.run_created_at.desc())
        .subquery()
    )
    stmt = select(latest).order_by(latest.c.run_created_at.desc())
    rows = (await db.execute(stmt)).all()

    exclude = exclude_fingerprints or set()
    result: list[dict] = []
    for r in rows:
        if r.test_fingerprint in exclude:
            continue
        result.append({
            "id": r.id,  # per-run TestCase id; safe as a list-row key
            "project_id": project_id,
            "title": r.test_name,
            "description": None,
            "objective": None,
            "preconditions": None,
            "steps": None,
            "expected_result": None,
            "test_data": None,
            "test_type": "automation",
            "priority": "medium",
            "severity": "major",
            "feature_area": r.class_name,
            "suite_name": r.suite_name,
            "tags": r.tags,
            "status": "active",
            "version": 1,
            "author_id": None,
            "assignee_id": None,
            "reviewer_id": None,
            "is_automated": True,
            "automation_status": "automated",
            "test_fingerprint": r.test_fingerprint,
            "ai_generated": False,
            "ai_quality_score": None,
            "ai_review_notes": None,
            "estimated_duration_minutes": None,
            "last_executed_at": r.run_created_at,
            "last_execution_status": r.status,
            "created_at": r.run_created_at,
            "updated_at": r.run_created_at,
            # Source tag — frontend distinguishes automation rows from
            # authored ManagedTestCase rows (these synthesised rows aren't
            # in the managed_test_cases table).
            "source": "automation",
        })
    return result


async def create_managed_test_case(
    db: AsyncSession,
    payload: ManagedTestCaseCreate,
    current_user: User,
) -> ManagedTestCase:
    # Validate the project exists before insert — otherwise asyncpg surfaces a
    # ForeignKeyViolationError that becomes an opaque 500. This commonly hits
    # when the frontend has a stale activeProjectId in localStorage.
    project = await db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {payload.project_id} not found — refresh the page or pick a different project.",
        )
    test_case = ManagedTestCase(
        **payload.model_dump(exclude_unset=True, exclude={"change_summary"}),
        author_id=current_user.id,
        status="draft",
        version=1,
    )
    db.add(test_case)
    await db.flush()

    db.add(
        TestCaseVersion(
            test_case_id=test_case.id,
            version=1,
            title=test_case.title,
            description=test_case.description,
            steps=test_case.steps,
            expected_result=test_case.expected_result,
            status="draft",
            changed_by_id=current_user.id,
            change_summary="Initial creation",
            change_type="created",
        )
    )
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "created", current_user,
        details=f"Test case '{test_case.title}' created",
    )
    return test_case


async def update_managed_test_case(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: ManagedTestCaseUpdate,
    current_user: User,
) -> ManagedTestCase:
    test_case = await get_test_case_or_404(db, case_id)
    if test_case.status in ("deprecated",):
        raise HTTPException(status_code=400, detail="Cannot edit a deprecated test case")

    old = {"title": test_case.title, "status": test_case.status, "version": test_case.version}
    update_data = payload.model_dump(exclude_unset=True)
    apply_model_updates(test_case, update_data)
    test_case.version += 1

    db.add(
        TestCaseVersion(
            test_case_id=test_case.id,
            version=test_case.version,
            title=test_case.title,
            description=test_case.description,
            steps=test_case.steps,
            expected_result=test_case.expected_result,
            status=test_case.status,
            changed_by_id=current_user.id,
            change_summary=payload.change_summary or f"Updated to v{test_case.version}",
            change_type="updated",
        )
    )
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "updated", current_user,
        old_values=old, new_values=update_data,
    )
    return test_case


async def deprecate_managed_test_case(
    db: AsyncSession,
    case_id: uuid.UUID,
    current_user: User,
) -> None:
    test_case = await get_test_case_or_404(db, case_id)
    old_status = test_case.status
    test_case.status = "deprecated"
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "deleted", current_user,
        old_values={"status": old_status}, new_values={"status": "deprecated"},
    )


async def request_test_case_review(db: AsyncSession, case_id: uuid.UUID, current_user: User) -> TestCaseReview:
    test_case = await get_test_case_or_404(db, case_id)
    if test_case.status not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot request review from status '{test_case.status}'")
    previous_status = test_case.status
    test_case.status = "review_requested"
    review = TestCaseReview(test_case_id=case_id, requested_by_id=current_user.id, status="pending")
    db.add(review)
    await db.flush()  # materialize review.id for the handler response
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "status_changed", current_user,
        old_values={"status": previous_status}, new_values={"status": "review_requested"},
    )
    return review


async def apply_review_action(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: ReviewActionRequest,
    current_user: User,
) -> ManagedTestCase:
    if current_user.role not in (UserRole.QA_LEAD, UserRole.ADMIN, UserRole.QA_ENGINEER):
        raise HTTPException(status_code=403, detail="Insufficient permissions to review")

    test_case = await get_test_case_or_404(db, case_id)
    if test_case.status not in ("review_requested", "under_review"):
        raise HTTPException(status_code=400, detail=f"Test case is not under review (status: {test_case.status})")

    if payload.action == "approve":
        test_case.status = "approved"
        new_review_status = "approved"
    elif payload.action == "reject":
        test_case.status = "rejected"
        new_review_status = "rejected"
    elif payload.action == "request_changes":
        test_case.status = "draft"
        new_review_status = "changes_requested"
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject|request_changes")

    review_query = select(TestCaseReview).where(TestCaseReview.test_case_id == case_id).order_by(TestCaseReview.created_at.desc()).limit(1)
    review = (await db.execute(review_query)).scalars().first()
    if review:
        review.status = new_review_status
        review.reviewer_id = current_user.id
        review.human_notes = payload.notes
        review.reviewed_at = datetime.now(timezone.utc)

    await audit_event(db, "test_case", test_case.id, test_case.project_id, payload.action, current_user, details=payload.notes)
    return test_case


async def add_test_case_comment(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: TestCaseCommentCreate,
    current_user: User,
) -> TestCaseComment:
    await get_test_case_or_404(db, case_id)
    comment = TestCaseComment(
        test_case_id=case_id,
        author_id=current_user.id,
        **payload.model_dump(exclude_unset=True),
    )
    db.add(comment)
    await db.flush()  # materialize comment.id for the handler response
    return comment


async def get_plan_or_404(db: AsyncSession, plan_id: uuid.UUID) -> TestPlan:
    return cast(TestPlan, await get_or_404(db, TestPlan, plan_id, "Test plan not found"))


async def get_plan_item_or_404(db: AsyncSession, plan_id: uuid.UUID, item_id: uuid.UUID) -> TestPlanItem:
    item = cast(TestPlanItem, await get_or_404(db, TestPlanItem, item_id, "Item not found"))
    if item.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


async def recompute_plan_counts(db: AsyncSession, plan: TestPlan) -> None:
    items = (await db.execute(select(TestPlanItem).where(TestPlanItem.plan_id == plan.id))).scalars().all()
    plan.total_cases = len(items)
    plan.executed_cases = sum(1 for item in items if item.execution_status not in ("not_run",))
    plan.passed_cases = sum(1 for item in items if item.execution_status == "passed")
    plan.failed_cases = sum(1 for item in items if item.execution_status == "failed")
    plan.blocked_cases = sum(1 for item in items if item.execution_status == "blocked")


async def list_test_plans(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    page: int,
    size: int,
    status: Optional[str] = None,
):
    query = select(TestPlan)
    if project_id:
        query = query.where(TestPlan.project_id == project_id)
    if status:
        query = query.where(TestPlan.status == status)
    return await paginate_scalars(db, query.order_by(TestPlan.created_at.desc()), page, size)


async def create_test_plan(db: AsyncSession, payload: TestPlanCreate, current_user: User) -> TestPlan:
    project = await db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {payload.project_id} not found — refresh the page or pick a different project.",
        )
    plan = TestPlan(**payload.model_dump(exclude_unset=True), created_by_id=current_user.id)
    db.add(plan)
    await db.flush()  # materialize plan.id so the audit row can reference it
    await audit_event(db, "test_plan", plan.id, plan.project_id, "created", current_user, details=f"Test plan '{plan.name}' created")
    return plan


async def update_test_plan(
    db: AsyncSession,
    plan_id: uuid.UUID,
    payload: TestPlanUpdate,
    current_user: User,
) -> TestPlan:
    plan = await get_plan_or_404(db, plan_id)
    apply_model_updates(plan, payload.model_dump(exclude_unset=True))
    await audit_event(db, "test_plan", plan.id, plan.project_id, "updated", current_user)
    return plan


async def add_test_plan_item(
    db: AsyncSession,
    plan_id: uuid.UUID,
    payload: TestPlanItemCreate,
    current_user: User,
) -> TestPlanItem:
    plan = await get_plan_or_404(db, plan_id)
    item = TestPlanItem(plan_id=plan_id, **payload.model_dump(exclude_unset=True))
    db.add(item)
    await db.flush()  # materialize item.id and expose it to recompute_plan_counts
    await recompute_plan_counts(db, plan)
    return item


async def remove_test_plan_item(db: AsyncSession, plan_id: uuid.UUID, item_id: uuid.UUID) -> None:
    plan = await get_plan_or_404(db, plan_id)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    await db.delete(item)
    await recompute_plan_counts(db, plan)


async def record_test_plan_execution(
    db: AsyncSession,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    execution_status: str,
    execution_notes: Optional[str],
    actual_duration_minutes: Optional[int],
    current_user: User,
) -> TestPlanItem:
    plan = await get_plan_or_404(db, plan_id)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    item.execution_status = execution_status
    item.executed_by_id = current_user.id
    item.executed_at = datetime.now(timezone.utc)
    item.execution_notes = execution_notes
    item.actual_duration_minutes = actual_duration_minutes
    await recompute_plan_counts(db, plan)
    return item
