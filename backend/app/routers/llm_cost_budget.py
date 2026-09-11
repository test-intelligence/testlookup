"""
LLM cost budget / billing API — Tier 1 item 2.

Three user-facing surfaces:

* ``GET/PUT /api/v1/projects/{id}/llm-quota`` — per-project billing config.
  Read is available to QA_LEAD+ (so project leads can see their own cap);
  write is ADMIN only since it's an audited config change.

* ``GET /api/v1/projects/{id}/llm-usage`` — current period meter +
  history. Accessible to any project member.

* ``GET /api/v1/billing/overview`` — workspace-wide billing table used by
  the ``/settings/billing`` admin page. Lists every project the caller
  has access to with its current-period cost and cap status.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import Project, User, UserRole
from app.models.schemas import (
    BillingOverviewProject,
    BillingOverviewResponse,
    LlmQuotaRead,
    LlmQuotaWrite,
    LlmUsageHistoryEntry,
    LlmUsageRead,
)
from app.services import llm_cost_budget as cost_service
from app.services.llm_pricing import PRICE_TABLE_UPDATED

router = APIRouter(prefix="/api/v1", tags=["LLM Cost Budget"])
logger = structlog.get_logger("routers.llm_cost_budget")


# ── Quota config ────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/llm-quota", response_model=LlmQuotaRead)
async def get_project_quota(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Read the billing config for a project. QA_LEAD+ or project member."""
    quota = await cost_service.get_quota(db, project_id)
    if quota is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No LLM cost budget configured for this project",
        )
    return quota


@router.put("/projects/{project_id}/llm-quota", response_model=LlmQuotaRead)
async def upsert_project_quota(
    project_id: uuid.UUID,
    payload: LlmQuotaWrite,
    db: AsyncSession = Depends(get_db),
    # Instance administrators only, deliberately WITHOUT allow_project_key
    # (re-audit N20): this sets the project's own LLM spending cap and
    # at-cap action, and a project's CI key must not raise its own budget.
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_project_access()),
):
    """Create or replace the billing config for a project. ADMIN only."""
    quota = await cost_service.upsert_quota(
        db,
        project_id=project_id,
        actor=current_user,
        enabled=payload.enabled,
        period_type=payload.period_type,
        included_usd=payload.included_usd,
        overage_rate_usd=payload.overage_rate_usd,
        hard_cap_usd=payload.hard_cap_usd,
        soft_warn_threshold_pct=payload.soft_warn_threshold_pct,
        at_cap_action=payload.at_cap_action,
    )
    return quota


# ── Usage (current + history) ───────────────────────────────────────────────


@router.get("/projects/{project_id}/llm-usage", response_model=LlmUsageRead)
async def get_current_usage(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Current-period usage with cap status, for the project overview card."""
    return await cost_service.get_current_usage(db, project_id)


@router.get(
    "/projects/{project_id}/llm-usage/history",
    response_model=list[LlmUsageHistoryEntry],
)
async def get_usage_history(
    project_id: uuid.UUID,
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Most recent ``limit`` billing periods for a project (default 12)."""
    limit = max(1, min(limit, 60))
    rows = await cost_service.list_usage_history(db, project_id, limit=limit)
    return [
        LlmUsageHistoryEntry(
            period_start=r.period_start,
            period_end=r.period_end,
            total_cost_usd=float(r.total_cost_usd),
            total_llm_calls=int(r.total_llm_calls),
            cap_hits=int(r.cap_hits),
        )
        for r in rows
    ]


# ── Workspace overview ──────────────────────────────────────────────────────


@router.get("/billing/overview", response_model=BillingOverviewResponse)
async def billing_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Workspace-wide billing view for the ``/settings/billing`` page.

    Non-admin callers see only the projects they're members of; admins
    see everything. The response is optimised for a single render —
    every project row already has its cost, cap, utilization, and status
    computed so the frontend is a pure table.
    """
    period_start, period_end = cost_service.current_period_bounds()

    accessible = await get_accessible_project_ids(db, current_user)
    project_filter = None
    if accessible is not None:
        # Non-admin — restrict to their memberships.
        if not accessible:
            return BillingOverviewResponse(
                period_start=period_start,
                period_end=period_end,
                total_cost_usd=0.0,
                total_llm_calls=0,
                projects=[],
                price_table_updated=PRICE_TABLE_UPDATED,
            )
        project_filter = list(accessible)

    # Load all projects the caller can see in one query so we can render
    # "UNLIMITED" rows for projects that have no quota configured.
    stmt = select(Project)
    if project_filter is not None:
        stmt = stmt.where(Project.id.in_(project_filter))
    projects = list((await db.execute(stmt)).scalars().all())

    rows: list[BillingOverviewProject] = []
    total_cost = 0.0
    total_calls = 0
    for project in projects:
        usage = await cost_service.get_current_usage(db, project.id)
        total_cost += float(usage["total_cost_usd"])
        total_calls += int(usage["total_llm_calls"])
        rows.append(
            BillingOverviewProject(
                project_id=project.id,
                project_name=project.name,
                current_cost_usd=float(usage["total_cost_usd"]),
                hard_cap_usd=usage.get("hard_cap_usd"),
                utilization_pct=usage.get("utilization_pct"),
                status=str(usage.get("status") or "UNLIMITED"),
                cap_hits=int(usage.get("cap_hits") or 0),
            )
        )

    # Sort: CAPPED first, then SOFT_WARN, then OK, then UNLIMITED — the
    # ops dashboard should surface problems before the quiet rows.
    status_order = {"CAPPED": 0, "SOFT_WARN": 1, "OK": 2, "UNLIMITED": 3}
    rows.sort(key=lambda r: (status_order.get(r.status, 99), -r.current_cost_usd))

    return BillingOverviewResponse(
        period_start=period_start,
        period_end=period_end,
        total_cost_usd=round(total_cost, 6),
        total_llm_calls=total_calls,
        projects=rows,
        price_table_updated=PRICE_TABLE_UPDATED,
    )
