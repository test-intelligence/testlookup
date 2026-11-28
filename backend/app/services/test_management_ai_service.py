from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.redis_client import get_redis
from app.models.postgres import ManagedTestCase, TestCaseReview, TestStrategy, User
from app.models.schemas import AICoverageAnalysisRequest, AIGenerateStrategyRequest, AIGenerateTestCasesRequest
from app.routers.test_management_shared import logger
from app.services.test_management_audit_service import audit_event
from app.services.test_management_service import get_test_case_or_404
from app.services.test_case_lifecycle_service import stage_test_case_snapshot

# ── Who may read an AI task's status ──────────────────────────────────────
#
# The status route (GET /test-management/cases/ai-task/{task_id}) reads the
# Celery result backend, which holds every task's result under its id. So each
# AI enqueue (cases, plans, strategies) records who queued the task and for
# which project, and the route answers only for ids that have such a record.

#: As long as a stored result lives (``result_expires`` in worker/celery_app.py).
AI_TASK_OWNER_TTL_SECONDS = 3600

AI_TASK_NOT_FOUND_DETAIL = (
    "AI task not found. A task's status can be read for one hour after it was "
    "queued, by the user who queued it or by a member of its project."
)


def ai_task_owner_key(task_id: str) -> str:
    return f"ai-task-owner:{task_id}"


async def record_ai_task_owner(
    task_id: str,
    *,
    user_id: uuid.UUID | str,
    project_id: uuid.UUID | str,
) -> None:
    """Record who queued an AI task and for which project.

    Call it right after ``<task>.delay(...)`` in every enqueue whose task id
    is polled through the status route; without the record the route answers
    404 for that id.
    """
    await get_redis().set(
        ai_task_owner_key(task_id),
        json.dumps({"user_id": str(user_id), "project_id": str(project_id)}),
        ex=AI_TASK_OWNER_TTL_SECONDS,
    )


async def _read_ai_task_owner(task_id: str) -> Optional[tuple[str, str]]:
    """``(user_id, project_id)`` for a recorded task, else None.

    None also when Redis cannot be read or the record is not what
    ``record_ai_task_owner`` writes: with no proof of ownership the caller is
    refused.
    """
    try:
        raw = await get_redis().get(ai_task_owner_key(task_id))
    except Exception as exc:
        logger.warning("ai_task_owner_read_failed", task_id=task_id, error=str(exc))
        return None
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    user_id, project_id = record.get("user_id"), record.get("project_id")
    if not isinstance(user_id, str) or not isinstance(project_id, str) or not user_id or not project_id:
        return None
    return user_id, project_id


async def generate_ai_cases(
    db: AsyncSession,
    payload: AIGenerateTestCasesRequest,
    current_user: User,
) -> dict:
    from app.services.test_case_ai_agent import ai_generate_test_cases

    result = await ai_generate_test_cases(payload.requirements, project_id=payload.project_id)
    created_ids: list[str] = []

    if payload.persist and result.get("test_cases"):
        for tc_data in result["test_cases"]:
            test_case = ManagedTestCase(
                project_id=payload.project_id,
                title=tc_data.get("title", "AI Generated Test Case"),
                description=tc_data.get("objective"),
                objective=tc_data.get("objective"),
                preconditions=tc_data.get("preconditions"),
                steps=tc_data.get("steps"),
                expected_result=tc_data.get("expected_result"),
                test_data=tc_data.get("test_data"),
                test_type=tc_data.get("test_type", "functional"),
                priority=tc_data.get("priority", "medium"),
                severity=tc_data.get("severity", "major"),
                feature_area=tc_data.get("feature_area"),
                tags=tc_data.get("tags", []),
                estimated_duration_minutes=tc_data.get("estimated_duration_minutes"),
                ai_generated=True,
                ai_generation_prompt=payload.requirements,
                author_id=current_user.id,
                status="draft",
                version=1,
            )
            db.add(test_case)
            await db.flush()
            stage_test_case_snapshot(
                db,
                test_case,
                actor_id=current_user.id,
                change_summary="AI generated",
                change_type="created",
                source="ai",
                changed_fields=[
                    "title", "description", "objective", "preconditions",
                    "steps", "expected_result", "test_data", "test_type",
                    "priority", "severity", "feature_area", "tags",
                    "estimated_duration_minutes", "status",
                ],
            )
            await audit_event(
                db,
                "test_case",
                test_case.id,
                payload.project_id,
                "ai_generated",
                current_user,
                details=f"AI generated from requirements: {payload.requirements[:100]}",
            )
            created_ids.append(str(test_case.id))
        # stage-only: router handler commits

    return {
        "test_cases": result.get("test_cases", []),
        "coverage_summary": result.get("coverage_summary"),
        "gaps_noted": result.get("gaps_noted", []),
        "created_ids": created_ids,
    }


