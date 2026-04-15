"""
Flaky-test quarantine API — Tier 1 item 3.

Endpoints exposed under ``/api/v1/quarantine``:

* ``GET /`` — list quarantine requests, filtered by project scope and status.
* ``GET /stats`` — counts per status for the header tiles on the UI.
* ``GET /{request_id}`` — single request detail.
* ``POST /`` — manual proposal (rarely used; detection agent is the primary path).
* ``POST /{request_id}/approve`` — QA Lead approves → test becomes actively quarantined.
* ``POST /{request_id}/reject`` — QA Lead refuses.
* ``POST /{request_id}/release`` — QA Lead ends an active quarantine early.

Write operations require ``QA_LEAD`` or higher per the original plan —
flaky policy ownership is a QA Lead decision.
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    get_db,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    FlakyQuarantineRead,
    QuarantineDecisionRequest,
    QuarantineProposeRequest,
    QuarantineStatsResponse,
)
from app.services import flaky_quarantine_service as svc

router = APIRouter(prefix="/api/v1/quarantine", tags=["Flaky Quarantine"])
logger = structlog.get_logger("routers.flaky_quarantine")


# ── List / stats ────────────────────────────────────────────────────────────


@router.get("", response_model=list[FlakyQuarantineRead])
async def list_quarantine_requests(
    project_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = None,
    live_only: bool = False,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List quarantine requests visible to the caller.

    Non-admin callers see only projects they're members of. Admins see
    everything. The ``status_filter`` parameter accepts any value from
    ``FlakyQuarantineStatus``.
    """
    project_ids: Optional[set[uuid.UUID]]
    if project_id is not None:
        await resolve_project_scope(db, current_user, str(project_id))
        project_ids = {project_id}
    else:
        accessible = await get_accessible_project_ids(db, current_user)
        project_ids = accessible  # None = admin (no filter)

    return await svc.list_requests(
        db,
        project_ids=project_ids,
        status_filter=status_filter,
        live_only=live_only,
        limit=min(max(1, limit), 500),
    )


@router.get("/stats", response_model=QuarantineStatsResponse)
async def get_quarantine_stats(
    project_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return status counts for the UI header."""
    project_ids: Optional[set[uuid.UUID]]
    if project_id is not None:
        await resolve_project_scope(db, current_user, str(project_id))
        project_ids = {project_id}
    else:
        project_ids = await get_accessible_project_ids(db, current_user)

    counts = await svc.stats_by_status(db, project_ids=project_ids)
    live_states = {
        "DETECTED", "PROPOSED", "APPROVED",
        "QUARANTINED", "RECHECK_SCHEDULED", "RE_QUARANTINED",
    }
    return QuarantineStatsResponse(
        detected=counts.get("DETECTED", 0),
        proposed=counts.get("PROPOSED", 0),
        approved=counts.get("APPROVED", 0),
        quarantined=counts.get("QUARANTINED", 0),
        recheck_scheduled=counts.get("RECHECK_SCHEDULED", 0),
        re_quarantined=counts.get("RE_QUARANTINED", 0),
        released=counts.get("RELEASED", 0),
        rejected=counts.get("REJECTED", 0),
        expired=counts.get("EXPIRED", 0),
        total_live=sum(v for k, v in counts.items() if k in live_states),
    )


@router.get("/{request_id}", response_model=FlakyQuarantineRead)
async def get_quarantine_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return row


# ── Writes ──────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=FlakyQuarantineRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    payload: QuarantineProposeRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Manually propose a test for quarantine. QA_LEAD+ only."""
    await resolve_project_scope(db, current_user, str(payload.project_id))
    row = await svc.propose_quarantine(
        project_id=payload.project_id,
        test_fingerprint=payload.test_fingerprint,
        test_name=payload.test_name,
        suite_name=payload.suite_name,
        detection_method=payload.detection_method,
        flip_rate=payload.flip_rate,
        flip_window_size=payload.flip_window_size,
        pass_count=payload.pass_count,
        fail_count=payload.fail_count,
        rationale=payload.rationale,
        quarantine_duration_days=payload.quarantine_duration_days,
        actor=current_user,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Flaky auto-quarantine is disabled. Ask an admin to enable "
                "the 'flaky_auto_quarantine' feature flag."
            ),
        )
    return row


@router.post("/{request_id}/approve", response_model=FlakyQuarantineRead)
async def approve_quarantine(
    request_id: uuid.UUID,
    payload: QuarantineDecisionRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return await svc.approve(
        db,
        request_id,
        current_user,
        notes=payload.notes,
        quarantine_duration_days=payload.quarantine_duration_days,
    )


@router.post("/{request_id}/reject", response_model=FlakyQuarantineRead)
async def reject_quarantine(
    request_id: uuid.UUID,
    payload: QuarantineDecisionRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return await svc.reject(db, request_id, current_user, notes=payload.notes)


@router.post("/{request_id}/release", response_model=FlakyQuarantineRead)
async def release_quarantine(
    request_id: uuid.UUID,
    payload: QuarantineDecisionRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return await svc.release(db, request_id, current_user, notes=payload.notes)
