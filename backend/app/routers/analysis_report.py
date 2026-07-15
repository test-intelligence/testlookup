"""
On-demand HTML analysis report download (PMF US-7.5).

``GET /api/v1/projects/{project_id}/reports/analysis?window=1d|7d`` returns
the same self-contained HTML document the digest dispatcher attaches to
daily/weekly digest emails — so the report is testable without SMTP and
downloadable from the Summary Report page.

Project-scoped: ``require_project_access`` applies (authorization ratchet)
plus a QA_ENGINEER+ role floor. The response is served with
``Content-Disposition: attachment`` so browsers download rather than render
a full standalone page inside the SPA origin.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.services.analysis_report_service import (
    build_analysis_report_html,
    report_attachment_filename,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["Analysis Report"])


@router.get("/{project_id}/reports/analysis")
async def download_analysis_report(
    project_id: uuid.UUID,
    window: Literal["1d", "7d"] = Query(
        "7d", description="Report window: 1d (daily) or 7d (weekly)."
    ),
    db: AsyncSession = Depends(get_db),
    _role: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
) -> Response:
    """Build and return the self-contained HTML analysis report."""
    now = datetime.now(timezone.utc)
    html = await build_analysis_report_html(db, project_id, window, now=now)

    # Reuse the digest attachment's slug lookup indirectly: the builder
    # already resolved the project; a cheap second name read keeps this
    # endpoint free of extra plumbing.
    from sqlalchemy import select

    from app.models.postgres import Project

    slug = (
        await db.execute(select(Project.slug).where(Project.id == project_id))
    ).scalar_one_or_none()
    filename = report_attachment_filename(slug, now, window)

    logger.info(
        "analysis_report_downloaded",
        project_id=str(project_id),
        window=window,
        size_bytes=len(html.encode("utf-8")),
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
