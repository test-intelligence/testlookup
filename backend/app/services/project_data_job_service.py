"""Project export / import job rows (W24 ADM-05 phase 1): create, claim, progress, finish, read.

Stage-only (``STAGE_ONLY_SERVICES``): every function flushes at most; the
routers and ``worker/project_data_runner.py`` own the commits (a worker must
never be handed a job id it cannot yet see — the route commits, THEN enqueues).

The read rule (contract §3.1) lives in :func:`load_job_for`: an export is
readable by a QA lead / ADMIN with access to ITS project, an import by the
ADMIN who requested it; every refusal — an unknown id, another project's
export, another admin's import, a role below QA lead, a project-bound key — is
the SAME 404 ``job_not_found`` (no oracle). A read never writes.

Log lines carry job / project ids, kinds, statuses, codes and counts only —
never a project or user name, a storage key or a digest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

import structlog
from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    _ROLE_ORDER,
    PROJECT_ADMIN_SCOPE,
    _api_key_bound_project,
    _normalize_user_role,
    api_key_grant,
    get_accessible_project_ids,
)
from app.models.postgres import Project, ProjectDataJob, User, UserRole
from app.models.schemas import (
    ProjectDataError,
    ProjectDataImportReport,
    ProjectDataJobRead,
    ProjectDataProgress,
)
from app.services.project_export_vocab import ERROR_SENTENCES, EXPORT_TTL_HOURS, JOBS_LIST_LIMIT

_slog = structlog.get_logger(__name__)

KIND_EXPORT, KIND_IMPORT = "export", "import"
QUEUED, RUNNING, SUCCEEDED, FAILED, EXPIRED = "queued", "running", "succeeded", "failed", "expired"
LIVE_STATUSES = (QUEUED, RUNNING)
DOWNLOAD_PATH = "/api/v1/project-data-jobs/{job_id}/download"


class ExportRunning(Exception):
    """``uq_pdj_one_live_export`` tripped: this project already has a queued / running export."""

    def __init__(self, job_id: Optional[uuid.UUID]) -> None:
        super().__init__("export_running")
        self.job_id = job_id


def error_detail(code: str, **fmt: Any) -> dict[str, str]:
    """``{"code", "message"}`` — the ``detail`` of every W24 4xx / 5xx and a failed job's error."""
    sentence = ERROR_SENTENCES[code]
    return {"code": code, "message": sentence.format(**fmt) if fmt else sentence}


