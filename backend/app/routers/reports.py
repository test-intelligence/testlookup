"""Reports router — PDF export, evidence bundle, share links, email trends."""
import io
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_link_access,
    require_run_access,
)
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, ReportShareLink, TestRun, User
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

    # ReportLab is synchronous and CPU-bound — run it off the event loop so
    # the request worker stays free to accept other connections.
    pdf_bytes = await run_in_threadpool(render_report_pdf, report)

    # Audit log — stage + single commit before streaming the response
    from sqlalchemy import select
    project_id = (await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )).scalar_one_or_none()
    _stage_audit(db, "report_export_pdf", current_user, project_id, {
        "run_id": str(run_id), "layout": layout,
    })
    await db.commit()

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

    # Audit log — stage + single commit before streaming the response
    from sqlalchemy import select
    project_id = (await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )).scalar_one_or_none()
    _stage_audit(db, "report_export_bundle", current_user, project_id, {
        "run_id": str(run_id),
    })
    await db.commit()

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
    # token is only populated on creation responses — it is hashed at rest,
    # so list endpoints cannot return it. Callers must copy the token the
    # one time it is shown.
    token: Optional[str] = None
    share_url: Optional[str] = None
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

    created = await create_share_link(
        db=db,
        run_id=run_id,
        project_id=project_id,
        created_by=current_user,
        layout=body.layout,
        expiry_days=body.expiry_days,
    )
    link = created.link
    raw_token = created.raw_token

    _stage_audit(db, "report_share_created", current_user, project_id, {
        "run_id": str(run_id), "layout": body.layout, "expiry_days": body.expiry_days,
    })
    # Single commit covers both the new link and its audit event.
    await db.commit()
    await db.refresh(link)

    from app.core.config import settings
    # A share link exists to be sent to someone else, so it must carry the
    # hostname *they* can open. This used to read SAML_BASE_URL ("reuse the
    # base URL setting") — an SSO setting that defaults to localhost:8000 and
    # has no reason to be set on a deployment that doesn't use SAML. Its
    # localhost warning only fires when SSO_ENABLED, so nothing flagged it.
    #
    # Measured live on a correctly-configured deployment: the issued URL was
    # http://localhost:8000/... and did not connect, while the same token on
    # the real ingress returned the report with HTTP 200. Only the host was
    # wrong — the link was otherwise valid, and both the CLI and the UI's
    # copy-to-clipboard handed it out.
    #
    # ``public_base_url`` is the setting documented for exactly this
    # (PUBLIC_BASE_URL → first CORS origin → localhost), already used by the
    # GitHub checks, PR-comment and GitLab integrations for the same purpose.
    base_url = settings.public_base_url
    share_url = f"{base_url}/api/v1/shared/reports/{raw_token}"

    return ShareLinkResponse(
        id=str(link.id),
        token=raw_token,
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

    links = await list_share_links(db, run_id)
    # Raw tokens are not stored, so list responses show metadata only.
    return [
        ShareLinkResponse(
            id=str(link.id),
            token=None,
            share_url=None,
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
    link: ReportShareLink = Depends(require_link_access()),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Revoke a share link. Creator, project members, and admins can revoke."""
    link.is_revoked = True
    _stage_audit(db, "report_share_revoked", current_user, link.project_id, {
        "link_id": str(link.id),
        "run_id": str(link.run_id),
    })
    await db.commit()
    return None


# ── Audit helper ─────────────────────────────────────────────────────────────


def _stage_audit(
    db: AsyncSession,
    action: str,
    actor: User | None,
    project_id: uuid.UUID | None,
    after_value: dict | None = None,
) -> None:
    """Stage a report export/share audit event on the current session.

    The caller owns the transaction and must call ``await db.commit()`` at
    the end of the request. Staging-only keeps us at one commit per request.
    """
    db.add(AccessAuditLog(
        actor_user_id=actor.id if actor else None,
        actor_name=actor.username if actor else "anonymous_share",
        project_id=project_id,
        action=action,
        after_value=after_value,
    ))
