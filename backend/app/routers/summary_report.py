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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the summary report payload for the active project."""
    if project_id is not None:
        await _enforce_project_access(db, current_user, project_id)

    payload = await svc.build_summary_report(
        db, project_id, days=days, mode=mode,
    )
    return SummaryReportResponse(**payload)


@router.get("/pdf")
async def export_summary_report_pdf(
    project_id: uuid.UUID = Query(..., description="Project UUID (required for PDF export)."),
    days: int = Query(7, ge=1, le=365),
    mode: SummaryMode = Query("window"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the summary report as a downloadable PDF."""
    await _enforce_project_access(db, current_user, project_id)

    payload = await svc.build_summary_report(
        db, project_id, days=days, mode=mode,
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
