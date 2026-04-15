"""
GitHub Checks integration API — Tier 1 item 5.

Endpoints:

* ``GET    /api/v1/projects/{project_id}/github-integration``
* ``PUT    /api/v1/projects/{project_id}/github-integration``
* ``POST   /api/v1/projects/{project_id}/github-integration/test``
* ``DELETE /api/v1/projects/{project_id}/github-integration``

PUT / DELETE / test require ``QA_LEAD`` or higher — flaky integration
config is a platform decision, not an individual dev's call. GET is
available to any project member so dev dashboards can show current
status without privileged access.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    GitHubConnectionTestResponse,
    GitHubIntegrationRead,
    GitHubIntegrationWrite,
)
from app.services import github_checks_service as svc

router = APIRouter(prefix="/api/v1/projects", tags=["GitHub Integration"])
logger = structlog.get_logger("routers.github_integration")


@router.get(
    "/{project_id}/github-integration",
    response_model=GitHubIntegrationRead,
)
async def get_github_integration(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    row = await svc.get_integration(db, project_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No GitHub integration configured for this project",
        )
    return row


@router.put(
    "/{project_id}/github-integration",
    response_model=GitHubIntegrationRead,
)
async def upsert_github_integration(
    project_id: uuid.UUID,
    payload: GitHubIntegrationWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Create or replace the GitHub integration for a project. QA_LEAD+."""
    row = await svc.upsert_integration(
        db,
        project_id=project_id,
        actor=current_user,
        enabled=payload.enabled,
        repo_owner=payload.repo_owner,
        repo_name=payload.repo_name,
        api_base_url=payload.api_base_url,
        pat=payload.pat,
    )
    return row


@router.post(
    "/{project_id}/github-integration/test",
    response_model=GitHubConnectionTestResponse,
)
async def test_github_integration(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Probe the configured repo + PAT and report whether the
    integration can actually reach GitHub. QA_LEAD+."""
    result = await svc.test_connection(db, project_id)
    return GitHubConnectionTestResponse(**result)


@router.delete(
    "/{project_id}/github-integration",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_github_integration(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Delete the GitHub integration row for a project. The stored PAT
    secret is cleared from ``secret_service`` via the standard upsert
    path with an empty value."""
    row = await svc.get_integration(db, project_id)
    if row is None:
        return None

    # Clear the PAT then drop the row. Audit trail comes from the
    # settings_audit_log entry written by upsert_integration when we
    # cleared the secret.
    await svc.upsert_integration(
        db,
        project_id=project_id,
        actor=current_user,
        enabled=False,
        repo_owner=row.repo_owner,
        repo_name=row.repo_name,
        api_base_url=row.api_base_url,
        pat="",
    )
    await db.delete(row)
    await db.commit()
    return None
