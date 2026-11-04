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

W30 (ADM-05 phase 2, contract §3): the export takes an OPTIONAL ``ProjectExportRequest`` body (no body ==
phase 1 byte for byte; with history → the job's ``options`` and the history time limits on ``apply_async``);
``GET /projects/{project_id}/export-estimate`` (counts only, writes nothing); the PARTED import upload —
``POST /project-imports/uploads`` (start), ``PUT …/{data_job_id}/parts/{part_number}`` (a raw body ≤ 32 MiB,
counted and hashed into a ≤ 1 MiB-in-RAM spool, then ONE object) and ``POST …/{data_job_id}/complete``. The
two ``{data_job_id}`` routes answer the SAME 404 for an unknown job, another admin's upload, an export job and
an under-role caller (``require_project_data_job_access``).

The GETs write nothing (no expiry, no "downloaded" stamp: the janitor is the only expiry path). Every
route uses the request session; the worker opens its own. Every refusal is ``{code, message}``.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import structlog
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import Project, ProjectDataJob, User, UserRole
from app.models.schemas import (
    ProjectDataJobList,
    ProjectDataJobRead,
    ProjectDataOptions,
    ProjectExportEstimate,
    ProjectExportRequest,
    ProjectImportPartReceipt,
    ProjectImportUploadStart,
)
from app.services import project_data_job_service as jobs
from app.services import project_export_vocab as vocab
from app.services.project_export_vocab import JOBS_LIST_LIMIT

_slog = structlog.get_logger(__name__)

#: A query parameter whose parse error reads as its §1.1 sentence (the estimate GET).
_LOC_CODES: dict[tuple[str, ...], str] = {
    ("query", "history_days"): "history_window_invalid",
    ("query", "max_runs"): "max_runs_invalid",
}


def _coded_validation(exc: RequestValidationError) -> Optional[dict[str, str]]:
    """``{code, message}`` for a validation error whose FIRST error is one of ours (a ``PydanticCustomError``
    typed with an ``ERROR_SENTENCES`` key, e.g. ``history_window_invalid`` / ``upload_parts_invalid``, or a
    ``_LOC_CODES`` query parameter); None leaves FastAPI's own 422 envelope."""
    errors = exc.errors()
    if not errors:
        return None
    first = errors[0]
    code = str(first.get("type") or "")
    if code in vocab.ERROR_SENTENCES:
        return {"code": code, "message": str(first.get("msg") or vocab.ERROR_SENTENCES[code])}
    by_loc = _LOC_CODES.get(tuple(str(part) for part in first.get("loc", ())))
    if by_loc is not None:
        return jobs.error_detail(by_loc)
    return None


class _CodedValidationRoute(APIRoute):
    """This router's routes answer a 422 of ours as ``detail = {code, message}`` (the card renders the sentence
    through ``extractErrorMessage``); any other validation error keeps FastAPI's envelope."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def coded(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                detail = _coded_validation(exc)
                if detail is None:
                    raise
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from None

        return coded


router = APIRouter(prefix="/api/v1", tags=["Project data"], route_class=_CodedValidationRoute)

_SLUG_UNSAFE = re.compile(r"[^a-z0-9-]+")
#: The PUT keeps at most this much of a part in RAM (the rest of the spool is a temp file).
_PART_SPOOL_RAM = 1024 * 1024
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _refusal(code: str, http_status: int, *, fmt: Optional[dict[str, Any]] = None, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = jobs.error_detail(code, **(fmt or {}))
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


def history_limits() -> dict[str, int]:
    """The per-message Celery limits of a history export / parted import (the global 1 860 s untouched)."""
    return {
        "time_limit": vocab.HISTORY_TIME_LIMIT_SECONDS,
        "soft_time_limit": vocab.HISTORY_TIME_LIMIT_SECONDS - 300,
    }


def dispatch(task: Any, job_id: uuid.UUID, *, history: bool = False) -> bool:
    """Hand a COMMITTED job to the worker with its real payload (``history``: + ``history_limits()``). A broker
    refusal leaves it ``queued``: the janitor re-dispatches it after ``QUEUED_REDISPATCH_MINUTES`` (never
    ``failed`` here)."""
    try:
        if history:
            task.apply_async(args=[str(job_id)], **history_limits())
        else:
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
    body: Optional[ProjectExportRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ProjectDataJobRead:
    """Queue an export of the project's settings + catalog (an archived project too: the access rule is
    ``require_project_access``'s, unchanged). 409 ``export_running`` names the live job. W30: no body /
    ``include_history: false`` == phase 1 exactly; ``include_history: true`` stores the options and dispatches
    with the history time limits (422 ``history_window_invalid`` / ``max_runs_invalid`` for a bad body)."""
    from app.worker.tasks import export_project_data

    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    options = None
    if body is not None and body.include_history:
        options = ProjectDataOptions(include_history=True, history_days=body.history_days, max_runs=body.max_runs)
    try:
        job = await jobs.create_export_job(db, project=project, actor=current_user, options=options)
    except jobs.ExportRunning as running:
        raise _refusal(
            "export_running", status.HTTP_409_CONFLICT, job_id=str(running.job_id) if running.job_id else None,
        ) from None
    await db.commit()
    await db.refresh(job)
    dispatch(export_project_data, job.id, history=options is not None)
    _slog.info("project_export_requested", job_id=str(job.id), project_id=str(project_id),
               history=options is not None)
    return jobs.to_read(job, requested_by_name=_name_of(current_user), now=_now())


@router.get(
    "/projects/{project_id}/export-estimate",
    response_model=ProjectExportEstimate,
    dependencies=[Depends(require_role(UserRole.QA_LEAD)), Depends(require_project_access())],
)
async def estimate_project_export(
    project_id: uuid.UUID,
    include_history: bool = Query(True),
    history_days: Optional[int] = Query(None),
    max_runs: int = Query(vocab.HISTORY_MAX_RUNS),
    db: AsyncSession = Depends(get_db),
) -> ProjectExportEstimate:
    """What an export with these options would hold — counts only (the export's rule: QA lead or ADMIN with
    access). Writes NOTHING (no job, no object, no cache). An unreachable Mongo / object store answers
    ``measured[store] = false`` with NULL counts (200, never 0). ``history_days`` absent = all history."""
    from app.db.mongo import get_mongo_db
    from app.db.storage import get_storage_provider
    from app.services.project_export_history import estimate

    if history_days is not None and history_days not in vocab.HISTORY_WINDOWS_DAYS:
        raise _refusal("history_window_invalid", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not 1 <= max_runs <= vocab.HISTORY_MAX_RUNS:
        raise _refusal("max_runs_invalid", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        mongo = get_mongo_db()
    except Exception as exc:  # noqa: BLE001 - a Mongo that cannot even be configured is "not measured"
        _slog.warning("project_export_estimate_mongo_unavailable", error_type=type(exc).__name__)
        mongo = None
    options = {"include_history": include_history, "history_days": history_days, "max_runs": max_runs}
    result: ProjectExportEstimate = await estimate(
        db, mongo if mongo is not None else _Unmeasured(), get_storage_provider(),
        project_id=project_id, options=options,
    )
    return result


class _Unmeasured:
    """A Mongo stand-in whose every read fails: the estimate answers ``measured.documents = false``."""

    def __getitem__(self, name: str) -> Any:
        raise ConnectionError("mongo unavailable")


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


def _download_name(slug: Optional[str], finished_at: Optional[datetime], *, history: bool = False) -> str:
    safe = _SLUG_UNSAFE.sub("-", (slug or "project").lower()).strip("-")[:100] or "project"
    stamp = (finished_at or _now()).astimezone(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{safe}-{'project-data' if history else 'settings-catalog'}-{stamp}.zip"


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

    history = jobs.job_options(job).include_history
    headers = {
        "Content-Disposition": f'attachment; filename="{_download_name(slug, job.finished_at, history=history)}"',
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


# ── the parted import upload (W30 §3.1) ──────────────────────────────────────

def _upload_refusal(refused: jobs.UploadRefused) -> HTTPException:
    """409 for a closed upload / a conflicting part / missing parts (naming them), 422 for a bad part plan."""
    conflict = isinstance(refused, (jobs.UploadNotOpen, jobs.PartConflict, jobs.UploadIncomplete))
    detail: dict[str, Any] = refused.detail()
    if isinstance(refused, jobs.UploadIncomplete):
        detail["missing"] = list(refused.missing)
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT if conflict else status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail,
    )


def _import_job_or_404(job: ProjectDataJob) -> ProjectDataJob:
    """The two ``{data_job_id}`` upload routes act on an IMPORT job only: an export job (visible to its
    project's QA leads through ``load_job_for``) reads the SAME 404 as an unknown id."""
    if job.kind != jobs.KIND_IMPORT:
        raise jobs.job_not_found()
    return job


# activity: none - the worker records project.imported on the new project
@router.post(
    "/project-imports/uploads",
    response_model=ProjectDataJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_import_upload(
    start: ProjectImportUploadStart,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> ProjectDataJobRead:
    """Open a parted upload (ADMIN): a NEW import job in ``uploading`` (201), or — the token checked FIRST — the
    caller's existing non-failed job of that ``client_token`` (200, with its received parts or its later
    status). Then the name check (W24's order): 422 ``name_invalid`` / 409 ``name_taken``. Nothing is read."""
    from app.services.project_import_service import ProjectImportFailed, check_name_free, clean_name

    job, created = await jobs.create_upload_job(db, actor=current_user, start=start)
    if not created:
        response.status_code = status.HTTP_200_OK
        return await _read(db, job)
    try:
        supplied = clean_name(start.name)
        if supplied is not None:
            await check_name_free(db, supplied)
    except ProjectImportFailed as refused:
        await db.rollback()
        http_status = status.HTTP_409_CONFLICT if refused.code == "name_taken" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=http_status, detail=jobs.error_detail(refused.code, **refused.fmt)) from None
    job.project_name = supplied
    await db.commit()
    await db.refresh(job)
    _slog.info("project_import_upload_started", job_id=str(job.id), parts=start.parts, bytes=start.bytes)
    return jobs.to_read(job, requested_by_name=_name_of(current_user), now=_now())


async def _spool_part(request: Request) -> tuple[Any, int, str]:
    """The raw body counted and hashed into a spool (≤ ``_PART_SPOOL_RAM`` in RAM, the rest a temp file). Past
    ``IMPORT_PART_MAX_BYTES`` the read stops at that + 1 byte → 413 ``part_too_large`` (nothing stored);
    ``Content-Length`` is advisory (a declared length past the cap is refused before reading)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > vocab.IMPORT_PART_MAX_BYTES:
        raise _refusal("part_too_large", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    spool = tempfile.SpooledTemporaryFile(max_size=_PART_SPOOL_RAM)
    digest = hashlib.sha256()
    size = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            size += len(chunk)
            if size > vocab.IMPORT_PART_MAX_BYTES:
                raise _refusal("part_too_large", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            digest.update(chunk)
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    return spool, size, digest.hexdigest()


def _known_digest(job: ProjectDataJob, part: int) -> Optional[str]:
    upload = job.upload if isinstance(job.upload, dict) else {}
    value = dict(upload.get("part_sha256") or {}).get(str(part))
    return str(value) if value else None


# activity: none - a part of an upload; the import records project.imported
@router.put(
    "/project-imports/uploads/{data_job_id}/parts/{part_number}",
    response_model=ProjectImportPartReceipt,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def upload_import_part(
    data_job_id: uuid.UUID,
    part_number: int,
    request: Request,
    x_part_sha256: Optional[str] = Header(None, alias="X-Part-Sha256"),
    db: AsyncSession = Depends(get_db),
    job: ProjectDataJob = Depends(require_project_data_job_access()),
) -> ProjectImportPartReceipt:
    """One part (raw ``application/octet-stream``, ≤ 32 MiB) of the caller's own open upload. Checked before the
    body is read: the job is this admin's import (else the ONE 404), still ``uploading`` (409
    ``upload_not_open``), the part number in its plan (422 ``part_number_invalid``), ``X-Part-Sha256`` 64 hex
    (422 ``part_digest_mismatch``); the same part again with the same digest → 200, nothing rewritten; another
    digest → 409 ``part_conflict``. Then the body is counted + hashed (413 ``part_too_large`` past 32 MiB, 422
    ``part_digest_mismatch`` / ``upload_parts_invalid`` — in every case NOTHING is stored), the job row locked
    (``FOR UPDATE``), the part written as ONE object, its receipt recorded, ONE commit."""
    from app.db.storage import get_storage_provider
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    job = _import_job_or_404(job)
    try:
        if job.status != jobs.UPLOADING:
            raise jobs.UploadNotOpen()
        expected = jobs.expected_part_bytes(job, part_number)
    except jobs.UploadRefused as refused:
        raise _upload_refusal(refused) from None
    claimed = (x_part_sha256 or "").strip().lower()
    if not _SHA256_HEX.fullmatch(claimed):
        raise _refusal("part_digest_mismatch", status.HTTP_422_UNPROCESSABLE_ENTITY, fmt={"part": part_number})
    known = _known_digest(job, part_number)
    if known is not None:
        if known != claimed:
            raise _upload_refusal(jobs.PartConflict(part=part_number))
        return await jobs.record_part(db, job, part=part_number, size=expected, sha256=claimed)
    spool, size, actual = await _spool_part(request)
    try:
        if actual != claimed:
            raise _refusal("part_digest_mismatch", status.HTTP_422_UNPROCESSABLE_ENTITY, fmt={"part": part_number})
        if size != expected:
            raise _upload_refusal(jobs.PartSizeInvalid())
        locked = await jobs.lock_job(db, job.id)
        if locked is None:
            raise jobs.job_not_found()
        try:
            if locked.status != jobs.UPLOADING:
                raise jobs.UploadNotOpen()
            again = _known_digest(locked, part_number)
            if again is not None and again != claimed:
                raise jobs.PartConflict(part=part_number)
            if again is None:
                await get_storage_provider().put_object_stream(
                    jobs.part_key(job.id, part_number), spool, size=size,
                    content_type="application/octet-stream", bucket=COMPLIANCE_BUCKET,
                )
            receipt = await jobs.record_part(db, locked, part=part_number, size=size, sha256=claimed)
        except jobs.UploadRefused as refused:
            await db.rollback()
            raise _upload_refusal(refused) from None
    finally:
        spool.close()
    await db.commit()
    return receipt


# activity: none - the worker records project.imported
@router.post(
    "/project-imports/uploads/{data_job_id}/complete",
    response_model=ProjectDataJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def complete_import_upload(
    data_job_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    job: ProjectDataJob = Depends(require_project_data_job_access()),
) -> ProjectDataJobRead:
    """Every part present at its planned size → ``queued``, COMMIT, then the worker is enqueued with the history
    time limits (202). A repeat on a job already queued / running / finished → 200 with it (no second enqueue —
    a lost one is the janitor's). 409 ``upload_incomplete`` names the missing parts; 409 ``upload_not_open``
    for a failed / expired upload."""
    from app.worker.tasks import import_project_data

    job = _import_job_or_404(job)
    locked = await jobs.lock_job(db, job.id)
    if locked is None:
        raise jobs.job_not_found()
    was_open = locked.status == jobs.UPLOADING
    try:
        done = await jobs.complete_upload(db, locked)
    except jobs.UploadRefused as refused:
        await db.rollback()
        raise _upload_refusal(refused) from None
    if not was_open:                      # unchanged (complete_upload wrote nothing): the request ends the lock
        response.status_code = status.HTTP_200_OK
        return await _read(db, done)
    await db.commit()
    await db.refresh(done)
    dispatch(import_project_data, done.id, history=True)
    _slog.info("project_import_upload_completed", job_id=str(done.id))
    return await _read(db, done)
