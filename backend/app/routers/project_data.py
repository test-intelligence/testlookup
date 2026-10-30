"""Project export / import (W24 ADM-05 phase 1: settings + catalog) — the six routes of contract §3.1.

* ``POST /api/v1/projects/{project_id}/export`` — QA lead or ADMIN with access to the project
  (``require_project_access``; a project-bound key refused by ``require_role``). Stages a ``queued``
  job, COMMITS, then enqueues the worker; 202.
* ``GET /api/v1/projects/{project_id}/data-jobs`` — that project's exports, newest first.
* ``GET /api/v1/project-data-jobs/{data_job_id}`` and ``…/download`` — the job (and its archive):
  ``require_project_data_job_access`` loads it and answers the ONE 404 ``job_not_found`` for an unknown
  id, another project's export, another admin's import and a role below QA lead alike (no oracle).
* ``POST /api/v1/projects/import`` — ADMIN; multipart (``file``, ``client_token``, ``name``). The body is
  counted into a spool BEFORE a job row exists (413 past 50 MB); a repeated ``client_token`` answers
  200 with the first job; the route never parses the archive (the worker does).
* ``GET /api/v1/project-imports`` — the caller's own imports (ADMIN).

The GETs write nothing (no expiry, no "downloaded" stamp: the janitor is the only expiry path). Every
route uses the request session; the worker opens its own. Every refusal is ``{code, message}``.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import Project, ProjectDataJob, User, UserRole
from app.models.schemas import ProjectDataJobList, ProjectDataJobRead
from app.services import project_data_job_service as jobs
from app.services.project_export_vocab import JOBS_LIST_LIMIT

_slog = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Project data"])

_SLUG_UNSAFE = re.compile(r"[^a-z0-9-]+")


def _refusal(code: str, http_status: int, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = jobs.error_detail(code)
    detail.update(extra)
    return HTTPException(status_code=http_status, detail=detail)


def _name_of(user: User) -> Optional[str]:
    return user.full_name or user.username


def _now() -> datetime:
    return datetime.now(timezone.utc)


def require_project_data_job_access():
    """The job of ``{data_job_id}``, or the ONE 404 ``job_not_found`` (``load_job_for``'s rule: an export
    for a QA lead / ADMIN with access to ITS project, an import for the ADMIN who requested it)."""

    async def _check(
        data_job_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> ProjectDataJob:
        return await jobs.load_job_for(db, data_job_id, current_user)

    return _check


def dispatch(task: Any, job_id: uuid.UUID) -> bool:
    """Hand a COMMITTED job to the worker with its real payload. A broker refusal leaves it ``queued``:
    the janitor re-dispatches it after ``QUEUED_REDISPATCH_MINUTES`` (never ``failed`` here)."""
    try:
        task.apply_async(args=[str(job_id)])
        return True
    except Exception as exc:  # noqa: BLE001 - any broker error leaves the job queued
        _slog.warning("project_data_dispatch_failed", job_id=str(job_id), error_type=type(exc).__name__)
        return False


async def _read(db: AsyncSession, job: ProjectDataJob) -> ProjectDataJobRead:
    names = await jobs.requester_names(db, [job])
    return jobs.to_read(job, requested_by_name=names.get(job.requested_by_id) if job.requested_by_id else None,
                        now=_now())


async def _page(db: AsyncSession, rows: list[ProjectDataJob], total: int) -> ProjectDataJobList:
    names = await jobs.requester_names(db, rows)
    now = _now()
    items = [
        jobs.to_read(job, requested_by_name=names.get(job.requested_by_id) if job.requested_by_id else None, now=now)
        for job in rows
    ]
    return ProjectDataJobList(items=items, total=total)


# ── export ───────────────────────────────────────────────────────────────────

# activity: none - the worker records project.exported when the archive is verified
@router.post(
    "/projects/{project_id}/export",
    response_model=ProjectDataJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role(UserRole.QA_LEAD)), Depends(require_project_access())],
)
async def start_project_export(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ProjectDataJobRead:
    """Queue an export of the project's settings + catalog (an archived project too: the access rule is
    ``require_project_access``'s, unchanged). 409 ``export_running`` names the live job."""
    from app.worker.tasks import export_project_data

    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        job = await jobs.create_export_job(db, project=project, actor=current_user)
    except jobs.ExportRunning as running:
        raise _refusal(
            "export_running", status.HTTP_409_CONFLICT, job_id=str(running.job_id) if running.job_id else None,
        ) from None
    await db.commit()
    await db.refresh(job)
    dispatch(export_project_data, job.id)
    _slog.info("project_export_requested", job_id=str(job.id), project_id=str(project_id))
    return jobs.to_read(job, requested_by_name=_name_of(current_user), now=_now())


@router.get(
    "/projects/{project_id}/data-jobs",
    response_model=ProjectDataJobList,
    dependencies=[Depends(require_role(UserRole.QA_LEAD)), Depends(require_project_access())],
)
async def list_project_exports(
    project_id: uuid.UUID,
    limit: int = Query(JOBS_LIST_LIMIT, ge=1, le=JOBS_LIST_LIMIT),
    db: AsyncSession = Depends(get_db),
) -> ProjectDataJobList:
    """The project's exports, newest first; ``total`` counts all of them. Writes nothing."""
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    rows, total = await jobs.list_exports(db, project_id, limit=limit)
    return await _page(db, rows, total)


