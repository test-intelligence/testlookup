"""Reports router — PDF export, evidence bundle, share links, the Trends email and PDF."""
import asyncio
import io
import logging
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.deps import (
    get_current_active_user,
    require_link_access,
    require_role,
    require_run_access,
    resolve_project_scope,
)
from app.db.postgres import get_db, release_read_connection
from app.models.postgres import AccessAuditLog, ProjectMember, ReportShareLink, TestRun, User, UserRole
from app.services import report_service, trends_report_pdf, trends_report_service
from app.services.activity.service import ActorRef, record as record_activity
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope, resolve_analytics_scope
from app.services.notification import egress, email_service

logger = logging.getLogger("routers.reports")

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])


# ── The Trends report: email (QA lead) and PDF (any member) — W08 TRND-01 ──


#: At most this many distinct addresses per email; at most 5 typed ones.
MAX_TRENDS_RECIPIENTS = 10
NOT_A_MEMBER = "Every recipient must be a member of this project."
NO_RECIPIENT = "Choose at least one recipient."
TOO_MANY_RECIPIENTS = "Choose at most 10 recipients."
EMAIL_NOT_CONFIGURED = "Email is not configured — Settings › Notifications."
RELAY_TIMEOUT = "The mail relay did not answer within 20 s; the report may still arrive."


class EmailTrendsRequest(BaseModel):
    project_id: str
    days: int = 30
    #: Scope the emailed report to the releases of the on-screen chart it
    #: exports (one id, as before, or several: VIZ-308). Omitted = every
    #: release, which is what it always did.
    release_id: str | list[str] | None = None
    #: VIZ-308: and to its suites (OR). Omitted = every suite.
    suite_name: list[str] | None = None
    #: The legacy single typed address (integration callers); optional since W08.
    recipient_email: Optional[EmailStr] = None
    #: W08: typed addresses (the route is QA_LEAD+), egress-gated offline.
    recipient_emails: list[EmailStr] = Field(default_factory=list, max_length=5)
    #: W08: project members by user id; each must be an active, human member of
    #: THIS project (an unknown id answers exactly like a non-member's).
    recipient_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    chart_ids: list[str] = []


class EmailTrendsResponse(BaseModel):
    status: str
    #: Distinct addresses the relay accepted the message for.
    sent_to: int
    #: Legacy: the first address (the old response's key).
    recipient: Optional[str] = None
    window_days: int


class EmailAvailability(BaseModel):
    available: bool
    #: ``not_configured`` | ``egress_blocked`` | ``None``.
    code: Optional[str] = None
    #: The text the dialog shows.
    reason: Optional[str] = None


#: The emailed report's scope: a project, a window of at most a year, and the
#: same release and suite filters as the analytics routes (VIZ-308).
_EMAIL_SCOPE = ScopePolicy(default_days=30, max_days=365, project_required=True)
#: The Trends PDF: the page's window (``/metrics/trends``: 1-90 days, default 14).
_TRENDS_PDF_SCOPE = ScopePolicy(default_days=14, max_days=90, project_required=True)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


async def _recipient_addresses(db: AsyncSession, project_id: uuid.UUID, body: EmailTrendsRequest) -> list[str]:
    """Member emails, then typed addresses, then the legacy one: lower-cased,
    de-duplicated, in that order. The member ids are resolved in ONE statement
    on the request session; any id it does not return (unknown, another
    project's, inactive, a service or synthetic account) is the same 422."""
    wanted = list(dict.fromkeys(body.recipient_user_ids))
    member_emails: list[str] = []
    if wanted:
        rows = (await db.execute(
            select(User.id, User.email)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                ProjectMember.project_id == project_id,
                User.id.in_(wanted),
                User.is_active.is_(True),
                User.is_service_account.isnot(True),
                User.is_synthetic.isnot(True),
            )
        )).all()
        found = {row.id: row.email for row in rows}
        if any(uid not in found for uid in wanted):
            raise _unprocessable(NOT_A_MEMBER)
        member_emails = [found[uid] for uid in wanted]
    typed = list(body.recipient_emails) + ([body.recipient_email] if body.recipient_email else [])
    addresses = list(dict.fromkeys(str(a).strip().lower() for a in [*member_emails, *typed] if str(a).strip()))
    if not addresses:
        raise _unprocessable(NO_RECIPIENT)
    if len(addresses) > MAX_TRENDS_RECIPIENTS:
        raise _unprocessable(TOO_MANY_RECIPIENTS)
    return addresses


async def _relay_status() -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """``(cfg, code, reason)`` of the relay saved in Settings › Notifications
    (else the environment's): the config and no code when email can go, else
    the code and the reason. Opens its own session (``get_smtp_config``):
    callers release theirs first."""
    cfg = await email_service.get_smtp_config()
    host = email_service.smtp_host(cfg)
    if not cfg.get("enabled") or not host:
        return None, "not_configured", EMAIL_NOT_CONFIGURED
    try:
        await egress.assert_delivery_allowed_async("SMTP", host, deployment_wide=True)
    except egress.OfflineEgressBlocked as exc:
        return None, "egress_blocked", str(exc)
    return cfg, None, None


