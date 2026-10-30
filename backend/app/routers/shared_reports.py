"""Public shared report endpoints — token-based access, no JWT required (ENT-03)."""
import html
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog

logger = logging.getLogger("routers.shared_reports")

router = APIRouter(prefix="/api/v1/shared", tags=["Shared Reports"])


def _wants_html(request: Optional[Request]) -> bool:
    """A browser opening the link asks for ``text/html``; the SPA and API
    clients ask for JSON (or anything) and keep the JSON 409 body."""
    if request is None:
        return False
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept and "application/json" not in accept


def _refusal_page(message: str) -> str:
    """The 409 page an anonymous viewer reads (W23c AGT-10, §3.2). The message
    is escaped although it is a constant today; no script, no style."""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="robots" content="noindex">'
        "<title>Report awaiting review</title></head>"
        "<body><main><h1>Report awaiting review</h1>"
        f"<p>{html.escape(message, quote=True)}</p>"
        "</main></body></html>\n"
    )


@router.get("/reports/{token}", response_class=HTMLResponse)
async def view_shared_report(
    token: str,
    db: AsyncSession = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI; None in direct calls
):
    """Render the shared HTML report view (no auth required — token-based).

    A refused (unreviewed, enforced) report answers 409: an HTML page for a
    browser, the JSON ``refusal_detail`` for every other caller."""
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

    # E8.4: a share link is the most exposed channel -- anyone holding the token
    # reads it, with no login. An unreviewed AI report is refused here once the
    # gate is enforced, unless the project allows drafts (watermarked, audited).
    from app.services.report_distribution_policy import (
        decide_run_distribution,
        record_distribution,
        refusal_detail,
    )

    decision = await decide_run_distribution(
        db, run_id=link.run_id, project_id=link.project_id, channel="share_link_html",
    )
    await record_distribution(
        db, decision, channel="share_link_html", run_id=link.run_id, project_id=link.project_id,
    )
    if not decision.allowed:
        await db.commit()
        detail = refusal_detail(decision)
        if _wants_html(request):
            return HTMLResponse(
                content=_refusal_page(str(detail["message"])),
                status_code=status.HTTP_409_CONFLICT,
            )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    report.draft_watermark = decision.watermark or ""

    html_content = await run_in_threadpool(
        render_report_html,
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

    # E8.4: a share link is the most exposed channel -- anyone holding the token
    # reads it, with no login. An unreviewed AI report is refused here once the
    # gate is enforced, unless the project allows drafts (watermarked, audited).
    from app.services.report_distribution_policy import (
        decide_run_distribution,
        record_distribution,
        refusal_detail,
    )

    decision = await decide_run_distribution(
        db, run_id=link.run_id, project_id=link.project_id, channel="share_link_pdf",
    )
    await record_distribution(
        db, decision, channel="share_link_pdf", run_id=link.run_id, project_id=link.project_id,
    )
    if not decision.allowed:
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(decision))
    report.draft_watermark = decision.watermark or ""

    pdf_bytes = await run_in_threadpool(render_report_pdf, report)

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
