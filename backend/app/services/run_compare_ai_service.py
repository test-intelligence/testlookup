"""Persistence and orchestration for AI run comparison reports."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.run_compare_agent import (
    PROMPT_VERSION,
    RunCompareAgent,
    build_validated_fallback_report,
)
from app.models.postgres import RunComparisonReport
from app.services.run_compare_service import normalize_suite_name


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, default=str))


async def get_cached_report(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
) -> Optional[dict[str, Any]]:
    row = await _get_row(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )
    if not row:
        return None
    if row.status == "ready" and row.ai_report:
        return dict(row.ai_report)
    if row.status == "queued":
        return {
            "status": "queued",
            "message": "AI comparison report is still being generated. It will be saved here when ready.",
            "executive_summary": "",
            "markdown_report": "",
            "risk_level": "LOW",
            "key_differences": [],
            "new_risks": [],
            "resolved_risks": [],
            "duration_concerns": [],
            "recommended_actions": [],
            "confidence": 0,
            "confidence_reason": "",
            "fallback_used": False,
        }
    if row.status == "failed":
        report = dict(row.ai_report or {})
        report.setdefault("status", "failed")
        report.setdefault("message", row.error_message or "AI comparison report generation failed.")
        return report
    return None


async def is_report_ready(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
) -> bool:
    """Return whether the exact persisted comparison is complete."""
    row = await _get_row(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )
    return bool(row and row.status == "ready" and row.ai_report)


async def mark_queued(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
    compare_payload: dict[str, Any],
    created_by_user_id: Optional[uuid.UUID] = None,
) -> None:
    row = await _get_row(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )
    if row:
        if row.status == "ready":
            return
        row.status = "queued"
        row.compare_payload = _jsonable(compare_payload)
        row.error_message = None
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(RunComparisonReport(
            project_id=project_id,
            left_run_id=left_run_id,
            right_run_id=right_run_id,
            suite_name=suite_name,
            suite_name_normalized=normalize_suite_name(suite_name),
            compare_payload=_jsonable(compare_payload),
            status="queued",
            prompt_version=PROMPT_VERSION,
            created_by_user_id=created_by_user_id,
        ))
    await db.flush()


async def generate_and_save_report(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
    compare_payload: dict[str, Any],
    created_by_user_id: Optional[uuid.UUID] = None,
    deterministic_only: bool = False,
    cost_budget_prechecked: bool = False,
) -> dict[str, Any]:
    if not cost_budget_prechecked:
        from app.services.llm_cost_budget import check_and_apply_cap

        budget_decision = await check_and_apply_cap(project_id)
        deterministic_only = deterministic_only or budget_decision.is_capped()

    await mark_queued(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
        compare_payload=compare_payload,
        created_by_user_id=created_by_user_id,
    )
    if deterministic_only:
        report = build_validated_fallback_report(compare_payload)
    else:
        from app.services.llm_cost_reservation import cost_budget_scope

        agent = RunCompareAgent()
        # Re-audit M13: the report's LLM calls reserve against the cap.
        with cost_budget_scope(project_id):
            report = await agent.generate(compare_payload)
    row = await _get_row(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )
    if row:
        row.status = "ready"
        row.ai_report = _jsonable(report)
        row.fallback_used = bool(report.get("fallback_used"))
        row.compare_payload = _jsonable(compare_payload)
        row.error_message = None
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()
    return report


async def mark_failed(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
    compare_payload: dict[str, Any],
    error_message: str,
) -> None:
    await mark_queued(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
        compare_payload=compare_payload,
    )
    row = await _get_row(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )
    if row:
        row.status = "failed"
        row.error_message = error_message[:1000]
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()


async def _get_row(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
) -> Optional[RunComparisonReport]:
    result = await db.execute(
        select(RunComparisonReport).where(
            RunComparisonReport.project_id == project_id,
            RunComparisonReport.left_run_id == left_run_id,
            RunComparisonReport.right_run_id == right_run_id,
            RunComparisonReport.suite_name_normalized == normalize_suite_name(suite_name),
            RunComparisonReport.prompt_version == PROMPT_VERSION,
        )
    )
    return result.scalar_one_or_none()
