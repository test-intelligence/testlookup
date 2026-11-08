"""Subject-rights job rows (W31 STRAT-12): create, claim, phase, finish, read — the W24 job-service shape.

Stage-only (``STAGE_ONLY_SERVICES``): every function flushes at most; the
routers (``routers/subject_rights.py``, ``routers/subject_erasure.py``) and the
runners (``worker/subject_rights_runner.py``, ``worker/subject_erasure_runner.py``)
own the commits (a worker must never be handed a job id it cannot yet see — the
route commits, THEN enqueues).

The read rule (contract §1.4 / §3.1) lives in :func:`load_job_for`: a job is
readable by its REQUESTER or any ADMIN, in a signed-in session only; every
refusal — an unknown id, another person's job, a QA lead who did not request
it, any API-key principal — is the SAME 404 ``job_not_found`` (no oracle). A
read never writes. :func:`require_signed_in_session` is the dependency both new
routers put in front of every route: an API-key principal is 403
``session_required`` before any id is read.

Log lines carry job / user ids, kinds, statuses, phases, codes and counts only —
never a name, a username, an e-mail, a storage key or a digest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

import structlog
from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CREDENTIAL_KIND_API_KEY,
    _api_key_bound_project,
    _normalize_user_role,
    api_key_grant,
    credential_kind,
    get_current_active_user,
)
from app.models.postgres import SubjectRightsJob, User, UserRole
from app.models.schemas import SubjectJobRead, SubjectRightsError
from app.services.subject_rights_vocab import (
    ERASED_DISPLAY,
    ERASURE_PHASES,
    ERROR_SENTENCES,
    EXPORT_TTL_HOURS,
    JOB_KINDS,
    JOBS_LIST_LIMIT,
    LIVE_STATUSES,
    REQUEST_SOURCES,
    SOURCE_SCIM,
)

_slog = structlog.get_logger(__name__)

KIND_EXPORT, KIND_ERASURE = "export", "erasure"
QUEUED, RUNNING, SUCCEEDED, FAILED, EXPIRED = "queued", "running", "succeeded", "failed", "expired"
PHASE_DONE = "done"
SOURCE_ADMIN = "admin"
#: W38 D-11: migration 0256 widened the shared ``ck_srj_source`` to admit "scim", but an identity provider only
#: ever ERASES — an export is still asked for by the person ("self") or an administrator ("admin").
EXPORT_SOURCES = tuple(source for source in REQUEST_SOURCES if source != SOURCE_SCIM)
#: The sources an erasure may carry (``ck_srj_erasure_is_admin`` of 0256).
ERASURE_SOURCES = (SOURCE_ADMIN, SOURCE_SCIM)


class ExportRunning(Exception):
    """``uq_srj_one_live_export`` tripped: this person already has a queued / running export."""

    def __init__(self, job_id: Optional[uuid.UUID]) -> None:
        super().__init__("export_running")
        self.job_id = job_id


class ErasureRunning(Exception):
    """``uq_srj_one_live_erasure`` tripped: this person already has a queued / running erasure."""

    def __init__(self, job_id: Optional[uuid.UUID]) -> None:
        super().__init__("erasure_running")
        self.job_id = job_id


def error_detail(code: str, **fmt: Any) -> dict[str, str]:
    """``{"code", "message"}`` — the ``detail`` of every W31 4xx / 5xx and a failed job's error."""
    sentence = ERROR_SENTENCES[code]
    return {"code": code, "message": sentence.format(**fmt) if fmt else sentence}


