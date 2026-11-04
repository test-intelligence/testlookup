"""A person's data export and the subject-rights job reads (W31 STRAT-12, Art. 15 / 20; contract §2.2, §3.1).

* ``POST /api/v1/me/data-export`` — the person, self-service (no id: the subject IS the caller);
* ``GET /api/v1/me/data-exports`` — their own exports of themselves;
* ``POST /api/v1/users/{user_id}/data-export`` — an ADMIN, for any person;
* ``GET /api/v1/users/{user_id}/data-jobs`` — an ADMIN: a person's export / erasure jobs (``?kind=``);
* ``GET /api/v1/subject-data-jobs/{subject_job_id}`` — one job (its requester or an ADMIN);
* ``GET /api/v1/subject-data-jobs/{subject_job_id}/download`` — the archive, streamed; ONE ``access_audit_logs``
  row per download ATTEMPT, committed before the first byte (the deliberate exception every download audit is).

Every route depends on ``require_signed_in_session()`` FIRST: any API-key principal (user-scoped, project-bound, an
admin's) is 403 ``session_required`` before any id is read (no oracle; a key is a machine credential and an
Art. 15 copy is for the person). The job routes then load the job through ``require_subject_job_access()``: an
unknown id, another person's job and a non-requester non-admin get the SAME 404. Refusals answer
``detail = {"code", "message"}`` (§1.1); a refusal writes nothing. A POST stages the job + its access-audit row,
commits, THEN enqueues (a lost enqueue is re-sent by the janitor). Log lines carry ids, codes and counts only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import SubjectRightsJob, User, UserRole
from app.models.schemas import SubjectJobList, SubjectJobRead
from app.services import subject_rights_job_service as jobs
from app.services.access_audit_service import log_access_change
from app.services.subject_rights_vocab import AUDIT_ACTIONS, JOB_KINDS

_slog = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Subject rights"])

_signed_in_session = jobs.require_signed_in_session()
_EXPORT_REQUESTED, _EXPORT_DOWNLOADED = AUDIT_ACTIONS[0], AUDIT_ACTIONS[1]
_KIND_PATTERN = "^(" + "|".join(JOB_KINDS) + ")$"


def _refusal(code: str, http_status: int, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = jobs.error_detail(code)
    detail.update(extra)
    return HTTPException(status_code=http_status, detail=detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def require_subject_job_access():
    """The job of ``{subject_job_id}`` for its REQUESTER or an ADMIN, in a signed-in session — every API-key
    principal is 403 ``session_required`` first; every other refusal is the ONE 404 ``job_not_found``."""

    async def _check(
        subject_job_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(_signed_in_session),
    ) -> SubjectRightsJob:
        return await jobs.load_job_for(db, subject_job_id, current_user)

    return _check


async def _read(db: AsyncSession, job: SubjectRightsJob) -> SubjectJobRead:
    names = await jobs.displays(db, [job])
    return jobs.to_read(
        job, subject_display=names.get(job.subject_user_id),
        requested_by_display=names.get(job.requested_by_id) if job.requested_by_id else None, now=_now(),
    )


async def _page(db: AsyncSession, rows: list[SubjectRightsJob], total: int) -> SubjectJobList:
    names = await jobs.displays(db, rows)
    now = _now()
    items = [
        jobs.to_read(job, subject_display=names.get(job.subject_user_id),
                     requested_by_display=names.get(job.requested_by_id) if job.requested_by_id else None, now=now)
        for job in rows
    ]
    return SubjectJobList(items=items, total=total)


def _dispatch(job_id: uuid.UUID) -> bool:
    """Hand a COMMITTED job to the worker with its real payload. A broker refusal leaves it ``queued``: the janitor
    re-sends it after ``QUEUED_REDISPATCH_MINUTES`` (never ``failed`` here)."""
    try:
        from app.worker.tasks import export_subject_data

        export_subject_data.apply_async(args=[str(job_id)])
        return True
    except Exception as exc:  # noqa: BLE001 - any broker error leaves the job queued
        _slog.warning("subject_export_dispatch_failed", job_id=str(job_id), error_type=type(exc).__name__)
        return False


async def _start_export(db: AsyncSession, *, subject: User, actor: User, source: str) -> SubjectJobRead:
    """The export refusals (§3.1), then job + audit row staged → commit → enqueue. An account whose erasure is
    under way (locked out, not yet pseudonymised) is refused like an erased one."""
    subject_id = subject.id
    if subject.is_service_account or subject.is_synthetic:
        raise _refusal("not_a_person", status.HTTP_409_CONFLICT)
    if subject.erased_at is not None or await jobs.live_job(db, subject_id=subject_id, kind=jobs.KIND_ERASURE):
        raise _refusal("user_erased", status.HTTP_409_CONFLICT)
    try:
        job = await jobs.create_export_job(db, subject=subject, actor=actor, source=source)
    except jobs.ExportRunning as running:
        raise _refusal("export_running", status.HTTP_409_CONFLICT,
                       job_id=str(running.job_id) if running.job_id else None) from None
    await log_access_change(
        db, _EXPORT_REQUESTED, actor, target_user_id=subject_id,
        after_value={"job_id": str(job.id), "source": source},
    )
    await db.commit()
    await db.refresh(job)
    _dispatch(job.id)
    _slog.info("subject_export_requested", job_id=str(job.id), subject_id=str(subject_id), source=source)
    return await _read(db, job)


# ── the person's own export ──────────────────────────────────────────────────

# activity: none - a subject export is not a project event; the access-audit row is the record (§2.2)
@router.post("/me/data-export", response_model=SubjectJobRead, status_code=status.HTTP_202_ACCEPTED)
async def start_my_data_export(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_signed_in_session),
) -> SubjectJobRead:
    """Queue an export of everything TestLookup holds about the caller (Art. 15 / 20). 409 ``export_running``
    names the live job in ``detail.job_id``."""
    return await _start_export(db, subject=current_user, actor=current_user, source="self")


@router.get("/me/data-exports", response_model=SubjectJobList)
async def list_my_data_exports(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_signed_in_session),
) -> SubjectJobList:
    """The caller's own exports of themselves, newest first (≤ 10; ``total`` counts all). Writes nothing."""
    rows, total = await jobs.list_my_exports(db, current_user)
    return await _page(db, rows, total)


