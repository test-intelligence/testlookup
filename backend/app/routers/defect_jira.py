"""
One-click Jira defect creation (PMF US-6.1 / US-6.3).

Endpoints (all project-scoped so ``require_project_access`` applies —
authorization ratchet):

* ``GET  /api/v1/projects/{project_id}/defects/jira/metadata`` — Jira
  projects + issue types for the dialog pickers (cached ~5 min in the
  service; graceful ``available=false`` shape when Jira is unreachable,
  unconfigured, or offline-gated).
* ``GET  /api/v1/projects/{project_id}/defects/jira/preview`` — server-side
  pre-filled summary/description for a failure signature (fingerprint or
  cluster id), including dedup info when an open linked defect exists.
* ``POST /api/v1/projects/{project_id}/defects/jira`` — QA_ENGINEER+.
  Dedup-first create: an already-linked open defect for the same signature
  gets a "recurred in build X" Jira comment instead of a duplicate issue.
  ``target="webhook"`` emits ``defect.create_requested`` through the
  outbound-webhook subsystem instead of calling Jira.

Route-order note: the literal ``/defects/jira/...`` sub-paths sit on their
own ``/api/v1/projects``-prefixed router (same pattern as the quarantine
manifest router) so no UUID path param can swallow them.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    JiraDefectCreateRequest,
    JiraDefectCreateResponse,
    JiraDefectMetadataResponse,
    JiraDefectPreviewResponse,
)
from app.services import defect_jira_service as svc

router = APIRouter(prefix="/api/v1/projects", tags=["Defects"])


@router.get(
    "/{project_id}/defects/jira/metadata",
    response_model=JiraDefectMetadataResponse,
)
async def jira_defect_metadata(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Jira projects + issue types for the create dialog. Never 5xxs for a
    broken Jira — returns ``available=false`` + ``reason`` instead."""
    return await svc.get_metadata(db, str(project_id))


@router.get(
    "/{project_id}/defects/jira/preview",
    response_model=JiraDefectPreviewResponse,
)
async def jira_defect_preview(
    project_id: uuid.UUID,
    fingerprint: str | None = Query(None, min_length=1, max_length=64),
    cluster_id: str | None = Query(None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Pre-filled Jira payload for the dialog's read-only preview.

    422 when neither ``fingerprint`` nor ``cluster_id`` is given; 404 when
    the signature matches nothing in the project.
    """
    return await svc.build_prefill(
        db, str(project_id), fingerprint=fingerprint, cluster_id=cluster_id,
    )


@router.post(
    "/{project_id}/defects/jira",
    response_model=JiraDefectCreateResponse,
)
async def create_jira_defect_one_click(
    project_id: uuid.UUID,
    payload: JiraDefectCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """One-click defect creation from a failure signature. QA_ENGINEER+.

    The user reviews the prefilled payload in the dialog first — this
    endpoint re-assembles it server-side (never trusts a client-edited
    stack trace) and applies dedup before filing anything.
    """
    result = await svc.create_or_link_issue(
        db,
        str(project_id),
        current_user,
        fingerprint=payload.fingerprint,
        cluster_id=payload.cluster_id,
        issue_type=payload.issue_type,
        jira_project_key=payload.jira_project_key,
        assignee=payload.assignee,
        extra_comment=payload.extra_comment,
        target=payload.target,
        confirm_not_filed=payload.confirm_not_filed,
    )
    # Router owns the unit of work: the dedup recurrence bump or the new
    # Defect row staged by the service commits here.
    await db.commit()
    return result
