"""Release management endpoints — CRUD for releases, phases, and test-run links.

Authorization policy:
  - Read endpoints (GET): any authenticated user
  - Mutation endpoints (POST, PUT, DELETE): QA_LEAD or ADMIN
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_release_access,
    require_role,
    require_run_access,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.serializers import serialize_model
from app.services import release_gate_service, release_service

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


@router.get("/{release_id}/gate")
async def get_release_gate(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    """The standing verdict for this release, read from the stored snapshot.

    Deliberately does NOT recompute. Recomputing would restate a past verdict
    under today's runs and today's policy, which is what the snapshot columns on
    ``ReleaseGateDecision`` exist to prevent.

    404 when the release has never been evaluated — distinct from a verdict of
    NOT_EVALUATED, which means it WAS evaluated and there was not enough
    evidence to say. Collapsing those two into one response would lose the
    difference between "we have not looked" and "we looked and cannot say".
    """
    gate = await release_gate_service.current_gate(db, release_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="This release has not been evaluated yet")
    return gate


@router.get("/{release_id}/gate/baseline")
async def get_release_gate_baseline(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    """Release-over-release comparison, or a stated reason it is not meaningful.

    Returns ``comparable: false`` with a reason rather than numbers whenever a
    delta would mislead — no baseline, or either side below the evidence floor.
    A delta against a release nothing ran in is arithmetically fine and
    completely meaningless, and once it is a number on a scorecard nobody
    re-derives whether it was meaningful.
    """
    return await release_gate_service.compare_to_baseline(db, release_id)


# ── Mutation endpoints (QA_LEAD or ADMIN) ────────────────────────────────────

@router.post("", status_code=201)
async def create_release(
    body: ReleaseIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    # ``require_role(QA_LEAD)`` gates by ROLE, never by project membership, and
    # ``project_id`` arrives in the BODY -- which the architectural ratchet
    # matched only in the path, so this route was auto-declared protected. A QA
    # lead of one project could therefore create a release, with its phases,
    # inside any other project on the deployment. Note the sibling
    # ``PUT /{release_id}`` immediately below already carries
    # ``require_release_access()``: create was the odd one out.
    await resolve_project_scope(db, current_user, str(body.project_id))
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


@router.post("/{release_id}/gate/evaluate")
async def evaluate_release_gate(
    release_id: str,
    record: bool = Query(
        True,
        description="Append the verdict to the release's audit history. "
        "Pass false to preview without recording.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    # BOTH guards, as dependencies. `require_role` answers "may this user record
    # verdicts at all"; `require_release_access` answers "may they touch THIS
    # release" — the per-id check the authorization ratchet is blind to once a
    # route satisfies it on any scoped parameter.
    #
    # It has to be a Depends: the guard reads `request.path_params`, so calling
    # it by hand with a `release_id=` keyword raises TypeError on every request.
    _access: User = Depends(require_release_access()),
):
    """Evaluate the gate and, by default, record the verdict.

    QA_LEAD, not plain membership. Recording appends to an append-only audit
    trail that a release decision is later justified by, so it is a privileged
    write even though it computes rather than edits.

    The router owns the commit, per the repo's transaction-boundary rule, so the
    demote of the previous verdict and the insert of the new one land as one
    unit of work — a failure cannot leave a release with two current verdicts or
    none.
    """
    result = await release_gate_service.evaluate_release(
        db, release_id, record=record, created_by_id=current_user.id
    )
    if record:
        await db.commit()
    return result


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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    link, is_new = await release_service.link_test_run(
        db, release_id, body, linked_by_id=current_user.id
    )
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
    # The route carries TWO scoped path params and only the run was checked.
    # The authz ratchet stops at the first scoped param it can satisfy, so it
    # cannot see the gap — a caller with access to the run but not the release
    # could unlink it from a release in a project they cannot reach.
    ___: User = Depends(require_release_access()),
):
    await release_service.unlink_test_run(db, release_id, run_id)
    await db.commit()
