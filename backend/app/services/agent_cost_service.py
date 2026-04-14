"""
Agent Cost & Alert Service -- Phase 6: Observability and Cost Control.

Provides:
  - Per-pipeline cost aggregation from stage results (already-recorded values)
  - Threshold-based alerting for repeated failures and cost spikes

Note: per-call cost estimation and standalone error taxonomy were removed
in item #10 cleanup — they had no production callers, only standalone
unit tests. If you need either, the implementations live in git history
(commit "Item #10 retire legacy paths") and can be revived with fresh
callers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentPipelineRun, AgentStageResult

logger = logging.getLogger("services.agent_cost")

# ── Alert thresholds ─────────────────────────────────────────────────────────

ALERT_CONSECUTIVE_FAILURES = 3        # alert after N consecutive stage failures
ALERT_COST_SPIKE_FACTOR = 3.0         # alert if cost > N× the rolling average
ALERT_COST_BUDGET_USD = 5.0           # alert if single pipeline exceeds this


async def get_pipeline_cost_summary(
    db: AsyncSession,
    pipeline_run_id: str,
) -> dict[str, Any]:
    """Aggregate cost and token data for a pipeline run."""
    import uuid as _uuid

    result = await db.execute(
        select(AgentStageResult).where(
            AgentStageResult.pipeline_run_id == _uuid.UUID(pipeline_run_id)
        ).order_by(AgentStageResult.started_at)
    )
    stages = result.scalars().all()

    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_llm_calls = 0
    stage_breakdown: list[dict] = []

    for s in stages:
        input_t = s.input_tokens or 0
        output_t = s.output_tokens or 0
        cost = s.cost_usd or 0.0
        calls = s.llm_calls_count or 0

        total_input_tokens += input_t
        total_output_tokens += output_t
        total_cost += cost
        total_llm_calls += calls

        duration = None
        if s.started_at and s.completed_at:
            duration = round((s.completed_at - s.started_at).total_seconds(), 2)

        stage_breakdown.append({
            "stage_name": s.stage_name,
            "status": s.status,
            "duration_seconds": duration,
            "input_tokens": input_t,
            "output_tokens": output_t,
            "total_tokens": s.total_tokens or (input_t + output_t),
            "llm_calls_count": calls,
            "cost_usd": round(cost, 6),
            "error_category": s.error_category,
            "fallback_used": s.fallback_used or False,
            "confidence_score": s.confidence_score,
            "evidence_count": s.evidence_count,
            "route_rationale": s.route_rationale,
        })

    return {
        "pipeline_run_id": pipeline_run_id,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "total_llm_calls": total_llm_calls,
        "stages": stage_breakdown,
    }


async def check_alerts(
    db: AsyncSession,
    pipeline_run_id: str,
) -> list[dict[str, Any]]:
    """
    Check for alert conditions after a pipeline run completes.

    Returns a list of alert dicts: [{type, severity, message, detail}].
    """
    import uuid as _uuid

    alerts: list[dict] = []

    # 1. Check for consecutive failures in the same stage
    result = await db.execute(
        select(AgentStageResult).where(
            AgentStageResult.pipeline_run_id == _uuid.UUID(pipeline_run_id),
            AgentStageResult.status == "failed",
        )
    )
    failed_stages = result.scalars().all()

    for fs in failed_stages:
        # Count recent consecutive failures for this stage
        recent = await db.execute(
            select(func.count(AgentStageResult.id))
            .join(AgentPipelineRun, AgentStageResult.pipeline_run_id == AgentPipelineRun.id)
            .where(
                AgentStageResult.stage_name == fs.stage_name,
                AgentStageResult.status == "failed",
                AgentPipelineRun.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        )
        count = recent.scalar() or 0
        if count >= ALERT_CONSECUTIVE_FAILURES:
            alerts.append({
                "type": "repeated_failure",
                "severity": "warning",
                "message": f"Stage '{fs.stage_name}' has failed {count} times in the last 24h",
                "detail": {
                    "stage_name": fs.stage_name,
                    "failure_count": count,
                    "error_category": fs.error_category,
                    "latest_error": (fs.error or "")[:200],
                },
            })

    # 2. Check for cost budget breach
    cost_summary = await get_pipeline_cost_summary(db, pipeline_run_id)
    if cost_summary["total_cost_usd"] > ALERT_COST_BUDGET_USD:
        alerts.append({
            "type": "cost_budget_exceeded",
            "severity": "warning",
            "message": f"Pipeline cost ${cost_summary['total_cost_usd']:.4f} exceeds ${ALERT_COST_BUDGET_USD} budget",
            "detail": {
                "total_cost_usd": cost_summary["total_cost_usd"],
                "budget_usd": ALERT_COST_BUDGET_USD,
                "total_tokens": cost_summary["total_tokens"],
            },
        })

    # 3. Check for cost spike vs rolling average
    avg_result = await db.execute(
        select(func.avg(AgentStageResult.cost_usd))
        .join(AgentPipelineRun, AgentStageResult.pipeline_run_id == AgentPipelineRun.id)
        .where(
            AgentStageResult.cost_usd.isnot(None),
            AgentStageResult.cost_usd > 0,
            AgentPipelineRun.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
        )
    )
    avg_cost = avg_result.scalar()
    if avg_cost and avg_cost > 0 and cost_summary["total_cost_usd"] > avg_cost * ALERT_COST_SPIKE_FACTOR:
        alerts.append({
            "type": "cost_spike",
            "severity": "info",
            "message": f"Pipeline cost ${cost_summary['total_cost_usd']:.4f} is {cost_summary['total_cost_usd']/avg_cost:.1f}x the 7-day average",
            "detail": {
                "current_cost": cost_summary["total_cost_usd"],
                "avg_cost_7d": round(float(avg_cost), 6),
                "spike_factor": round(cost_summary["total_cost_usd"] / float(avg_cost), 1),
            },
        })

    return alerts
