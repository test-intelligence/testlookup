"""Project Summary Report — consolidated view of every test suite.

Lives under ``/api/v1/reports/summary``:

  * ``GET  /``      → JSON envelope (powers the in-app report page).
  * ``GET  /pdf``   → PDF export of the same data (ReportLab, off-loop).
  * ``GET  /xlsx``  → the same as an Excel workbook (VIZ-607).
  * ``POST /exports`` → VIZ-607: download now, or queue a background export
    when the report is large (or the reader asks); ``GET /exports`` lists
    the reader's recent ones; ``/exports/{export_id}`` (status), ``/download``
    and ``/retry`` act on one. See ``services/report_export_service``.

Both endpoints honour the ``mode`` query param: ``window`` (default;
aggregate every run in the window) or ``latest`` (one snapshot per
suite). See ``services/summary_report_service`` for the math.
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from typing import Literal
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.config import settings
from app.core.deps import get_current_active_user, require_report_export_access
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, ReportExport, ReportExportStatus, User
from app.models.schemas import ReportExportOut, ReportExportRequestOut, SummaryReportResponse
from app.services import report_export_service as export_svc
from app.services import summary_report_service as svc
from app.services.analytics_meta import build_meta, nothing_applied, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.metrics_service import PASS_RATE_BASIS_UNIQUE_TESTS

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/reports/summary", tags=["Summary Report"])


SummaryMode = Literal["window", "latest"]


# VIZ-201: the report's scope. ``days`` 1-365 (default 7). ``release_id`` and
# (VIZ-202) ``suite_name`` repeat (OR) and every release id is authorised
# before the report is built. EVERY section of the report honours them. A
# project the caller cannot read is a 403; no project at all is the empty
# envelope, as it always was.
_REPORT_SCOPE = ScopePolicy(default_days=7, max_days=365)
_PDF_SCOPE = ScopePolicy(default_days=7, max_days=365, project_required=True)


@router.get("", response_model=SummaryReportResponse)
@analytics_error_contract
async def get_summary_report(
    mode: SummaryMode = Query(
        "window",
        description="``window`` aggregates every run in the window; ``latest`` takes the most recent run per suite.",
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_REPORT_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """Return the summary report payload for the active project."""
    payload = await svc.build_summary_report(
        db, scope.project_id, days=scope.window_days, mode=mode,
        release_id=scope.release_arg, suite_name=scope.suite_arg,
    )
    if scope.project_id is None:
        # The empty envelope: no project was applied, so none is listed.
        meta = await build_meta(
            db, nothing_applied(scope),
            pass_rate_basis=PASS_RATE_BASIS_UNIQUE_TESTS, measured=False,
            reason="The summary report is per project: select a project.",
        )
    else:
        meta = await build_meta(db, scope, pass_rate_basis=PASS_RATE_BASIS_UNIQUE_TESTS)
    return SummaryReportResponse(**with_meta(payload, meta))


async def _export(
    fmt: str, mode: SummaryMode, scope: AnalyticsScope, db: AsyncSession, current_user: User,
) -> StreamingResponse:
    # VIZ-607: the one renderer the background job uses too, so a file made
    # either way is the same file. Timed as ``path="sync"`` (Phase D G1): the
    # reader is waiting on this one.
    started = time.perf_counter()
    try:
        rendered = await export_svc.render_summary_export(db, scope, mode, fmt)
    finally:
        export_svc.observe_export_render(fmt, "sync", time.perf_counter() - started)
    # Every server export leaves an audit row (reports.py always did).
    db.add(export_svc.audit_row(
        user=current_user, scope=scope, mode=mode, fmt=fmt, size_bytes=len(rendered.content),
    ))
    await db.commit()
    logger.info(
        f"summary_report_{fmt}_exported",
        project_id=str(scope.project_id),
        days=scope.window_days,
        mode=mode,
        size_bytes=len(rendered.content),
    )
    return StreamingResponse(
        io.BytesIO(rendered.content),
        media_type=rendered.media_type,
        headers={"Content-Disposition": f'attachment; filename="{rendered.filename}"'},
    )


@router.get("/pdf")
@analytics_error_contract
async def export_summary_report_pdf(
    mode: SummaryMode = Query("window"),
    # The export has to carry the same scope as the screen. A release-scoped
    # report whose PDF is project-wide is the "one response, two scopes" defect
    # in its worst form: the discrepancy leaves the product entirely, in a file
    # somebody attaches to a sign-off.
    scope: AnalyticsScope = Depends(analytics_scope(_PDF_SCOPE)),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the summary report as a downloadable PDF, charts included (VIZ-607)."""
    return await _export("pdf", mode, scope, db, current_user)


