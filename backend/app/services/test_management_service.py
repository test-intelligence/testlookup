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
import structlog
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.run_compare_service import normalize_suite_name
from app.models.postgres import (
    ManagedTestCase,
    Project,
    TestCaseComment,
    TestCaseReview,
    TestPlan,
    TestPlanItem,
    TestCaseLifecycleState,
    User,
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
from app.routers.test_management_shared import apply_model_updates, get_or_404, paginate_scalars
from app.services.test_management_audit_service import audit_event
from app.services.test_management_metrics_service import stage_test_management_counter
from app.services.test_case_lifecycle_service import (
    LifecycleAction,
    auto_claim_and_decide,
    stage_test_case_snapshot,
    transition,
)

logger = structlog.get_logger(__name__)


async def get_test_case_or_404(
    db: AsyncSession,
    case_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ManagedTestCase:
    if for_update:
        test_case = (
            await db.execute(
                select(ManagedTestCase)
                .where(ManagedTestCase.id == case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if test_case is None:
            raise HTTPException(status_code=404, detail="Test case not found")
        return test_case
    return cast(
        ManagedTestCase,
        await get_or_404(db, ManagedTestCase, case_id, "Test case not found"),
    )


async def list_managed_test_cases(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    page: int,
    size: int,
    status: Optional[TestCaseLifecycleState | str] = None,
    include_archived: bool = False,
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
        status_value = status.value if isinstance(status, TestCaseLifecycleState) else status
        query = query.where(ManagedTestCase.status == status_value)
    elif include_archived:
        query = query.where(ManagedTestCase.status != TestCaseLifecycleState.DEPRECATED.value)
    else:
        query = query.where(
            ManagedTestCase.status.notin_(
                [
                    TestCaseLifecycleState.DEPRECATED.value,
                    TestCaseLifecycleState.ARCHIVED.value,
                ]
            )
        )
    if test_type:
        query = query.where(ManagedTestCase.test_type == test_type)
    if priority:
        query = query.where(ManagedTestCase.priority == priority)
    if feature_area:
        query = query.where(ManagedTestCase.feature_area == feature_area)
    if ai_generated is not None:
        query = query.where(ManagedTestCase.ai_generated == ai_generated)
    if suite_name:
        # Case-insensitive, like every other suite filter in the product.
        # A raw ``==`` made this the odd one out: measured live, suite "api"
        # returned 5 rows here while "API" returned 0, where both
        # ``analytics/coverage`` and ``runs/compare`` returned 5 for either
        # spelling. See ``run_compare_service.normalize_suite_name``.
        query = query.where(
            func.lower(func.trim(ManagedTestCase.suite_name))
            == normalize_suite_name(suite_name)
        )
    if search:
        from app.services.sql_utils import like_contains
        query = query.where(ManagedTestCase.title.ilike(like_contains(search), escape="\\"))
    return await paginate_scalars(db, query.order_by(ManagedTestCase.created_at.desc()), page, size)


async def list_managed_test_case_fingerprints(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
) -> set[tuple[uuid.UUID, str]]:
    """Return project-scoped identities independent of authored-list filters."""
    stmt = select(ManagedTestCase.project_id, ManagedTestCase.test_fingerprint).where(
        ManagedTestCase.test_fingerprint.is_not(None)
    )
    if project_id is not None:
        stmt = stmt.where(ManagedTestCase.project_id == project_id)
    result = await db.execute(stmt)
    return {
        (row_project_id, fingerprint)
        for row_project_id, fingerprint in result.all()
        if fingerprint
    }


async def list_combined_test_case_identities(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    *,
    include_archived: bool,
    test_type: Optional[str],
    search: Optional[str],
    suite_name: Optional[str],
    page: int,
    size: int,
) -> tuple[list[tuple[str, uuid.UUID]], int, int]:
    """Page managed and latest automation identities in one bounded SQL query."""
    managed = select(
        literal("managed").label("source"),
        ManagedTestCase.id.label("entity_id"),
        func.coalesce(
            ManagedTestCase.last_executed_at,
            ManagedTestCase.created_at,
        ).label("recency"),
    )
    if project_id is not None:
        managed = managed.where(ManagedTestCase.project_id == project_id)
    excluded_states = [TestCaseLifecycleState.DEPRECATED.value]
    if not include_archived:
        excluded_states.append(TestCaseLifecycleState.ARCHIVED.value)
    managed = managed.where(ManagedTestCase.status.notin_(excluded_states))
    if test_type:
        managed = managed.where(ManagedTestCase.test_type == test_type)
    if suite_name:
        managed = managed.where(
            func.lower(func.trim(ManagedTestCase.suite_name))
            == normalize_suite_name(suite_name)
        )
    if search:
        from app.services.sql_utils import like_contains

        managed = managed.where(
            ManagedTestCase.title.ilike(like_contains(search), escape="\\")
        )

    sources = [managed]
    if not test_type or test_type.lower() == "automation":
        from app.models.postgres import TestCase, TestRun
        from app.services.sql_utils import like_contains

        automation_base = select(
            TestCase.id.label("entity_id"),
            TestCase.test_fingerprint.label("fingerprint"),
            TestRun.project_id.label("project_id"),
            TestRun.created_at.label("recency"),
            func.row_number().over(
                partition_by=(TestRun.project_id, TestCase.test_fingerprint),
                order_by=(TestRun.created_at.desc(), TestCase.id.asc()),
            ).label("row_number"),
        ).join(TestRun, TestCase.test_run_id == TestRun.id).where(
            TestCase.test_fingerprint.is_not(None)
        )
        if project_id is not None:
            automation_base = automation_base.where(TestRun.project_id == project_id)
        if suite_name:
            suite_key = normalize_suite_name(suite_name)
            automation_base = automation_base.where(
                or_(
                    func.lower(func.trim(TestCase.suite_name)) == suite_key,
                    and_(
                        TestRun.trigger_source == "live_stream",
                        func.lower(func.trim(TestRun.primary_suite_name)) == suite_key,
                    ),
                )
            )
        if search:
            pattern = like_contains(search)
            automation_base = automation_base.where(
                TestCase.test_name.ilike(pattern, escape="\\")
                | TestCase.class_name.ilike(pattern, escape="\\")
            )
        latest = automation_base.subquery()
        linked = select(ManagedTestCase.id).where(
            ManagedTestCase.project_id == latest.c.project_id,
            ManagedTestCase.test_fingerprint == latest.c.fingerprint,
        ).exists()
        sources.append(
            select(
                literal("automation").label("source"),
                latest.c.entity_id,
                latest.c.recency,
            ).where(latest.c.row_number == 1, ~linked)
        )

    combined = union_all(*sources).subquery() if len(sources) > 1 else managed.subquery()
    total = int(
        (await db.execute(select(func.count()).select_from(combined))).scalar() or 0
    )
    rows = (
        await db.execute(
            select(combined.c.source, combined.c.entity_id)
            .order_by(combined.c.recency.desc(), combined.c.entity_id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return [(source, entity_id) for source, entity_id in rows], total, -(-total // size)


async def list_automation_test_cases(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    *,
    search: Optional[str] = None,
    suite_name: Optional[str] = None,
    exclude_fingerprints: Optional[set[str] | set[tuple[uuid.UUID, str]]] = None,
    case_ids: Optional[set[uuid.UUID]] = None,
) -> list[dict]:
    """Return synthesized ``ManagedTestCase``-shaped rows derived from per-run
    ``TestCase`` rows.

    Backs the "include automation-ingested tests" toggle on
    /test-management. Dedupes by ``test_fingerprint`` (one row per logical
    test) and joins via the latest TestRun so ``last_executed_at`` /
    ``last_execution_status`` reflect the most recent run.

    When ``project_id`` is ``None`` the project filter is dropped and rows
    span every project — this is the All-Projects path and is only safe
    when the caller has already enforced admin gating (the router does this
    via ``get_accessible_project_ids`` before reaching the merge path).
    Each synthesised row reports its own ``project_id`` from the joined
    ``TestRun`` so the frontend can route mutations correctly.

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
            TestCase.owner,
            TestCase.canonical_test_case_id,
            TestCase.failure_category,
            TestCase.tags,
            TestRun.created_at.label("run_created_at"),
            TestRun.id.label("run_id"),
            TestRun.project_id.label("run_project_id"),
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(TestCase.test_fingerprint.isnot(None))
    )
    if project_id is not None:
        base = base.where(TestRun.project_id == project_id)
    if case_ids is not None:
        if not case_ids:
            return []
        base = base.where(TestCase.id.in_(case_ids))
    if suite_name:
        # Case-insensitive (see ``list_managed_test_cases``) *and* honouring the
        # effective-suite rule: for a ``live_stream`` run the SDK sends the
        # suite once at session-create, so it lands on
        # ``TestRun.primary_suite_name`` while per-event ``TestCase.suite_name``
        # stays NULL. A bare per-row filter returns nothing for those runs even
        # though they are correctly tagged. Mirrors
        # ``analytics_service._effective_suite_sql()`` and the shape #559 gave
        # ``run_compare_service._load_test_rows``: the run-level label applies
        # to live_stream runs only, since a multi-``<testsuite>`` upload has an
        # authoritative per-row value that must win.
        #
        # Stated honestly: the live-stream half is NOT reproducible on the
        # homelab today — no live_stream run there has both a NULL per-row
        # suite and a ``primary_suite_name``. The case-sensitivity half was
        # reproduced (api -> 5, API -> 0).
        suite_key = normalize_suite_name(suite_name)
        base = base.where(
            or_(
                func.lower(func.trim(TestCase.suite_name)) == suite_key,
                and_(
                    TestRun.trigger_source == "live_stream",
                    func.lower(func.trim(TestRun.primary_suite_name)) == suite_key,
                ),
            )
        )
    if search:
        pattern = like_contains(search)
        base = base.where(
            TestCase.test_name.ilike(pattern, escape="\\")
            | TestCase.class_name.ilike(pattern, escape="\\")
        )
    base_sq = base.subquery()

    latest = (
        select(base_sq)
        .distinct(base_sq.c.run_project_id, base_sq.c.test_fingerprint)
        .order_by(
            base_sq.c.run_project_id,
            base_sq.c.test_fingerprint,
            base_sq.c.run_created_at.desc(),
        )
        .subquery()
    )
    stmt = select(latest).order_by(latest.c.run_created_at.desc())
    rows = (await db.execute(stmt)).all()

    exclude = exclude_fingerprints or set()
    result: list[dict] = []
    for r in rows:
        row_project_id = r.run_project_id if project_id is None else project_id
        if (
            r.test_fingerprint in exclude
            or (row_project_id, r.test_fingerprint) in exclude
        ):
            continue
        result.append({
            "id": r.id,  # per-run TestCase id; safe as a list-row key
            "owner": r.owner,
            "latest_run_id": r.run_id,
            "latest_test_case_id": r.id,
            "canonical_test_case_id": r.canonical_test_case_id,
            "project_id": row_project_id,
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
    # Tenant guard: the caller must be a member of the target project. Without
    # this a user could author a case in ANY project by supplying its id.
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))

    # Migration 0087 — resolve or create the structured suite anchor when the
    # caller supplied a ``suite_name``. This lets authored cases participate
    # in the same catalog graph as executed ones (rename-propagation,
    # cross-project move refusal, suite-scoped queries). Legacy callers that
    # don't set ``suite_name`` keep working — ``test_suite_id`` stays NULL.
    test_suite_id: Optional[uuid.UUID] = None
    suite_name = (payload.suite_name or "").strip() or None
    if suite_name is not None:
        from app.services.test_suite_service import get_or_create_suite_by_name
        suite = await get_or_create_suite_by_name(
            db, payload.project_id, suite_name
        )
        test_suite_id = suite.id

    # Keep authored cases discoverable from executed results when the
    # automation uses the same title and has no class discriminator. This is
    # only a fallback identity; an explicit canonical link remains authoritative.
    from app.services.ingestion import make_test_fingerprint
    test_case = ManagedTestCase(
        **payload.model_dump(exclude_unset=True, exclude={"change_summary"}),
        author_id=current_user.id,
        status="draft",
        version=1,
        test_suite_id=test_suite_id,
        test_fingerprint=make_test_fingerprint(payload.title, None),
    )
    db.add(test_case)
    await db.flush()

    stage_test_case_snapshot(
        db,
        test_case,
        actor_id=current_user.id,
        change_summary="Initial creation",
        change_type="created",
        changed_fields=[
            "title", "description", "objective", "preconditions", "steps",
            "parameters", "expected_result", "test_data", "test_type",
            "priority", "severity", "feature_area", "suite_name", "tags",
            "estimated_duration_minutes", "is_automated", "automation_status",
            "test_fingerprint", "status",
        ],
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
    test_case = await get_test_case_or_404(db, case_id, for_update=True)
    if test_case.status in ("deprecated", "archived"):
        raise HTTPException(status_code=409, detail=f"Cannot edit a {test_case.status} test case")
    if test_case.version != payload.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "current_version": test_case.version,
                "expected_version": payload.expected_version,
                "message": "Test case changed; refresh before saving edits",
            },
        )

    old = {"title": test_case.title, "status": test_case.status, "version": test_case.version}
    update_data = payload.model_dump(
        exclude_unset=True,
        exclude={"change_summary", "expected_version"},
    )
    if not update_data:
        return test_case

    # Migration 0087 — keep ``test_suite_id`` in lockstep with ``suite_name``
    # changes. When the caller renames the suite (or clears it), we
    # resolve-or-create the new suite under the case's own project; never
    # cross-project. The structured FK update happens *before*
    # apply_model_updates so the version snapshot below already reflects
    # the resolved anchor.
    if "suite_name" in update_data:
        new_suite_name = (update_data["suite_name"] or "").strip() or None
        if new_suite_name is None:
            update_data["test_suite_id"] = None
        else:
            from app.services.test_suite_service import get_or_create_suite_by_name
            suite = await get_or_create_suite_by_name(
                db, test_case.project_id, new_suite_name
            )
            update_data["test_suite_id"] = suite.id

    apply_model_updates(test_case, update_data)
    changed_fields = sorted(update_data)
    if old["status"] in ("approved", "active"):
        # The policy's default is require-rereview.  Running this through the
        # state machine preserves the one-writer invariant and snapshots the
        # edited content together with its approval invalidation.
        await transition(
            db,
            test_case.id,
            LifecycleAction.FLAG_STALE,
            current_user,
            reason="edited_after_approval",
            notes=payload.change_summary,
            changed_fields=changed_fields,
        )
    else:
        test_case.version += 1
        stage_test_case_snapshot(
            db,
            test_case,
            actor_id=current_user.id,
            change_summary=payload.change_summary or f"Updated to v{test_case.version}",
            change_type="updated",
            changed_fields=changed_fields,
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
    reason: Optional[str] = None,
) -> None:
    test_case = await get_test_case_or_404(db, case_id)
    compatibility_reason = reason
    if not compatibility_reason or not compatibility_reason.strip():
        compatibility_reason = "(no reason supplied)"
        logger.warning(
            "test_case_deprecated_without_reason",
            test_case_id=str(test_case.id),
            project_id=str(test_case.project_id),
        )
        stage_test_management_counter(
            db,
            "deprecation_without_reason",
            (str(test_case.project_id),),
        )
    await transition(
        db,
        case_id,
        LifecycleAction.DEPRECATE,
        current_user,
        reason=compatibility_reason,
    )


async def request_test_case_review(db: AsyncSession, case_id: uuid.UUID, current_user: User) -> TestCaseReview:
    # If ``case_id`` doesn't resolve to a ManagedTestCase, check whether
    # it's actually a per-run TestCase id — that's the common mistake
    # when a caller hits this endpoint with an automation-row id from
    # the merged /cases response. The clearer 400 with explicit guidance
    # is much more debuggable than the bare "Test case not found".
    managed = await db.get(ManagedTestCase, case_id)
    if managed is None:
        from app.models.postgres import TestCase as _TC
        is_automation = await db.execute(
            select(_TC.id).where(_TC.id == case_id).limit(1),
        )
        if is_automation.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This id belongs to an automation TestCase row, not a "
                    "managed_test_cases row. Automation rows must be "
                    "promoted to a managed test case before a review can "
                    "be requested."
                ),
            )
        raise HTTPException(status_code=404, detail="Test case not found")
    result = await transition(
        db, case_id, LifecycleAction.REQUEST_REVIEW, current_user
    )
    assert result.review is not None
    return result.review


async def apply_review_action(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: ReviewActionRequest,
    current_user: User,
) -> ManagedTestCase:
    result = await auto_claim_and_decide(
        db,
        case_id,
        payload.action,
        current_user,
        notes=payload.notes,
    )
    return result.case


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


async def get_plan_or_404(
    db: AsyncSession,
    plan_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> TestPlan:
    if not for_update:
        return cast(
            TestPlan,
            await get_or_404(db, TestPlan, plan_id, "Test plan not found"),
        )
    statement = select(TestPlan).where(TestPlan.id == plan_id)
    statement = statement.with_for_update()
    plan = (await db.execute(statement)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Test plan not found")
    return cast(TestPlan, plan)


async def get_plan_item_or_404(db: AsyncSession, plan_id: uuid.UUID, item_id: uuid.UUID) -> TestPlanItem:
    item = cast(TestPlanItem, await get_or_404(db, TestPlanItem, item_id, "Item not found"))
    if item.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


async def recompute_plan_counts(db: AsyncSession, plan: TestPlan) -> None:
    # Aggregate the per-status counts in one query instead of materialising
    # every TestPlanItem and running five Python passes. ``executed`` is
    # derived as ``total - not_run`` (NOT count(status != 'not_run')) so a NULL
    # execution_status counts as executed — matching the original
    # ``status not in ('not_run',)`` (None is "not not_run"); the column is
    # nullable. passed/failed/blocked use ``== X``, which excludes NULL in both
    # the SQL and the original Python, so they match exactly.
    row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count(
                    case((TestPlanItem.execution_status == "not_run", 1))
                ).label("not_run"),
                func.count(
                    case((TestPlanItem.execution_status == "passed", 1))
                ).label("passed"),
                func.count(
                    case((TestPlanItem.execution_status == "failed", 1))
                ).label("failed"),
                func.count(
                    case((TestPlanItem.execution_status == "blocked", 1))
                ).label("blocked"),
            ).where(TestPlanItem.plan_id == plan.id)
        )
    ).one()
    total = int(row.total or 0)
    plan.total_cases = total
    plan.executed_cases = total - int(row.not_run or 0)
    plan.passed_cases = int(row.passed or 0)
    plan.failed_cases = int(row.failed or 0)
    plan.blocked_cases = int(row.blocked or 0)


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
    # Tenant guard — caller must be a member of the target project.
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))
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
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    apply_model_updates(plan, payload.model_dump(exclude_unset=True))
    await audit_event(db, "test_plan", plan.id, plan.project_id, "updated", current_user)
    return plan


async def add_test_plan_item(
    db: AsyncSession,
    plan_id: uuid.UUID,
    payload: TestPlanItemCreate,
    current_user: User,
) -> TestPlanItem:
    # The plan row is the aggregate and membership serialization point. This
    # prevents concurrent add/remove/execute requests from publishing counts
    # calculated from different snapshots.
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    case_id = await db.scalar(
        select(ManagedTestCase.id).where(
            ManagedTestCase.id == payload.test_case_id,
            ManagedTestCase.project_id == plan.project_id,
        )
    )
    if case_id is None:
        # Do not disclose whether an identifier belongs to another project.
        raise HTTPException(status_code=404, detail="Test case not found")
    existing_item_id = await db.scalar(
        select(TestPlanItem.id).where(
            TestPlanItem.plan_id == plan_id,
            TestPlanItem.test_case_id == payload.test_case_id,
        )
    )
    if existing_item_id is not None:
        raise HTTPException(status_code=409, detail="Test case is already in this plan")
    item = TestPlanItem(plan_id=plan_id, **payload.model_dump(exclude_unset=True))
    db.add(item)
    await db.flush()  # materialize item.id and expose it to recompute_plan_counts
    await recompute_plan_counts(db, plan)
    await audit_event(
        db,
        "test_plan_item",
        item.id,
        plan.project_id,
        "added",
        current_user,
        new_values={"test_case_id": str(item.test_case_id)},
    )
    return item


async def remove_test_plan_item(
    db: AsyncSession,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: User,
) -> None:
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    test_case_id = item.test_case_id
    await db.delete(item)
    await recompute_plan_counts(db, plan)
    await audit_event(
        db,
        "test_plan_item",
        item.id,
        plan.project_id,
        "removed",
        current_user,
        old_values={"test_case_id": str(test_case_id)},
    )


async def record_test_plan_execution(
    db: AsyncSession,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    execution_status: str,
    execution_notes: Optional[str],
    actual_duration_minutes: Optional[int],
    current_user: User,
) -> TestPlanItem:
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    previous_status = item.execution_status
    item.execution_status = execution_status
    item.executed_by_id = current_user.id
    item.executed_at = datetime.now(timezone.utc)
    item.execution_notes = execution_notes
    item.actual_duration_minutes = actual_duration_minutes
    await recompute_plan_counts(db, plan)
    await audit_event(
        db,
        "test_plan_item",
        item.id,
        plan.project_id,
        "executed",
        current_user,
        old_values={"execution_status": previous_status},
        new_values={"execution_status": execution_status},
    )
    return item
