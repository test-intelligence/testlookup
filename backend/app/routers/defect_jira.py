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
* ``PATCH /api/v1/projects/{project_id}/defects/{defect_id}`` — QA_ENGINEER+.
  Triage (DEF-01): status, assignee, owner team, Jira link. Offline the key
  is saved locally; online it is checked against Jira first, and a status
  change on a linked defect is written back after the commit.

Route-order note: the literal ``/defects/jira/...`` sub-paths sit on their
own ``/api/v1/projects``-prefixed router (same pattern as the quarantine
manifest router) so no UUID path param can swallow them.

W36 STRAT-03 — suggest-only Jira epics for recurring systemic clusters
(``services/cluster_epic_service.py``; opt-in per project, default off):

* ``GET  /api/v1/projects/{project_id}/systemic-clusters/epics`` — the
  opt-in, Jira's availability, the Epic issue type, the stored links.
* ``GET  /api/v1/projects/{project_id}/systemic-clusters/{membership_key}/epic/preview``
  — the epic's summary / description; 404 gone, 422 ``not_recurring``.
* ``POST /api/v1/projects/{project_id}/systemic-clusters/{membership_key}/epic``
  — QA_ENGINEER+; 201 filed now, 200 an epic was already stored. Never a
  Defect row; nothing files an epic but this POST.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    ClusterEpicCreateRequest,
    ClusterEpicCreateResponse,
    ClusterEpicPreviewResponse,
    ClusterEpicStatusResponse,
    DefectJiraLinkResult,
    DefectPatchRequest,
    DefectPatchResponse,
    DefectWriteBackResult,
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
    current_user: User = Depends(require_project_access()),
):
    """Jira projects + issue types for the create dialog. Never 5xxs for a
    broken Jira — returns ``available=false`` + ``reason`` instead."""
    return await svc.get_metadata(db, str(project_id), user=current_user)


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
    # VIZ-212: a new OPEN defect changes the cached dashboard's counts.
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(project_id)
    return result


@router.patch(
    "/{project_id}/defects/{defect_id}",
    response_model=DefectPatchResponse,
)
async def update_defect(
    project_id: uuid.UUID,
    defect_id: uuid.UUID,
    body: DefectPatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """Triage a defect: status, assignee, owner team, Jira link (DEF-01). QA_ENGINEER+.

    Only the fields SENT change. Each step's failure leaves nothing written:
    the Jira lookup (online only) runs with the request's connection handed
    back and BEFORE any write; the writes and their ledger rows commit
    together; the Jira status write-back runs after the commit and never
    undoes it.
    """
    from app.db.postgres import release_read_connection
    from app.services import analytics_service
    from app.services import defect_triage_service as triage
    from app.services.cache_service import bump_analytics_epoch

    fields = triage.patch_fields(body)
    if not fields:
        raise HTTPException(status_code=422, detail=triage.NOTHING_TO_CHANGE)

    # 404 for a missing AND for another project's defect: ids are no oracle.
    await triage.get_defect_in_project(db, project_id, defect_id)

    cfg = None
    reason = None
    if "jira_key" in fields or "resolution_status" in fields:
        cfg = await svc.resolve_jira_config(db)
        reason = svc.availability_reason(cfg)

    issue = None
    if "jira_key" in fields and body.jira_key and body.jira_key.strip() and cfg is not None and reason is None:
        key = triage.normalize_jira_key(body.jira_key)
        # Nothing is staged yet: give the connection back for the Jira call.
        await release_read_connection(db)
        issue = await svc.get_issue(cfg, key)

    try:
        outcome = await triage.update_defect(
            db, project_id=project_id, defect_id=defect_id, actor=current_user,
            patch=body, jira_cfg=cfg, jira_issue=issue,
        )
        await db.commit()
    except IntegrityError as exc:
        # The backstop for a concurrent second OPEN that slipped past the
        # service's check: the partial unique index refuses it.
        if "ix_defects_test_case_open_unique" not in str(getattr(exc, "orig", exc)):
            raise
        await db.rollback()
        twin = await _open_twin_of(db, project_id, defect_id)
        if twin is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": triage.REOPEN_CONFLICT,
                    "message": (
                        "This test already has an open defect. Close that one first, "
                        "or link this failure to it."
                    ),
                    "defect_id": None,
                },
            ) from exc
        raise triage.reopen_conflict(twin) from exc

    if outcome.changed_fields:
        # The list, the KPIs and the gate's cached counts read these rows.
        await bump_analytics_epoch(project_id)

    write_back = None
    if outcome.write_back_target and outcome.jira_key and cfg is not None:
        write_back = await svc.transition_issue(cfg, outcome.jira_key, outcome.write_back_target)
    elif outcome.status_change is not None and outcome.jira_key and reason is not None:
        write_back = {"status": "not_sent", "reason": reason}

    row = await analytics_service.defect_row(db, project_id, defect_id)
    return DefectPatchResponse(
        defect=row or {},
        changed_fields=list(outcome.changed_fields),
        jira=DefectJiraLinkResult(**outcome.jira) if outcome.jira else None,
        write_back=DefectWriteBackResult(**write_back) if write_back else None,
    )


