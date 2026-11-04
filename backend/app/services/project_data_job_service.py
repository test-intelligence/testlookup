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
    ProjectDataOptions,
    ProjectDataProgress,
    ProjectDataUpload,
    ProjectImportPartReceipt,
    ProjectImportUploadStart,
)
from app.services.project_export_vocab import (
    CATALOG_SCHEMA_VERSION,
    ERROR_SENTENCES,
    EXPORT_TTL_HOURS,
    IMPORT_PART_MAX_BYTES,
    JOB_PHASES,
    JOBS_LIST_LIMIT,
    SCHEMA_VERSION,
    STORAGE_PREFIX_IMPORT_PARTS,
)

_slog = structlog.get_logger(__name__)

KIND_EXPORT, KIND_IMPORT = "export", "import"
QUEUED, RUNNING, SUCCEEDED, FAILED, EXPIRED = "queued", "running", "succeeded", "failed", "expired"
UPLOADING = "uploading"                         # W30: a parted import upload (migration 0252)
LIVE_STATUSES = (QUEUED, RUNNING)
DOWNLOAD_PATH = "/api/v1/project-data-jobs/{job_id}/download"


class ExportRunning(Exception):
    """``uq_pdj_one_live_export`` tripped: this project already has a queued / running export."""

    def __init__(self, job_id: Optional[uuid.UUID]) -> None:
        super().__init__("export_running")
        self.job_id = job_id


class UploadRefused(Exception):
    """A parted-upload refusal: ``code`` is an ``ERROR_SENTENCES`` key, ``detail()`` the ``{code, message}``."""

    code = "upload_not_open"

    def __init__(self, **fmt: Any) -> None:
        super().__init__(self.code)
        self.fmt = fmt

    def detail(self) -> dict[str, str]:
        return error_detail(self.code, **self.fmt)


class UploadNotOpen(UploadRefused):
    """The job is not ``uploading`` (failed / expired, or not a parted import) — 409."""

    code = "upload_not_open"


class PartNumberInvalid(UploadRefused):
    """``part`` outside 1 … parts — 422."""

    code = "part_number_invalid"


class PartSizeInvalid(UploadRefused):
    """A part whose size is not the plan's (every part but the last is exactly IMPORT_PART_MAX_BYTES) — 422."""

    code = "upload_parts_invalid"


class PartConflict(UploadRefused):
    """The part was already received with a DIFFERENT digest — 409 (the same digest is an idempotent repeat)."""

    code = "part_conflict"


class UploadIncomplete(UploadRefused):
    """``complete`` with parts missing (or sizes that do not add up) — 409 naming them."""

    code = "upload_incomplete"

    def __init__(self, missing: list[int]) -> None:
        super().__init__(missing=", ".join(str(n) for n in missing))
        self.missing = missing


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


def stored_options(options: Optional[ProjectDataOptions]) -> dict[str, Any]:
    """The ``options`` JSON of an export: ``{}`` for a catalog-only export (no body / ``include_history:
    false`` — the phase-1 row exactly), else ``{"include_history": true, "history_days", "max_runs"}``."""
    if options is None or not options.include_history:
        return {}
    return {"include_history": True, "history_days": options.history_days, "max_runs": options.max_runs}


def job_options(job: ProjectDataJob) -> ProjectDataOptions:
    """An export job's options (the default — catalog only — for a phase-1 row or an import)."""
    raw = job.options if isinstance(job.options, dict) else {}
    if job.kind != KIND_EXPORT or not raw.get("include_history"):
        return ProjectDataOptions()
    return ProjectDataOptions(include_history=True, history_days=raw.get("history_days"), max_runs=raw.get("max_runs"))


