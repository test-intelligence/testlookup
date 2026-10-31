"""W24 ADM-05 — the project export / import worker side: it owns every commit (contract §2.2 E-2 … E-7, §2.3).

The services only stage (``project_export_service``, ``project_import_service``,
``project_data_job_service``); this module is what the three Celery tasks run:

* **claim first**, committed before any work: a redelivered message sees the row
  ``running`` (or finished) and does nothing;
* **export** -- the build reads on ONE read-only ``REPEATABLE READ`` snapshot
  session (the source project is never written); progress and the stored key are
  committed on FRESH short sessions; the signing key (a deployment's first pack)
  on its own session; the outcome + ``project.exported`` in one commit;
* **import** -- G-C's ``run_import`` on ONE transaction committed here (a failure
  anywhere rolls back the whole new project); the upload is removed afterwards;
* **failure** -- recorded AFTER the rollback on a FRESH session: when the error was
  the connection itself, the work's session is the one thing that cannot record it;
* **janitor** -- ``project_export_service.sweep`` committed, then a job still
  ``queued`` past ``QUEUED_REDISPATCH_MINUTES`` (its enqueue was lost) is re-sent
  with its real payload, at most 3 times, then failed ``interrupted``.

The job catches its own failures, so Celery reports success for a failed job:
``testlookup_project_data_jobs_total`` (literal ``.inc()`` calls below) is how an
operator tells them apart. A timeout is not a failure: a slow export stays
``running`` until it ends or the janitor reports ``interrupted``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import structlog

from app.core.metrics import project_data_jobs_total
from app.services import project_data_job_service as jobs
from app.services import project_export_vocab as vocab

logger = structlog.get_logger(__name__)

#: A queued job is re-sent at most this many times by the janitor, then failed ``interrupted``.
REDISPATCH_MAX = 3
#: ``SET TRANSACTION`` for the export's snapshot: one consistent view, and Postgres refuses any write on it.
SNAPSHOT_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


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
    from sqlalchemy import update

    from app.models.postgres import ProjectDataJob

    async with session_factory() as db:
        await db.execute(
            update(ProjectDataJob).where(ProjectDataJob.id == job_id).values(storage_key=key)
            .execution_options(synchronize_session=False)
        )
        await db.commit()


def _progress_writer(session_factory: Callable[[], Any], job_id: uuid.UUID) -> Any:
    async def on_table(done: int, total: int) -> None:
        async with session_factory() as db:
            await jobs.set_progress(db, job_id, done=done, total=total)
            await db.commit()

    return on_table


# ── export ───────────────────────────────────────────────────────────────────

async def run_export_job(
    job_id: uuid.UUID, *, storage: Any = None, session_factory: Optional[Callable[[], Any]] = None,
) -> str:
    """The export worker's whole job; returns the final status (``succeeded`` / ``failed`` / ``skipped``).
    Counted once per job that ran; a job that raises out of here counts as ``failed``."""
    outcome = "failed"
    try:
        outcome = await _run_export(job_id, storage=storage, session_factory=_factory(session_factory))
        return outcome
    finally:
        if outcome == "succeeded":
            project_data_jobs_total.labels(kind="export", outcome="succeeded").inc()
        elif outcome == "failed":
            project_data_jobs_total.labels(kind="export", outcome="failed").inc()


async def _run_export(job_id: uuid.UUID, *, storage: Any, session_factory: Callable[[], Any]) -> str:
    from sqlalchemy import text

    from app.models.postgres import Project, User
    from app.services.activity.service import ActorRef
    from app.services.activity.service import record as record_activity
    from app.services.project_export_service import ProjectExportFailed, run_project_export

    async with session_factory() as db:
        claimed = await jobs.claim(db, job_id, kind=jobs.KIND_EXPORT)
        await db.commit()
        if claimed is None:
            logger.info("project_export_nothing_to_claim", job_id=str(job_id))
            return "skipped"
        project_id, requester_id = claimed.project_id, claimed.requested_by_id

    store = _storage(storage)
    started = _now()

    async def on_written(key: str) -> None:
        await _set_storage_key(session_factory, job_id, key)

    try:
        if project_id is None:
            raise ProjectExportFailed("export_build", error_type="ProjectNotFound")
        async with session_factory() as snapshot:
            if _dialect(snapshot) == "postgresql":
                await snapshot.execute(text(SNAPSHOT_SQL))
            project = await snapshot.get(Project, project_id)
            if project is None:
                raise ProjectExportFailed("export_build", error_type="ProjectNotFound")
            project_name = str(project.name)
            async with session_factory() as sign_db:
                result = await run_project_export(
                    snapshot, job_id=job_id, project=project, storage=store, now=started,
                    on_written=on_written, sign_db=sign_db, on_table=_progress_writer(session_factory, job_id),
                )
                await sign_db.commit()      # a deployment's first pack created the signing key here
            await snapshot.rollback()       # end the read-only snapshot
    except ProjectExportFailed as failed:
        await _record_failure(session_factory, job_id, failed.code, **failed.params)
        logger.warning("project_export_job_failed", job_id=str(job_id), code=failed.code)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - only the type name reaches the job row
        await _record_failure(session_factory, job_id, "export_build", error_type=type(exc).__name__)
        logger.warning("project_export_job_failed", job_id=str(job_id), code="export_build",
                       error_type=type(exc).__name__)
        return "failed"

    finished = _now()
    async with session_factory() as db:
        await jobs.finish_export(db, job_id, outcome={
            "key": result.key, "bytes": result.bytes, "file_count": result.file_count,
            "manifest_sha256": result.manifest_sha256, "archive_sha256": result.archive_sha256,
            "signature": result.signature, "signing_key_id": result.signing_key_id, "counts": result.counts,
        }, now=finished)
        requester = await db.get(User, requester_id) if requester_id is not None else None
        await record_activity(
            db,
            project_id=project_id,
            event_type="project.exported",
            actor=ActorRef.from_user(requester) if requester is not None else ActorRef.system("project-export"),
            entity_id=project_id,
            entity_label=project_name,
            context={"rows": sum(result.counts.values())},
        )
        await db.commit()
    logger.info("project_export_job_succeeded", job_id=str(job_id), project_id=str(project_id),
                tables=len(result.counts), rows=sum(result.counts.values()), bytes=result.bytes)
    return "succeeded"


# ── import ───────────────────────────────────────────────────────────────────

async def run_import_job(
    job_id: uuid.UUID, *, storage: Any = None, session_factory: Optional[Callable[[], Any]] = None,
) -> str:
    """The import worker's whole job; returns ``succeeded`` / ``failed`` / ``skipped``. Counted once per job
    that ran; a job that raises out of here counts as ``failed``."""
    outcome = "failed"
    try:
        outcome = await _run_import(job_id, storage=storage, session_factory=_factory(session_factory))
        return outcome
    finally:
        if outcome == "succeeded":
            project_data_jobs_total.labels(kind="import", outcome="succeeded").inc()
        elif outcome == "failed":
            project_data_jobs_total.labels(kind="import", outcome="failed").inc()


async def _remove_upload(session_factory: Callable[[], Any], store: Any, job_id: uuid.UUID, key: Optional[str]) -> None:
    """The upload is not kept once the job ended; a storage fault leaves the key for the janitor."""
    from app.services.retention_export_service import remove_archive

    if key and await remove_archive(store, key=key):
        await _set_storage_key(session_factory, job_id, None)


async def _run_import(job_id: uuid.UUID, *, storage: Any, session_factory: Callable[[], Any]) -> str:
    from sqlalchemy import update

    from app.core.deps import invalidate_membership_cache
    from app.models.postgres import ProjectDataJob, User
    from app.services.project_import_service import ProjectImportFailed, run_import

    async with session_factory() as db:
        claimed = await jobs.claim(db, job_id, kind=jobs.KIND_IMPORT)
        await db.commit()
        if claimed is None:
            logger.info("project_import_nothing_to_claim", job_id=str(job_id))
            return "skipped"
        upload_key, requester_id = claimed.storage_key, claimed.requested_by_id

    store = _storage(storage)

    async def on_manifest(source_name: str, total: int) -> None:
        async with session_factory() as fresh:
            await fresh.execute(
                update(ProjectDataJob).where(ProjectDataJob.id == job_id)
                .values(project_name=source_name[:255], progress={"done": 0, "total": int(total)})
                .execution_options(synchronize_session=False)
            )
            await fresh.commit()

    extra: dict[str, Any] = {}
    actor_id: Optional[uuid.UUID] = None
    try:
        async with session_factory() as db:
            job = await db.get(ProjectDataJob, job_id)
            actor = await db.get(User, requester_id) if requester_id is not None else None
            if job is None or actor is None or not actor.is_active:
                raise ProjectImportFailed("import_failed", error_type="RequesterUnavailable")
            actor_id = actor.id
            try:
                report = await run_import(
                    db, job=job, storage=store, actor=actor, now=_now(),
                    on_table=_progress_writer(session_factory, job_id), on_manifest=on_manifest, outcome=extra,
                )
                await db.commit()           # ONE transaction: the project, its catalog, the job's success
            except BaseException:
                await db.rollback()
                raise
    except ProjectImportFailed as failed:
        await _record_failure(session_factory, job_id, failed.code, **failed.fmt)
        await _remove_upload(session_factory, store, job_id, upload_key)
        logger.warning("project_import_job_failed", job_id=str(job_id), code=failed.code)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - only the type name reaches the job row
        await _record_failure(session_factory, job_id, "import_failed", error_type=type(exc).__name__)
        await _remove_upload(session_factory, store, job_id, upload_key)
        logger.warning("project_import_job_failed", job_id=str(job_id), code="import_failed",
                       error_type=type(exc).__name__)
        return "failed"

    # After the commit, in create_project's order: the requester's and the service accounts' caches.
    for user_id in [actor_id, *extra.get("enrolled_service_account_ids", [])]:
        if user_id is not None:
            await invalidate_membership_cache(user_id)
    await _remove_upload(session_factory, store, job_id, upload_key)
    logger.info("project_import_job_succeeded", job_id=str(job_id), project_id=str(report.project_id),
                rows=sum(report.inserted.values()))
    return "succeeded"


# ── the janitor (E-7) ────────────────────────────────────────────────────────

def _task_for(kind: str) -> Any:
    from app.worker.tasks import export_project_data, import_project_data

    return export_project_data if kind == jobs.KIND_EXPORT else import_project_data


async def _redispatch_queued(
    session_factory: Callable[[], Any], *, now: datetime, task_for: Callable[[str], Any],
) -> dict[str, int]:
    """A job still ``queued`` past ``QUEUED_REDISPATCH_MINUTES`` lost its enqueue: re-send it (``attempts``
    counts the re-sends, guarded on the value read), at most ``REDISPATCH_MAX`` times, then ``interrupted``."""
    from sqlalchemy import select, update

    from app.models.postgres import ProjectDataJob as Job

    out = {"redispatched": 0, "interrupted_export": 0, "interrupted_import": 0}
    resend: list[tuple[uuid.UUID, str]] = []
    before = now - timedelta(minutes=vocab.QUEUED_REDISPATCH_MINUTES)
    async with session_factory() as db:
        rows = (await db.execute(
            select(Job.id, Job.kind, Job.attempts)
            .where(Job.status == jobs.QUEUED, Job.requested_at <= before)
            .order_by(Job.requested_at).limit(200)
        )).all()
        for job_id, kind, attempts in rows:
            guard = (Job.id == job_id, Job.status == jobs.QUEUED, Job.attempts == attempts)
            if int(attempts or 0) >= REDISPATCH_MAX:
                result = await db.execute(update(Job).where(*guard).values(
                    status=jobs.FAILED, error_code="interrupted", error=vocab.ERROR_SENTENCES["interrupted"],
                    finished_at=now,
                ).execution_options(synchronize_session=False))
                if result.rowcount:
                    out["interrupted_export" if kind == jobs.KIND_EXPORT else "interrupted_import"] += 1
                continue
            result = await db.execute(update(Job).where(*guard).values(attempts=Job.attempts + 1)
                                      .execution_options(synchronize_session=False))
            if result.rowcount:
                resend.append((job_id, kind))
        await db.commit()
    for job_id, kind in resend:
        try:
            task_for(kind).apply_async(args=[str(job_id)])
            out["redispatched"] += 1
        except Exception as exc:  # noqa: BLE001 - still queued: the next run re-sends it
            logger.warning("project_data_redispatch_failed", job_id=str(job_id), error_type=type(exc).__name__)
    return out


async def run_sweep(
    *, now: Optional[datetime] = None, storage: Any = None, session_factory: Optional[Callable[[], Any]] = None,
    task_for: Callable[[str], Any] = _task_for,
) -> dict[str, int]:
    """The hourly janitor (beat ``sweep-project-data-jobs`` at :35). Never raises out: a fault leaves its rows
    for the next hour. The ONLY expiry path -- no GET expires anything."""
    from app.services import project_export_service

    factory = _factory(session_factory)
    at = now or _now()
    summary: dict[str, int] = {}
    try:
        async with factory() as db:
            summary.update(await project_export_service.sweep(db, _storage(storage), now=at))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("project_data_sweep_failed", step="sweep", error_type=type(exc).__name__)
    try:
        queued = await _redispatch_queued(factory, now=at, task_for=task_for)
    except Exception as exc:  # noqa: BLE001
        logger.warning("project_data_sweep_failed", step="redispatch", error_type=type(exc).__name__)
        queued = {}
    summary["redispatched"] = queued.get("redispatched", 0)
    for kind in (jobs.KIND_EXPORT, jobs.KIND_IMPORT):
        key = f"interrupted_{kind}"
        summary[key] = summary.get(key, 0) + queued.get(key, 0)
    if summary.get("expired"):
        project_data_jobs_total.labels(kind="export", outcome="expired").inc(summary["expired"])
    if summary.get("interrupted_export"):
        project_data_jobs_total.labels(kind="export", outcome="interrupted").inc(summary["interrupted_export"])
    if summary.get("interrupted_import"):
        project_data_jobs_total.labels(kind="import", outcome="interrupted").inc(summary["interrupted_import"])
    logger.info("project_data_jobs_swept", **summary)
    return summary