@router.get("/xlsx")
@analytics_error_contract
async def export_summary_report_xlsx(
    mode: SummaryMode = Query("window"),
    scope: AnalyticsScope = Depends(analytics_scope(_PDF_SCOPE)),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """VIZ-607: the summary report as an Excel workbook: a context sheet, then
    one sheet per part of the report, each with a native chart over its data."""
    return await _export("xlsx", mode, scope, db, current_user)


# ── VIZ-607: background exports ─────────────────────────────────────────────


def _project_of(scope: AnalyticsScope) -> uuid.UUID:
    """``_PDF_SCOPE`` requires a project; this narrows the type and says so."""
    if scope.project_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select a single project.")
    return scope.project_id


def _export_out(export: ReportExport) -> ReportExportOut:
    completed = export.status == ReportExportStatus.COMPLETED.value
    return ReportExportOut(
        id=export.id,
        project_id=export.project_id,
        format=export.format,  # type: ignore[arg-type]
        status=export.status,  # type: ignore[arg-type]
        attempts=export.attempts,
        params=export.params or {},
        filename=export.filename,
        size_bytes=export.size_bytes,
        error=export.error,
        requested_at=export.requested_at,
        started_at=export.started_at,
        finished_at=export.finished_at,
        expires_at=export.expires_at,
        retryable=export_svc.retryable(export),
        download_url=f"{router.prefix}/exports/{export.id}/download" if completed else None,
    )


# activity: none -- an export reads the report; its AccessAuditLog row (written
# when the file is made) records who exported what.
@router.post("/exports", response_model=ReportExportRequestOut)
@analytics_error_contract
async def request_summary_report_export(
    format: Literal["pdf", "xlsx"] = Query(..., description="``pdf`` or ``xlsx``."),
    mode: SummaryMode = Query("window"),
    background: bool = Query(
        False, description="Queue a background export even when the report is small.",
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_PDF_SCOPE)),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """VIZ-607: download now, or queue a background export.

    ``delivery: "download"`` -- the report is small: fetch ``GET /pdf`` or
    ``/xlsx`` with the same query. ``delivery: "background"`` (202) -- a job
    was queued; poll ``GET /exports/{id}`` and download from ``download_url``
    when it completes. ``dispatched: false`` means the worker could not be
    reached: the job stays queued (it is not failed) and can be retried.
    """
    estimated = await export_svc.estimate_tests(db, _project_of(scope), scope.window_days)
    if not background and estimated <= settings.REPORT_EXPORT_SYNC_MAX_TESTS:
        return ReportExportRequestOut(delivery="download", estimated_tests=estimated)
    export = await export_svc.create_export(db, user=current_user, scope=scope, mode=mode, fmt=format)
    await db.commit()
    await db.refresh(export)
    # Commit first, then dispatch: the worker must be able to see the row.
    dispatched = export_svc.dispatch(export.id)
    body = ReportExportRequestOut(
        delivery="background", estimated_tests=estimated,
        export=_export_out(export), dispatched=dispatched,
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body.model_dump(mode="json"))


@router.get("/exports", response_model=list[ReportExportOut])
@analytics_error_contract
async def list_summary_report_exports(
    scope: AnalyticsScope = Depends(analytics_scope(_PDF_SCOPE)),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The reader's own background exports for the project that have not expired, newest first."""
    rows = await export_svc.list_recent(db, _project_of(scope), current_user.id)
    return [_export_out(row) for row in rows]


async def _load_export(db: AsyncSession, export_id: uuid.UUID) -> ReportExport:
    export = await db.get(ReportExport, export_id)
    if export is None:  # the guard already 404s; deleted in between
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not found")
    return export


@router.get("/exports/{export_id}", response_model=ReportExportOut)
async def get_summary_report_export(
    export_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_report_export_access()),
):
    """One background export's status (any member of its project)."""
    return _export_out(await _load_export(db, export_id))


@router.get("/exports/{export_id}/download")
async def download_summary_report_export(
    export_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_report_export_access()),
):
    """The finished file, through the API (no storage URL leaves the server).

    409 until the export has completed; 410 once it has expired.
    """
    from app.db.storage import get_storage_provider

    export = await _load_export(db, export_id)
    if export.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This export has expired. Export the report again.")
    if export.status != ReportExportStatus.COMPLETED.value or not export.storage_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This export is {export.status}, not ready to download.",
        )
    db.add(AccessAuditLog(
        actor_user_id=current_user.id,
        actor_name=current_user.username,
        project_id=export.project_id,
        action=f"report_summary_export_{export.format}_download",
        after_value={"export_id": str(export.id), "size_bytes": export.size_bytes},
    ))
    await db.commit()
    media_type = export_svc.EXPORTS[export.format][2]
    return StreamingResponse(
        get_storage_provider().stream_object(export.storage_key),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )


# activity: none -- re-queues the reader's own export job; nothing in the
# project changes, and the file it makes is audited when it is made.
@router.post("/exports/{export_id}/retry", response_model=ReportExportOut)
async def retry_summary_report_export(
    export_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_report_export_access()),
):
    """Re-queue a failed export, or one whose message or worker was lost. 409 otherwise."""
    export = await _load_export(db, export_id)
    if not await export_svc.retry(db, export):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This export is {export.status} and cannot be retried.",
        )
    await db.commit()
    await db.refresh(export)
    export_svc.dispatch(export.id)
    return _export_out(export)