async def create_export_job(
    db: AsyncSession, *, project: Project, actor: User, options: Optional[ProjectDataOptions] = None,
) -> ProjectDataJob:
    """Stage a ``queued`` export of ``project`` (W30: with its ``options``; None / no history == phase 1).
    Raises :class:`ExportRunning` (with the live job's id) when one is already queued / running — checked
    first, and again by ``uq_pdj_one_live_export`` for a concurrent request."""
    running = await _live_export_id(db, project.id)
    if running is not None:
        raise ExportRunning(running)
    job = ProjectDataJob(
        kind=KIND_EXPORT, status=QUEUED, project_id=project.id, project_name=project.name,
        requested_by_id=actor.id, counts={}, attempts=0, requested_at=_now(), options=stored_options(options),
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


# ── W30: the parted import upload (the route streams each part to storage; the worker assembles) ──

def part_key(job_id: uuid.UUID | str, part: int) -> str:
    """``project-imports/parts/<job_id>/part-0001`` — the ONE place a part's storage key is built."""
    return f"{STORAGE_PREFIX_IMPORT_PARTS}{job_id}/part-{int(part):04d}"


def parts_prefix(job_id: uuid.UUID | str) -> str:
    """Every part of one upload lives under this prefix (removed when the job ends / by the janitor)."""
    return f"{STORAGE_PREFIX_IMPORT_PARTS}{job_id}/"


def _plan(job: ProjectDataJob) -> tuple[int, int]:
    """``(parts, bytes)`` of a parted upload (from ``options``, written at start)."""
    raw = job.options if isinstance(job.options, dict) else {}
    if not raw.get("parted"):
        raise UploadNotOpen()
    return int(raw["parts"]), int(raw["bytes"])


def expected_part_bytes(job: ProjectDataJob, part: int) -> int:
    """The size part ``part`` must have: IMPORT_PART_MAX_BYTES, the last one the remainder. Raises
    :class:`PartNumberInvalid` outside 1 … parts (the route checks it BEFORE reading the body)."""
    parts, total = _plan(job)
    if not 1 <= int(part) <= parts:
        raise PartNumberInvalid(part=part, parts=parts)
    return IMPORT_PART_MAX_BYTES if part < parts else total - (parts - 1) * IMPORT_PART_MAX_BYTES


async def create_upload_job(
    db: AsyncSession, *, actor: User, start: ProjectImportUploadStart,
) -> tuple[ProjectDataJob, bool]:
    """``(job, created)`` for ``POST /project-imports/uploads``: a NEW import job in ``uploading`` (no part
    yet), or — the token checked FIRST, before the caller's name check — the requester's existing non-failed
    job of the same ``client_token`` as ``(it, False)`` (``uq_pdj_import_token`` covers ``uploading``, so a
    re-sent start answers the open upload with its received parts, or its later status). The plan and the
    whole-file digest go into ``options`` (the worker reads them after assembly); nothing is committed."""
    existing = await _token_job(db, actor.id, start.client_token)
    if existing is not None:
        return existing, False
    job = ProjectDataJob(
        kind=KIND_IMPORT, status=UPLOADING, project_id=None, project_name=start.name,
        requested_by_id=actor.id, client_token=start.client_token, counts={}, attempts=0, requested_at=_now(),
        options={"parted": True, "parts": start.parts, "bytes": start.bytes, "sha256": start.sha256},
        upload={"parts": start.parts, "bytes": start.bytes, "received": [], "received_bytes": 0,
                "part_sha256": {}, "part_bytes": {}},
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        raced = await _token_job(db, actor.id, start.client_token)
        if raced is None:
            raise
        return raced, False
    _slog.info("project_data_job_created", job_id=str(job.id), kind=KIND_IMPORT, parts=start.parts,
               bytes=start.bytes)
    return job, True


async def lock_job(db: AsyncSession, job_id: uuid.UUID) -> Optional[ProjectDataJob]:
    """``SELECT … FOR UPDATE`` on the job with ``populate_existing`` (two concurrent PUTs of different parts
    serialise their ``received`` update; a cached identity is refreshed — the W25e lesson). The caller has
    already checked access; no write here."""
    result = await db.execute(
        select(ProjectDataJob).where(ProjectDataJob.id == job_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def _receipt(job: ProjectDataJob, part: int, size: int) -> ProjectImportPartReceipt:
    parts, _total = _plan(job)
    received = sorted(int(n) for n in (job.upload or {}).get("received", []))
    return ProjectImportPartReceipt(part=part, bytes=size, received=received, complete=len(received) == parts)


async def record_part(
    db: AsyncSession, job: ProjectDataJob, *, part: int, size: int, sha256: str,
) -> ProjectImportPartReceipt:
    """Record one stored part on the (locked) job: idempotent — the same part with the same digest again
    changes nothing; a different digest → :class:`PartConflict`. Refuses a job not ``uploading``
    (:class:`UploadNotOpen`), a part number outside the plan, a size that is not the plan's. Flushes; the
    route commits. The digest is stored, never logged."""
    if job.kind != KIND_IMPORT or job.status != UPLOADING or not isinstance(job.upload, dict):
        raise UploadNotOpen()
    expected = expected_part_bytes(job, part)
    if int(size) != expected:
        raise PartSizeInvalid()
    state = dict(job.upload)
    digests = dict(state.get("part_sha256") or {})
    sizes = dict(state.get("part_bytes") or {})
    known = digests.get(str(part))
    if known is not None:
        if known != sha256:
            raise PartConflict(part=part)
        return _receipt(job, part, int(size))
    digests[str(part)], sizes[str(part)] = sha256, int(size)
    state.update(
        part_sha256=digests, part_bytes=sizes, received=sorted(int(n) for n in digests),
        received_bytes=sum(int(v) for v in sizes.values()),
    )
    job.upload = state                              # a NEW dict: JSONB change tracking sees the assignment
    await db.flush()
    _slog.info("project_data_part_recorded", job_id=str(job.id), part=part, bytes=int(size))
    return _receipt(job, part, int(size))


def missing_parts(job: ProjectDataJob) -> list[int]:
    """Part numbers not yet received — or received with a size that is not the plan's (re-send them)."""
    parts, _total = _plan(job)
    sizes = dict((job.upload or {}).get("part_bytes") or {})
    return [n for n in range(1, parts + 1) if int(sizes.get(str(n), -1)) != expected_part_bytes(job, n)]


async def complete_upload(db: AsyncSession, job: ProjectDataJob) -> ProjectDataJob:
    """Every part present and the sizes adding up → ``queued`` (``upload`` cleared); the route commits, THEN
    enqueues. A job already queued / running / finished is returned unchanged (an idempotent repeat); a
    failed / expired one → :class:`UploadNotOpen`; missing parts → :class:`UploadIncomplete` naming them."""
    if job.kind != KIND_IMPORT:
        raise UploadNotOpen()
    if job.status in (QUEUED, RUNNING, SUCCEEDED):
        return job
    if job.status != UPLOADING:
        raise UploadNotOpen()
    missing = missing_parts(job)          # every part at its planned size ⇔ the sizes add up to ``bytes``
    if missing:
        raise UploadIncomplete(missing)
    parts, total = _plan(job)
    job.status = QUEUED
    job.upload = None
    await db.flush()
    _slog.info("project_data_upload_completed", job_id=str(job.id), parts=parts, bytes=total)
    return job


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


def progress_value(
    *, done: int, total: int, phase: Optional[str] = None, phase_done: Optional[int] = None,
    phase_total: Optional[int] = None,
) -> dict[str, Any]:
    """The ``progress`` JSON: ``{done, total}`` (phase 1's exact shape when no phase is given), plus
    ``phase`` (a ``JOB_PHASES`` value) / ``phase_done`` / ``phase_total`` for a history job (W30)."""
    value: dict[str, Any] = {"done": int(done), "total": int(total)}
    if phase is not None:
        if phase not in JOB_PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        value["phase"] = phase
        if phase_done is not None:
            value["phase_done"] = int(phase_done)
        if phase_total is not None:
            value["phase_total"] = int(phase_total)
    return value


async def set_progress(
    db: AsyncSession, job_id: uuid.UUID, *, done: int, total: int, phase: Optional[str] = None,
    phase_done: Optional[int] = None, phase_total: Optional[int] = None,
) -> None:
    """``progress = {done, total[, phase, phase_done, phase_total]}``. Run it on a FRESH short session —
    never on the export's snapshot or the import's transaction."""
    await db.execute(
        update(ProjectDataJob)
        .where(ProjectDataJob.id == job_id)
        .values(progress=progress_value(done=done, total=total, phase=phase, phase_done=phase_done,
                                        phase_total=phase_total))
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


def stamp_schema_version(job: ProjectDataJob, schema_version: int) -> None:
    """Record the archive's manifest ``schema_version`` on an import job (``options["schema_version"]``,
    read back by :func:`to_read`). No SQL — the caller's session flushes it."""
    raw = dict(job.options) if isinstance(job.options, dict) else {}
    raw["schema_version"] = int(schema_version)
    job.options = raw


def finish_import(
    job: ProjectDataJob, *, report: ProjectDataImportReport, manifest_sha256: Optional[str], now: datetime,
    schema_version: Optional[int] = None,
) -> None:
    """Stamp an import ``succeeded`` IN the import transaction (I-8): the new
    project id is written with the rows, so "succeeded" and "the project
    exists" can never disagree. No SQL here — the caller's session flushes it.
    W30: ``schema_version`` (the manifest's) is stamped when given."""
    if schema_version is not None:
        stamp_schema_version(job, schema_version)
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


def _progress(raw: Any) -> Optional[ProjectDataProgress]:
    if not isinstance(raw, dict) or not {"done", "total"} <= set(raw):
        return None
    phase = raw.get("phase") if raw.get("phase") in JOB_PHASES else None
    return ProjectDataProgress(
        done=int(raw["done"]), total=int(raw["total"]), phase=phase,
        phase_done=int(raw["phase_done"]) if phase and raw.get("phase_done") is not None else None,
        phase_total=int(raw["phase_total"]) if phase and raw.get("phase_total") is not None else None,
    )


def _upload(job: ProjectDataJob) -> Optional[ProjectDataUpload]:
    """The parted upload's PUBLIC state while ``uploading`` — the per-part digests never leave the row."""
    if job.status != UPLOADING or not isinstance(job.upload, dict):
        return None
    raw = job.upload
    return ProjectDataUpload(
        parts=int(raw.get("parts") or 0), bytes=int(raw.get("bytes") or 0),
        received=sorted(int(n) for n in raw.get("received") or []), received_bytes=int(raw.get("received_bytes") or 0),
    )


def _schema_version(job: ProjectDataJob, report: Optional[ProjectDataImportReport]) -> Optional[int]:
    """Export: the schema its options write (2 with history, else 1); import: the manifest's (stamped), else
    inferred from a finished report (a history block ⇔ schema 2); None while unknown."""
    if job.kind == KIND_EXPORT:
        return SCHEMA_VERSION if job_options(job).include_history else CATALOG_SCHEMA_VERSION
    raw = job.options if isinstance(job.options, dict) else {}
    stamped = raw.get("schema_version")
    if isinstance(stamped, int) and not isinstance(stamped, bool):
        return stamped
    if report is not None:
        return SCHEMA_VERSION if report.history is not None else CATALOG_SCHEMA_VERSION
    return None


def to_read(job: ProjectDataJob, *, requested_by_name: Optional[str], now: datetime) -> ProjectDataJobRead:
    """The wire shape. ``download_path`` only for a succeeded export with ``expires_at > now``;
    the storage key is NEVER part of it, and neither is the ``cleanup`` ledger (deferred — never touched
    here, so a read never lazy-loads it). W30: ``options`` (an export with history), ``upload`` (while
    ``uploading``), ``schema_version``, the progress phase."""
    progress = _progress(job.progress)
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
        options=job_options(job) if job.kind == KIND_EXPORT and job_options(job).include_history else None,
        upload=_upload(job), schema_version=_schema_version(job, report),
    )
