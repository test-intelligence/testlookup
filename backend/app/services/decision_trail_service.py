"""
Decision trail service — Tier 0B.

Aggregates the AI decision audit trail for a single test run into one
document suitable for a user-facing drawer. Pulls from three sources:

1. **Postgres** (``agent_pipeline_runs`` + ``agent_stage_results``) — every
   completed stage, its duration, its decision_log array, its analysis
   mode, fallback info, and cost.

2. **Postgres** (``ai_analysis.routing_metadata``) — per-test audit dicts
   persisted by ``_batch_upsert_analyses``: which engine ran, confidence
   adjustments, retries.

3. **Postgres + MongoDB** — workflow router decisions are mirrored into
   ``AgentPipelineRun.execution_metadata.workflow_route_decisions`` for
   durability, with Mongo ``decision_made`` events used as the timeline copy.

The response is intentionally a flat JSON document — the frontend drawer
renders it as a timeline without needing additional round-trips. Access
control is enforced at the router layer via ``resolve_project_scope``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AgentPipelineRun,
    AgentStageResult,
    AIAnalysis,
    TestCase,
    TestRun,
)

logger = structlog.get_logger("services.decision_trail")


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _duration_seconds(start: Any, end: Any) -> Optional[float]:
    s = _parse_iso(start)
    e = _parse_iso(end)
    if s is None or e is None:
        return None
    return round((e - s).total_seconds(), 3)


async def _load_pipeline_run(db: AsyncSession, run_id: uuid.UUID) -> Optional[AgentPipelineRun]:
    result = await db.execute(
        select(AgentPipelineRun)
        .where(AgentPipelineRun.test_run_id == run_id)
        .order_by(AgentPipelineRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_stages(db: AsyncSession, pipeline_run_id: uuid.UUID) -> list[AgentStageResult]:
    result = await db.execute(
        select(AgentStageResult)
        .where(AgentStageResult.pipeline_run_id == pipeline_run_id)
        .order_by(AgentStageResult.started_at.asc().nulls_last())
    )
    return list(result.scalars().all())


async def _load_per_test_routing(
    db: AsyncSession, run_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Fetch AIAnalysis rows joined to TestCase for the run, return only
    tests that have routing metadata or per-test decision audit."""
    result = await db.execute(
        select(
            AIAnalysis.test_case_id,
            AIAnalysis.routing_metadata,
            TestCase.test_name,
        )
        .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
        .where(TestCase.test_run_id == run_id)
        .where(AIAnalysis.routing_metadata.is_not(None))
    )
    rows: list[dict[str, Any]] = []
    for row in result.all():
        meta = row.routing_metadata or {}
        rows.append({
            "test_case_id": row.test_case_id,
            "test_name": row.test_name,
            "analysis_mode": meta.get("analysis_mode"),
            "mode_requested": meta.get("mode_requested"),
            "fallback_from": meta.get("fallback_from"),
            "fallback_reason": meta.get("fallback_reason"),
            "confidence_adjustments": meta.get("confidence_adjustments") or [],
            "retry_count": meta.get("retry_count") or 0,
            "duration_seconds": meta.get("analysis_duration_seconds"),
            # US-15.2: the confidence-gate evaluation recorded at analysis
            # time — {threshold, observed_confidence, passed, source}. None on
            # rows analysed before the gate existed (no backfill, no guess).
            "threshold_check": (
                meta.get("threshold_check")
                if isinstance(meta.get("threshold_check"), dict)
                else None
            ),
        })
    return rows


