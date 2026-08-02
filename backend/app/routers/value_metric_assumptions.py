"""
Per-project hours-saved assumptions API — PMF US-12.1.

Endpoints (contract — frontend built in parallel):

* ``GET /api/v1/projects/{project_id}/value-metrics/assumptions``
  → ValueMetricAssumptionsRead (effective values + source). Any project
  member — the value-metrics page shows the rate card without privilege.
* ``PUT /api/v1/projects/{project_id}/value-metrics/assumptions``
  → ValueMetricAssumptionsRead. QA_LEAD+ (the rate card is a platform
  decision); body is ``ValueMetricAssumptionsWrite`` (all-optional floats,
  0 < x <= 480 — outside bounds 422s via the schema).

Guard pattern mirrors ``gitlab_integration``: ``require_role`` +
``require_project_access()`` (authorization ratchet). The service stages
(``flush``); the router session owns the commit (transaction ratchet —
``get_db`` commits on successful return).
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    ValueMetricAssumptionsRead,
    ValueMetricAssumptionsWrite,
)
from app.services import value_metrics_service as svc

router = APIRouter(prefix="/api/v1/projects", tags=["Value Metrics"])
logger = structlog.get_logger("routers.value_metric_assumptions")


@router.get(
    "/{project_id}/value-metrics/assumptions",
    response_model=ValueMetricAssumptionsRead,
)
async def get_value_metric_assumptions(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Effective hours-saved assumptions for a project (defaults when no
    row exists — the UI always renders the form)."""
    effective = await svc.get_effective_assumptions(db, project_id)
    return ValueMetricAssumptionsRead(
        **effective.as_dict(), source=effective.source
    )


@router.put(
    "/{project_id}/value-metrics/assumptions",
    response_model=ValueMetricAssumptionsRead,
)
async def put_value_metric_assumptions(
    project_id: uuid.UUID,
    payload: ValueMetricAssumptionsWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Upsert the project's assumptions row. QA_LEAD+. Omitted fields keep
    their current (or default) value; returns the effective assumptions."""
    await svc.upsert_assumptions(
        db,
        project_id=project_id,
        actor_id=current_user.id,
        triage_minutes_per_failure=payload.triage_minutes_per_failure,
        blocked_run_wait_minutes=payload.blocked_run_wait_minutes,
        defect_filing_minutes=payload.defect_filing_minutes,
    )
    logger.info(
        "value-metric assumptions upserted",
        project_id=str(project_id),
        actor_id=str(current_user.id),
        fields_set=[
            name
            for name, value in (
                ("triage_minutes_per_failure", payload.triage_minutes_per_failure),
                ("blocked_run_wait_minutes", payload.blocked_run_wait_minutes),
                ("defect_filing_minutes", payload.defect_filing_minutes),
            )
            if value is not None
        ],
    )
    effective = await svc.get_effective_assumptions(db, project_id)
    return ValueMetricAssumptionsRead(
        **effective.as_dict(), source=effective.source
    )
