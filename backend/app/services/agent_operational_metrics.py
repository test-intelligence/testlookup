"""Scrape-time gauges for E2.3 agent operations alerts."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select

from app.core.config import settings
from app.core.metrics import (
    agent_dlq_depth,
    agent_in_progress_overdue_seconds,
    pending_review_oldest_age_seconds,
)

logger = structlog.get_logger(__name__)


def _age_seconds(value: datetime | None, now: datetime) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds())


async def refresh_agent_operational_metrics(*, now: datetime | None = None) -> None:
    """Refresh durable agent state without ever failing the metrics scrape."""
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AgentPipelineRun, ReviewRequest

    observed_at = now or datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            oldest_run = (
                await db.execute(
                    select(func.min(func.coalesce(AgentPipelineRun.started_at, AgentPipelineRun.created_at))).where(
                        AgentPipelineRun.status.in_(("pending", "running", "retry_wait"))
                    )
                )
            ).scalar_one_or_none()
            oldest_review = (
                await db.execute(
                    select(func.min(ReviewRequest.created_at)).where(
                        ReviewRequest.state == "pending_review"
                    )
                )
            ).scalar_one_or_none()

        deadline = float(settings.AI_PIPELINE_DEADLINE_SECONDS)
        grace = float(settings.AGENT_PIPELINE_ALERT_GRACE_SECONDS)
        overrun = (
            max(0.0, _age_seconds(oldest_run, observed_at) - deadline - grace)
            if deadline > 0
            else 0.0
        )
        agent_in_progress_overdue_seconds.set(overrun)
        pending_review_oldest_age_seconds.set(
            _age_seconds(oldest_review, observed_at)
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_operational_db_metrics_failed", error=str(exc))

    try:
        from app.services.ingestion_dlq import get_dlq_count, get_stream_dlq_count

        persist_depth = await get_dlq_count("persist_live_session")
        stream_depth = await get_stream_dlq_count()
        if persist_depth is not None and stream_depth is not None:
            agent_dlq_depth.set(float(persist_depth + stream_depth))
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_operational_dlq_metric_failed", error=str(exc))
