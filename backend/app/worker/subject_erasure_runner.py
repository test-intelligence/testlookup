"""The erasure worker (W31 STRAT-12; contract §2.3 E-3 … E-11): it owns every commit of an erasure job.

``run_erasure_job`` claims the job (``queued`` → ``running``, committed before any
work: a redelivered message is a no-op), then runs the phases AFTER the last
committed one, each on its own session and committed together with the job's
phase and counts:

* ``postgres`` — ``subject_erasure_service.run_postgres_phase`` (ONE transaction:
  the deletes, the rewrites, the user row);
* ``objects`` — the person's exports and the project exports that list them;
* ``redis`` — the id-keyed caches (best effort);
* ``done`` — status ``succeeded`` + the ``subject_erased`` access-audit row, in one
  commit (``record_activity`` is NOT called: no project event).

A phase that raises is rolled back; the job stays ``running`` with
``error_code="erasure_incomplete"`` naming the last committed phase (recorded on a
FRESH session — a timeout is not a failure), and the next run (the janitor's
re-dispatch, or the admin's resume) starts at that phase again. An erasure is
never ``failed``. Log lines carry the job / user id, the phase and error TYPE
names only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import structlog

from app.services import subject_erasure_service as erasure
from app.services import subject_rights_job_service as jobs

_slog = structlog.get_logger(__name__)

SUCCEEDED, INCOMPLETE, SKIPPED = "succeeded", "incomplete", "skipped"


def _factory(session_factory: Optional[Callable[[], Any]]) -> Callable[[], Any]:
    if session_factory is not None:
        return session_factory
    from app.db.postgres import get_session_factory

    return get_session_factory()


def _storage(storage: Any) -> Any:
    if storage is not None:
        return storage
    from app.db.storage import get_storage_provider

    return get_storage_provider()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _incomplete(factory: Callable[[], Any], job_id: uuid.UUID, *, phase: str, exc: BaseException) -> str:
    """The step after ``phase`` raised (its session already rolled back): record ``erasure_incomplete`` on a
    FRESH session; the job stays ``running``."""
    _slog.warning("subject_erasure_phase_failed", job_id=str(job_id), phase=phase, error_type=type(exc).__name__)
    try:
        async with factory() as db:
            await jobs.mark_erasure_incomplete(db, job_id, phase=phase)
            await db.commit()
    except Exception as record_exc:  # noqa: BLE001 - the janitor still re-dispatches a running job
        _slog.error("subject_erasure_incomplete_not_recorded", job_id=str(job_id),
                    error_type=type(record_exc).__name__)
    return INCOMPLETE


async def _claim(factory: Callable[[], Any], job_id: uuid.UUID) -> Optional[tuple[Optional[str], uuid.UUID]]:
    async with factory() as db:
        job = await jobs.claim(db, job_id, kind=jobs.KIND_ERASURE)
        if job is None:
            return None
        claimed = (job.phase, job.subject_user_id)
        await db.commit()
    return claimed


async def run_erasure_job(
    job_id: Any, *, storage: Any = None, session_factory: Optional[Callable[[], Any]] = None, redis: Any = None,
) -> str:
    """Claim, then every phase after the last committed one, each committed with the job row.
    Returns ``"succeeded"`` (phase ``done``), ``"incomplete"`` (a phase raised; it resumes on the next run)
    or ``"skipped"`` (nothing queued under that id: redelivered, finished, or not an erasure)."""
    job_id = uuid.UUID(str(job_id))
    factory = _factory(session_factory)
    claimed = await _claim(factory, job_id)
    if claimed is None:
        _slog.info("subject_erasure_skipped", job_id=str(job_id))
        return SKIPPED
    phase, subject_id = claimed
    phase = phase or "lockout"

    if phase == "lockout":
        try:
            async with factory() as db:
                counts = await erasure.run_postgres_phase(db, job_id=job_id, now=_now())
                await jobs.set_phase(db, job_id, phase="postgres", counts=counts, now=_now())
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - the whole transaction rolled back; resumes at this phase
            return await _incomplete(factory, job_id, phase="lockout", exc=exc)
        phase = "postgres"

    if phase == "postgres":
        try:
            async with factory() as db:
                counts = await erasure.run_object_phase(db, _storage(storage), job_id=job_id)
                await jobs.set_phase(db, job_id, phase="objects", counts=counts, now=_now())
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            return await _incomplete(factory, job_id, phase="postgres", exc=exc)
        phase = "objects"

    if phase == "objects":
        try:
            counts = await erasure.run_redis_phase(user_id=subject_id, redis=redis)
            async with factory() as db:
                await jobs.set_phase(db, job_id, phase="redis", counts=counts, now=_now())
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            return await _incomplete(factory, job_id, phase="objects", exc=exc)
        phase = "redis"

    if phase == "redis":
        try:
            async with factory() as db:
                await jobs.set_phase(db, job_id, phase=jobs.PHASE_DONE, counts={}, now=_now())
                job = await db.get(erasure.SubjectRightsJob, job_id, populate_existing=True)
                await erasure.stage_erased_audit(db, job=job)
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            return await _incomplete(factory, job_id, phase="redis", exc=exc)

    _slog.info("subject_erasure_done", job_id=str(job_id), user_id=str(subject_id))
    return SUCCEEDED