async def _load_workflow_events(pipeline_run: AgentPipelineRun) -> list[dict[str, Any]]:
    """Pull workflow-level decision events from the immutable event log.

    These are ``_emit_route_decision`` entries that the sync LangGraph
    router functions fire via ``asyncio.create_task``. If Mongo is missing
    events, fall back to the durable PG execution metadata mirror.
    """
    metadata = pipeline_run.execution_metadata or {}
    fallback_events = list(metadata.get("workflow_route_decisions") or [])
    try:
        from app.db.mongo import get_mongo_db
        db = get_mongo_db()
        cursor = (
            db["pipeline_event_log"]
            .find(
                {
                    "pipeline_run_id": str(pipeline_run.id),
                    "event_type": "decision_made",
                    "stage_name": "workflow",
                },
                {"_id": 0},
            )
            .sort("timestamp", 1)
            .limit(200)
        )
        events: list[dict[str, Any]] = []
        async for doc in cursor:
            detail = doc.get("detail") or {}
            events.append({
                "at": detail.get("at") or (
                    doc.get("timestamp").isoformat() if isinstance(doc.get("timestamp"), datetime) else None
                ),
                "decision_point": detail.get("decision_point", ""),
                "chosen": detail.get("chosen", ""),
                "rationale": detail.get("rationale", ""),
                "alternatives": detail.get("alternatives"),
                "context": detail.get("context"),
            })
        return events or fallback_events
    except Exception as exc:
        logger.warning("workflow event log query failed", error=str(exc))
        return fallback_events


def _summarize_stage(stage: AgentStageResult) -> dict[str, Any]:
    return {
        "stage_name": stage.stage_name,
        "status": stage.status,
        "started_at": stage.started_at,
        "completed_at": stage.completed_at,
        "duration_seconds": _duration_seconds(stage.started_at, stage.completed_at),
        "analysis_mode": getattr(stage, "analysis_mode", None),
        "fallback_used": bool(stage.fallback_used) if stage.fallback_used is not None else None,
        "fallback_reason": getattr(stage, "fallback_reason", None),
        "route_rationale": stage.route_rationale,
        "error_category": stage.error_category,
        "skipped_reason": stage.skipped_reason,
        "execution_path": stage.execution_path,
        "confidence_score": stage.confidence_score,
        "evidence_count": stage.evidence_count,
        "input_tokens": stage.input_tokens,
        "output_tokens": stage.output_tokens,
        "cost_usd": float(stage.cost_usd) if stage.cost_usd is not None else None,
        "decision_log": list(stage.decision_log or []),
    }


async def build_trail(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """Assemble the full decision trail document for a test run.

    Returns ``None`` if the run does not exist. Returns a partial document
    if no pipeline has been executed yet (stages + per_test will be empty).
    """
    # Verify the run exists — the caller has already enforced project scope.
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if run is None:
        return None

    pipeline_run = await _load_pipeline_run(db, run_id)
    stages: list[AgentStageResult] = []
    workflow_events: list[dict[str, Any]] = []
    if pipeline_run is not None:
        stages = await _load_stages(db, pipeline_run.id)
        workflow_events = await _load_workflow_events(pipeline_run)

    per_test = await _load_per_test_routing(db, run_id)

    # Aggregate totals across stages.
    total_cost_usd = 0.0
    total_tokens = 0
    for stage in stages:
        if stage.cost_usd:
            total_cost_usd += float(stage.cost_usd)
        if stage.total_tokens:
            total_tokens += int(stage.total_tokens)

    # Mode distribution and fallback count from per-test rollup.
    mode_distribution: dict[str, int] = {}
    fallback_count = 0
    below_threshold_count = 0
    for row in per_test:
        mode = row.get("analysis_mode") or "unknown"
        mode_distribution[mode] = mode_distribution.get(mode, 0) + 1
        if row.get("fallback_from"):
            fallback_count += 1
        # US-15.2 rollup: how many analyses failed the confidence gate. Only
        # counts tests that actually recorded a check — an un-gated legacy row
        # is neither "passed" nor "below".
        check = row.get("threshold_check")
        if isinstance(check, dict) and check.get("passed") is False:
            below_threshold_count += 1

    return {
        "run_id": run_id,
        "pipeline_run_id": pipeline_run.id if pipeline_run else None,
        "workflow_type": pipeline_run.workflow_type if pipeline_run else None,
        "pipeline_status": pipeline_run.status if pipeline_run else None,
        "started_at": pipeline_run.started_at if pipeline_run else None,
        "completed_at": pipeline_run.completed_at if pipeline_run else None,
        "total_cost_usd": round(total_cost_usd, 6),
        "total_tokens": total_tokens,
        "stages": [_summarize_stage(s) for s in stages],
        "workflow_events": workflow_events,
        "per_test": per_test,
        "mode_distribution": mode_distribution,
        "fallback_count": fallback_count,
        "below_threshold_count": below_threshold_count,
    }
