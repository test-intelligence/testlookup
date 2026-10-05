"""VIZ-607 — Summary Report exports: one renderer, and background export jobs.

**One renderer.** The synchronous download (``GET /api/v1/reports/summary/pdf``
and ``/xlsx``) and the background job both call :func:`render_summary_export`,
so a file made in the background is byte-for-byte the file the download would
have made: the same builder, the same VIZ-308 context block, the same charts.

**Synchronous or background.** ``POST /api/v1/reports/summary/exports``
estimates the report's size with one cheap query (:func:`estimate_tests`: the
tests recorded on the window's runs, an upper bound because the release and
suite filters are not applied to it). At or under
``settings.REPORT_EXPORT_SYNC_MAX_TESTS`` the answer is "download it now";
above it, or when the reader asks for the background, a ``report_exports``
row is queued and :func:`run_export_job` renders it on the ``default`` queue.

**The job's life** (``ReportExportStatus``): ``queued`` → ``running`` →
``completed`` or ``failed``. No ``cancelled``: nothing can produce it.

* :func:`claim` moves ``queued`` → ``running`` with one guarded UPDATE, so a
  redelivered message is a no-op. A ``running`` row whose worker died is
  claimable again once Celery's hard time limit has passed: past that, the
  worker that held it is certainly gone.
* The service only stages; ``app/worker/report_export_runner`` owns every
  commit (the repo's transaction-boundary rule): the claim is committed before
  the render, the outcome after it.
* Success and failure are written fenced by ``attempts``: an older attempt
  that wakes up late cannot overwrite a newer one's result.
* A failure is written after the job's rollback, on a FRESH session: when
  the error was the database connection itself, the job's session is the
  one thing that cannot record it.
* A message the broker never accepted leaves the row ``queued``, never
  ``failed``: the work was not attempted, and it is retryable.
* :func:`retry` re-queues a ``failed`` row, a ``queued`` one older than
  ``STUCK_QUEUED_AFTER`` (its message was lost), or a ``running`` one past
  the hard limit (its worker died).

**Access.** Any signed-in reader of the project may export, as for the
synchronous download. The worker re-checks the requester's access before it
renders: access removed between the request and the render is refused. The
download is served through the API (no presigned URL leaves the server), to
any member of the project, until ``expires_at``; then it is 410.

**Retention.** MinIO has no lifecycle rules. Files are stored under
``report-exports/YYYY/MM/DD/<project>/<export>/`` by the day they were
requested, and :func:`sweep_expired` deletes each day folder older than
``RETENTION`` with ``delete_prefix`` (which also removes files whose project,
and so whose row, was deleted), then the rows themselves.
"""
from __future__ import annotations

import importlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

import structlog
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AccessAuditLog,
    ReportExport,
    ReportExportStatus,
    TestRun,
)
from app.services import summary_report_service as svc
from app.services.analytics_meta import build_meta
from app.services.analytics_scope import AnalyticsScope
from app.services.metrics_service import PASS_RATE_BASIS_UNIQUE_TESTS

logger = structlog.get_logger(__name__)

ExportFormat = Literal["pdf", "xlsx"]
SummaryMode = Literal["window", "latest"]

