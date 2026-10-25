"""The test library's server reads and writes (W13 "test-library").

Included by ``routers/test_management.py`` AFTER the cases router, so every
route here is under ``/api/v1/test-management``:

- ``GET /health`` — library health over the whole library (TCL-03); writes nothing.
- ``GET /cases/{case_id}/timeline`` — one timeline per authored case (TCL-05).
- ``POST /cases/import`` — CSV / XLSX import, a dry run by default (TCL-04).
- ``GET`` / ``POST /cases/{case_id}/requirements``, ``DELETE /cases/{case_id}/requirements/{requirement_link_id}``,
  ``GET /traceability/gaps`` — requirement traceability "lite" behind its flag (TCL-06).

Every QUERY ``project_id`` goes through ``resolve_project_scope`` FIRST; every
``{case_id}`` route depends on ``require_case_access``. The services are
commit-free: the writes here commit in their handler.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_role, resolve_project_scope
from app.core.test_library_vocab import (
    HEALTH_NEEDS_PROJECT_SENTENCE,
    TIMELINE_KIND_INVALID,
    TIMELINE_KINDS,
    TIMELINE_LIMIT_DEFAULT,
    TIMELINE_LIMIT_MAX,
    TRACEABILITY_NEEDS_PROJECT,
    TRACEABILITY_OFF,
)
from app.db.postgres import get_db
from app.models.postgres import ManagedTestCase, User, UserRole
from app.models.schemas import (
    LibraryHealthResponse,
    RequirementLinkCreate,
    RequirementLinkListResponse,
    RequirementLinkRead,
    TestCaseImportResponse,
    TestCaseTimelineResponse,
    TraceabilityGapsResponse,
)
from app.routers.test_management_shared import require_case_access
from app.services.requirement_traceability_service import (
    add_link,
    link_read,
    list_links,
    remove_link,
    traceability_enabled,
    traceability_gaps,
)
from app.services.test_case_import_service import import_cases, read_bounded
from app.services.test_case_timeline_service import case_timeline
from app.services.test_library_health_service import library_health
from app.services.test_management_metrics_service import emit_staged_test_management_metrics

router = APIRouter()


@router.get("/health", response_model=LibraryHealthResponse)
async def get_library_health(
    project_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Library health for ONE project (All Projects has none: 422 with the sentence)."""
    if project_id is None:
        raise HTTPException(status_code=422, detail=HEALTH_NEEDS_PROJECT_SENTENCE)
    await resolve_project_scope(db, current_user, str(project_id))
    include_requirements = await traceability_enabled(db, project_id=project_id, user=current_user)
    return await library_health(
        db,
        project_id=project_id,
        now=datetime.now(timezone.utc),
        include_requirements=include_requirements,
    )


@router.get("/cases/{case_id}/timeline", response_model=TestCaseTimelineResponse)
async def get_test_case_timeline(
    case_id: uuid.UUID,
    kinds: Optional[List[str]] = Query(None),
    cursor: Optional[str] = Query(None, max_length=512),
    limit: int = Query(TIMELINE_LIMIT_DEFAULT, ge=1, le=TIMELINE_LIMIT_MAX),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    case: ManagedTestCase = Depends(require_case_access),
):
    """The case's timeline (TCL-05): newest first, keyset-paged by ``cursor``."""
    requested = list(dict.fromkeys(k.strip() for k in (kinds or []) if k and k.strip()))
    if any(k not in TIMELINE_KINDS for k in requested):
        raise HTTPException(status_code=422, detail=TIMELINE_KIND_INVALID)
    return await case_timeline(db, case, kinds=requested, cursor=cursor, limit=limit)


# ── TCL-04: import from CSV / Excel ──────────────────────────────────────────


@router.post("/cases/import", response_model=TestCaseImportResponse)
async def import_test_cases(
    project_id: uuid.UUID = Query(...),
    dry_run: bool = Query(True),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
):
    """A dry run by default (nothing written); ``dry_run=false`` creates every valid row as a Draft."""
    await resolve_project_scope(db, current_user, str(project_id))   # FIRST: before the body is read
    content = await read_bounded(file)
    result = await import_cases(db, project_id=project_id, actor=current_user,
                                file_name=file.filename or "upload", content=content, dry_run=dry_run)
    if not dry_run and result["summary"]["created"] > 0:
        await db.commit()
        await emit_staged_test_management_metrics(db)
    return result


# ── TCL-06: requirement traceability "lite" (behind its flag) ────────────────


async def _require_traceability(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if not await traceability_enabled(db, project_id=project_id, user=user):
        raise HTTPException(status_code=404, detail=TRACEABILITY_OFF)


@router.get("/cases/{case_id}/requirements", response_model=RequirementLinkListResponse)
async def list_case_requirements(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    case: ManagedTestCase = Depends(require_case_access),
):
    """The case's manual requirement links, oldest first."""
    await _require_traceability(db, case.project_id, current_user)
    items = await list_links(db, case)
    return {"items": items, "total": len(items)}


@router.post(
    "/cases/{case_id}/requirements", response_model=RequirementLinkRead, status_code=status.HTTP_201_CREATED,
)
async def add_case_requirement(
    case_id: uuid.UUID,
    payload: RequirementLinkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
    case: ManagedTestCase = Depends(require_case_access),
):
    """Link a requirement key to the case (TESTER+); records ``test_case.requirement_linked``."""
    await _require_traceability(db, case.project_id, current_user)
    link = await add_link(db, case, payload, current_user)
    await db.commit()
    await db.refresh(link)
    name = (current_user.full_name or "").strip() or (current_user.username or "").strip() or None
    return link_read(link, name)


@router.delete(
    "/cases/{case_id}/requirements/{requirement_link_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_case_requirement(
    case_id: uuid.UUID,
    requirement_link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
    case: ManagedTestCase = Depends(require_case_access),
):
    """Unlink one of THIS case's requirement links (TESTER+); records ``test_case.requirement_unlinked``."""
    await _require_traceability(db, case.project_id, current_user)
    await remove_link(db, case, requirement_link_id, current_user)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/traceability/gaps", response_model=TraceabilityGapsResponse)
async def get_traceability_gaps(
    project_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The project's uncovered requirements, paged, and the cases linking none."""
    if project_id is None:
        raise HTTPException(status_code=422, detail=TRACEABILITY_NEEDS_PROJECT)
    await resolve_project_scope(db, current_user, str(project_id))
    await _require_traceability(db, project_id, current_user)
    return await traceability_gaps(db, project_id=project_id, page=page, size=size)