async def _record_export(
    db: AsyncSession, user: User, project_id: Optional[uuid.UUID], entity_id: Any, label: str, context: dict,
) -> None:
    """W21 ACT-01 slice (c): ``report.exported`` beside the export's audit row,
    on the same single commit (outcome mode). A run with no project (gone
    mid-request) records nothing."""
    if project_id is None:
        return
    await record_activity(
        db, project_id=project_id, event_type="report.exported", actor=ActorRef.from_user(user),
        entity_id=str(entity_id), entity_type="export", entity_label=label, context=context,
    )


@router.post("/email-trends", response_model=EmailTrendsResponse)
@analytics_error_contract
async def email_trends_report(
    body: EmailTrendsRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    # QA_LEAD: the report leaves the system, so no later access control can
    # contain it. Membership alone let a VIEWER mail project data anywhere, as
    # often as they liked (E2E 2026-10-10). No project-bound key opts in.
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Email the Trends report of one project (the page's window, releases and
    suites) to project members and up to 5 typed addresses, through the relay
    saved in Settings › Notifications. One message for every recipient."""
    # The caller supplies BOTH the project and the destination addresses: the
    # project is verified before anything is read or built, so another tenant's
    # trends can never be mailed (TestEmailTrendsIsScoped orders this call).
    pinned, _ = await resolve_project_scope(db, current_user, body.project_id)
    # VIZ-308: the releases and suites are authorised and parsed exactly as the
    # analytics routes do it, and the email carries the context block those
    # filters produce (``meta``), so a mailed report says what it covers.
    scope = await resolve_analytics_scope(
        db, current_user, policy=_EMAIL_SCOPE,
        project_id=str(pinned) if pinned is not None else body.project_id,
        release_id=body.release_id, suite_name=body.suite_name, days=body.days,
    )
    if scope.project_id is None:  # pragma: no cover - the policy requires a project
        raise _unprocessable("Select a single project.")
    addresses = await _recipient_addresses(db, scope.project_id, body)
    payload = await trends_report_service.build_trends_report(db, scope)
    payload["chart_ids"] = list(body.chart_ids)
    html = report_service.build_trends_email_html(payload)
    subject = f"Trends report — {payload['project_name']} — last {scope.window_days} days"

    # Nothing below needs the database until the ledger row, and the relay may
    # take up to 20 s: hand the connection back first (get_smtp_config opens a
    # session of its own on the same pool).
    await release_read_connection(db)
    cfg, _code, reason = await _relay_status()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=reason)
    try:
        await egress.assert_recipients_allowed_async(email_service.smtp_host(cfg), addresses)
    except egress.OfflineEgressBlocked as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    try:
        await report_service.send_trends_email(cfg, addresses, subject, html)
    except asyncio.TimeoutError:
        # A timeout is not a failure: the relay may still deliver. No ledger
        # row, no retry.
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=RELAY_TIMEOUT) from None
    except egress.OfflineEgressBlocked as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 -- the relay's text can carry its banner: name the type only
        logger.warning("trends report email refused by the relay: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The mail relay refused the report ({type(exc).__name__}).",
        ) from None

    await record_activity(
        db,
        project_id=scope.project_id,
        event_type="report.emailed",
        actor=ActorRef.from_user(current_user),
        entity_id=str(scope.project_id),
        entity_type="export",
        entity_label=f"Trends, last {scope.window_days} days",
        # The count, never an address: addresses are personal data.
        context={"count": len(addresses), "window_days": scope.window_days},
    )
    await db.commit()
    logger.info("trends report emailed: recipients=%d days=%d", len(addresses), scope.window_days)
    return EmailTrendsResponse(
        status="sent", sent_to=len(addresses), recipient=addresses[0], window_days=scope.window_days,
    )


@router.get("/email-trends/availability", response_model=EmailAvailability)
async def email_trends_availability(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Can the Trends report be emailed right now? The relay saved in Settings ›
    Notifications must be enabled, have a host, and pass the offline egress
    gate (the recipients are judged when sending). Reads nothing of a project."""
    await release_read_connection(db)
    cfg, code, reason = await _relay_status()
    return EmailAvailability(available=cfg is not None, code=code, reason=reason)


def _slug(name: Optional[str]) -> str:
    """The Summary export's file-name rule (``report_export_service``)."""
    slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "project").lower()).strip("_")
    return slug or "project"


