"""Deployment-wide storage reads for administrators (S3).

Separate router rather than a path on ``retention.py`` for a concrete reason:
that router is mounted at ``/api/v1/projects``, so any literal segment added
under it — ``/projects/deleted/...`` — is matched against the ``{project_id}``
UUID converter first and 422s. This prefix cannot collide.

Read-only. Nothing here deletes, and nothing here enables retention.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db, require_role
from app.models.postgres import User, UserRole
from app.models.schemas import DeletedProjectsStorageResponse
from app.services import storage_accounting_service

router = APIRouter(prefix="/api/v1/admin/storage", tags=["Admin Storage"])
logger = structlog.get_logger("routers.admin_storage")


@router.get(
    "/deleted-projects",
    response_model=DeletedProjectsStorageResponse,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def get_deleted_project_storage(
    limit: int = Query(
        storage_accounting_service.MAX_DELETED_PROJECTS_SCANNED, ge=1, le=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """What deleted projects still cost, and what will never reclaim it.

    Deleting a project sets ``is_active = False`` and revokes its credentials;
    nothing reconciles the data. Because ``ProjectRetentionPolicy.enabled``
    defaults to ``False``, a project deleted without retention turned on is
    purged by **nothing, ever** — across all five stores, invisible on every
    other screen.

    ``reachable_by_retention`` is the distinction that matters: the nightly
    beat selects on ``enabled`` alone and does **not** filter ``is_active``, so
    a project that opted in before deletion still gets swept. One that did not
    is stranded, and ``unreachable_by_retention`` counts those.

    Deployment-wide, so it is ADMIN-only and not project-scoped — there is no
    ``{project_id}`` here to guard, and a per-project footprint already exists
    at ``GET /api/v1/projects/{project_id}/storage``.

    Bounded by ``limit``: each footprint costs at least one paginated
    object-store listing. ``truncated`` and ``projects_measured`` say when the
    answer is partial, so a capped total cannot quietly understate the number
    this endpoint exists to surface.
    """
    report = await storage_accounting_service.deleted_project_footprints(
        db, limit=limit
    )
    logger.info(
        "deleted_project_storage_read",
        actor_id=str(current_user.id),
        projects_total=report.projects_total,
        projects_measured=len(report.projects),
        unreachable_by_retention=report.unreachable_by_retention,
    )
    return DeletedProjectsStorageResponse(**report.as_payload())