# ── one job, its download ────────────────────────────────────────────────────

@router.get("/project-data-jobs/{data_job_id}", response_model=ProjectDataJobRead)
async def get_project_data_job(
    data_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    job: ProjectDataJob = Depends(require_project_data_job_access()),
) -> ProjectDataJobRead:
    """One export or import job (the page and the CLI poll it). Writes nothing."""
    return await _read(db, job)


def _download_name(slug: Optional[str], finished_at: Optional[datetime]) -> str:
    safe = _SLUG_UNSAFE.sub("-", (slug or "project").lower()).strip("-")[:100] or "project"
    stamp = (finished_at or _now()).astimezone(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{safe}-settings-catalog-{stamp}.zip"


@router.get("/project-data-jobs/{data_job_id}/download")
async def download_project_export(
    data_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    job: ProjectDataJob = Depends(require_project_data_job_access()),
) -> StreamingResponse:
    """Stream a succeeded, unexpired export (``Content-Length`` = the stored size). 409 not an export / not
    ready / failed, 410 expired, 502 when storage cannot give the first byte. Writes NOTHING."""
    from app.db.storage import get_storage_provider
    from app.services.retention_export_service import stream_archive

    if job.kind != jobs.KIND_EXPORT:
        raise _refusal("not_an_export", status.HTTP_409_CONFLICT)
    if job.status in jobs.LIVE_STATUSES:
        raise _refusal("export_not_ready", status.HTTP_409_CONFLICT)
    if job.status == jobs.FAILED:
        raise _refusal("export_failed", status.HTTP_409_CONFLICT)
    now = _now()
    if job.status == jobs.EXPIRED or job.expires_at is None or job.expires_at <= now:
        raise _refusal("export_expired", status.HTTP_410_GONE)
    if not job.storage_key:
        raise _refusal("archive_unreadable", status.HTTP_502_BAD_GATEWAY)
    slug = None
    if job.project_id is not None:
        slug = (await db.execute(select(Project.slug).where(Project.id == job.project_id))).scalar_one_or_none()
    chunks = stream_archive(get_storage_provider(), key=job.storage_key)
    try:
        first = await chunks.__anext__()
    except StopAsyncIteration:
        first = b""
    except Exception as exc:  # noqa: BLE001 - any storage failure before the first byte
        _slog.warning("project_export_unreadable", job_id=str(job.id), error_type=type(exc).__name__)
        raise _refusal("archive_unreadable", status.HTTP_502_BAD_GATEWAY) from exc

    async def _body() -> Any:
        yield first
        async for chunk in chunks:
            yield chunk

    headers = {
        "Content-Disposition": f'attachment; filename="{_download_name(slug, job.finished_at)}"',
        "Cache-Control": "no-store",
    }
    if job.bytes is not None:
        headers["Content-Length"] = str(int(job.bytes))
    _slog.info("project_export_downloaded", job_id=str(job.id), bytes=job.bytes)
    return StreamingResponse(_body(), media_type="application/zip", headers=headers)


# ── import ───────────────────────────────────────────────────────────────────

# activity: none - the worker records project.imported on the new project
@router.post(
    "/projects/import",
    response_model=ProjectDataJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_project_import(
    response: Response,
    file: Optional[UploadFile] = File(None),
    client_token: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> ProjectDataJobRead:
    """Accept an export ZIP to import as a NEW project. The body is counted into a spool before any job
    row exists (413 past 50 MB, nothing stored); a repeated ``client_token`` answers 200 with its job and
    reads nothing. Commit, then enqueue; 202."""
    from app.db.storage import get_storage_provider
    from app.services.project_import_service import ROUTE_STATUS, ProjectImportFailed, accept_upload, parse_client_token
    from app.worker.tasks import import_project_data

    try:
        token = parse_client_token(client_token)
        if file is None:
            raise ProjectImportFailed("upload_empty")
        job, created = await accept_upload(
            db, get_storage_provider(), file=file, name=name, client_token=token, actor=current_user,
        )
    except ProjectImportFailed as refused:
        await db.rollback()
        raise HTTPException(status_code=ROUTE_STATUS.get(refused.code, 422), detail=refused.detail) from None
    if not created:
        response.status_code = status.HTTP_200_OK
        return await _read(db, job)
    await db.commit()
    await db.refresh(job)
    dispatch(import_project_data, job.id)
    return jobs.to_read(job, requested_by_name=_name_of(current_user), now=_now())


@router.get("/project-imports", response_model=ProjectDataJobList)
async def list_my_project_imports(
    limit: int = Query(JOBS_LIST_LIMIT, ge=1, le=JOBS_LIST_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> ProjectDataJobList:
    """The caller's OWN import jobs, newest first. Writes nothing."""
    rows, total = await jobs.list_my_imports(db, current_user, limit=limit)
    return await _page(db, rows, total)
