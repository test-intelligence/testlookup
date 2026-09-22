"""
Flaky-test quarantine API — Tier 1 item 3.

Endpoints exposed under ``/api/v1/quarantine``:

* ``GET /`` — list quarantine requests, filtered by project scope and status.
* ``GET /stats`` — counts per status for the header tiles on the UI.
* ``GET /{request_id}`` — single request detail.
* ``POST /`` — manual proposal (rarely used; detection agent is the primary path).
* ``POST /{request_id}/approve`` — QA Lead approves → test becomes actively quarantined.
* ``POST /{request_id}/reject`` — QA Lead refuses.
* ``POST /{request_id}/release`` — QA Lead ends an active quarantine early.

Write operations require ``QA_LEAD`` or higher per the original plan —
flaky policy ownership is a QA Lead decision.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    FlakyQuarantineRead,
    QuarantineDecisionRequest,
    QuarantineLifecyclePolicyResponse,
    QuarantineLifecyclePolicyUpdate,
    QuarantineManifestEntry,
    QuarantineManifestResponse,
    QuarantineProposeRequest,
    QuarantineStatsResponse,
)
from app.core.analytics_errors import analytics_error_contract
from app.services import flaky_quarantine_service as svc
from app.services.analytics_meta import build_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope

router = APIRouter(prefix="/api/v1/quarantine", tags=["Flaky Quarantine"])
# US-5.1 — CI quarantine manifest lives under the project scope so the
# ``require_project_access`` guard (authorization ratchet) applies directly.
# Separate router because ``router``'s prefix is /api/v1/quarantine and its
# ``GET /{request_id}`` would swallow literal sub-paths as UUIDs.
manifest_router = APIRouter(prefix="/api/v1/projects", tags=["Flaky Quarantine"])
logger = structlog.get_logger("routers.flaky_quarantine")


# ── List / stats ────────────────────────────────────────────────────────────


async def _with_owner_names(db: AsyncSession, rows) -> list[FlakyQuarantineRead]:
    """Serialize rows and attach ``owner_name`` via ONE batched User lookup
    (PMF US-5.4 — ``owner_user_id`` alone is unreadable on the UI), plus the
    linked defect's Jira key / mirrored status via ONE batched Defect lookup
    (PMF US-6.1/US-6.2 — the quarantine table renders the Jira link and the
    "closed in Jira but still failing" conflict badge)."""
    from sqlalchemy import select as _select

    from app.models.postgres import Defect

    payloads = [FlakyQuarantineRead.model_validate(r) for r in rows]
    owner_ids = {p.owner_user_id for p in payloads if p.owner_user_id}
    if owner_ids:
        result = await db.execute(
            _select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        names = {r.id: r.username for r in result.all()}
        for p in payloads:
            if p.owner_user_id:
                p.owner_name = names.get(p.owner_user_id)

    defect_ids = {p.defect_id for p in payloads if p.defect_id}
    if defect_ids:
        result = await db.execute(
            _select(
                Defect.id,
                Defect.jira_ticket_id,
                Defect.jira_ticket_url,
                Defect.jira_status,
                Defect.external_status_conflict,
            ).where(Defect.id.in_(defect_ids))
        )
        links = {r.id: r for r in result.all()}
        for p in payloads:
            link = links.get(p.defect_id) if p.defect_id else None
            if link is not None:
                p.defect_jira_key = link.jira_ticket_id
                p.defect_jira_url = link.jira_ticket_url
                p.defect_external_status = link.jira_status
                p.defect_external_status_conflict = bool(link.external_status_conflict)
    return payloads


@router.get("", response_model=list[FlakyQuarantineRead])
async def list_quarantine_requests(
    project_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = None,
    live_only: bool = False,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List quarantine requests visible to the caller.

    Non-admin callers see only projects they're members of. Admins see
    everything. The ``status_filter`` parameter accepts any value from
    ``FlakyQuarantineStatus``.
    """
    project_ids: Optional[set[uuid.UUID]]
    if project_id is not None:
        await resolve_project_scope(db, current_user, str(project_id))
        project_ids = {project_id}
    else:
        accessible = await get_accessible_project_ids(db, current_user)
        project_ids = accessible  # None = admin (no filter)

    rows = await svc.list_requests(
        db,
        project_ids=project_ids,
        status_filter=status_filter,
        live_only=live_only,
        limit=min(max(1, limit), 500),
    )
    return await _with_owner_names(db, rows)


#: VIZ-201/202: the shared scope. Quarantine counts are current state, so the
#: route has no window (declared in ``meta.ignored_filters``); ``release_id`` /
#: ``suite_name`` count the requests whose test ran in scope.
_STATS_SCOPE = ScopePolicy(default_days=None)


