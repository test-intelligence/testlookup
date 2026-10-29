"""Expected run cadences (W23a TRND-03): list, status, add, change, remove, pause, resume.

A cadence is TestLookup's own EXPECTATION ("a run of <suite | any run> arrives
by <time>"): nothing here starts, stops or pauses CI. Every route sits under
``/api/v1/projects/{project_id}`` behind ``require_project_access()``. The
five writes also need ``require_role(UserRole.QA_LEAD)`` WITHOUT
``allow_project_key`` — a project-bound API key is refused (N26): CI keys
report runs, a person sets expectations.

``{cadence_id}`` is always loaded ``WHERE id AND project_id … FOR UPDATE``
(``run_cadence_service.get_cadence``), so another project's cadence and an
unknown id answer the same 404. Every refusal is ``{"detail": {"code",
"message"}}`` (``run_cadence_vocab.CADENCE_ERRORS``).

``GET …/status`` is declared BEFORE every ``/{cadence_id}`` route (there is
no ``GET /{cadence_id}``, so nothing can shadow it). The GETs write nothing.
The service flushes; each write handler stages its ``run_cadence.*`` ledger
row on the same session (outcome mode: a rolled-back write leaves no row),
commits once, then refreshes the row (a commit expires it).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    RunCadenceCreate,
    RunCadenceList,
    RunCadencePatch,
    RunCadencePauseRequest,
    RunCadenceRead,
    RunCadenceStatusResponse,
)
from app.services import run_cadence_service as cadences
from app.services.activity.service import ActorRef, record as record_activity
from app.services.run_cadence_schedule import CadenceError

router = APIRouter(prefix="/api/v1/projects/{project_id}/run-cadences", tags=["Expected runs"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refusal(exc: CadenceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


@router.get("", response_model=RunCadenceList)
async def list_run_cadences(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """The project's expected cadences (at most 20); ``total`` is the full count."""
    now = _now()
    rows = await cadences.list_cadences(db, project_id)
    total = await cadences.count_cadences(db, project_id)
    names = await cadences.user_names(db, cadences.people_of(rows))
    return {"items": [cadences.cadence_read(r, names=names, now=now) for r in rows], "total": total}


@router.get("/status", response_model=RunCadenceStatusResponse)
async def run_cadence_status(
    project_id: uuid.UUID,
    days: int = Query(14, ge=1, le=90, description="The window: the last N days (1-90)."),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Met / missed / paused / pending windows per cadence in the last ``days`` days (writes nothing)."""
    return await cadences.cadence_status(db, project_id, days=days, now=_now())


@router.post("", response_model=RunCadenceRead, status_code=status.HTTP_201_CREATED)
async def create_run_cadence(
    project_id: uuid.UUID,
    body: RunCadenceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Add an expected cadence (QA lead or above; a project-bound key is refused)."""
    now = _now()
    try:
        row = await cadences.create_cadence(db, project_id, body, user=current_user, now=now)
    except CadenceError as exc:
        raise _refusal(exc) from None
    names = await cadences.user_names(db, cadences.people_of([row]))
    await record_activity(
        db,
        project_id=project_id,
        event_type="run_cadence.created",
        actor=ActorRef.from_user(current_user),
        entity_id=str(row.id),
        entity_type="run_cadence",
        entity_label=cadences.scope_label(row),
        context={"summary": cadences.summary_of(row)},
        after=cadences.cadence_values(row),
    )
    await db.commit()
    await db.refresh(row)
    return cadences.cadence_read(row, names=names, now=now)


@router.patch("/{cadence_id}", response_model=RunCadenceRead)
async def update_run_cadence(
    project_id: uuid.UUID,
    cadence_id: uuid.UUID,
    body: RunCadencePatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Change the fields sent; a change re-defines the expectation from now. Nothing changed → nothing written."""
    now = _now()
    try:
        row, changed, before, after = await cadences.update_cadence(db, project_id, cadence_id, body, now=now)
    except CadenceError as exc:
        raise _refusal(exc) from None
    names = await cadences.user_names(db, cadences.people_of([row]))
    if changed:
        await record_activity(
            db,
            project_id=project_id,
            event_type="run_cadence.updated",
            actor=ActorRef.from_user(current_user),
            entity_id=str(row.id),
            entity_type="run_cadence",
            entity_label=cadences.scope_label(row),
            context={"changed": ", ".join(changed)},
            changed_fields=changed,
            before=before,
            after=after,
        )
        await db.commit()
        await db.refresh(row)
    return cadences.cadence_read(row, names=names, now=now)


@router.delete("/{cadence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run_cadence(
    project_id: uuid.UUID,
    cadence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Remove an expected cadence; its window history goes with it."""
    try:
        values = await cadences.delete_cadence(db, project_id, cadence_id)
    except CadenceError as exc:
        raise _refusal(exc) from None
    await record_activity(
        db,
        project_id=project_id,
        event_type="run_cadence.deleted",
        actor=ActorRef.from_user(current_user),
        entity_id=str(values["id"]),
        entity_type="run_cadence",
        entity_label=values["scope"],
        before={k: v for k, v in values.items() if k not in ("id", "scope", "summary")},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{cadence_id}/pause", response_model=RunCadenceRead)
async def pause_run_cadence(
    project_id: uuid.UUID,
    cadence_id: uuid.UUID,
    body: RunCadencePauseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Pause the EXPECTATION (never CI) until a time ≤ 90 days away, with a reason; no alerts meanwhile."""
    now = _now()
    try:
        row = await cadences.pause_cadence(
            db, project_id, cadence_id, until=body.until, reason=body.reason, user=current_user, now=now,
        )
    except CadenceError as exc:
        raise _refusal(exc) from None
    names = await cadences.user_names(db, cadences.people_of([row]))
    await record_activity(
        db,
        project_id=project_id,
        event_type="run_cadence.paused",
        actor=ActorRef.from_user(current_user),
        entity_id=str(row.id),
        entity_type="run_cadence",
        entity_label=cadences.scope_label(row),
        context={"until": cadences.until_label(row), "reason": row.paused_reason},
    )
    await db.commit()
    await db.refresh(row)
    return cadences.cadence_read(row, names=names, now=now)


@router.post("/{cadence_id}/resume", response_model=RunCadenceRead)
async def resume_run_cadence(
    project_id: uuid.UUID,
    cadence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """End a current pause; the next deadline is expected again (decided paused windows stay paused)."""
    now = _now()
    try:
        row = await cadences.resume_cadence(db, project_id, cadence_id, now=now)
    except CadenceError as exc:
        raise _refusal(exc) from None
    names = await cadences.user_names(db, cadences.people_of([row]))
    await record_activity(
        db,
        project_id=project_id,
        event_type="run_cadence.resumed",
        actor=ActorRef.from_user(current_user),
        entity_id=str(row.id),
        entity_type="run_cadence",
        entity_label=cadences.scope_label(row),
    )
    await db.commit()
    await db.refresh(row)
    return cadences.cadence_read(row, names=names, now=now)
