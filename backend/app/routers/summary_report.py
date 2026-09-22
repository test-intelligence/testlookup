"""Project Summary Report — consolidated view of every test suite.

Lives under ``/api/v1/reports/summary``. Two endpoints:

  * ``GET  /``      → JSON envelope (powers the in-app report page).
  * ``GET  /pdf``   → PDF export of the same data (ReportLab, off-loop).

Both endpoints honour the ``mode`` query param: ``window`` (default;
aggregate every run in the window) or ``latest`` (one snapshot per
suite). See ``services/summary_report_service`` for the math.
"""
from __future__ import annotations

import io
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.db.postgres import get_db
from app.models.schemas import SummaryReportResponse
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
):
    """Return the summary report as a downloadable PDF."""
    project_id, days = scope.project_id, scope.window_days
    # The same builder and scope as the JSON report, so the PDF prints the
    # numbers the screen shows.
    payload = await svc.build_summary_report(
        db, project_id, days=days, mode=mode,
        release_id=scope.release_arg, suite_name=scope.suite_arg,
    )

    # ReportLab is sync + CPU-bound — keep the event loop free.
    from app.services.summary_report_pdf import render_summary_report_pdf

    pdf_bytes = await run_in_threadpool(render_summary_report_pdf, payload)

    project_slug = (payload.get("project_name") or "project").replace(" ", "_").lower()
    filename = f"summary-{project_slug}-{days}d-{mode}.pdf"

    logger.info(
        "summary_report_pdf_exported",
        project_id=str(project_id),
        days=days,
        mode=mode,
        size_bytes=len(pdf_bytes),
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