def job_not_found() -> HTTPException:
    """The ONE refusal of a job read (unknown, another person's, under-role, any API key alike)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_detail("job_not_found"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── the session-only principal ───────────────────────────────────────────────

def is_api_key_principal(user: User) -> bool:
    """True for every API-key credential: a scoped or legacy key (its grant), a project-bound key, or a
    streaming key (its credential kind). A machine credential never exports or erases personal data."""
    return (
        api_key_grant(user) is not None
        or _api_key_bound_project(user) is not None
        or credential_kind(user) == CREDENTIAL_KIND_API_KEY
    )


def require_signed_in_session() -> Callable[..., Awaitable[User]]:
    """The FastAPI dependency in front of EVERY W31 route: ``get_current_active_user``, then 403
    ``session_required`` for any API-key principal — before the handler reads an id (no oracle).

    (A scoped key WITHOUT ``project:write`` on a POST is refused one step earlier, by
    ``get_current_active_user``'s own N32 write-scope rule — a 403 before any id is read either way.)
    """

    async def signed_in_session(current_user: User = Depends(get_current_active_user)) -> User:
        if is_api_key_principal(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_detail("session_required"))
        return current_user

    return signed_in_session


# ── create ───────────────────────────────────────────────────────────────────

async def _live_id(db: AsyncSession, subject_id: uuid.UUID, kind: str) -> Optional[uuid.UUID]:
    result = await db.execute(
        select(SubjectRightsJob.id)
        .where(
            SubjectRightsJob.subject_user_id == subject_id,
            SubjectRightsJob.kind == kind,
            SubjectRightsJob.status.in_(LIVE_STATUSES),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def live_job(db: AsyncSession, *, subject_id: uuid.UUID, kind: str) -> Optional[SubjectRightsJob]:
    """The subject's queued / running job of ``kind``, or None (at most one: the partial uniques)."""
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    result = await db.execute(
        select(SubjectRightsJob)
        .where(
            SubjectRightsJob.subject_user_id == subject_id,
            SubjectRightsJob.kind == kind,
            SubjectRightsJob.status.in_(LIVE_STATUSES),
        )
        .order_by(SubjectRightsJob.requested_at.desc(), SubjectRightsJob.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _stage(db: AsyncSession, job: SubjectRightsJob, refusal: type[Exception]) -> SubjectRightsJob:
    """Flush ``job`` in a SAVEPOINT; a concurrent request that won the partial unique → ``refusal``."""
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        raise refusal(await _live_id(db, job.subject_user_id, job.kind)) from None
    _slog.info("subject_rights_job_created", job_id=str(job.id), kind=job.kind, source=job.source,
               subject_id=str(job.subject_user_id))
    return job


async def create_export_job(db: AsyncSession, *, subject: User, actor: User, source: str) -> SubjectRightsJob:
    """Stage a ``queued`` export of ``subject`` asked for by ``actor`` (``source`` "self" | "admin"; never
    "scim" — ``EXPORT_SOURCES``). Raises :class:`ExportRunning` (with the live job's id) when one is already queued / running —
    checked first, and again by ``uq_srj_one_live_export`` for a concurrent request."""
    if source not in EXPORT_SOURCES:
        raise ValueError(f"unknown request source {source!r}")
    running = await _live_id(db, subject.id, KIND_EXPORT)
    if running is not None:
        raise ExportRunning(running)
    job = SubjectRightsJob(
        kind=KIND_EXPORT, status=QUEUED, source=source, phase=None, subject_user_id=subject.id,
        requested_by_id=actor.id, counts={}, attempts=0, requested_at=_now(),
    )
    return await _stage(db, job, ExportRunning)


async def create_erasure_job(
    db: AsyncSession, *, subject: User, actor: Optional[User], source: str = SOURCE_ADMIN,
) -> SubjectRightsJob:
    """Stage a ``queued`` erasure of ``subject`` (phase NULL — ``stage_lockout`` sets ``lockout``). Raises
    :class:`ErasureRunning` when one is already queued / running.

    ``source`` "admin" (the default: an administrator, who is the ``actor`` and the requester) or W38's
    "scim" (an identity provider's DELETE: no actor, ``requested_by_id`` NULL). Any other pairing is a bug."""
    if source not in ERASURE_SOURCES:
        raise ValueError(f"unknown erasure source {source!r}")
    if (actor is None) != (source == SOURCE_SCIM):
        raise ValueError("an admin erasure names its actor; a SCIM erasure has none")
    running = await _live_id(db, subject.id, KIND_ERASURE)
    if running is not None:
        raise ErasureRunning(running)
    job = SubjectRightsJob(
        kind=KIND_ERASURE, status=QUEUED, source=source, phase=None, subject_user_id=subject.id,
        requested_by_id=actor.id if actor is not None else None, counts={}, attempts=0, requested_at=_now(),
    )
    return await _stage(db, job, ErasureRunning)


# ── the worker's transitions (each run on the runner's session; the runner commits) ──

async def claim(db: AsyncSession, job_id: uuid.UUID, *, kind: str) -> Optional[SubjectRightsJob]:
    """``queued`` → ``running`` (``attempts + 1``; ``started_at`` = the FIRST claim). ``None`` when there
    is nothing to do — already running, finished, of the other kind, or gone: a redelivered message is a
    no-op. The runner commits the claim before any work, so another delivery sees it."""
    now = _now()
    result = await db.execute(
        update(SubjectRightsJob)
        .where(SubjectRightsJob.id == job_id, SubjectRightsJob.kind == kind, SubjectRightsJob.status == QUEUED)
        .values(
            status=RUNNING, started_at=func.coalesce(SubjectRightsJob.started_at, now),
            attempts=SubjectRightsJob.attempts + 1,
        )
        .returning(SubjectRightsJob.id)
        .execution_options(synchronize_session=False)
    )
    claimed = result.scalar_one_or_none()
    if claimed is None:
        return None
    return await db.get(SubjectRightsJob, claimed, populate_existing=True)


async def requeue(db: AsyncSession, job_id: uuid.UUID, *, kind: str) -> bool:
    """A live (queued / running) job of ``kind`` → ``queued`` again, so the next dispatch can
    :func:`claim` it (the janitor's re-dispatch of a stuck erasure; the admin's "Resume erasure").
    The error stays until the next phase commits. False when the job is not live."""
    result = await db.execute(
        update(SubjectRightsJob)
        .where(SubjectRightsJob.id == job_id, SubjectRightsJob.kind == kind,
               SubjectRightsJob.status.in_(LIVE_STATUSES))
        .values(status=QUEUED)
        .returning(SubjectRightsJob.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def finish_export(db: AsyncSession, job_id: uuid.UUID, *, outcome: Mapping[str, Any], now: datetime) -> None:
    """``succeeded`` with the verified archive's facts; ``expires_at = now + EXPORT_TTL_HOURS``.
    ``outcome`` keys: key, bytes, file_count, manifest_sha256, archive_sha256, signature,
    signing_key_id, counts (rows per member file)."""
    counts = {str(k): int(v) for k, v in dict(outcome.get("counts") or {}).items()}
    await db.execute(
        update(SubjectRightsJob)
        .where(SubjectRightsJob.id == job_id, SubjectRightsJob.kind == KIND_EXPORT)
        .values(
            status=SUCCEEDED, storage_key=outcome.get("key"), bytes=outcome.get("bytes"),
            file_count=outcome.get("file_count"), manifest_sha256=outcome.get("manifest_sha256"),
            archive_sha256=outcome.get("archive_sha256"), signature=outcome.get("signature"),
            signing_key_id=outcome.get("signing_key_id"), counts=counts, finished_at=now,
            expires_at=now + timedelta(hours=EXPORT_TTL_HOURS), error_code=None, error=None,
        )
        .execution_options(synchronize_session=False)
    )
    _slog.info("subject_rights_job_succeeded", job_id=str(job_id), kind=KIND_EXPORT, members=len(counts),
               rows=sum(counts.values()))


async def set_phase(
    db: AsyncSession, job_id: uuid.UUID, *, phase: str, counts: Mapping[str, int], now: datetime,
) -> None:
    """An erasure committed ``phase``: ``job.phase = phase``, ``counts`` MERGED into the job's (a phase's
    keys replace the same keys — each phase counts its own locations), the previous attempt's error
    cleared (the step it named has now succeeded). ``"done"`` → ``status = succeeded`` + ``finished_at``.
    Runs on the phase's own session, IN its transaction (the caller commits both together)."""
    if phase not in ERASURE_PHASES:
        raise ValueError(f"unknown erasure phase {phase!r}")
    job = await db.get(SubjectRightsJob, job_id, populate_existing=True)
    if job is None or job.kind != KIND_ERASURE:
        raise LookupError(f"no erasure job {job_id}")
    merged = {str(k): int(v) for k, v in dict(job.counts or {}).items()}
    merged.update({str(k): int(v) for k, v in dict(counts).items()})
    job.phase = phase
    job.counts = merged
    job.error_code = None
    job.error = None
    if phase == PHASE_DONE:
        job.status = SUCCEEDED
        job.finished_at = now
    await db.flush()
    _slog.info("subject_erasure_phase", job_id=str(job_id), phase=phase, locations=len(counts),
               rows=sum(int(v) for v in dict(counts).values()))


async def mark_failed(db: AsyncSession, job_id: uuid.UUID, *, code: str, now: datetime, **fmt: Any) -> None:
    """``failed`` with ``error_code`` and the §1.1 sentence (type names only in ``fmt``). An EXPORT only —
    an erasure is never failed (it records ``erasure_incomplete`` and stays ``running``, E-8). Run it on
    a FRESH session after the work's rollback."""
    detail = error_detail(code, **fmt)
    await db.execute(
        update(SubjectRightsJob)
        .where(SubjectRightsJob.id == job_id)
        .values(status=FAILED, error_code=code, error=detail["message"], finished_at=now)
        .execution_options(synchronize_session=False)
    )
    _slog.info("subject_rights_job_failed", job_id=str(job_id), error_code=code)


async def mark_erasure_incomplete(db: AsyncSession, job_id: uuid.UUID, *, phase: str) -> None:
    """An erasure step raised: the job stays ``running`` (a timeout is not a failure) with
    ``error_code="erasure_incomplete"`` naming the LAST committed phase. Run it on a FRESH session."""
    detail = error_detail("erasure_incomplete", phase=phase)
    await db.execute(
        update(SubjectRightsJob)
        .where(SubjectRightsJob.id == job_id, SubjectRightsJob.kind == KIND_ERASURE)
        .values(error_code="erasure_incomplete", error=detail["message"])
        .execution_options(synchronize_session=False)
    )
    _slog.info("subject_erasure_incomplete", job_id=str(job_id), phase=phase)


# ── reads (never write) ──────────────────────────────────────────────────────

def _is_admin(user: User) -> bool:
    try:
        return _normalize_user_role(user.role) == UserRole.ADMIN
    except ValueError:
        return False


def may_read_job(job: SubjectRightsJob, user: User) -> bool:
    """The §1.4 rule: a signed-in session of the job's REQUESTER, or of any ADMIN. Every API-key
    principal is refused (answered as the one 404)."""
    if is_api_key_principal(user):
        return False
    if job.requested_by_id is not None and job.requested_by_id == user.id:
        return True
    return _is_admin(user)


async def load_job_for(db: AsyncSession, job_id: uuid.UUID, user: User) -> SubjectRightsJob:
    """The job, or the ONE 404 ``job_not_found`` for every refusal (the job GET and the download)."""
    job = await db.get(SubjectRightsJob, job_id)
    if job is None or not may_read_job(job, user):
        raise job_not_found()
    return job


async def _page(db: AsyncSession, where: Iterable[Any]) -> tuple[list[SubjectRightsJob], int]:
    conditions = list(where)
    rows = await db.execute(
        select(SubjectRightsJob).where(*conditions)
        .order_by(SubjectRightsJob.requested_at.desc(), SubjectRightsJob.id.desc()).limit(JOBS_LIST_LIMIT)
    )
    total = await db.execute(select(func.count()).select_from(SubjectRightsJob).where(*conditions))
    return list(rows.scalars().all()), int(total.scalar_one())


async def list_for_subject(
    db: AsyncSession, subject_id: uuid.UUID, *, kind: Optional[str] = None,
) -> tuple[list[SubjectRightsJob], int]:
    """The subject's jobs (of ``kind`` when given), newest first (≤ ``JOBS_LIST_LIMIT``), and how many
    there are in all (a top-N length is not a count)."""
    where: list[Any] = [SubjectRightsJob.subject_user_id == subject_id]
    if kind is not None:
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        where.append(SubjectRightsJob.kind == kind)
    return await _page(db, where)


async def list_my_exports(db: AsyncSession, user: User) -> tuple[list[SubjectRightsJob], int]:
    """The caller's OWN exports of THEMSELVES (requested_by = subject = the caller), newest first."""
    return await _page(db, (
        SubjectRightsJob.kind == KIND_EXPORT,
        SubjectRightsJob.requested_by_id == user.id,
        SubjectRightsJob.subject_user_id == user.id,
    ))


async def displays(db: AsyncSession, jobs: Iterable[SubjectRightsJob]) -> dict[uuid.UUID, str]:
    """user id → full name, else username, for every subject and requester of ``jobs`` (never an e-mail
    address); an erased user (``erased_at`` set) → "Erased user" whatever the row still holds."""
    ids = {
        user_id for job in jobs for user_id in (job.subject_user_id, job.requested_by_id) if user_id is not None
    }
    if not ids:
        return {}
    result = await db.execute(
        select(User.id, User.full_name, User.username, User.erased_at).where(User.id.in_(ids))
    )
    return {
        row.id: ERASED_DISPLAY if row.erased_at is not None else (row.full_name or row.username)
        for row in result.all()
    }


def _location_labels() -> dict[str, str]:
    """Location key → the inventory's label (pure data; imported here, not at module load, to keep this module's
    import graph as it was)."""
    from app.services.subject_data_inventory import INVENTORY

    return {location.key: location.label for location in INVENTORY}


def to_read(
    job: SubjectRightsJob, *, subject_display: Optional[str], requested_by_display: Optional[str], now: datetime,
) -> SubjectJobRead:
    """The wire shape. ``download_path`` only for a succeeded export with ``expires_at > now``; the
    storage key (and the archive digest / signing key id) is NEVER part of it."""
    error = None
    if job.error_code:
        message = job.error or ERROR_SENTENCES.get(job.error_code, job.error_code)
        error = SubjectRightsError(code=job.error_code, message=message)
    downloadable = (
        job.kind == KIND_EXPORT and job.status == SUCCEEDED and job.expires_at is not None and job.expires_at > now
    )
    counts = {str(k): int(v) for k, v in (job.counts or {}).items()}
    count_labels = None
    if job.kind == KIND_ERASURE and counts:
        # The finished erasure lists "API keys 1", not "api_keys.user_id 1" (local demo, 2026-11-04): the
        # inventory's labels are static text; a key the inventory does not name keeps its key.
        labels = _location_labels()
        count_labels = {key: labels.get(key, key) for key in counts}
    return SubjectJobRead(
        id=job.id, kind=job.kind, status=job.status, source=job.source, subject_user_id=job.subject_user_id,
        subject_display=subject_display, requested_by_display=requested_by_display,
        requested_at=job.requested_at, started_at=job.started_at, finished_at=job.finished_at,
        phase=job.phase if job.kind == KIND_ERASURE else None, bytes=job.bytes, file_count=job.file_count,
        manifest_sha256=job.manifest_sha256, signature=job.signature, expires_at=job.expires_at,
        counts=counts, count_labels=count_labels, error=error,
        download_path=f"/api/v1/subject-data-jobs/{job.id}/download" if downloadable else None,
    )
