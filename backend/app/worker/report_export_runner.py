"""VIZ-607 — the background report export's worker side: it owns every commit.

``services/report_export_service`` only stages (the repo's transaction-boundary
rule: services are stage-only; the worker that runs the job owns its
transaction). This module is what ``generate_report_export`` and
``sweep_report_exports`` run:

* the claim is committed BEFORE the render, so a redelivered message sees the
  row ``running`` and does nothing;
* the outcome is committed after it, fenced by the attempt the claim returned;
* a failure is staged after the job's rollback on a FRESH session and
  committed there: when the error was the database connection itself, the
  job's session is the one thing that cannot record it.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

import structlog

from app.models.postgres import ReportExport, ReportExportStatus
from app.services import report_export_service as export_svc
from app.services.report_export_service import ExportRefused, SummaryMode

logger = structlog.get_logger(__name__)


async def run_export_job(export_id: uuid.UUID) -> str:
    """The worker's whole job. Returns the final status for the task result.

    Counted once per job in ``testlookup_report_export_jobs_total`` with the
    status it returns (``export_svc.EXPORT_JOB_OUTCOMES``): the job catches its
    own errors, so Celery reports success for a failed export and only this
    counter tells them apart. A job that raises out of here (its failure could
    not even be recorded) counts as ``failed``.
    """
    job: dict[str, Optional[str]] = {"format": None}
    outcome = ReportExportStatus.FAILED.value
    try:
        outcome = await _run(export_id, job)
        return outcome
    finally:
        export_svc.count_export_job(job["format"], outcome)


async def _run(export_id: uuid.UUID, job: dict[str, Optional[str]]) -> str:
    from app.db.postgres import AsyncSessionLocal
    from app.db.storage import get_storage_provider

    async with AsyncSessionLocal() as db:
        attempt = await export_svc.claim(db, export_id)
        await db.commit()
        if attempt is None:
            logger.info("report_export_nothing_to_claim", export_id=str(export_id))
            return "skipped"
        try:
            export = await db.get(ReportExport, export_id)
            if export is None:
                return "skipped"
            job["format"] = export.format
            user, scope = await export_svc._requester_scope(db, export)
            mode: SummaryMode = "latest" if (export.params or {}).get("mode") == "latest" else "window"
            started = time.perf_counter()
            try:
                rendered = await export_svc.render_summary_export(db, scope, mode, export.format)
            finally:
                export_svc.observe_export_render(
                    export.format, "background", time.perf_counter() - started
                )
            key = export_svc.object_key(export, rendered.filename)
            await get_storage_provider().put_object(key, rendered.content, content_type=rendered.media_type)
            if not await export_svc.complete(db, export_id, attempt, key=key, rendered=rendered):
                # A newer attempt owns the row; its result stands.
                await db.rollback()
                return "superseded"
            db.add(export_svc.audit_row(
                user=user, scope=scope, mode=mode, fmt=export.format,
                size_bytes=len(rendered.content), export_id=export_id,
            ))
            await db.commit()
            logger.info(
                "report_export_completed", export_id=str(export_id),
                fmt=export.format, size_bytes=len(rendered.content),
            )
            return ReportExportStatus.COMPLETED.value
        except Exception as exc:
            await db.rollback()
            reason = (
                str(exc) if isinstance(exc, ExportRefused)
                else f"The export could not be generated: {type(exc).__name__}: {exc}"
            )
            async with AsyncSessionLocal() as failure_db:
                await export_svc.fail(failure_db, export_id, attempt, reason)
                await failure_db.commit()
            logger.warning("report_export_failed", export_id=str(export_id), error=reason)
            return ReportExportStatus.FAILED.value


async def sweep() -> dict[str, int]:
    """The nightly retention sweep, committed."""
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        out = await export_svc.sweep_expired(db)
        await db.commit()
        return out
