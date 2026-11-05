"""W31 STRAT-12 — the subject export worker and the subject-rights janitor: they own every commit (contract §1.4,
§2.2, §2.3 E-8). The ``project_data_runner`` shape, over ``subject_rights_jobs``.

The services only stage (``subject_export_service``, ``subject_rights_job_service``); this module is what two of
the three Celery tasks run (the erasure's is G-C's ``subject_erasure_runner``):

* **claim first**, committed before any work: a redelivered message sees the row ``running`` (or finished) and
  does nothing (``skipped``);
* **export** — the build reads on ONE read-only ``REPEATABLE READ`` snapshot session (nothing of the person is
  written); the stored key is committed on a FRESH short session the moment it is uploaded (a crash before the
  verification leaves the object for the janitor); the signing key (a deployment's first pack) on its own
  session; the outcome (``succeeded``, ``expires_at`` = +72 h) in one commit. No activity row (§2.2: the access
  audit row the route wrote is the record);
* **failure** — recorded AFTER the rollback on a FRESH session (when the error was the connection itself, the work's
  session is the one thing that cannot record it): ``failed`` with the §1.1 sentence;
* **janitor** — ``subject_export_service.sweep`` committed (expiry — the ONLY expiry path —, objects of failed /
  expired exports, a stale ``running`` export → ``interrupted``); then a QUEUED export older than
  ``QUEUED_REDISPATCH_MINUTES`` (its enqueue was lost) is re-sent with its real payload at most ``REDISPATCH_MAX``
  times, then failed ``interrupted``; an ERASURE left queued, stale ``running`` or ``erasure_incomplete`` is
  re-queued (``requeue``) and re-sent WITHOUT a cap and is NEVER failed — a half-erased account must finish.

The job catches its own failures, so Celery reports success for a failed job: the log lines (job id, code) tell
them apart. A timeout is not a failure: a slow export stays ``running`` until it ends or the janitor reports it
``interrupted``. Log lines carry job / user ids, kinds, codes and counts — never a name, a key or a digest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import structlog

from app.services import subject_rights_job_service as jobs
from app.services.subject_rights_vocab import (
    ERROR_SENTENCES,
    JOB_STALE_MINUTES,
    QUEUED_REDISPATCH_MINUTES,
    REDISPATCH_MAX,
)

logger = structlog.get_logger(__name__)

#: ``SET TRANSACTION`` for the export's snapshot: one consistent view, and Postgres refuses any write on it.
SNAPSHOT_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
#: The janitor reads at most this many rows per step and run (the rest wait for the next hour).
REDISPATCH_LIMIT = 200


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


def _dialect(session: Any) -> str:
    """The session's dialect name (the snapshot's ``SET TRANSACTION`` is Postgres syntax)."""
    try:
        return str(session.get_bind().dialect.name)
    except Exception:  # noqa: BLE001 - a session double has no bind
        return ""


async def _record_failure(session_factory: Callable[[], Any], job_id: uuid.UUID, code: str, **fmt: Any) -> None:
    """``failed`` with the §1.1 sentence, on a FRESH session (the work's session was rolled back)."""
    async with session_factory() as db:
        await jobs.mark_failed(db, job_id, code=code, now=_now(), **fmt)
        await db.commit()


async def _set_storage_key(session_factory: Callable[[], Any], job_id: uuid.UUID, key: Optional[str]) -> None:
    """The uploaded object's key, committed at once (never returned, never logged)."""
    from sqlalchemy import update

    from app.models.postgres import SubjectRightsJob

    async with session_factory() as db:
        await db.execute(
            update(SubjectRightsJob).where(SubjectRightsJob.id == job_id).values(storage_key=key)
            .execution_options(synchronize_session=False)
        )
        await db.commit()


# ── export ───────────────────────────────────────────────────────────────────

async def run_export_job(
    job_id: Any, *, storage: Any = None, session_factory: Optional[Callable[[], Any]] = None,
) -> str:
    """The export worker's whole job; returns ``succeeded`` / ``failed`` / ``skipped`` (nothing to claim)."""
    from sqlalchemy import text

    from app.models.postgres import User
    from app.services.subject_export_service import SubjectExportFailed, run_subject_export

    factory = _factory(session_factory)
    job_id = uuid.UUID(str(job_id))
    async with factory() as db:
        claimed = await jobs.claim(db, job_id, kind=jobs.KIND_EXPORT)
        await db.commit()
        if claimed is None:
            logger.info("subject_export_nothing_to_claim", job_id=str(job_id))
            return "skipped"
        subject_id, source, requested_at = claimed.subject_user_id, claimed.source, claimed.requested_at

    store = _storage(storage)
    started = _now()

    async def on_written(key: str) -> None:
        await _set_storage_key(factory, job_id, key)

    try:
        async with factory() as snapshot:
            if _dialect(snapshot) == "postgresql":
                await snapshot.execute(text(SNAPSHOT_SQL))
            subject = await snapshot.get(User, subject_id)
            if subject is None:
                raise SubjectExportFailed("export_build", error_type="UserNotFound")
            if subject.erased_at is not None:
                raise SubjectExportFailed("export_build", error_type="UserErased")
            async with factory() as sign_db:
                result = await run_subject_export(
                    snapshot, job_id=job_id, subject=subject, storage=store, now=started, on_written=on_written,
                    sign_db=sign_db, source=source, requested_at=requested_at,
                )
                await sign_db.commit()      # a deployment's first pack created the signing key here
            await snapshot.rollback()       # (already ended after the build; harmless)
    except SubjectExportFailed as failed:
        await _record_failure(factory, job_id, failed.code, **failed.params)
        logger.warning("subject_export_job_failed", job_id=str(job_id), code=failed.code)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - only the type name reaches the job row
        await _record_failure(factory, job_id, "export_build", error_type=type(exc).__name__)
        logger.warning("subject_export_job_failed", job_id=str(job_id), code="export_build",
                       error_type=type(exc).__name__)
        return "failed"

    async with factory() as db:
        await jobs.finish_export(db, job_id, outcome={
            "key": result.key, "bytes": result.bytes, "file_count": result.file_count,
            "manifest_sha256": result.manifest_sha256, "archive_sha256": result.archive_sha256,
            "signature": result.signature, "signing_key_id": result.signing_key_id, "counts": result.counts,
        }, now=_now())
        await db.commit()
    logger.info("subject_export_job_succeeded", job_id=str(job_id), subject_id=str(subject_id),
                members=len(result.counts), rows=sum(result.counts.values()), bytes=result.bytes)
    return "succeeded"


# ── the janitor (§2.2 Expiry, §2.3 E-8) ──────────────────────────────────────

def _task_for(kind: str) -> Any:
    from app.worker.tasks import erase_subject_data, export_subject_data

    return export_subject_data if kind == jobs.KIND_EXPORT else erase_subject_data


async def _redispatch(
    session_factory: Callable[[], Any], *, now: datetime, task_for: Callable[[str], Any],
) -> dict[str, int]:
    """Stage + commit the re-dispatch decisions, THEN send (a worker must never be handed a row it cannot see).

    * a queued EXPORT past ``QUEUED_REDISPATCH_MINUTES``: ``attempts + 1`` (guarded on the value read) and re-sent,
      at most ``REDISPATCH_MAX`` times, then ``failed`` ``interrupted`` (nothing was kept);
    * a queued ERASURE past ``QUEUED_REDISPATCH_MINUTES``: re-sent (no cap);
    * a ``running`` ERASURE whose run ended ``erasure_incomplete`` or whose worker died (``started_at`` past
      ``JOB_STALE_MINUTES``): ``requeue`` → ``queued`` and re-sent (no cap). An erasure is never failed here."""
    from sqlalchemy import or_, select, update

    from app.models.postgres import SubjectRightsJob as Job

    out = {"redispatched_export": 0, "redispatched_erasure": 0, "interrupted_queued": 0, "redispatch_failed": 0}
    resend: list[tuple[uuid.UUID, str]] = []
    queued_before = now - timedelta(minutes=QUEUED_REDISPATCH_MINUTES)
    async with session_factory() as db:
        exports = (await db.execute(
            select(Job.id, Job.attempts)
            .where(Job.kind == jobs.KIND_EXPORT, Job.status == jobs.QUEUED, Job.requested_at <= queued_before)
            .order_by(Job.requested_at).limit(REDISPATCH_LIMIT)
        )).all()
        for job_id, attempts in exports:
            guard = (Job.id == job_id, Job.status == jobs.QUEUED, Job.attempts == attempts)
            if int(attempts or 0) >= REDISPATCH_MAX:
                result = await db.execute(update(Job).where(*guard).values(
                    status=jobs.FAILED, error_code="interrupted", error=ERROR_SENTENCES["interrupted"],
                    finished_at=now,
                ).execution_options(synchronize_session=False))
                out["interrupted_queued"] += int(result.rowcount or 0)
                continue
            result = await db.execute(update(Job).where(*guard).values(attempts=Job.attempts + 1)
                                      .execution_options(synchronize_session=False))
            if result.rowcount:
                resend.append((job_id, jobs.KIND_EXPORT))
        erasures = (await db.execute(
            select(Job.id, Job.status)
            .where(Job.kind == jobs.KIND_ERASURE, or_(
                (Job.status == jobs.QUEUED) & (Job.requested_at <= queued_before),
                (Job.status == jobs.RUNNING) & or_(
                    Job.error_code == "erasure_incomplete",
                    Job.started_at <= now - timedelta(minutes=JOB_STALE_MINUTES),
                ),
            ))
            .order_by(Job.requested_at).limit(REDISPATCH_LIMIT)
        )).all()
        for job_id, status in erasures:
            if status == jobs.QUEUED or await jobs.requeue(db, job_id, kind=jobs.KIND_ERASURE):
                resend.append((job_id, jobs.KIND_ERASURE))
        await db.commit()
    for job_id, kind in resend:
        try:
            task_for(kind).apply_async(args=[str(job_id)])
            out[f"redispatched_{kind}"] += 1
        except Exception as exc:  # noqa: BLE001 - still queued: the next run re-sends it
            out["redispatch_failed"] += 1
            logger.warning("subject_rights_redispatch_failed", job_id=str(job_id), kind=kind,
                           error_type=type(exc).__name__)
    return out


async def run_sweep(
    *, now: Optional[datetime] = None, storage: Any = None, session_factory: Optional[Callable[[], Any]] = None,
    task_for: Callable[[str], Any] = _task_for,
) -> dict[str, int]:
    """The hourly janitor (beat ``sweep-subject-rights-jobs`` at :50). Never raises out: a fault leaves its rows for
    the next hour. The ONLY expiry path — no GET expires anything. W24's ``project_data_jobs`` janitor is separate
    and untouched."""
    from app.services import subject_export_service

    factory = _factory(session_factory)
    at = now or _now()
    summary: dict[str, int] = {}
    try:
        async with factory() as db:
            summary.update(await subject_export_service.sweep(db, _storage(storage), now=at))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject_rights_sweep_failed", step="sweep", error_type=type(exc).__name__)
    try:
        summary.update(await _redispatch(factory, now=at, task_for=task_for))
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject_rights_sweep_failed", step="redispatch", error_type=type(exc).__name__)
    logger.info("subject_rights_jobs_swept", **summary)
    return summary
