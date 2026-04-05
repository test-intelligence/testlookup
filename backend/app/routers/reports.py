"""Reports router — PDF export, evidence bundle, share links, email trends."""
import io
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_run_access
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, TestRun, User
from app.services import report_service

logger = logging.getLogger("routers.reports")

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])


# ── Legacy email trends ─────────────────────────────────────────────────────


class EmailTrendsRequest(BaseModel):
    project_id: str
    days: int = 30
    recipient_email: EmailStr
    chart_ids: list[str] = []


@router.post("/email-trends")
async def email_trends_report(
    body: EmailTrendsRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Generate and email a trends report for the specified project and period."""
    return await report_service.email_trends_report(db, body)


# ── ENT-03: PDF Export ──────────────────────────────────────────────────────


@router.get("/runs/{run_id}/pdf")
async def export_run_report_pdf(
    run_id: uuid.UUID,
    layout: str = Query(default="executive", pattern="^(executive|engineering)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_run_access()),
):
    """Generate and download a PDF intelligence report for the given run."""
    from app.services.report_composition_service import compose_report
    from app.services.report_pdf_renderer import render_report_pdf

    try:
        report = await compose_report(db, run_id, layout=layout)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    pdf_bytes = render_report_pdf(report)

    # Audit log
    run_result = await db.execute(
        __import__("sqlalchemy").select(TestRun.project_id).where(TestRun.id == run_id)
    )
    project_id = run_result.scalar_one_or_none()
    await _log_audit(db, "report_export_pdf", current_user, project_id, {
        "run_id": str(run_id), "layout": layout,
    })

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report-{str(run_id)[:8]}-{layout}.pdf"'},
    )


# ── ENT-03: Evidence Bundle ─────────────────────────────────────────────────


@router.get("/runs/{run_id}/evidence-bundle")
async def export_evidence_bundle(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_run_access()),
):
    """Generate and download a ZIP evidence bundle for the given run."""
    from app.services.evidence_bundle_service import build_evidence_bundle

    try:
        zip_bytes = await build_evidence_bundle(db, run_id)
    except Exception as exc:
        logger.error("Evidence bundle failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Bundle generation failed")

    # Audit log
    from sqlalchemy import select
    run_result = await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    project_id = run_result.scalar_one_or_none()
    await _log_audit(db, "report_export_bundle", current_user, project_id, {
        "run_id": str(run_id),
    })

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="evidence-{str(run_id)[:8]}.zip"'},
    )


# ── ENT-03: Share Links ─────────────────────────────────────────────────────


class CreateShareLinkRequest(BaseModel):
    layout: str = Field(default="executive", pattern="^(executive|engineering)$")
    expiry_days: int = Field(default=7, ge=1, le=30)


class ShareLinkResponse(BaseModel):
    id: str
    token: str
    share_url: str
    report_layout: str
    expires_at: str
    created_by_name: Optional[str] = None
    access_count: int = 0
    is_revoked: bool = False
    created_at: str


@router.post("/runs/{run_id}/share", response_model=ShareLinkResponse, status_code=201)
async def create_share_link_endpoint(
    run_id: uuid.UUID,
    body: CreateShareLinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_run_access()),
):
    """Create a time-limited share link for the run report."""
    from sqlalchemy import select
    from app.services.share_link_service import create_share_link

    # Resolve project_id
    run_result = await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    project_id = run_result.scalar_one_or_none()
    if not project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    link = await create_share_link(
        db=db,
        run_id=run_id,
        project_id=project_id,
        created_by=current_user,
        layout=body.layout,
        expiry_days=body.expiry_days,
    )
    await db.commit()
    await db.refresh(link)

    await _log_audit(db, "report_share_created", current_user, project_id, {
        "run_id": str(run_id), "layout": body.layout, "expiry_days": body.expiry_days,
    })

    from app.core.config import settings
    base_url = settings.SAML_BASE_URL  # reuse the base URL setting
    share_url = f"{base_url}/api/v1/shared/reports/{link.token}"

    return ShareLinkResponse(
        id=str(link.id),
        token=link.token,
        share_url=share_url,
        report_layout=link.report_layout,
        expires_at=link.expires_at.isoformat(),
        created_by_name=link.created_by_name,
        access_count=link.access_count or 0,
        is_revoked=link.is_revoked,
        created_at=link.created_at.isoformat(),
    )


@router.get("/runs/{run_id}/share-links", response_model=list[ShareLinkResponse])
async def list_share_links_endpoint(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_run_access()),
):
    """List all share links for a run."""
    from app.services.share_link_service import list_share_links
    from app.core.config import settings

    links = await list_share_links(db, run_id)
    base_url = settings.SAML_BASE_URL

    return [
        ShareLinkResponse(
            id=str(link.id),
            token=link.token,
            share_url=f"{base_url}/api/v1/shared/reports/{link.token}",
            report_layout=link.report_layout,
            expires_at=link.expires_at.isoformat(),
            created_by_name=link.created_by_name,
            access_count=link.access_count or 0,
            is_revoked=link.is_revoked,
            created_at=link.created_at.isoformat(),
        )
        for link in links
    ]


@router.delete("/share-links/{link_id}", status_code=204)
async def revoke_share_link_endpoint(
    link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Revoke a share link."""
    from app.services.share_link_service import revoke_share_link

    try:
        await revoke_share_link(db, link_id)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return None


# ── Audit helper ─────────────────────────────────────────────────────────────


async def _log_audit(
    db: AsyncSession,
    action: str,
    actor: User | None,
    project_id: uuid.UUID | None,
    after_value: dict | None = None,
) -> None:
    """Persist a report export/share audit event."""
    db.add(AccessAuditLog(
        actor_user_id=actor.id if actor else None,
        actor_name=actor.username if actor else "anonymous_share",
        project_id=project_id,
        action=action,
        after_value=after_value,
    ))
    await db.commit()