# ── W36 STRAT-03: Jira epics for recurring systemic clusters ────────────────

#: ``systemic_cluster_service.membership_key``: the first 32 hex characters of a sha256.
_MEMBERSHIP_KEY_PATTERN = r"^[0-9a-f]{32}$"


@router.get(
    "/{project_id}/systemic-clusters/epics",
    response_model=ClusterEpicStatusResponse,
)
async def cluster_epic_status(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """The cluster list's one status read (reads only; no Jira call while Jira is unavailable)."""
    from app.services import cluster_epic_service

    return await cluster_epic_service.get_epic_status(db, project_id, user=current_user)


@router.get(
    "/{project_id}/systemic-clusters/{membership_key}/epic/preview",
    response_model=ClusterEpicPreviewResponse,
)
async def cluster_epic_preview(
    project_id: uuid.UUID,
    membership_key: str = Path(..., pattern=_MEMBERSHIP_KEY_PATTERN),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """The epic this person would file for this cluster (its description names them as the filer).
    404 gone, 422 ``not_recurring``."""
    from app.services import cluster_epic_service

    return await cluster_epic_service.preview_epic(db, project_id, membership_key, actor=current_user)


@router.post(
    "/{project_id}/systemic-clusters/{membership_key}/epic",
    response_model=ClusterEpicCreateResponse,
    status_code=201,
)
async def create_cluster_epic(
    project_id: uuid.UUID,
    payload: ClusterEpicCreateRequest,
    response: Response,
    membership_key: str = Path(..., pattern=_MEMBERSHIP_KEY_PATTERN),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """File one Jira epic for a recurring cluster (QA_ENGINEER+, the project's opt-in on).

    201 ``created: true`` when this request stored the link; 200 ``created: false`` when an epic was
    already stored for this member set (nothing is sent to Jira). The router owns the unit of work:
    the link row and its activity row commit together.
    """
    from app.services import cluster_epic_service

    result = await cluster_epic_service.create_epic_for_cluster(
        db,
        project_id,
        current_user,
        membership_key,
        jira_project_key=payload.jira_project_key,
        summary=payload.summary,
        confirm_not_filed=payload.confirm_not_filed,
    )
    await db.commit()
    if not result["created"]:
        response.status_code = 200
    return result


async def _open_twin_of(db: AsyncSession, project_id: uuid.UUID, defect_id: uuid.UUID):
    """The OPEN defect that owns the test case of ``defect_id`` (after a 409 rollback)."""
    from sqlalchemy import select

    from app.models.postgres import Defect

    test_case_id = (
        await db.execute(
            select(Defect.test_case_id).where(
                Defect.id == defect_id, Defect.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if test_case_id is None:
        return None
    return (
        await db.execute(
            select(Defect)
            .where(
                Defect.project_id == project_id,
                Defect.test_case_id == test_case_id,
                Defect.id != defect_id,
                Defect.resolution_status == "OPEN",
            )
            .limit(1)
        )
    ).scalars().first()
