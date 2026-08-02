"""
Per-project retention policy API — PMF US-11.4.

Endpoints (contract — frontend built in parallel):

* ``GET /api/v1/projects/{project_id}/retention-policy`` — any project
  member: effective policy (+``source``) and the latest execute-mode
  purge summary (``last_purge``).
* ``PUT /api/v1/projects/{project_id}/retention-policy`` — ADMIN: partial
  update of ``enabled`` + the four day windows. Bounds 422 at the schema;
  the audit≥runs cross-check 422s from the service on merged values.
* ``POST /api/v1/projects/{project_id}/retention-policy/preview`` — ADMIN:
  runs the purge service in preview mode NOW, synchronously. Read-only —
  works even while the policy is disabled (preview is how admins decide
  whether to enable).
* ``POST /api/v1/projects/{project_id}/retention-policy/purge`` — ADMIN:
  typed-name confirmation, 409 while the policy is disabled; enqueues the
  Celery purge for THIS project in execute mode → 202 ``{queued: true}``.

Guard pattern mirrors ``projects.py``'s reset endpoint: ``require_role``
+ ``require_project_access()`` (authorization ratchet — ADMIN bypasses the
membership check, the project guard ties the route to its ``{project_id}``
scope). The service stages; the router session owns the commit
(transaction ratchet — ``get_db`` commits on successful return).
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    RetentionPolicyRead,
    RetentionPolicyWrite,
    RetentionPreviewResponse,
    RetentionPurgeQueued,
    RetentionPurgeRequest,
)
from app.services import retention_service as svc

router = APIRouter(prefix="/api/v1/projects", tags=["Retention"])
logger = structlog.get_logger("routers.retention")


@router.get(
    "/{project_id}/retention-policy",
    response_model=RetentionPolicyRead,
)
async def get_retention_policy(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Effective retention policy for a project (defaults when no row
    exists — the UI always renders the form) plus the latest purge."""
    effective = await svc.get_effective_policy(db, project_id)
    last_purge = await svc.get_last_purge(db, project_id)
    return RetentionPolicyRead(
        **effective.as_dict(), source=effective.source, last_purge=last_purge,
    )


@router.put(
    "/{project_id}/retention-policy",
    response_model=RetentionPolicyRead,
)
async def put_retention_policy(
    project_id: uuid.UUID,
    payload: RetentionPolicyWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_project_access()),
):
    """Upsert the project's retention policy. ADMIN-only — retention drives
    a destructive scheduled purge. Omitted fields keep their current (or
    default) value; returns the effective policy."""
    try:
        effective = await svc.upsert_policy(
            db,
            project_id=project_id,
            actor_id=current_user.id,
            actor_name=current_user.full_name or current_user.email,
            enabled=payload.enabled,
            raw_events_days=payload.raw_events_days,
            runs_days=payload.runs_days,
            artifacts_days=payload.artifacts_days,
            audit_days=payload.audit_days,
        )
    except svc.RetentionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    last_purge = await svc.get_last_purge(db, project_id)
    return RetentionPolicyRead(
        **effective.as_dict(), source=effective.source, last_purge=last_purge,
    )


@router.post(
    "/{project_id}/retention-policy/preview",
    response_model=RetentionPreviewResponse,
)
async def preview_retention_purge(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_project_access()),
):
    """Dry-run the purge NOW (synchronous, read-only): per-class cutoffs and
    candidate counts. Deliberately available while the policy is disabled."""
    out = await svc.run_purge(db, project_id=project_id, mode="preview")
    return RetentionPreviewResponse(
        cutoffs=out["cutoffs"], candidates=out["candidates"],
    )


@router.post(
    "/{project_id}/retention-policy/purge",
    response_model=RetentionPurgeQueued,
    status_code=202,
)
async def enqueue_retention_purge(
    project_id: uuid.UUID,
    payload: RetentionPurgeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_project_access()),
):
    """Enqueue an execute-mode purge for THIS project (202).

    Two-step confirmation: ``confirmation_name`` must equal the project's
    name exactly (422 on mismatch — the project-reset convention). 409
    while the policy is disabled.
    """
    try:
        project = await svc.validate_purge_request(
            db, project_id=project_id, confirmation_name=payload.confirmation_name,
        )
    except svc.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except svc.ConfirmationMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except svc.PolicyDisabled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Lazy import — routers must not pull the Celery app at import time.
    from app.worker.tasks import run_retention_purges

    run_retention_purges.apply_async(kwargs={"project_id": str(project_id)})
    logger.info(
        "retention_purge_enqueued",
        project_id=str(project_id),
        project_name=project.name,
        actor_id=str(current_user.id),
    )
    return RetentionPurgeQueued(queued=True)