async def enqueue_ai_case_generation(payload: AIGenerateTestCasesRequest, current_user: User) -> dict:
    try:
        from app.worker.tasks import generate_ai_test_cases_task

        task = generate_ai_test_cases_task.delay(
            payload.requirements,
            str(payload.project_id),
            str(current_user.id),
        )
        await record_ai_task_owner(task.id, user_id=current_user.id, project_id=payload.project_id)
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        # Keyword fields: this is a structlog logger, and once logging is
        # configured it rejects %-style positional arguments with a TypeError.
        logger.error("ai_task_enqueue_failed", task="generate_ai_test_cases_task", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI task queue unavailable. Check that the Celery worker and Redis are running.",
        )


def get_ai_task_status(task_id: str) -> dict:
    """Map a Celery result to the status response, for ANY task id.

    Not scoped to a caller: a route answers through
    ``get_ai_task_status_for_caller``.
    """
    from celery.result import AsyncResult
    from app.worker.celery_app import celery_app as _celery_app

    result = AsyncResult(task_id, app=_celery_app)
    if result.state == "SUCCESS":
        return {"task_id": task_id, "status": "success", "result": result.result}
    if result.state in ("FAILURE", "REVOKED"):
        # D-58 review: the page shows ``error``. Only a task's own plain
        # sentence (``AIPlanNotBuilt``) is passed on; any other exception's
        # text can carry a host, a URL or a driver message, so it is logged
        # here and the answer carries no text (the page says it generically).
        # Matched by name: the result backend may rebuild the class.
        if type(result.info).__name__ == "AIPlanNotBuilt":
            return {"task_id": task_id, "status": "failure", "error": str(result.info)}
        logger.warning("ai_task_failed", task_id=task_id, error=str(result.info))
        return {"task_id": task_id, "status": "failure", "error": None}
    return {"task_id": task_id, "status": "pending"}


async def get_ai_task_status_for_caller(
    db: AsyncSession,
    task_id: str,
    current_user: User,
) -> dict:
    """The status of an AI task the caller may see; 404 for every other id.

    The caller may see a task when it has an owner record and either they
    queued it or they have access to the project it was queued for. The same
    404 covers "no such task", "not an AI task" and "not yours", and the
    result backend is read only after the check passes.
    """
    owner = await _read_ai_task_owner(task_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=AI_TASK_NOT_FOUND_DETAIL)
    owner_user_id, owner_project_id = owner
    if owner_user_id != str(current_user.id):
        from app.core.deps import resolve_project_scope

        try:
            await resolve_project_scope(db, current_user, owner_project_id)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=AI_TASK_NOT_FOUND_DETAIL,
            ) from None
    return get_ai_task_status(task_id)


async def review_test_case_with_ai(
    db: AsyncSession,
    case_id: uuid.UUID,
    current_user: User,
) -> dict:
    from app.core.deps import resolve_project_scope

    test_case = await get_test_case_or_404(db, case_id)
    # Tenant guard — get_test_case_or_404 is a bare by-id fetch; verify the
    # caller can access the case's project before reading it into the LLM
    # prompt and writing back an AI quality score (class-i IDOR otherwise).
    # Done before importing the LLM tool so a denied request never touches it.
    await resolve_project_scope(db, current_user, str(test_case.project_id))

    from app.services.test_case_ai_agent import ai_review_test_case

    tc_dict = {
        "title": test_case.title,
        "objective": test_case.objective,
        "preconditions": test_case.preconditions,
        "steps": test_case.steps or [],
        "expected_result": test_case.expected_result,
        "test_data": test_case.test_data,
        "test_type": test_case.test_type,
    }
    result = await ai_review_test_case(tc_dict, project_id=test_case.project_id)

    test_case.ai_quality_score = result.get("quality_score")
    test_case.ai_review_notes = result

    # AI feedback is evidence, never human review ownership.  A distinct
    # terminal record prevents the AI path from claiming/reusing a pending
    # review or leaving a phantom ``in_progress`` claimant.
    review = TestCaseReview(
        test_case_id=case_id,
        requested_by_id=current_user.id,
        reviewer_id=None,
        status="ai_completed",
        ai_review_completed=True,
    )
    db.add(review)
    review.ai_quality_score = result.get("quality_score")
    review.ai_review_notes = result
    review.ai_reviewed_at = datetime.now(timezone.utc)

    await audit_event(
        db,
        "test_case",
        test_case.id,
        test_case.project_id,
        "ai_reviewed",
        current_user,
        new_values={"ai_quality_score": result.get("quality_score")},
    )
    # stage-only: router handler commits
    return result


