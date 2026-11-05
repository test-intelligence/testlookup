"""The erasure of a person (W31 STRAT-12, Art. 17; contract §2.3, §3.1) — ADMIN-only, session-only, never self.

* ``GET /api/v1/users/{user_id}/erasure-preview`` — what would change, what is
  kept (Art. 17(3)) and what stays open; READS ONLY (a GET never writes).
* ``POST /api/v1/users/{user_id}/erasure`` — E-1's refusals (checked again under
  the user row's lock), the typed confirmation, then the lock-out in ONE commit
  (the job + the token revocation + the session / key lock-out + the audit row),
  then the worker is enqueued → 202. A RESUME of an unfinished erasure re-queues
  that job (same id) instead of a 409.

Every route sits behind ``require_signed_in_session()`` (an API-key principal is
403 ``session_required`` before any id is read) and ``require_role(ADMIN)``.
Refusals answer ``detail = {"code", "message"}`` (§1.1). The worker
(``worker/subject_erasure_runner.py``) runs the remaining phases; G-B's
``main.py`` mounts this router and ``worker/tasks.py`` defines the task.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import invalidate_membership_cache, require_role
from app.db.postgres import get_db
from app.models.postgres import SubjectRightsJob, User, UserRole
from app.models.schemas import ErasurePreview, ErasureRequest, SubjectJobRead
from app.services import subject_erasure_service as erasure
from app.services import subject_rights_job_service as jobs

_slog = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["User Management"])

_signed_in_session = jobs.require_signed_in_session()


def _refused(code: str) -> HTTPException:
    return HTTPException(status_code=erasure.REFUSAL_STATUS[code], detail=erasure.refusal_detail(code))


async def _subject(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> User:
    """The target user (404 ``user_not_found``); under ``FOR UPDATE`` for the POST, so two concurrent
    requests for one person are serialised and the second sees the first's job."""
    statement = select(User).where(User.id == user_id)
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement.execution_options(populate_existing=True))
    subject = result.scalar_one_or_none()
    if subject is None:
        raise _refused("user_not_found")
    return subject


async def _read(db: AsyncSession, job: SubjectRightsJob) -> SubjectJobRead:
    names = await jobs.displays(db, [job])
    return jobs.to_read(
        job, subject_display=names.get(job.subject_user_id),
        requested_by_display=names.get(job.requested_by_id) if job.requested_by_id else None,
        now=datetime.now(timezone.utc),
    )


def _dispatch(job_id: uuid.UUID) -> None:
    """Enqueue the worker AFTER the commit. A lost enqueue is not fatal: the janitor re-dispatches a job
    left ``queued`` (G-B's sweep), and the admin may resume."""
    try:
        from app.worker.tasks import erase_subject_data

        erase_subject_data.apply_async(args=[str(job_id)])
    except Exception as exc:  # noqa: BLE001 - the job row is committed; the janitor re-sends it
        _slog.warning("subject_erasure_dispatch_failed", job_id=str(job_id), error_type=type(exc).__name__)


@router.get("/{user_id}/erasure-preview", response_model=ErasurePreview)
async def get_erasure_preview(
    user_id: uuid.UUID,
    current_user: User = Depends(_signed_in_session),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ErasurePreview:
    """Counts of what erasing this person would delete / rewrite, keep and leave open, the blockers (listed,
    not raised), the pseudonym, and an unfinished erasure job to resume. Writes nothing."""
    subject = await _subject(db, user_id)
    return await erasure.preview(db, subject=subject, actor=current_user)


# activity: none - an erasure is not a project event; its two access-audit rows are the record (§2.3 E-11)
@router.post("/{user_id}/erasure", response_model=SubjectJobRead, status_code=202)
async def erase_user(
    user_id: uuid.UUID,
    payload: ErasureRequest,
    current_user: User = Depends(_signed_in_session),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SubjectJobRead:
    """Erase (pseudonymise) this person: refusals in E-1's order, the exact typed username, then the
    lock-out committed in ONE transaction and the worker enqueued. An unfinished erasure is resumed."""
    subject = await _subject(db, user_id, lock=True)
    code = await erasure.refusal(db, subject=subject, actor=current_user)
    if code is not None:
        raise _refused(code)
    if not erasure.confirm_matches(subject, payload.confirm_username):
        raise _refused("confirm_mismatch")
    unfinished = await jobs.live_job(db, subject_id=subject.id, kind=jobs.KIND_ERASURE)
    if unfinished is not None:
        # refusal() let it through, so it is resumable: re-queue the SAME job (no second lock-out)
        await jobs.requeue(db, unfinished.id, kind=jobs.KIND_ERASURE)
        await db.commit()
        job = await db.get(SubjectRightsJob, unfinished.id, populate_existing=True)
        _slog.info("subject_erasure_resumed", job_id=str(unfinished.id), user_id=str(subject.id))
    else:
        try:
            job = await jobs.create_erasure_job(db, subject=subject, actor=current_user)
        except jobs.ErasureRunning:
            raise _refused("erasure_running") from None
        await erasure.stage_lockout(db, subject=subject, actor=current_user, job=job)
        await db.commit()
        await db.refresh(job)
    if job is None:  # pragma: no cover - the row was committed above
        raise _refused("user_not_found")
    await invalidate_membership_cache(subject.id)
    _dispatch(job.id)
    return await _read(db, job)
