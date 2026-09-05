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
import uuid
from typing import Literal, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    resolve_release_query_scope,
    get_current_active_user,
)
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import SummaryReportResponse
from app.services import summary_report_service as svc

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/reports/summary", tags=["Summary Report"])


SummaryMode = Literal["window", "latest"]


async def _enforce_project_access(
    db: AsyncSession, user: User, project_id: uuid.UUID
) -> None:
    accessible = await get_accessible_project_ids(db, user)
    if accessible is None:
        return
    if project_id not in accessible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("", response_model=SummaryReportResponse)
async def get_summary_report(
    project_id: Optional[uuid.UUID] = Query(
        None, description="Project UUID. Required; omit returns an empty envelope."
    ),
    days: int = Query(7, ge=1, le=365, description="Time-window size in days."),
    mode: SummaryMode = Query(
        "window",
        description="``window`` aggregates every run in the window; ``latest`` takes the most recent run per suite.",
    ),
    # APPENDED, not inserted. Adding a parameter mid-signature reorders it for
    # every positional caller — three existing router tests then passed their
    # project UUID into `release_id` and got a 422. FastAPI resolves these by
    # name at runtime, so position only matters to direct callers, which is
    # exactly what the tests are.
    release_id: Optional[str] = Query(
        None,
        description=(
            "Scope every number in the report to one release. Omit for all "
            "releases — the SQL is then byte-identical to before this existed."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the summary report payload for the active project."""
    if project_id is not None:
        await _enforce_project_access(db, current_user, project_id)

    # The release axis (S4a's shape). Resolved through the shared helper so the
    # id is access-checked and the Unattributed sentinel is handled the same way
    # every other release-scoped read handles it.
    release_id = await resolve_release_query_scope(db, release_id, current_user)

    payload = await svc.build_summary_report(
        db, project_id, days=days, mode=mode, release_id=release_id,
    )
    return SummaryReportResponse(**payload)


@router.get("/pdf")
async def export_summary_report_pdf(
    project_id: uuid.UUID = Query(..., description="Project UUID (required for PDF export)."),
    days: int = Query(7, ge=1, le=365),
    mode: SummaryMode = Query("window"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    # The export has to carry the same scope as the screen. A release-scoped
    # report whose PDF is project-wide is the "one response, two scopes" defect
    # in its worst form: the discrepancy leaves the product entirely, in a file
    # somebody attaches to a sign-off.
    release_id: Optional[str] = Query(
        None, description="Scope the exported report to one release."
    ),
):
    """Return the summary report as a downloadable PDF."""
    await _enforce_project_access(db, current_user, project_id)
    release_id = await resolve_release_query_scope(db, release_id, current_user)

    payload = await svc.build_summary_report(
        db, project_id, days=days, mode=mode, release_id=release_id,
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