@router.get("/trends/pdf")
@analytics_error_contract
async def export_trends_report_pdf(
    scope: AnalyticsScope = Depends(analytics_scope(_TRENDS_PDF_SCOPE)),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The Trends report as a PDF (any member): the page's KPIs, verdict, daily
    series and suite table for its window, releases and suites. Audited."""
    payload = await trends_report_service.build_trends_report(db, scope)
    # ReportLab is synchronous and CPU-bound: keep the event loop free.
    content = await run_in_threadpool(trends_report_pdf.render_trends_report_pdf, payload)
    await _record_export(db, current_user, scope.project_id, scope.project_id,
                         f"Trends PDF, last {scope.window_days} days", {"format": "pdf", "kind": "trends"})
    _stage_audit(db, "report_trends_export_pdf", current_user, scope.project_id, {
        "days": scope.window_days,
        "release_ids": list(scope.release_ids),
        "suite_names": list(scope.suite_names),
        "size_bytes": len(content),
    })
    await db.commit()
    filename = f"trends-{_slug(payload.get('project_name'))}-{scope.window_days}d.pdf"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── ENT-03: PDF Export ──────────────────────────────────────────────────────


@router.get("/runs/{run_id}/pdf")
async def export_run_report_pdf(
    run_id: uuid.UUID,
    layout: str = Query(default="executive", pattern="^(executive|engineering)$"),
    include_unreviewed: bool = Query(
        default=False,
        description=(
            "Export an AI report still awaiting human review, watermarked DRAFT. "
            "QA_LEAD or ADMIN only; audited."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_run_access()),
):
    """Generate and download a PDF intelligence report for the given run.

    E8.4: an AI report nobody has accepted is refused (409) once the review gate
    is enforced, unless the project allows drafts or a QA lead passes
    ``include_unreviewed``; either way it is watermarked and audited.
    """
    from app.services.report_composition_service import compose_report
    from app.services.report_pdf_renderer import render_report_pdf

    try:
        report = await compose_report(db, run_id, layout=layout)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    from sqlalchemy import select as _select

    from app.services.report_distribution_policy import (
        decide_run_distribution,
        record_distribution,
        refusal_detail,
    )

    role_value = str(getattr(current_user.role, "value", current_user.role))
    if include_unreviewed and role_value not in ("QA_LEAD", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="include_unreviewed requires QA_LEAD or ADMIN",
        )
    report_project_id = (
        await db.execute(_select(TestRun.project_id).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    decision = await decide_run_distribution(
        db, run_id=run_id, project_id=report_project_id, channel="report_pdf",
        include_unreviewed=include_unreviewed,
    )
    await record_distribution(
        db, decision, channel="report_pdf", run_id=run_id,
        project_id=report_project_id, actor=current_user,
    )
    if not decision.allowed:
        await db.commit()  # the refusal's audit row is the point; keep it
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(decision))
    report.draft_watermark = decision.watermark or ""

    # ReportLab is synchronous and CPU-bound — run it off the event loop so
    # the request worker stays free to accept other connections.
    pdf_bytes = await run_in_threadpool(render_report_pdf, report)

    # Audit log — stage + single commit before streaming the response
    from sqlalchemy import select
    project_id = (await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )).scalar_one_or_none()
    await _record_export(db, current_user, project_id, run_id, f"Run {str(run_id)[:8]} PDF ({layout})",
                         {"format": "pdf", "kind": "run", "layout": layout})
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

    project_id = (await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )).scalar_one_or_none()
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found")

    try:
        zip_bytes = await build_evidence_bundle(db, project_id, run_id)
    except Exception as exc:
        logger.error("Evidence bundle failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Bundle generation failed")

    # Audit log — stage + single commit before streaming the response
    await _record_export(db, current_user, project_id, run_id, f"Run {str(run_id)[:8]} evidence bundle",
                         {"format": "zip", "kind": "evidence_bundle"})
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
    # UAT-004. A share link could be minted for a run with no intelligence
    # snapshot: 201 with a valid-looking URL that 404s for the recipient, with
    # nothing said at creation time.
    #
    # Deliberately a warning and NOT a 409. The link **self-heals** — once deep
    # investigation runs, the same token resolves — so refusing to create it
    # would break the legitimate "mint the link, kick off the investigation,
    # send it when it lands" order. Both fields are additive, so existing
    # clients are unaffected.
    #
    # ``None`` means NOT EVALUATED rather than "ready": the list endpoint does
    # not run the check, and reporting True there would be inventing a value
    # nobody measured.
    snapshot_ready: Optional[bool] = None
    warning: Optional[str] = None


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

    # Exactly the condition ``report_composition_service.compose_report``
    # applies when the recipient opens the link — a snapshot row AND a
    # non-empty payload. Checking anything narrower here would warn on links
    # that work, or stay silent on links that do not.
    from app.models.postgres import RunIntelligenceSnapshot

    snapshot_row = (
        await db.execute(
            select(RunIntelligenceSnapshot.payload).where(
                RunIntelligenceSnapshot.run_id == run_id
            )
        )
    ).scalar_one_or_none()
    snapshot_ready = bool(snapshot_row)

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

    # W21 ACT-01 slice (c): never the token, only who shared which run and for how long.
    await record_activity(
        db, project_id=project_id, event_type="report.shared", actor=ActorRef.from_user(current_user),
        entity_id=str(run_id), entity_type="export", entity_label=f"Run {str(run_id)[:8]} ({link.report_layout})",
        context={"expires_at": link.expires_at.isoformat(), "layout": link.report_layout},
    )
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
        snapshot_ready=snapshot_ready,
        warning=(
            None if snapshot_ready else
            "This run has no intelligence snapshot yet, so anyone opening the "
            "link right now gets a 404. Trigger deep investigation for the run "
            "— the same link starts working once it completes."
        ),
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
