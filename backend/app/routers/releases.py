"""Release management endpoints — CRUD for releases, phases, and test-run links.

Authorization policy:
  - Read endpoints (GET): any authenticated user
  - Mutation endpoints (POST, PUT, DELETE): QA_LEAD or ADMIN
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_release_access,
    require_role,
    require_run_access,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.serializers import serialize_model
from app.services import release_service

router = APIRouter(prefix="/api/v1/releases", tags=["Releases"])


class PhaseIn(BaseModel):
    name: str
    phase_type: str = "qa_testing"
    status: str = "pending"
    description: Optional[str] = None
    order_index: int = 0
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    exit_criteria: Optional[dict] = None
    notes: Optional[str] = None


class PhaseUpdate(BaseModel):
    name: Optional[str] = None
    phase_type: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    order_index: Optional[int] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    exit_criteria: Optional[dict] = None
    notes: Optional[str] = None


class ReleaseIn(BaseModel):
    project_id: str
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    status: str = "planning"
    planned_date: Optional[datetime] = None
    phases: list[PhaseIn] = []


class ReleaseUpdate(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    planned_date: Optional[datetime] = None
    released_at: Optional[datetime] = None


class LinkRunRequest(BaseModel):
    test_run_id: str
    phase_id: Optional[str] = None


# ── Read endpoints (any authenticated user) ──────────────────────────────────

@router.get("")
async def list_releases(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    accessible = await get_accessible_project_ids(db, current_user)
    if project_id is not None:
        # Verify access to the explicitly-requested project. Previously a
        # provided project_id skipped the accessible-projects gate entirely
        # (only the no-project_id path was guarded), so any authenticated user
        # could list another tenant's releases via ?project_id=<foreign-uuid>.
        try:
            requested = uuid.UUID(project_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid project_id") from exc
        if accessible is not None and requested not in accessible:
            raise HTTPException(
                status_code=403, detail="You do not have access to this project"
            )
    elif accessible is not None and not accessible:
        # Non-admin with no memberships, all-projects view → nothing to show.
        return []
    # Pass the accessible set so the service confines results to the caller's
    # projects (fan-out in all-projects mode; defence-in-depth when pinned).
    return await release_service.list_releases(
        db, project_id, status, accessible_project_ids=accessible,
    )


@router.get("/{release_id}")
async def get_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    return await release_service.get_release_details(db, release_id)


# ── Mutation endpoints (QA_LEAD or ADMIN) ────────────────────────────────────

@router.post("", status_code=201)
async def create_release(
    body: ReleaseIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
):
    release = await release_service.create_release(db, body)
    await db.commit()
    return await release_service.serialize_created_release(db, release)


@router.put("/{release_id}")
async def update_release(
    release_id: str,
    body: ReleaseUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    release = await release_service.update_release(db, release_id, body)
    await db.commit()
    await db.refresh(release)
    return serialize_model(release)


@router.delete("/{release_id}", status_code=204)
async def delete_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
    __: User = Depends(require_release_access()),
):
    await release_service.delete_release(db, release_id)
    await db.commit()


@router.post("/{release_id}/phases", status_code=201)
async def add_phase(
    release_id: str,
    body: PhaseIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    phase = await release_service.add_phase(db, release_id, body)
    await db.commit()
    await db.refresh(phase)
    return serialize_model(phase)


@router.put("/{release_id}/phases/{phase_id}")
async def update_phase(
    release_id: str,
    phase_id: str,
    body: PhaseUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    phase, all_done = await release_service.update_phase(db, release_id, phase_id, body)
    await db.commit()
    await db.refresh(phase)
    result = serialize_model(phase)
    result["all_phases_completed"] = all_done
    return result


@router.delete("/{release_id}/phases/{phase_id}", status_code=204)
async def delete_phase(
    release_id: str,
    phase_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
    __: User = Depends(require_release_access()),
):
    await release_service.delete_phase(db, release_id, phase_id)
    await db.commit()


@router.post("/{release_id}/test-runs")
async def link_test_run(
    release_id: str,
    body: LinkRunRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    link, is_new = await release_service.link_test_run(db, release_id, body)
    if is_new:
        await db.commit()
        await db.refresh(link)
        return serialize_model(link)
    # Idempotent: link already existed, service returned the existing row.
    return {"message": "Already linked", "id": str(link.id)}


@router.delete("/{release_id}/test-runs/{run_id}", status_code=204)
async def unlink_test_run(
    release_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
    __: User = Depends(require_run_access()),
):
    await release_service.unlink_test_run(db, release_id, run_id)
    await db.commit()
