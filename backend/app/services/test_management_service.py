from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ManagedTestCase,
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
    if search:
        query = query.where(ManagedTestCase.title.ilike(f"%{search}%"))
    return await paginate_scalars(db, query.order_by(ManagedTestCase.created_at.desc()), page, size)


async def create_managed_test_case(
    db: AsyncSession,
    payload: ManagedTestCaseCreate,
    current_user: User,
) -> ManagedTestCase:
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
    await db.commit()
    await db.refresh(test_case)
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
    await db.commit()
    await db.refresh(test_case)
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
    await db.commit()


async def request_test_case_review(db: AsyncSession, case_id: uuid.UUID, current_user: User) -> TestCaseReview:
    test_case = await get_test_case_or_404(db, case_id)
    if test_case.status not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot request review from status '{test_case.status}'")
    previous_status = test_case.status
    test_case.status = "review_requested"
    review = TestCaseReview(test_case_id=case_id, requested_by_id=current_user.id, status="pending")
    db.add(review)
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "status_changed", current_user,
        old_values={"status": previous_status}, new_values={"status": "review_requested"},
    )
    await db.commit()
    await db.refresh(review)
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
    await db.commit()
    await db.refresh(test_case)
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
    await db.commit()
    await db.refresh(comment)
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
    plan = TestPlan(**payload.model_dump(exclude_unset=True), created_by_id=current_user.id)
    db.add(plan)
    await db.flush()
    await audit_event(db, "test_plan", plan.id, plan.project_id, "created", current_user, details=f"Test plan '{plan.name}' created")
    await db.commit()
    await db.refresh(plan)
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
    await db.commit()
    await db.refresh(plan)
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
    await db.flush()
    await recompute_plan_counts(db, plan)
    await db.commit()
    await db.refresh(item)
    return item


async def remove_test_plan_item(db: AsyncSession, plan_id: uuid.UUID, item_id: uuid.UUID) -> None:
    plan = await get_plan_or_404(db, plan_id)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    await db.delete(item)
    await recompute_plan_counts(db, plan)
    await db.commit()


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
    await db.commit()
    await db.refresh(item)
    return item