# ── an administrator, for any person ─────────────────────────────────────────

async def _user_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    subject = await db.get(User, user_id)
    if subject is None:
        raise _refusal("user_not_found", status.HTTP_404_NOT_FOUND)
    return subject


# activity: none - a subject export is not a project event; the access-audit row is the record (§2.2)
@router.post(
    "/users/{user_id}/data-export",
    response_model=SubjectJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_signed_in_session), Depends(require_role(UserRole.ADMIN))],
)
async def start_user_data_export(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_signed_in_session),
) -> SubjectJobRead:
    """Queue an export of one person's data (any user, the admin included). 404 ``user_not_found``."""
    subject = await _user_or_404(db, user_id)
    return await _start_export(db, subject=subject, actor=current_user, source="admin")


@router.get(
    "/users/{user_id}/data-jobs",
    response_model=SubjectJobList,
    dependencies=[Depends(_signed_in_session), Depends(require_role(UserRole.ADMIN))],
)
async def list_user_data_jobs(
    user_id: uuid.UUID,
    kind: Optional[str] = Query(None, pattern=_KIND_PATTERN),
    db: AsyncSession = Depends(get_db),
) -> SubjectJobList:
    """One person's export / erasure jobs, newest first (≤ 10; ``total`` counts all); ``?kind=export|erasure``
    (FastAPI's 422 otherwise — the pattern is built from ``JOB_KINDS``). Writes nothing."""
    await _user_or_404(db, user_id)
    rows, total = await jobs.list_for_subject(db, user_id, kind=kind)
    return await _page(db, rows, total)


# ── one job, its download ────────────────────────────────────────────────────

@router.get("/subject-data-jobs/{subject_job_id}", response_model=SubjectJobRead)
async def get_subject_data_job(
    subject_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    job: SubjectRightsJob = Depends(require_subject_job_access()),
) -> SubjectJobRead:
    """One export or erasure job (the cards poll it). Writes nothing."""
    return await _read(db, job)


def _download_name(finished_at: Optional[datetime]) -> str:
    """``testlookup-personal-data-<YYYYMMDD-HHMM>.zip`` — no username in a file name."""
    stamp = (finished_at or _now()).astimezone(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"testlookup-personal-data-{stamp}.zip"


# activity: none - the access-audit row is the record of a download (§2.2)
@router.get("/subject-data-jobs/{subject_job_id}/download")
async def download_subject_export(
    subject_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    job: SubjectRightsJob = Depends(require_subject_job_access()),
    current_user: User = Depends(_signed_in_session),
) -> StreamingResponse:
    """Stream a succeeded, unexpired export. 409 not an export / not ready / failed, 410 expired (no audit row on
    a refusal); then ONE ``access_audit_logs`` row is COMMITTED — the attempt is recorded even when storage
    cannot give the first byte (502 ``archive_unreadable``). The job row is never touched."""
    from app.db.storage import get_storage_provider
    from app.services.retention_export_service import stream_archive

    if job.kind != jobs.KIND_EXPORT:
        raise _refusal("not_an_export", status.HTTP_409_CONFLICT)
    if job.status in (jobs.QUEUED, jobs.RUNNING):
        raise _refusal("export_not_ready", status.HTTP_409_CONFLICT)
    if job.status == jobs.FAILED:
        raise _refusal("export_failed", status.HTTP_409_CONFLICT)
    now = _now()
    if job.status == jobs.EXPIRED or job.expires_at is None or job.expires_at <= now:
        raise _refusal("export_expired", status.HTTP_410_GONE)
    job_id, key, size, finished_at = job.id, job.storage_key, job.bytes, job.finished_at
    await log_access_change(
        db, _EXPORT_DOWNLOADED, current_user, target_user_id=job.subject_user_id, after_value={"job_id": str(job_id)},
    )
    await db.commit()
    if not key:
        raise _refusal("archive_unreadable", status.HTTP_502_BAD_GATEWAY)
    chunks = stream_archive(get_storage_provider(), key=key)
    try:
        first = await chunks.__anext__()
    except StopAsyncIteration:
        first = b""
    except Exception as exc:  # noqa: BLE001 - any storage failure before the first byte
        _slog.warning("subject_export_unreadable", job_id=str(job_id), error_type=type(exc).__name__)
        raise _refusal("archive_unreadable", status.HTTP_502_BAD_GATEWAY) from exc

    async def _body() -> Any:
        yield first
        async for chunk in chunks:
            yield chunk

    headers = {
        "Content-Disposition": f'attachment; filename="{_download_name(finished_at)}"',
        "Cache-Control": "no-store",
    }
    if size is not None:
        headers["Content-Length"] = str(int(size))
    _slog.info("subject_export_downloaded", job_id=str(job_id), bytes=size)
    return StreamingResponse(_body(), media_type="application/zip", headers=headers)