#: The formats the report exports, with their renderer and media type.
EXPORTS: dict[str, tuple[str, str, str]] = {
    "pdf": ("app.services.summary_report_pdf", "render_summary_report_pdf", "application/pdf"),
    "xlsx": (
        "app.services.summary_report_xlsx",
        "render_summary_report_xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}

KEY_PREFIX = "report-exports"
RETENTION = timedelta(days=7)
#: A queued row whose message never reached a worker becomes retryable after this.
STUCK_QUEUED_AFTER = timedelta(minutes=2)
#: How far back the sweep looks for day folders. It runs daily, so a month
#: covers any outage of the beat; older folders were swept long ago.
SWEEP_LOOKBACK_DAYS = 31


def _hard_time_limit() -> timedelta:
    """Celery's hard limit: past it, the worker that claimed a row is gone."""
    from app.worker.celery_app import celery_app

    return timedelta(seconds=int(celery_app.conf.task_time_limit or 1860))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Rendering ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RenderedExport:
    content: bytes
    filename: str
    media_type: str


async def render_summary_export(
    db: AsyncSession, scope: AnalyticsScope, mode: SummaryMode, fmt: str,
) -> RenderedExport:
    """The Summary Report as ``fmt``, for an authorised ``scope``."""
    project_id, days = scope.project_id, scope.window_days
    # The same builder and scope as the JSON report, so the export prints the
    # numbers the screen shows.
    payload = await svc.build_summary_report(
        db, project_id, days=days, mode=mode,
        release_id=scope.release_arg, suite_name=scope.suite_arg,
    )
    # VIZ-308: the same context block as the screen's report chrome, from the
    # same envelope the JSON report carries.
    meta = await build_meta(db, scope, pass_rate_basis=PASS_RATE_BASIS_UNIQUE_TESTS)

    module_name, function_name, media_type = EXPORTS[fmt]
    renderer = getattr(importlib.import_module(module_name), function_name)
    # ReportLab and openpyxl are sync + CPU-bound -- keep the event loop free.
    content = await run_in_threadpool(renderer, {**payload, "meta": meta})

    slug = re.sub(r"[^a-z0-9_-]+", "_", (payload.get("project_name") or "project").lower()).strip("_")
    filename = f"summary-{slug or 'project'}-{days}d-{mode}.{fmt}"
    return RenderedExport(content=content, filename=filename, media_type=media_type)


# ── Metrics (Phase D G1) ─────────────────────────────────────────────────

#: Every ``outcome`` of ``testlookup_report_export_jobs_total``: exactly the
#: statuses ``report_export_runner.run_export_job`` returns. ``superseded`` is
#: a late attempt whose result a newer one already owns; ``skipped`` a job
#: with nothing to claim (a redelivered message, a row already gone).
EXPORT_JOB_OUTCOMES = ("completed", "failed", "superseded", "skipped")

#: Every ``path`` of ``testlookup_report_export_render_seconds``: ``sync`` is
#: the download-now route, ``background`` the worker job.
EXPORT_RENDER_PATHS = ("sync", "background")

#: The ``format`` label when the job ended before it read the row, or the row
#: names a format this module no longer renders. Bounds the label to
#: :data:`EXPORTS` plus this one value.
UNKNOWN_FORMAT = "unknown"


def format_label(fmt: Optional[str]) -> str:
    return fmt if fmt in EXPORTS else UNKNOWN_FORMAT


def count_export_job(fmt: Optional[str], outcome: str) -> None:
    """Count one finished export job. Never raises: telemetry cannot fail a job."""
    try:
        from app.core.metrics import report_export_jobs_total

        report_export_jobs_total.labels(format=format_label(fmt), outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def observe_export_render(fmt: Optional[str], path: str, seconds: float) -> None:
    """Observe one render. Never raises: telemetry cannot fail an export."""
    try:
        from app.core.metrics import report_export_render_seconds

        report_export_render_seconds.labels(format=format_label(fmt), path=path).observe(
            max(0.0, float(seconds))
        )
    except Exception:  # noqa: BLE001
        pass


def _zero_export_job_series() -> None:
    """Every (format, outcome) series at 0 from import, so ``increase()`` sees
    the FIRST failed job instead of a series that appears already at 1."""
    try:
        from app.core.metrics import report_export_jobs_total

        for fmt in EXPORTS:
            for outcome in EXPORT_JOB_OUTCOMES:
                report_export_jobs_total.labels(format=fmt, outcome=outcome)
    except Exception:  # noqa: BLE001
        pass


_zero_export_job_series()


def audit_row(
    *, user: Any, scope: AnalyticsScope, mode: str, fmt: str, size_bytes: int,
    export_id: Optional[uuid.UUID] = None,
) -> AccessAuditLog:
    """Every server export leaves an audit row: who, which project, which scope.

    The action starts ``report_`` because the audit dashboard lists report
    exports by that prefix.
    """
    after: dict[str, Any] = {
        "days": scope.window_days,
        "mode": mode,
        "release_ids": list(scope.release_ids),
        "suite_names": list(scope.suite_names),
        "size_bytes": size_bytes,
    }
    if export_id is not None:
        after["export_id"] = str(export_id)
    return AccessAuditLog(
        actor_user_id=user.id,
        actor_name=user.username,
        project_id=scope.project_id,
        action=f"report_summary_export_{fmt}",
        after_value=after,
    )


# ── Synchronous or background ────────────────────────────────────────────


async def estimate_tests(db: AsyncSession, project_id: uuid.UUID, days: int) -> int:
    """The tests recorded on the project's runs in the window: an upper bound
    on the report's size, from one indexed aggregate over ``test_runs``."""
    since = _now() - timedelta(days=days)
    total = await db.scalar(
        select(func.coalesce(func.sum(TestRun.total_tests), 0)).where(
            TestRun.project_id == project_id, TestRun.created_at >= since,
        )
    )
    return int(total or 0)


def scope_params(scope: AnalyticsScope, mode: str) -> dict[str, Any]:
    """What the worker needs to rebuild (and re-authorise) the scope."""
    return {
        "mode": mode,
        "days": scope.window_days,
        "release_ids": list(scope.release_ids),
        "suite_names": list(scope.suite_names),
    }


# ── The job ──────────────────────────────────────────────────────────────


def object_key(export: ReportExport, filename: str) -> str:
    day = export.requested_at.astimezone(timezone.utc)
    return f"{KEY_PREFIX}/{day:%Y/%m/%d}/{export.project_id}/{export.id}/{filename}"


async def create_export(
    db: AsyncSession, *, user: Any, scope: AnalyticsScope, mode: str, fmt: str,
) -> ReportExport:
    """Stage a ``queued`` row. The caller commits, THEN dispatches: a worker
    must never be handed an id it cannot yet see."""
    now = _now()
    export = ReportExport(
        project_id=scope.project_id,
        requested_by_id=user.id,
        report="summary",
        format=fmt,
        params=scope_params(scope, mode),
        status=ReportExportStatus.QUEUED.value,
        attempts=0,
        requested_at=now,
        expires_at=now + RETENTION,
    )
    db.add(export)
    await db.flush()
    return export


def dispatch(export_id: uuid.UUID) -> bool:
    """Hand the job to the worker. ``False`` when the broker refused it: the
    row stays ``queued`` (never ``failed`` -- nothing was attempted) and
    :func:`retry` can re-send it once ``STUCK_QUEUED_AFTER`` has passed."""
    from app.worker.tasks import generate_report_export

    try:
        generate_report_export.apply_async(args=[str(export_id)])
        return True
    except Exception as exc:  # noqa: BLE001 -- any broker error leaves it queued
        logger.warning("report_export_dispatch_failed", export_id=str(export_id), error=str(exc))
        return False


async def claim(db: AsyncSession, export_id: uuid.UUID) -> Optional[int]:
    """``queued`` (or abandoned ``running``) → ``running``. Returns the
    attempt number this worker now owns, or ``None`` when there is nothing to
    do: already running elsewhere, finished, or gone. Stage-only: the worker
    commits the claim before it renders, so another delivery sees it."""
    now = _now()
    result = await db.execute(
        update(ReportExport)
        .where(
            ReportExport.id == export_id,
            or_(
                ReportExport.status == ReportExportStatus.QUEUED.value,
                and_(
                    ReportExport.status == ReportExportStatus.RUNNING.value,
                    ReportExport.started_at < now - _hard_time_limit(),
                ),
            ),
        )
        .values(
            status=ReportExportStatus.RUNNING.value,
            attempts=ReportExport.attempts + 1,
            started_at=now,
            finished_at=None,
            error=None,
        )
        .returning(ReportExport.attempts)
    )
    return result.scalar_one_or_none()


async def complete(
    db: AsyncSession, export_id: uuid.UUID, attempt: int, *, key: str, rendered: RenderedExport,
) -> bool:
    result = await db.execute(
        update(ReportExport)
        .where(
            ReportExport.id == export_id,
            ReportExport.attempts == attempt,
            ReportExport.status == ReportExportStatus.RUNNING.value,
        )
        .values(
            status=ReportExportStatus.COMPLETED.value,
            storage_key=key,
            filename=rendered.filename,
            size_bytes=len(rendered.content),
            finished_at=_now(),
        )
        .returning(ReportExport.id)
    )
    return result.scalar_one_or_none() is not None


async def fail(db: AsyncSession, export_id: uuid.UUID, attempt: int, reason: str) -> None:
    """Stage a failure. The worker stages it on a fresh session and commits it
    (see the module notes)."""
    await db.execute(
        update(ReportExport)
        .where(
            ReportExport.id == export_id,
            ReportExport.attempts == attempt,
            ReportExport.status == ReportExportStatus.RUNNING.value,
        )
        .values(
            status=ReportExportStatus.FAILED.value,
            error=reason[:2000],
            finished_at=_now(),
        )
    )


class ExportRefused(Exception):
    """The job cannot run, for a reason the reader should see."""


async def _requester_scope(db: AsyncSession, export: ReportExport) -> tuple[Any, AnalyticsScope]:
    """Rebuild the scope AS THE REQUESTER, re-checking their access now."""
    from fastapi import HTTPException

    from app.models.postgres import User
    from app.routers.summary_report import _PDF_SCOPE
    from app.services.analytics_scope import resolve_analytics_scope

    user = await db.get(User, export.requested_by_id) if export.requested_by_id else None
    if user is None or not getattr(user, "is_active", True):
        raise ExportRefused("The person who requested this export no longer has an active account.")
    params = export.params or {}
    try:
        scope = await resolve_analytics_scope(
            db, user, policy=_PDF_SCOPE,
            project_id=str(export.project_id),
            release_id=params.get("release_ids") or None,
            suite_name=params.get("suite_names") or None,
            days=params.get("days"),
        )
    except HTTPException as exc:
        raise ExportRefused(f"Access to this report was refused when the export ran ({exc.status_code}).") from exc
    if scope.denied or scope.project_id is None:
        raise ExportRefused("Access to this report was refused when the export ran.")
    return user, scope


def retryable(export: ReportExport, now: Optional[datetime] = None) -> bool:
    now = now or _now()
    if export.status == ReportExportStatus.FAILED.value:
        return True
    if export.status == ReportExportStatus.QUEUED.value:
        return export.requested_at < now - STUCK_QUEUED_AFTER
    if export.status == ReportExportStatus.RUNNING.value:
        return export.started_at is not None and export.started_at < now - _hard_time_limit()
    return False


async def retry(db: AsyncSession, export: ReportExport) -> bool:
    """Re-queue a retryable row (the caller commits, then dispatches)."""
    if not retryable(export):
        return False
    export.status = ReportExportStatus.QUEUED.value
    export.error = None
    export.finished_at = None
    export.requested_at = _now()
    export.expires_at = export.requested_at + RETENTION
    await db.flush()
    return True


async def list_recent(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[ReportExport]:
    """The reader's own recent exports for the project, newest first."""
    rows = await db.execute(
        select(ReportExport)
        .where(
            ReportExport.project_id == project_id,
            ReportExport.requested_by_id == user_id,
            ReportExport.expires_at > _now(),
        )
        .order_by(ReportExport.requested_at.desc())
        .limit(limit)
    )
    return list(rows.scalars())


# ── Retention ────────────────────────────────────────────────────────────


async def sweep_expired(db: AsyncSession, now: Optional[datetime] = None) -> dict[str, int]:
    """Delete export files older than ``RETENTION``, then stage the rows'
    deletion (the worker commits)."""
    from app.db.storage import get_storage_provider

    now = now or _now()
    storage = get_storage_provider()
    objects = 0
    first_expired_day = (now - RETENTION).date() - timedelta(days=1)
    for back in range(SWEEP_LOOKBACK_DAYS):
        day = first_expired_day - timedelta(days=back)
        objects += await storage.delete_prefix(f"{KEY_PREFIX}/{day:%Y/%m/%d}/")
    result = await db.execute(
        delete(ReportExport)
        .where(ReportExport.expires_at <= now)
        .returning(ReportExport.id)
    )
    rows = len(result.all())
    logger.info("report_exports_swept", objects=objects, rows=rows)
    return {"objects": objects, "rows": rows}