def job_not_found() -> HTTPException:
    """The ONE refusal of a job read (unknown, foreign and under-role alike)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_detail("job_not_found"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── create ───────────────────────────────────────────────────────────────────

async def _live_export_id(db: AsyncSession, project_id: uuid.UUID) -> Optional[uuid.UUID]:
    result = await db.execute(
        select(ProjectDataJob.id)
        .where(
            ProjectDataJob.project_id == project_id,
            ProjectDataJob.kind == KIND_EXPORT,
            ProjectDataJob.status.in_(LIVE_STATUSES),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_export_job(db: AsyncSession, *, project: Project, actor: User) -> ProjectDataJob:
    """Stage a ``queued`` export of ``project``. Raises :class:`ExportRunning`
    (with the live job's id) when one is already queued / running — checked
    first, and again by ``uq_pdj_one_live_export`` for a concurrent request."""
    running = await _live_export_id(db, project.id)
    if running is not None:
        raise ExportRunning(running)
    job = ProjectDataJob(
        kind=KIND_EXPORT, status=QUEUED, project_id=project.id, project_name=project.name,
        requested_by_id=actor.id, counts={}, attempts=0, requested_at=_now(),
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        raise ExportRunning(await _live_export_id(db, project.id)) from None
    _slog.info("project_data_job_created", job_id=str(job.id), kind=KIND_EXPORT, project_id=str(project.id))
    return job


async def _token_job(db: AsyncSession, actor_id: uuid.UUID, client_token: uuid.UUID) -> Optional[ProjectDataJob]:
    result = await db.execute(
        select(ProjectDataJob)
        .where(
            ProjectDataJob.kind == KIND_IMPORT,
            ProjectDataJob.requested_by_id == actor_id,
            ProjectDataJob.client_token == client_token,
            ProjectDataJob.status != FAILED,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_import_job(
    db: AsyncSession, *, actor: User, client_token: uuid.UUID, project_name: Optional[str],
) -> tuple[ProjectDataJob, bool]:
    """``(job, created)``. An existing non-failed import of the same requester
    with the same ``client_token`` is returned as ``(it, False)`` — nothing is
    staged (the idempotent retry); a failed job's token may be reused."""
    existing = await _token_job(db, actor.id, client_token)
    if existing is not None:
        return existing, False
    job = ProjectDataJob(
        kind=KIND_IMPORT, status=QUEUED, project_id=None, project_name=project_name,
        requested_by_id=actor.id, client_token=client_token, counts={}, attempts=0, requested_at=_now(),
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        raced = await _token_job(db, actor.id, client_token)
        if raced is None:
            raise
        return raced, False
    _slog.info("project_data_job_created", job_id=str(job.id), kind=KIND_IMPORT)
    return job, True


# ── claim / progress / finish (the runner commits each) ──────────────────────

async def claim(db: AsyncSession, job_id: uuid.UUID, *, kind: str) -> Optional[ProjectDataJob]:
    """``queued`` → ``running`` (``started_at``, ``attempts + 1``). ``None`` when
    there is nothing to do — already running, finished, of the other kind, or
    gone: a redelivered message is a no-op. The runner commits the claim
    before any work, so another delivery sees it."""
    result = await db.execute(
        update(ProjectDataJob)
        .where(ProjectDataJob.id == job_id, ProjectDataJob.kind == kind, ProjectDataJob.status == QUEUED)
        .values(status=RUNNING, started_at=_now(), attempts=ProjectDataJob.attempts + 1)
        .returning(ProjectDataJob.id)
        .execution_options(synchronize_session=False)
    )
    claimed = result.scalar_one_or_none()
    if claimed is None:
        return None
    return await db.get(ProjectDataJob, claimed, populate_existing=True)


async def set_progress(db: AsyncSession, job_id: uuid.UUID, *, done: int, total: int) -> None:
    """``progress = {done, total}`` (tables finished / tables in the plan). Run it on a
    FRESH short session — never on the export's snapshot or the import's transaction."""
    await db.execute(
        update(ProjectDataJob)
        .where(ProjectDataJob.id == job_id)
        .values(progress={"done": int(done), "total": int(total)})
        .execution_options(synchronize_session=False)
    )


async def finish_export(db: AsyncSession, job_id: uuid.UUID, *, outcome: Mapping[str, Any], now: datetime) -> None:
    """``succeeded`` with the verified archive's facts; ``expires_at = now + EXPORT_TTL_HOURS``.
    ``outcome`` keys: key, bytes, file_count, manifest_sha256, archive_sha256, signature,
    signing_key_id, counts (rows per entity)."""
    counts = {str(k): int(v) for k, v in dict(outcome.get("counts") or {}).items()}
    await db.execute(
        update(ProjectDataJob)
        .where(ProjectDataJob.id == job_id)
        .values(
            status=SUCCEEDED, storage_key=outcome.get("key"), bytes=outcome.get("bytes"),
            file_count=outcome.get("file_count"), manifest_sha256=outcome.get("manifest_sha256"),
            archive_sha256=outcome.get("archive_sha256"), signature=outcome.get("signature"),
            signing_key_id=outcome.get("signing_key_id"), counts=counts, finished_at=now,
            expires_at=now + timedelta(hours=EXPORT_TTL_HOURS), error_code=None, error=None,
            progress={"done": len(counts), "total": len(counts)},
        )
        .execution_options(synchronize_session=False)
    )
    _slog.info("project_data_job_succeeded", job_id=str(job_id), kind=KIND_EXPORT, entities=len(counts),
               rows=sum(counts.values()))


def finish_import(
    job: ProjectDataJob, *, report: ProjectDataImportReport, manifest_sha256: Optional[str], now: datetime,
) -> None:
    """Stamp an import ``succeeded`` IN the import transaction (I-8): the new
    project id is written with the rows, so "succeeded" and "the project
    exists" can never disagree. No SQL here — the caller's session flushes it."""
    job.project_id = report.project_id
    job.status = SUCCEEDED
    job.report = report.model_dump(mode="json")
    job.counts = dict(report.inserted)
    job.manifest_sha256 = manifest_sha256
    job.signature = report.signature
    job.finished_at = now
    job.error_code = None
    job.error = None
    job.progress = {"done": len(report.inserted), "total": len(report.inserted)}


async def mark_failed(db: AsyncSession, job_id: uuid.UUID, *, code: str, now: datetime, **fmt: Any) -> None:
    """``failed`` with ``error_code`` and the §1.1 sentence (type names only in ``fmt``).
    Run it on a FRESH session after the work's rollback."""
    detail = error_detail(code, **fmt)
    await db.execute(
        update(ProjectDataJob)
        .where(ProjectDataJob.id == job_id)
        .values(status=FAILED, error_code=code, error=detail["message"], finished_at=now)
        .execution_options(synchronize_session=False)
    )
    _slog.info("project_data_job_failed", job_id=str(job_id), error_code=code)


# ── reads (never write) ──────────────────────────────────────────────────────

def _role_index(user: User) -> int:
    try:
        return _ROLE_ORDER.index(_normalize_user_role(user.role))
    except ValueError:
        return -1


async def may_read_job(db: AsyncSession, job: ProjectDataJob, user: User) -> bool:
    """The §3.1 rule. A project-bound key, or a scoped key without ``project:admin``,
    never reads a job (``require_role(QA_LEAD)``'s refusals, answered as a 404)."""
    if _api_key_bound_project(user) is not None:
        return False
    grant = api_key_grant(user)
    if grant is not None and not grant.allows(PROJECT_ADMIN_SCOPE):
        return False
    role = _role_index(user)
    if job.kind == KIND_IMPORT:
        return role == _ROLE_ORDER.index(UserRole.ADMIN) and job.requested_by_id == user.id
    if job.kind != KIND_EXPORT or role < _ROLE_ORDER.index(UserRole.QA_LEAD):
        return False
    accessible = await get_accessible_project_ids(db, user)
    if accessible is None:          # ADMIN: every project
        return True
    return job.project_id is not None and job.project_id in accessible


async def load_job_for(db: AsyncSession, job_id: uuid.UUID, user: User) -> ProjectDataJob:
    """The job, or the ONE 404 ``job_not_found`` for every refusal (the job GET and the download)."""
    job = await db.get(ProjectDataJob, job_id)
    if job is None or not await may_read_job(db, job, user):
        raise job_not_found()
    return job


async def _page(db: AsyncSession, where: Iterable[Any], limit: int) -> tuple[list[ProjectDataJob], int]:
    conditions = list(where)
    limit = max(1, min(int(limit), JOBS_LIST_LIMIT))
    rows = await db.execute(
        select(ProjectDataJob).where(*conditions)
        .order_by(ProjectDataJob.requested_at.desc(), ProjectDataJob.id.desc()).limit(limit)
    )
    total = await db.execute(select(func.count()).select_from(ProjectDataJob).where(*conditions))
    return list(rows.scalars().all()), int(total.scalar_one())


async def list_exports(db: AsyncSession, project_id: uuid.UUID, *, limit: int = JOBS_LIST_LIMIT) -> tuple[list[ProjectDataJob], int]:
    """The project's EXPORTS, newest first (≤ ``JOBS_LIST_LIMIT``), and how many there are in all."""
    return await _page(db, (ProjectDataJob.kind == KIND_EXPORT, ProjectDataJob.project_id == project_id), limit)


async def list_my_imports(db: AsyncSession, user: User, *, limit: int = JOBS_LIST_LIMIT) -> tuple[list[ProjectDataJob], int]:
    """The caller's OWN imports, newest first, and how many there are in all."""
    return await _page(db, (ProjectDataJob.kind == KIND_IMPORT, ProjectDataJob.requested_by_id == user.id), limit)


async def requester_names(db: AsyncSession, jobs: Iterable[ProjectDataJob]) -> dict[uuid.UUID, str]:
    """requested_by_id → full name, else username (never an e-mail address)."""
    ids = {job.requested_by_id for job in jobs if job.requested_by_id is not None}
    if not ids:
        return {}
    result = await db.execute(select(User.id, User.full_name, User.username).where(User.id.in_(ids)))
    return {row.id: (row.full_name or row.username) for row in result.all()}


def to_read(job: ProjectDataJob, *, requested_by_name: Optional[str], now: datetime) -> ProjectDataJobRead:
    """The wire shape. ``download_path`` only for a succeeded export with ``expires_at > now``;
    the storage key is NEVER part of it."""
    progress = None
    if isinstance(job.progress, dict) and {"done", "total"} <= set(job.progress):
        progress = ProjectDataProgress(done=int(job.progress["done"]), total=int(job.progress["total"]))
    report = None
    if job.report:
        try:
            report = ProjectDataImportReport.model_validate(job.report)
        except ValidationError:
            _slog.warning("project_data_job_report_unreadable", job_id=str(job.id))
    error = None
    if job.error_code:
        message = job.error or ERROR_SENTENCES.get(job.error_code, job.error_code)
        error = ProjectDataError(code=job.error_code, message=message)
    downloadable = (
        job.kind == KIND_EXPORT and job.status == SUCCEEDED and job.expires_at is not None and job.expires_at > now
    )
    return ProjectDataJobRead(
        id=job.id, kind=job.kind, status=job.status, project_id=job.project_id, project_name=job.project_name,
        requested_by_name=requested_by_name, requested_at=job.requested_at, started_at=job.started_at,
        finished_at=job.finished_at, progress=progress, bytes=job.bytes, file_count=job.file_count,
        manifest_sha256=job.manifest_sha256, signature=job.signature, expires_at=job.expires_at,
        counts={str(k): int(v) for k, v in (job.counts or {}).items()}, import_report=report, error=error,
        download_path=DOWNLOAD_PATH.format(job_id=job.id) if downloadable else None,
    )
