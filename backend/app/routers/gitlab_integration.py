"""
GitLab integration API — PMF backlog Epic 3 (US-3.1).

Endpoints (config contract — frontend built in parallel):

* ``GET  /api/v1/projects/{project_id}/integrations/gitlab``      → GitLabConfig
* ``PUT  /api/v1/projects/{project_id}/integrations/gitlab``      → GitLabConfig
* ``POST /api/v1/projects/{project_id}/integrations/gitlab/test`` → test result

The PAT is NEVER returned — ``has_token`` is the only token signal on GET.
PUT accepts an optional write-only ``token`` that sets/rotates the PAT via
``secret_service``. PUT / test require ``QA_LEAD`` or higher (integration
config is a platform decision); GET is available to any project member so
dashboards can show status without privileged access. GET returns the
default config (disabled, ``https://gitlab.com``, empty path) when nothing
is configured yet rather than 404 — the UI always renders the form.
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import GitLabIntegration, User, UserRole
from app.models.schemas import GitLabConfig, GitLabConnectionTestResponse
from app.services import gitlab_integration_service as svc

router = APIRouter(prefix="/api/v1/projects", tags=["GitLab Integration"])
logger = structlog.get_logger("routers.gitlab_integration")


def _to_config(row: Optional[GitLabIntegration]) -> GitLabConfig:
    """Serialize a row to the config contract — the PAT is NEVER included;
    ``has_token`` is the only token signal. ``None`` → default config."""
    if row is None:
        return GitLabConfig()
    return GitLabConfig(
        enabled=row.enabled,
        base_url=row.base_url,
        project_path=row.project_path,
        mr_comment_mode=row.mr_comment_mode,  # type: ignore[arg-type]
        commit_status_enabled=row.commit_status_enabled,
        has_token=row.has_pat,
        last_error=row.last_error,
        last_error_at=row.last_error_at,
    )


@router.get(
    "/{project_id}/integrations/gitlab",
    response_model=GitLabConfig,
    response_model_exclude={"token"},
)
async def get_gitlab_integration(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Return the project's GitLab config (defaults when unconfigured)."""
    row = await svc.get_integration(db, project_id)
    return _to_config(row)


@router.put(
    "/{project_id}/integrations/gitlab",
    response_model=GitLabConfig,
    response_model_exclude={"token"},
)
async def upsert_gitlab_integration(
    project_id: uuid.UUID,
    payload: GitLabConfig,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Create or replace the GitLab integration for a project. QA_LEAD+.

    ``token`` is write-only: ``None`` leaves the stored PAT alone, ``""``
    clears it, any value sets/rotates it."""
    row = await svc.upsert_integration(
        db,
        project_id=project_id,
        actor=current_user,
        enabled=payload.enabled,
        base_url=payload.base_url,
        project_path=payload.project_path,
        mr_comment_mode=payload.mr_comment_mode,
        commit_status_enabled=payload.commit_status_enabled,
        token=payload.token,
    )
    return _to_config(row)


@router.post(
    "/{project_id}/integrations/gitlab/test",
    response_model=GitLabConnectionTestResponse,
)
async def test_gitlab_integration(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Probe the configured project + PAT and report reachability. QA_LEAD+."""
    result = await svc.test_connection(db, project_id)
    return GitLabConnectionTestResponse(**result)
