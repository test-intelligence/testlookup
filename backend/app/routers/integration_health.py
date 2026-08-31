"""Integration Health router — dashboard, on-demand probe, history (OPS-01)."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import IntegrationHealthCheck, IntegrationProbeResult, User, UserRole

logger = logging.getLogger("routers.integration_health")

router = APIRouter(prefix="/api/v1/integration-health", tags=["Integration Health"])


@router.get("/status")
async def get_all_status(
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Get latest health status for all integration providers."""
    result = await db.execute(
        select(IntegrationHealthCheck).order_by(IntegrationHealthCheck.provider)
    )
    checks = result.scalars().all()
    return [
        {
            "provider": hc.provider,
            "status": hc.status,
            "last_checked_at": hc.last_checked_at.isoformat() if hc.last_checked_at else None,
            "message": hc.message,
            "response_ms": hc.response_ms,
            "consecutive_failures": hc.consecutive_failures or 0,
            "last_success_at": hc.last_success_at.isoformat() if hc.last_success_at else None,
        }
        for hc in checks
    ]


@router.post("/probe")
async def trigger_probe(
    provider: Optional[str] = None,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an on-demand health probe for all or a specific provider (QA_LEAD+)."""
    from app.services.integration_probe_service import (
        ALL_PROBES,
        persist_probe_results,
    )

    if provider and provider not in ALL_PROBES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown integration provider: {provider}. "
                f"Supported: {', '.join(sorted(ALL_PROBES))}"
            ),
        )

    if provider and provider in ALL_PROBES:
        if provider in ("slack", "teams"):
            from app.services.integration_config_service import (
                resolve_global_notification_webhooks,
            )

            notification_cfg = await resolve_global_notification_webhooks(db)
            results = [await ALL_PROBES[provider](notification_cfg)]
        else:
            results = [await ALL_PROBES[provider]()]
    else:
        from app.services.integration_probe_service import run_all_probes
        results = await run_all_probes()

    await persist_probe_results(results)

    return [
        {
            "provider": r.provider,
            "status": r.status,
            "response_ms": r.response_ms,
            "message": r.message,
            "auth_valid": r.auth_valid,
            "payload_valid": r.payload_valid,
        }
        for r in results
    ]


@router.get("/history/{provider}")
async def get_provider_history(
    provider: str,
    days: int = Query(default=7, ge=1, le=90),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Get probe history for a specific provider."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(IntegrationProbeResult)
        .where(
            IntegrationProbeResult.provider == provider,
            IntegrationProbeResult.checked_at >= cutoff,
        )
        .order_by(IntegrationProbeResult.checked_at.desc())
        .limit(500)
    )
    probes = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "status": p.status,
            "response_ms": p.response_ms,
            "message": p.message,
            "auth_valid": p.auth_valid,
            "payload_valid": p.payload_valid,
            "checked_at": p.checked_at.isoformat() if p.checked_at else None,
        }
        for p in probes
    ]


@router.get("/trends")
async def get_health_trends(
    days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated health trends for all providers over a period.

    Every status the probe service persists gets its own counter. The probers
    emit five (``skipped`` is dropped before insert, see
    ``integration_probe_service._persist``), and reporting only three left
    ``auth_error`` and ``timeout`` probes counted in ``total_probes`` — so they
    dragged ``uptime_pct`` down — while appearing in no column at all. A
    provider whose token had expired rendered as 0% uptime with 0 healthy,
    0 degraded and 0 down, which reads as "no data" rather than "your
    credentials are wrong".
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Count by provider and status
    result = await db.execute(
        select(
            IntegrationProbeResult.provider,
            IntegrationProbeResult.status,
            func.count(IntegrationProbeResult.id).label("count"),
        )
        .where(IntegrationProbeResult.checked_at >= cutoff)
        .group_by(IntegrationProbeResult.provider, IntegrationProbeResult.status)
    )
    rows = result.all()

    # Average latency, one row per provider. Computed separately rather than
    # per-status: the per-status averages cannot be combined without their
    # weights, and the previous code simply let the last group win — an
    # order-dependent value, since a GROUP BY has no defined row order.
    # ``response_ms = 0`` is the sentinel a ``down`` probe records when it never
    # got a response (`ProbeResult(provider, "down", 0, ...)`), not a 0ms
    # measurement, so those rows are excluded from the average instead of
    # deflating it.
    latency = await db.execute(
        select(
            IntegrationProbeResult.provider,
            func.avg(IntegrationProbeResult.response_ms).label("avg_ms"),
        )
        .where(
            IntegrationProbeResult.checked_at >= cutoff,
            IntegrationProbeResult.response_ms > 0,
        )
        .group_by(IntegrationProbeResult.provider)
    )
    avg_by_provider = {r.provider: r.avg_ms for r in latency.all()}

    trends: dict[str, dict] = {}
    for row in rows:
        provider = row.provider
        if provider not in trends:
            trends[provider] = {
                "provider": provider,
                "total_probes": 0,
                "healthy": 0,
                "degraded": 0,
                "down": 0,
                "timeout": 0,
                "auth_error": 0,
                "avg_response_ms": 0,
            }
        trends[provider][row.status] = trends[provider].get(row.status, 0) + row.count
        trends[provider]["total_probes"] += row.count

    # Calculate uptime percentage
    for provider, t in trends.items():
        total = t["total_probes"]
        if total > 0:
            t["uptime_pct"] = round((t.get("healthy", 0) / total) * 100, 1)
        else:
            t["uptime_pct"] = 0
        avg_ms = avg_by_provider.get(provider)
        t["avg_response_ms"] = round(float(avg_ms), 0) if avg_ms is not None else 0

    return list(trends.values())