async def analyze_case_coverage(db: AsyncSession, payload: AICoverageAnalysisRequest) -> dict:
    from app.services.test_case_ai_agent import ai_analyze_coverage

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.project_id == payload.project_id,
            ManagedTestCase.status.in_(["approved", "active", "draft"]),
        ).limit(200)
    )
    existing = [{"title": test_case.title, "objective": test_case.objective or ""} for test_case in result.scalars().all()]
    return await ai_analyze_coverage(payload.requirements, existing, project_id=payload.project_id)


async def list_strategies(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    allowed_project_ids: Optional[set[uuid.UUID]] = None,
) -> list[TestStrategy]:
    query = select(TestStrategy).order_by(TestStrategy.created_at.desc())
    if project_id:
        query = query.where(TestStrategy.project_id == project_id)
    elif allowed_project_ids is not None:
        # All Projects for a non-admin: the projects they belong to (S-D-4).
        query = query.where(TestStrategy.project_id.in_(allowed_project_ids))
    result = await db.execute(query)
    return list(result.scalars().all())


async def generate_ai_strategy(
    db: AsyncSession,
    payload: AIGenerateStrategyRequest,
    current_user: User,
) -> TestStrategy:
    from app.services.test_case_ai_agent import ai_generate_strategy

    result = await ai_generate_strategy(payload.project_context, project_id=payload.project_id)
    strategy = TestStrategy(
        project_id=payload.project_id,
        name=payload.strategy_name or f"Test Strategy - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        version_label="v1.0",
        status="draft",
        objective=result.get("objective"),
        scope=result.get("scope"),
        out_of_scope=result.get("out_of_scope"),
        test_approach=result.get("test_approach"),
        risk_assessment=result.get("risk_assessment"),
        test_types=result.get("test_types"),
        entry_criteria=result.get("entry_criteria"),
        exit_criteria=result.get("exit_criteria"),
        environments=result.get("environments"),
        automation_approach=result.get("automation_approach"),
        defect_management=result.get("defect_management"),
        ai_generated=True,
        generation_context=payload.project_context,
        ai_model_used=settings.LLM_MODEL,
        created_by_id=current_user.id,
    )
    db.add(strategy)
    await db.flush()  # materialize strategy.id for the audit row
    await audit_event(db, "test_strategy", strategy.id, payload.project_id, "ai_generated", current_user, details="AI-generated test strategy")
    # stage-only: router handler commits + refreshes
    return strategy


async def enqueue_ai_strategy_generation(payload: AIGenerateStrategyRequest, current_user: User) -> dict:
    try:
        from app.worker.tasks import generate_ai_strategy_task

        task = generate_ai_strategy_task.delay(
            str(payload.project_id),
            str(current_user.id),
            payload.project_context,
            payload.strategy_name,
        )
        await record_ai_task_owner(task.id, user_id=current_user.id, project_id=payload.project_id)
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        logger.error("ai_task_enqueue_failed", task="generate_ai_strategy_task", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI task queue unavailable. Check that the Celery worker and Redis are running.",
        )


async def get_strategy_or_404(db: AsyncSession, strategy_id: uuid.UUID) -> TestStrategy:
    strategy = await db.get(TestStrategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


async def update_strategy(
    db: AsyncSession,
    strategy_id: uuid.UUID,
    payload,
    current_user: User,
) -> TestStrategy:
    strategy = await get_strategy_or_404(db, strategy_id)
    changes = payload.model_dump(exclude_unset=True)
    old_values = {field: getattr(strategy, field) for field in changes}
    for field, value in changes.items():
        setattr(strategy, field, value)
    await audit_event(
        db, "test_strategy", strategy.id, strategy.project_id, "updated", current_user,
        old_values=old_values, new_values=changes,
    )
    # stage-only: router handler commits + refreshes
    return strategy
