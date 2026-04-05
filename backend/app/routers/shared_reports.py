"""Public shared report endpoints — token-based access, no JWT required (ENT-03)."""
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog

logger = logging.getLogger("routers.shared_reports")

router = APIRouter(prefix="/api/v1/shared", tags=["Shared Reports"])


@router.get("/reports/{token}", response_class=HTMLResponse)
async def view_shared_report(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Render the shared HTML report view (no auth required — token-based)."""
    from app.services.share_link_service import validate_share_link
    from app.services.report_composition_service import compose_report
    from app.services.report_html_renderer import render_report_html

    try:
        link = await validate_share_link(db, token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    try:
        report = await compose_report(db, link.run_id, layout=link.report_layout)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    html_content = render_report_html(
        report,
        shared_by=link.created_by_name or "Unknown",
        expires_at=link.expires_at.isoformat() if link.expires_at else "",
    )

    # Audit the access (anonymous — no actor)
    db.add(AccessAuditLog(
        actor_name="anonymous_share",
        project_id=link.project_id,
        action="report_share_accessed",
        after_value={"run_id": str(link.run_id), "token_hint": token[:8] + "..."},
    ))
    await db.commit()

    return HTMLResponse(content=html_content)


@router.get("/reports/{token}/pdf")
async def download_shared_report_pdf(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Download the shared PDF report (no auth required — token-based)."""
    from app.services.share_link_service import validate_share_link
    from app.services.report_composition_service import compose_report
    from app.services.report_pdf_renderer import render_report_pdf

    try:
        link = await validate_share_link(db, token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    try:
        report = await compose_report(db, link.run_id, layout=link.report_layout)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    pdf_bytes = render_report_pdf(report)

    # Audit
    db.add(AccessAuditLog(
        actor_name="anonymous_share",
        project_id=link.project_id,
        action="report_share_accessed",
        after_value={"run_id": str(link.run_id), "format": "pdf", "token_hint": token[:8] + "..."},
    ))
    await db.commit()

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="shared-report-{str(link.run_id)[:8]}.pdf"'},
    )