@router.get("/stats", response_model=QuarantineStatsResponse)
@analytics_error_contract
async def get_quarantine_stats(
    scope: AnalyticsScope = Depends(analytics_scope(_STATS_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """Return status counts for the UI header."""
    # The pin, else the caller's memberships, else (admin) every project --
    # exactly the two branches this route resolved by hand before VIZ-202.
    project_ids: Optional[set[uuid.UUID]]
    if scope.project_id is not None:
        project_ids = {scope.project_id}
    else:
        project_ids = (
            set(scope.allowed_project_ids) if scope.allowed_project_ids is not None else None
        )

    counts = await svc.stats_by_status(
        db, project_ids=project_ids,
        release_id=scope.release_arg, suite_name=scope.suite_arg,
    )
    live_states = {
        "DETECTED", "PROPOSED", "APPROVED",
        "QUARANTINED", "RECHECK_SCHEDULED", "RE_QUARANTINED",
    }
    return QuarantineStatsResponse(
        detected=counts.get("DETECTED", 0),
        proposed=counts.get("PROPOSED", 0),
        approved=counts.get("APPROVED", 0),
        quarantined=counts.get("QUARANTINED", 0),
        recheck_scheduled=counts.get("RECHECK_SCHEDULED", 0),
        re_quarantined=counts.get("RE_QUARANTINED", 0),
        released=counts.get("RELEASED", 0),
        rejected=counts.get("REJECTED", 0),
        expired=counts.get("EXPIRED", 0),
        total_live=sum(v for k, v in counts.items() if k in live_states),
        meta=await build_meta(db, scope, measured=True),
    )


@router.get("/{request_id}", response_model=FlakyQuarantineRead)
async def get_quarantine_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    payloads = await _with_owner_names(db, [row])
    return payloads[0]


# ── Writes ──────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=FlakyQuarantineRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    payload: QuarantineProposeRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Manually propose a test for quarantine. QA_LEAD+ only."""
    await resolve_project_scope(db, current_user, str(payload.project_id))
    row = await svc.propose_quarantine(
        project_id=payload.project_id,
        test_fingerprint=payload.test_fingerprint,
        test_name=payload.test_name,
        suite_name=payload.suite_name,
        detection_method=payload.detection_method,
        flip_rate=payload.flip_rate,
        flip_window_size=payload.flip_window_size,
        pass_count=payload.pass_count,
        fail_count=payload.fail_count,
        rationale=payload.rationale,
        quarantine_duration_days=payload.quarantine_duration_days,
        actor=current_user,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Flaky auto-quarantine is disabled. Ask an admin to enable "
                "the 'flaky_auto_quarantine' feature flag."
            ),
        )
    return row


@router.post("/{request_id}/approve", response_model=FlakyQuarantineRead)
async def approve_quarantine(
    request_id: uuid.UUID,
    payload: QuarantineDecisionRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return await svc.approve(
        db,
        request_id,
        current_user,
        notes=payload.notes,
        quarantine_duration_days=payload.quarantine_duration_days,
    )


@router.post("/{request_id}/reject", response_model=FlakyQuarantineRead)
async def reject_quarantine(
    request_id: uuid.UUID,
    payload: QuarantineDecisionRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return await svc.reject(db, request_id, current_user, notes=payload.notes)


# ── CI quarantine manifest (US-5.1) ────────────────────────────────────────


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """Compare an ``If-None-Match`` header against our ETag.

    Tolerates quoted / unquoted / weak (``W/"..."``) forms and the
    multi-value comma-separated syntax. ``*`` matches any representation.
    """
    for candidate in if_none_match.split(","):
        token = candidate.strip()
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:]
        if token.strip('"') == etag:
            return True
    return False


def _manifest_reason(row) -> Optional[str]:
    """Human-readable one-liner for why the test is quarantined."""
    if row.reviewer_notes:
        return row.reviewer_notes
    rationale = row.rationale or {}
    if isinstance(rationale, dict) and rationale.get("method"):
        return f"flaky ({rationale['method']})"
    return f"flaky ({row.detection_method})" if row.detection_method else None


@manifest_router.get(
    "/{project_id}/quarantine/manifest",
    response_model=QuarantineManifestResponse,
)
async def get_quarantine_manifest(
    project_id: uuid.UUID,
    response: Response,
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """CI-consumable manifest of currently-effective quarantines.

    Returns only tests whose quarantine is active right now (QUARANTINED,
    RECHECK_SCHEDULED, RE_QUARANTINED) — released / rejected / expired rows
    and un-reviewed proposals are excluded. Each entry carries the identity
    tuple a CI-side matcher needs: ``fingerprint`` (primary), plus
    ``test_name`` / ``suite_name`` / ``class_name`` for name-based fallback.

    Sends a content-hash ``ETag`` and honours ``If-None-Match`` with 304 so
    CI can poll cheaply. Empty when the ``flaky_auto_quarantine`` feature
    flag is off (quarantine isn't enforced anywhere in that state).
    """
    pairs = await svc.active_quarantine_entries(db, project_id)
    etag = svc.manifest_etag([row for row, _cls in pairs])
    quoted = f'"{etag}"'
    if if_none_match and _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": quoted})

    response.headers["ETag"] = quoted
    entries = [
        QuarantineManifestEntry(
            fingerprint=row.test_fingerprint,
            test_name=row.test_name,
            suite_name=row.suite_name,
            class_name=class_name,
            status=row.status,
            quarantined_at=row.quarantine_start,
            expires_at=row.quarantine_expires_at,
            reason=_manifest_reason(row),
            # Lifecycle surfacing (US-5.4/5.5) — getattr keeps pre-0104 test
            # doubles working.
            stale=bool(getattr(row, "stale", False)),
            ready_to_promote=bool(getattr(row, "ready_to_promote", False)),
        )
        for row, class_name in pairs
    ]
    return QuarantineManifestResponse(
        version=1,
        project_id=project_id,
        generated_at=datetime.now(timezone.utc),
        etag=etag,
        count=len(entries),
        entries=entries,
    )


# ── Quarantine lifecycle policy (US-5.4 / US-5.5 / US-5.6) ─────────────────


@manifest_router.get(
    "/{project_id}/quarantine/policy",
    response_model=QuarantineLifecyclePolicyResponse,
)
async def get_quarantine_lifecycle_policy(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """The project's quarantine lifecycle policy. A missing row means the
    code defaults apply (SLA 14d, no auto-defect, no auto-promote, promote
    after 20 passes, detection floor 20% over 10 runs)."""
    row = await svc.get_lifecycle_policy_row(db, project_id)
    if row is not None:
        return QuarantineLifecyclePolicyResponse(
            project_id=project_id,
            sla_days=row.sla_days,
            auto_create_defect=row.auto_create_defect,
            auto_promote=row.auto_promote,
            promote_after_passes=row.promote_after_passes,
            detection_flip_rate_threshold=row.detection_flip_rate_threshold,
            detection_min_runs=row.detection_min_runs,
            is_default=False,
        )
    policy = await svc.get_lifecycle_policy(db, project_id)
    return QuarantineLifecyclePolicyResponse(
        project_id=project_id,
        sla_days=policy.sla_days,
        auto_create_defect=policy.auto_create_defect,
        auto_promote=policy.auto_promote,
        promote_after_passes=policy.promote_after_passes,
        detection_flip_rate_threshold=policy.detection_flip_rate_threshold,
        detection_min_runs=policy.detection_min_runs,
        is_default=True,
    )


@manifest_router.put(
    "/{project_id}/quarantine/policy",
    response_model=QuarantineLifecyclePolicyResponse,
)
async def update_quarantine_lifecycle_policy(
    project_id: uuid.UUID,
    payload: QuarantineLifecyclePolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Create or replace the project's quarantine lifecycle policy.
    QA_LEAD+ — flaky policy ownership is a QA Lead decision."""
    row = await svc.upsert_lifecycle_policy(
        db,
        project_id,
        sla_days=payload.sla_days,
        auto_create_defect=payload.auto_create_defect,
        auto_promote=payload.auto_promote,
        promote_after_passes=payload.promote_after_passes,
        detection_flip_rate_threshold=payload.detection_flip_rate_threshold,
        detection_min_runs=payload.detection_min_runs,
    )
    await db.commit()
    await db.refresh(row)
    return QuarantineLifecyclePolicyResponse(
        project_id=project_id,
        sla_days=row.sla_days,
        auto_create_defect=row.auto_create_defect,
        auto_promote=row.auto_promote,
        promote_after_passes=row.promote_after_passes,
        detection_flip_rate_threshold=row.detection_flip_rate_threshold,
        detection_min_runs=row.detection_min_runs,
        is_default=False,
    )


@router.post("/{request_id}/release", response_model=FlakyQuarantineRead)
async def release_quarantine(
    request_id: uuid.UUID,
    payload: QuarantineDecisionRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    row = await svc.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine request not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return await svc.release(db, request_id, current_user, notes=payload.notes)
