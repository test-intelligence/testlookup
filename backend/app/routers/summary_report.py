"""Project Summary Report — consolidated view of every test suite.

Lives under ``/api/v1/reports/summary``. Two endpoints:

  * ``GET  /``      → JSON envelope (powers the in-app report page).
  * ``GET  /pdf``   → PDF export of the same data (ReportLab, off-loop).

Both endpoints honour the ``mode`` query param: ``window`` (default;
aggregate every run in the window) or ``latest`` (one snapshot per
suite). See ``services/summary_report_service`` for the math.
"""
from __future__ import annotations

import importlib
import io
import re
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, User
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


#: VIZ-607: the formats the report exports, with their renderer and media
#: type. Both render from the same payload and envelope.
_EXPORTS = {
    "pdf": ("app.services.summary_report_pdf", "render_summary_report_pdf", "application/pdf"),
    "xlsx": (
        "app.services.summary_report_xlsx",
        "render_summary_report_xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


async def _export(
    fmt: str, mode: SummaryMode, scope: AnalyticsScope, db: AsyncSession, current_user: User,
) -> StreamingResponse:
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

    module_name, function_name, media_type = _EXPORTS[fmt]
    renderer = getattr(importlib.import_module(module_name), function_name)
    # ReportLab and openpyxl are sync + CPU-bound -- keep the event loop free.
    content = await run_in_threadpool(renderer, {**payload, "meta": meta})

    # VIZ-607: every server export leaves an audit row (reports.py always did;
    # this route did not): who, which project, which scope and format.
    db.add(AccessAuditLog(
        actor_user_id=current_user.id,
        actor_name=current_user.username,
        project_id=project_id,
        action=f"summary_report_export_{fmt}",
        after_value={
            "days": days,
            "mode": mode,
            "release_ids": list(scope.release_ids),
            "suite_names": list(scope.suite_names),
            "size_bytes": len(content),
        },
    ))
    await db.commit()

    slug = re.sub(r"[^a-z0-9_-]+", "_", (payload.get("project_name") or "project").lower()).strip("_")
    filename = f"summary-{slug or 'project'}-{days}d-{mode}.{fmt}"
    logger.info(
        f"summary_report_{fmt}_exported",
        project_id=str(project_id),
        days=days,
        mode=mode,
        size_bytes=len(content),
    )
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
