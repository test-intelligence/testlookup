from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import ManagedTestCase, TestCaseReview, TestCaseVersion, TestStrategy, User
from app.models.schemas import AICoverageAnalysisRequest, AIGenerateStrategyRequest, AIGenerateTestCasesRequest
from app.routers.test_management_shared import audit_event, logger
from app.services.test_management_service import get_test_case_or_404


async def generate_ai_cases(
    db: AsyncSession,
    payload: AIGenerateTestCasesRequest,
    current_user: User,
) -> dict:
    from app.services.test_case_ai_agent import ai_generate_test_cases

    result = await ai_generate_test_cases(payload.requirements)
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
                    change_summary="AI generated",
                    change_type="created",
                )
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
        await db.commit()

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
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        logger.error("Failed to enqueue generate_ai_test_cases_task: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI task queue unavailable. Check that the Celery worker and Redis are running.",
        )


def get_ai_task_status(task_id: str) -> dict:
    from celery.result import AsyncResult
    from app.worker.celery_app import celery_app as _celery_app

    result = AsyncResult(task_id, app=_celery_app)
    if result.state == "SUCCESS":
        return {"task_id": task_id, "status": "success", "result": result.result}
    if result.state in ("FAILURE", "REVOKED"):
        return {"task_id": task_id, "status": "failure", "error": str(result.info)}
    return {"task_id": task_id, "status": "pending"}


async def review_test_case_with_ai(
    db: AsyncSession,
    case_id: uuid.UUID,
    current_user: User,
) -> dict:
    from app.services.test_case_ai_agent import ai_review_test_case

    test_case = await get_test_case_or_404(db, case_id)
    tc_dict = {
        "title": test_case.title,
        "objective": test_case.objective,
        "preconditions": test_case.preconditions,
        "steps": test_case.steps or [],
        "expected_result": test_case.expected_result,
        "test_data": test_case.test_data,
        "test_type": test_case.test_type,
    }
    result = await ai_review_test_case(tc_dict)

    test_case.ai_quality_score = result.get("quality_score")
    test_case.ai_review_notes = result

    review_query = select(TestCaseReview).where(
        TestCaseReview.test_case_id == case_id,
        TestCaseReview.ai_review_completed == False,  # noqa: E712
    ).limit(1)
    review = (await db.execute(review_query)).scalars().first()
    if not review:
        review = TestCaseReview(test_case_id=case_id, requested_by_id=current_user.id, status="in_progress")
        db.add(review)
        await db.flush()

    review.ai_review_completed = True
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
    await db.commit()
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
    return await ai_analyze_coverage(payload.requirements, existing)


async def list_strategies(db: AsyncSession, project_id: Optional[uuid.UUID]) -> list[TestStrategy]:
    query = select(TestStrategy).order_by(TestStrategy.created_at.desc())
    if project_id:
        query = query.where(TestStrategy.project_id == project_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def generate_ai_strategy(
    db: AsyncSession,
    payload: AIGenerateStrategyRequest,
    current_user: User,
) -> TestStrategy:
    from app.services.test_case_ai_agent import ai_generate_strategy

    result = await ai_generate_strategy(payload.project_context)
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
    await db.flush()
    await audit_event(db, "test_strategy", strategy.id, payload.project_id, "ai_generated", current_user, details="AI-generated test strategy")
    await db.commit()
    await db.refresh(strategy)
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
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        logger.error("Failed to enqueue generate_ai_strategy_task: %s", exc)
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
    await db.commit()
    await db.refresh(strategy)
    return strategy
