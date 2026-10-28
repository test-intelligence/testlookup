"""Release management endpoints — CRUD for releases, phases, and test-run links.

Authorization policy:
  - Read endpoints (GET): any authenticated user
  - Mutation endpoints (POST, PUT, DELETE): QA_LEAD or ADMIN
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_release_access,
    require_role,
    require_run_access,
    resolve_project_scope,
)
from app.core.metrics import release_phases_created_total
from app.core.release_planning import (
    MAX_PRD_BYTES,
    PHASE_DRAFT_SOURCES,
    PORTFOLIO_WEEKS_DEFAULT,
    PORTFOLIO_WEEKS_MAX,
    PORTFOLIO_WEEKS_MIN,
    PRD_TOO_LARGE,
    RELEASE_NOT_FOUND,
    SOURCE_IS_TARGET,
)
from app.db.postgres import get_db, release_read_connection
from app.models.postgres import AccessAuditLog, Project, Release, TestRun, User, UserRole
from app.models.schemas import (
    PhaseDraftResponse,
    ReleasePhasesBulkRequest,
    ReleasePhasesCloneRequest,
    ReleasePhasesCreatedResponse,
    ReleasePortfolioTrend,
)
from app.services.activity.service import ActorRef, record as record_activity
from app.models.serializers import serialize_model
from app.services import (
    github_release_sync,
    gitlab_release_sync,
    release_lifecycle_service,
    jira_release_sync,
    release_gate_reasons,
    release_gate_service,
    release_outcome_service,
    release_owner_service,
    release_phase_gate_service,
    release_portfolio_service,
    release_schedule_export,
    release_service,
    retention_hold_service,
)
from app.services.ai_config_resolver import get_effective_ai_config
from app.services.release_phase_generator import (
    DraftContext,
    PrdInputError,
    draft_phases,
    extract_prd_text,
)

router = APIRouter(prefix="/api/v1/releases", tags=["Releases"])


def _comparable(value: Any) -> Any:
    """A value as the change test compares it: ids as strings, a naive datetime as UTC, else as is.

    The row holds an aware UTC ``planned_date``; the edit dialog sends a bare date, which parses
    naive. Compared raw they always differed, so every edit logged "changed: planned_date".
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class PhaseIn(BaseModel):
    name: str
    phase_type: str = "qa_testing"
    status: str = "pending"
    description: Optional[str] = None
    order_index: int = 0
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    exit_criteria: Optional[dict] = None
    notes: Optional[str] = None


class PhaseUpdate(BaseModel):
    name: Optional[str] = None
    phase_type: Optional[str] = None
    status: Optional[str] = None
    #: Completing a phase whose gate does not say GO, on purpose.
    #:
    #: Enforcement without an override is an invariant that gets routed around:
    #: a release manager who cannot ship marks the phase "skipped" instead, and
    #: the gate has achieved nothing except a worse audit trail. The override
    #: exists so the honest action is also the easy one — and it is RECORDED.
    gate_override_reason: Optional[str] = Field(None, min_length=3, max_length=1000)
    #: Why a phase is being skipped. A skip is a decision not to test something;
    #: it stays allowed, but it stops being invisible.
    skip_reason: Optional[str] = Field(None, min_length=3, max_length=1000)
    description: Optional[str] = None
    order_index: Optional[int] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    exit_criteria: Optional[dict] = None
    notes: Optional[str] = None


#: major | minor | patch | hotfix | rc — the vocabulary the model documents.
#:
#: A closed set so a typo is a 422 rather than a stored value that silently
#: matches no policy: `release_type` drives gate-policy resolution, and a
#: release typed "Hotfix" would quietly fall back to the project default while
#: reading, to anyone looking at the row, as though it were typed.
ReleaseType = Literal["major", "minor", "patch", "hotfix", "rc"]


class ReleaseIn(BaseModel):
    project_id: str
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    status: str = "planning"
    planned_date: Optional[datetime] = None
    phases: list[PhaseIn] = []

    # ── S1 identity (migration 0151) ─────────────────────────────────────────
    #
    # These columns shipped with the migration and no way to set them: zero
    # mentions in any router or schema. The consequence was not cosmetic —
    # `cutoff_start_at`/`cutoff_end_at` are the attribution ladder's rung-4
    # input, so that rung could never fire, and `baseline_release_id` is what
    # release-over-release comparison needs, so it always answered "this
    # release has no baseline".
    release_type: Optional[ReleaseType] = None
    target_environment: Optional[str] = Field(None, max_length=100)
    cutoff_start_at: Optional[datetime] = None
    cutoff_end_at: Optional[datetime] = None
    #: Omit to inherit the previous release by sort_key — see
    #: ``resolve_baseline``. Set it to override, which is what a hotfix needs:
    #: its baseline is its parent release, not whatever shipped most recently.
    baseline_release_id: Optional[str] = None
    #: REL-02. A person who can work on the project (an ADMIN, or an active
    #: non-service member); anyone else is a 422 with one text for every case.
    #: Omitted -> the creating human (never an API key / service account);
    #: ``null`` or ``""`` -> "No owner".
    owner_user_id: Optional[str] = None


class ReleaseUpdate(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    planned_date: Optional[datetime] = None
    released_at: Optional[datetime] = None
    release_type: Optional[ReleaseType] = None
    target_environment: Optional[str] = Field(None, max_length=100)
    cutoff_start_at: Optional[datetime] = None
    cutoff_end_at: Optional[datetime] = None
    baseline_release_id: Optional[str] = None
    #: REL-02. Sent at all (``model_fields_set``): ``null`` / ``""`` clears the
    #: owner, anything else is validated against the release's project.
    owner_user_id: Optional[str] = None


class LinkRunRequest(BaseModel):
    test_run_id: str
    phase_id: Optional[str] = None


class ReleaseSyncIn(BaseModel):
    project_id: str
    #: A closed set, so an unknown source is a 422 naming the valid ones rather
    #: than a silent no-op that reports "0 created" and looks like an empty
    #: milestone list.
    source: Literal["github", "jira", "gitlab"]  # W19 INT-05: gitlab = project milestones
    #: Jira only. Falls back to the deployment's configured default key.
    jira_project_key: Optional[str] = None


class ReleaseOutcomeIn(BaseModel):
    """A production outcome reported by a release owner for G5 drift."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["incident", "rollback"]
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_must_contain_text(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return clean


# ── Read endpoints (any authenticated user) ──────────────────────────────────

@router.get("")
async def list_releases(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    accessible = await get_accessible_project_ids(db, current_user)
    if project_id is not None:
        # Verify access to the explicitly-requested project. Previously a
        # provided project_id skipped the accessible-projects gate entirely
        # (only the no-project_id path was guarded), so any authenticated user
        # could list another tenant's releases via ?project_id=<foreign-uuid>.
        try:
            requested = uuid.UUID(project_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid project_id") from exc
        if accessible is not None and requested not in accessible:
            raise HTTPException(
                status_code=403, detail="You do not have access to this project"
            )
    elif accessible is not None and not accessible:
        # Non-admin with no memberships, all-projects view → nothing to show,
        # in the same ``{items, total}`` shape as every other answer. A bare
        # ``[]`` here was the one answer of another shape: the client reads
        # ``data.items`` / ``data.total`` and got ``undefined`` off it.
        return {"items": [], "total": 0}
    # Pass the accessible set so the service confines results to the caller's
    # projects (fan-out in all-projects mode; defence-in-depth when pinned).
    return await release_service.list_releases(
        db, project_id, status, accessible_project_ids=accessible,
    )


# ── W11 REL-06 / REL-03: static GETs, declared BEFORE ``/{release_id}`` ──────
#
# ``GET /{release_id}`` below would otherwise capture ``schedule.ics`` /
# ``portfolio-trend`` as a release id (``tests/test_release_schedule_export.py``
# pins the order).


async def _schedule_response(
    fmt: str,
    db: AsyncSession,
    scoped: Optional[uuid.UUID],
    allowed: Optional[set[uuid.UUID]],
    start: Optional[date],
    end: Optional[date],
) -> Response:
    """The schedule file for an ALREADY-RESOLVED scope (the handlers resolve it first)."""
    if start is not None and end is not None and end < start:
        raise HTTPException(status_code=422, detail="to is before from.")
    project_ids = {scoped} if scoped is not None else allowed
    try:
        releases = await release_schedule_export.load_schedule(
            db, project_ids=project_ids, start=start, end=end,
        )
    except release_schedule_export.ScheduleTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now(timezone.utc)
    if scoped is not None:
        label = (await db.execute(select(Project.name).where(Project.id == scoped))).scalar_one_or_none() or "project"
        calendar_name = f"TestLookup releases: {label}"
    else:
        label = "all-projects"
        calendar_name = "TestLookup releases: all projects"
    filename = release_schedule_export.schedule_filename(label, fmt, now.date())
    if fmt == "ics":
        body = release_schedule_export.write_ics(
            releases, calendar_name=calendar_name, now=now, base_url=settings.public_base_url,
        )
        media_type = "text/calendar; charset=utf-8"
    else:
        body = release_schedule_export.write_csv(releases)
        media_type = "text/csv; charset=utf-8"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/schedule.ics")
async def export_release_schedule_ics(
    project_id: Optional[str] = Query(None),
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The release schedule as an iCalendar file (REL-06): download only, no token in any URL.

    ``project_id`` is resolved FIRST (400 malformed, 403 foreign); without it
    the file covers every project the caller can see. A cancelled release is
    not on the calendar. A GET: writes nothing.
    """
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    return await _schedule_response("ics", db, scoped, allowed, from_, to)


@router.get("/schedule.csv")
async def export_release_schedule_csv(
    project_id: Optional[str] = Query(None),
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The release schedule as CSV (REL-06): one row per phase, formula-guarded, a BOM.

    Scoped exactly like the ICS twin; a cancelled release IS listed, with its
    status. A GET: writes nothing.
    """
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    return await _schedule_response("csv", db, scoped, allowed, from_, to)


@router.get("/portfolio-trend", response_model=ReleasePortfolioTrend)
async def get_release_portfolio_trend(
    project_id: Optional[str] = Query(None),
    weeks: int = Query(PORTFOLIO_WEEKS_DEFAULT, ge=PORTFOLIO_WEEKS_MIN, le=PORTFOLIO_WEEKS_MAX),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The release KPI cards' trend (REL-03): each card's rule at ``weeks`` week ends and now.

    ``project_id`` is resolved FIRST; without it, every project the caller can
    see. Four statements; a GET that writes and caches nothing (the page asks
    only when the "Release health" disclosure is open).
    """
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    project_ids = {scoped} if scoped is not None else allowed
    trend = await release_portfolio_service.portfolio_trend(
        db, project_ids=project_ids, weeks=weeks, now=datetime.now(timezone.utc),
    )
    return {"project_id": scoped, **trend}


async def _live_release_holds(db: AsyncSession, release_id: str) -> list:
    """Every LIVE retention hold covering the release: its own and its project's."""
    return await retention_hold_service.holds_covering_release(
        db, release_id=uuid.UUID(release_id), now=datetime.now(timezone.utc)
    )


async def _refuse_while_held(db: AsyncSession, release_id: str) -> None:
    """W22a H-13 / H-14: a held release (or a release of a held project) is
    neither deleted nor stripped of a run — an unlink would silently drop the
    run out of the hold. 409 ``{"blockers": ["Held: <reason>", …]}``, whatever
    the credential. Reads the PATH id."""
    held = await _live_release_holds(db, release_id)
    if held:
        raise HTTPException(
            status_code=409, detail={"blockers": [h.sentence() for h in held]}
        )


@router.get("/{release_id}")
async def get_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    details = await release_service.get_release_details(db, release_id)
    # W22a: the live holds covering the release ride the detail answer (read
    # LAST), so the release page needs no new request.
    held = await _live_release_holds(db, release_id)
    if isinstance(details, dict):
        return {**details, "holds": [h.as_json() for h in held]}
    return details


@router.get("/{release_id}/gate")
async def get_release_gate(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    """The standing verdict for this release, read from the stored snapshot.

    Deliberately does NOT recompute. Recomputing would restate a past verdict
    under today's runs and today's policy, which is what the snapshot columns on
    ``ReleaseGateDecision`` exist to prevent.

    404 when the release has never been evaluated — distinct from a verdict of
    NOT_EVALUATED, which means it WAS evaluated and there was not enough
    evidence to say. Collapsing those two into one response would lose the
    difference between "we have not looked" and "we looked and cannot say".
    """
    gate = await release_gate_service.current_gate(db, release_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="This release has not been evaluated yet")
    reasons = gate.get("blocking_reasons") or []
    if reasons:
        # REL-01. The stored reasons name an ingested test by its opaque
        # fingerprint ("checkout-e2e::3f9a... is FAILED"); the display strings
        # sit BESIDE them (the raw list stays the record) and read "test_pay
        # (checkout-e2e) is FAILED". Only when there is something to translate:
        # a verdict with no blocking reason costs no further query.
        gate["blocking_reasons_display"] = await release_gate_reasons.display_reasons_for_release(
            db, uuid.UUID(str(release_id)), reasons[: release_gate_service.GATE_DETAIL_REASON_CAP]
        )
    return gate


@router.get("/{release_id}/gate/baseline")
async def get_release_gate_baseline(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    """Release-over-release comparison, or a stated reason it is not meaningful.

    Returns ``comparable: false`` with a reason rather than numbers whenever a
    delta would mislead — no baseline, or either side below the evidence floor.
    A delta against a release nothing ran in is arithmetically fine and
    completely meaningless, and once it is a number on a scorecard nobody
    re-derives whether it was meaningful.
    """
    return await release_gate_service.compare_to_baseline(db, release_id)


# ── Mutation endpoints (QA_LEAD or ADMIN) ────────────────────────────────────

@router.post("", status_code=201)
async def create_release(
    body: ReleaseIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
):
    # ``require_role(QA_LEAD)`` gates by ROLE, never by project membership, and
    # ``project_id`` arrives in the BODY -- which the architectural ratchet
    # matched only in the path, so this route was auto-declared protected. A QA
    # lead of one project could therefore create a release, with its phases,
    # inside any other project on the deployment. Note the sibling
    # ``PUT /{release_id}`` immediately below already carries
    # ``require_release_access()``: create was the odd one out.
    await resolve_project_scope(db, current_user, str(body.project_id))
    # The actor is the creator (``created_by_id``) and, when an interactive
    # human, the default owner (REL-02).
    release = await release_service.create_release(db, body, actor=current_user)

    # Epic ACT. Staged on this session so the event and the release land
    # together: a "release created" row for a create that rolled back would be
    # a lie the feed could not walk back.
    await record_activity(
        db,
        project_id=body.project_id,
        event_type="release.created",
        actor=ActorRef.from_user(current_user),
        entity_id=release.id,
        entity_label=release.name,
        release_id=release.id,
        context={"version": getattr(release, "version", None)},
    )

    await db.commit()
    return await release_service.serialize_created_release(db, release)


@router.post("/sync")
async def sync_releases_from_external(
    body: ReleaseSyncIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
):
    """Pull releases in from GitHub milestones or Jira fix versions.

    **Why this endpoint exists at all.** ``sync_milestones`` and
    ``sync_fix_versions`` shipped complete, gated and tested, and nothing
    called either of them -- so rung 2 of the attribution ladder ("the run's
    external match") could never fire in production, because nothing created a
    release carrying a ``source_system`` for it to match against. Two finished
    features were unreachable behind a missing route.

    **Manual, not scheduled.** No beat entry: ``AI_OFFLINE_MODE`` defaults to
    True and a periodic job that egresses on its own is a materially larger
    change than making a finished feature reachable. A person asking for a sync
    is also the point at which a 503 explaining WHY it cannot run is useful.

    Only identity is written -- name, external id, external url. A synced
    release's phases, criteria, gate policy and run attribution are never
    touched, because those are TestLookup's and neither GitHub nor Jira knows
    anything about them.
    """
    # ``require_role(QA_LEAD)`` gates by ROLE, never by project membership, and
    # ``project_id`` arrives in the BODY -- which the architectural ratchet
    # matches only in the PATH, so this route would otherwise be auto-declared
    # protected while a QA lead of one project synced releases into any other
    # project on the deployment. Same repair as ``create_release`` above.
    scoped, _allowed = await resolve_project_scope(db, current_user, body.project_id)
    if scoped is None:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    sync_started = datetime.now(timezone.utc)
    try:
        if body.source == "github":
            summary = await github_release_sync.sync_milestones(db, scoped)
        elif body.source == "gitlab":
            summary = await gitlab_release_sync.sync_milestones(db, scoped)
        else:
            summary = await jira_release_sync.sync_fix_versions(
                db, scoped, body.jira_project_key
            )
    except (
        github_release_sync.GitHubSyncUnavailable,
        gitlab_release_sync.GitLabSyncUnavailable,
        jira_release_sync.JiraSyncUnavailable,
    ) as exc:
        # 503 with the gate's own words. "AI_OFFLINE_MODE is enabled" and "No
        # Jira domain configured" are different problems with different fixes,
        # and collapsing them into one message is how an operator ends up
        # re-checking credentials that were never the issue.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        # REL-08. The source could not be reached at all (DNS, refused
        # connection, timeout): nothing caught it, so it answered an opaque 500
        # that read like a TestLookup bug. 502 = the upstream failed. The class
        # name only -- ``str(exc)`` carries the URL, which names the repository
        # or Jira host and does not belong in a toast.
        source = {"github": "GitHub", "gitlab": "GitLab"}.get(body.source, "Jira")
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach {source}: {type(exc).__name__}",
        ) from exc

    # W21 ACT-01: one row per release the sync created or refreshed. The three
    # services stamp ``last_synced_at`` on exactly those rows, so this reads
    # them back instead of changing three return shapes. Outcome mode: staged
    # here, committed with the sync itself.
    provider = {"github": "GitHub", "gitlab": "GitLab"}.get(body.source, "Jira")
    touched: list[tuple[uuid.UUID, str]] = []
    if summary.get("created") or summary.get("updated"):
        touched = [
            (row[0], row[1])
            for row in (await db.execute(
                select(Release.id, Release.name).where(
                    Release.project_id == scoped,
                    Release.last_synced_at >= sync_started,
                )
            )).all()
        ]
    for touched_id, touched_name in touched:
        await record_activity(
            db,
            project_id=scoped,
            event_type="release.synced_external",
            actor=ActorRef.from_user(current_user),
            entity_id=touched_id,
            entity_label=touched_name,
            release_id=touched_id,
            context={"provider": provider},
        )

    # The services deliberately do not commit -- the router owns the
    # transaction, so one commit covers the whole sync and a failure part-way
    # through leaves no half-imported set of releases.
    await db.commit()
    return {"source": body.source, **summary}


@router.put("/{release_id}")
async def update_release(
    release_id: str,
    body: ReleaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    # Snapshot BEFORE the service applies the update. update_release mutates
    # the row and returns it, so comparing afterwards would find every field
    # equal and record nothing at all — a worse bug than the no-op events this
    # is fixing.
    submitted = body.model_dump(exclude_none=True)
    # REL-02: the owner is compared on its own below (``null`` clears it, which
    # ``exclude_none`` cannot carry) and reported by NAME, never by id.
    submitted.pop("owner_user_id", None)
    owner_sent = "owner_user_id" in body.model_fields_set
    existing = await release_service.get_release_or_404(db, release_id)
    before = {f: getattr(existing, f, None) for f in submitted}
    owner_before = getattr(existing, "owner_user_id", None)

    release = await release_service.update_release(db, release_id, body)

    # A release's name, dates and status are what a gate decision is read
    # against later, so "who changed this release, and to what" is exactly the
    # question the feed exists to answer. Only fields that actually DIFFER
    # count: a PUT that re-sends the current name is not a rename. Ids are
    # compared as strings: the row holds a UUID and the body a str, so a
    # re-sent baseline used to read as a change every time.
    changed = sorted(
        f for f, v in submitted.items() if _comparable(before.get(f)) != _comparable(v)
    )
    owner_after = getattr(release, "owner_user_id", None)
    owner_changed = owner_sent and _comparable(owner_before) != _comparable(owner_after)
    diff: dict[str, Any] = {}
    if owner_changed:
        changed = sorted([*changed, "owner"])
        names = await release_owner_service.owners_by_id(db, [owner_before, owner_after])

        def _label(owner_id):
            owner = names.get(owner_id) if owner_id is not None else None
            return owner["name"] if owner else release_owner_service.NO_OWNER_LABEL

        diff = {"before": {"owner": _label(owner_before)}, "after": {"owner": _label(owner_after)}}
    if changed:
        await record_activity(
            db,
            project_id=release.project_id,
            event_type="release.updated",
            actor=ActorRef.from_user(current_user),
            entity_id=release.id,
            entity_label=release.name,
            release_id=release.id,
            changed_fields=changed,
            context={"changed": ", ".join(changed)},
            **diff,
        )
    # W21 ACT-01: a release entering a terminal status is the lifecycle event
    # the feed filters on, distinct from the field-level "changed" row above.
    old_status, new_status = before.get("status"), getattr(release, "status", None)
    if (
        "status" in changed
        and new_status in release_lifecycle_service.TERMINAL_STATUSES
        and old_status not in release_lifecycle_service.TERMINAL_STATUSES
    ):
        await record_activity(
            db,
            project_id=release.project_id,
            event_type="release.closed",
            actor=ActorRef.from_user(current_user),
            entity_id=release.id,
            entity_label=release.name,
            release_id=release.id,
            before={"status": old_status},
            after={"status": new_status},
            changed_fields=["status"],
        )

    await db.commit()
    await db.refresh(release)
    return serialize_model(release)


@router.post("/{release_id}/outcomes", status_code=201)
async def mark_release_outcome(
    release_id: str,
    body: ReleaseOutcomeIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _access: User = Depends(require_release_access()),
):
    """Append a human incident or rollback outcome for later G5 evaluation."""
    release = await release_service.get_release_or_404(db, release_id)
    outcome = await release_outcome_service.record_outcome(
        db,
        release=release,
        outcome_kind=body.outcome,
        reason=body.reason,
        marked_by_user_id=current_user.id,
    )
    await record_activity(
        db,
        project_id=release.project_id,
        event_type="release.outcome_marked",
        actor=ActorRef.from_user(current_user),
        entity_id=release.id,
        entity_label=release.name,
        release_id=release.id,
        context={"outcome": body.outcome, "reason": body.reason},
    )
    await db.commit()
    return serialize_model(outcome)


@router.delete("/{release_id}", status_code=204)
async def delete_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    # N20: require_release_access() confines a project-bound key to the
    # release's own project.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    # Read BEFORE the delete: afterwards the name is gone and the row could
    # only say that "something" was removed. release_id is deliberately NOT
    # set on the event - the FK is ON DELETE SET NULL, so it would be nulled
    # moments later; the label snapshot is what survives.
    doomed = await release_service.get_release_or_404(db, release_id)
    project_id = getattr(doomed, "project_id", None)
    label = getattr(doomed, "name", None)
    await _refuse_while_held(db, release_id)

    await release_service.delete_release(db, release_id)

    if project_id is not None:
        await record_activity(
            None,
            project_id=project_id,
            event_type="release.deleted",
            actor=ActorRef.from_user(current_user),
            entity_id=release_id,
            entity_label=label,
        )

    await db.commit()
    # VIZ-212: deleting a release re-points its runs' primary release.
    if project_id is not None:
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(project_id)


@router.post("/{release_id}/gate/evaluate")
async def evaluate_release_gate(
    release_id: str,
    record: bool = Query(
        True,
        description="Append the verdict to the release's audit history. "
        "Pass false to preview without recording.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    # BOTH guards, as dependencies. `require_role` answers "may this user record
    # verdicts at all"; `require_release_access` answers "may they touch THIS
    # release" — the per-id check the authorization ratchet is blind to once a
    # route satisfies it on any scoped parameter.
    #
    # It has to be a Depends: the guard reads `request.path_params`, so calling
    # it by hand with a `release_id=` keyword raises TypeError on every request.
    _access: User = Depends(require_release_access()),
):
    """Evaluate the gate and, by default, record the verdict.

    QA_LEAD, not plain membership. Recording appends to an append-only audit
    trail that a release decision is later justified by, so it is a privileged
    write even though it computes rather than edits.

    The router owns the commit, per the repo's transaction-boundary rule, so the
    demote of the previous verdict and the insert of the new one land as one
    unit of work — a failure cannot leave a release with two current verdicts or
    none.
    """
    result = await release_gate_service.evaluate_release(
        db, release_id, record=record, created_by_id=current_user.id
    )
    if record:
        await db.commit()
    return result


@router.post("/{release_id}/phases", status_code=201)
async def add_phase(
    release_id: str,
    body: PhaseIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    phase = await release_service.add_phase(db, release_id, body, actor=current_user)
    await db.commit()
    await db.refresh(phase)
    return serialize_model(phase)


# ── W11 REL-05 / REL-07: a release's first schedule, copied or drafted ───────
#
# QA_LEAD without ``allow_project_key``: these are page actions (Copy phases
# from..., Draft from a PRD...), so a project-bound CI key is refused by
# default; opting it in is a reviewed entry in
# ``tests/regression/test_admin_routes_refuse_project_keys.py`` (re-audit N26).


@router.post(
    "/{release_id}/phases/clone", status_code=201, response_model=ReleasePhasesCreatedResponse,
)
async def clone_release_phases(
    release_id: str,
    body: ReleasePhasesCloneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    """Copy another release's phases into this one (REL-05): dates moved, all Pending.

    The BODY id is checked by ONE statement scoped to the target's project
    BEFORE anything else reads it: an unknown, malformed (Pydantic's 422) or
    FOREIGN source is the same 404 text, so the route is no oracle for another
    project's release ids. ``require_release_access`` scopes the path id.
    """
    target = await release_service.get_release_or_404(db, release_id)
    source = (
        await db.execute(
            select(Release).where(
                Release.id == body.from_release_id,
                Release.project_id == target.project_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail=RELEASE_NOT_FOUND)
    if source.id == target.id:
        raise HTTPException(status_code=422, detail=SOURCE_IS_TARGET)
    rows, shifted = await release_service.clone_phases(db, target, source, body.shift_to)
    # Outcome mode: staged on this session, so the event commits with the
    # phases or not at all.
    await record_activity(
        db,
        project_id=target.project_id,
        event_type="release.phases_cloned",
        actor=ActorRef.from_user(current_user),
        entity_id=target.id,
        entity_label=target.name,
        release_id=target.id,
        context={
            "count": len(rows),
            "source_release": source.name,
            "source_release_id": str(source.id),
            "shifted_by_days": shifted,
        },
    )
    await db.commit()
    release_phases_created_total.labels(source="clone").inc(len(rows))
    for row in rows:
        await db.refresh(row)
    return {
        "release_id": target.id,
        "created": len(rows),
        "phases": [serialize_model(row) for row in rows],
        "source_release_id": source.id,
        "shifted_by_days": shifted,
    }


async def _read_prd_upload(file: UploadFile) -> bytes:
    """The upload, read in chunks: 413 as soon as it passes ``MAX_PRD_BYTES``.

    The ``routers/ingest.py`` pattern, restated (not imported): the whole body
    must never be resident before the size check fires.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_PRD_BYTES:
            raise HTTPException(status_code=413, detail=PRD_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


# activity: none - drafts phases from a PRD for review; persists nothing (the bulk create records the event)
@router.post("/{release_id}/phases/generate", response_model=PhaseDraftResponse)
async def draft_release_phases(
    release_id: str,
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    """Draft phases from a PRD for the reader to review (REL-07). Persists nothing.

    The heading parser always runs; the model is asked only in ``llm`` /
    ``auto`` analysis mode, and any model failure is the rules draft with a
    ``fallback_reason`` -- never an error. The request's connection is handed
    back BEFORE the config read and the model call, so no connection is held
    while the model runs. The PRD is never stored or logged.
    """
    release = await release_service.get_release_or_404(db, release_id)
    data = await _read_prd_upload(file) if file is not None else None
    try:
        prd = extract_prd_text(
            text=text, filename=file.filename if file is not None else None, data=data,
        )
    except PrdInputError as exc:
        status_code = 413 if str(exc) == PRD_TOO_LARGE else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    project_id = str(release.project_id)
    release_id_value = release.id
    # FIRST: nothing more is read on ``db``. The config read (its own short
    # session on a cache miss) and the model's cost check never overlap the
    # request's connection.
    await release_read_connection(db)
    ctx = DraftContext.from_config(await get_effective_ai_config(), project_id=project_id)
    body = await draft_phases(ctx, prd)
    return {"release_id": release_id_value, **body}


@router.post(
    "/{release_id}/phases/bulk", status_code=201, response_model=ReleasePhasesCreatedResponse,
)
async def create_release_phases_bulk(
    release_id: str,
    body: ReleasePhasesBulkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    """Create a release's first schedule in one request (REL-07's Create; also a manual bulk).

    409 when the release already has phases (single phases keep
    ``POST /phases``); every name unique in the batch; ``source`` /
    ``model_name`` are recorded in the activity row and the metric only.
    """
    release = await release_service.get_release_or_404(db, release_id)
    rows = await release_service.create_phases_bulk(db, release, body.phases)
    source = body.source if body.source in PHASE_DRAFT_SOURCES else "manual"
    await record_activity(
        db,
        project_id=release.project_id,
        event_type="release.phases_generated",
        actor=ActorRef.from_user(current_user),
        entity_id=release.id,
        entity_label=release.name,
        release_id=release.id,
        context={
            "count": len(rows),
            "source": source,
            "model_name": body.model_name if source == "llm" else None,
        },
    )
    await db.commit()
    release_phases_created_total.labels(source=source).inc(len(rows))
    for row in rows:
        await db.refresh(row)
    return {
        "release_id": release.id,
        "created": len(rows),
        "phases": [serialize_model(row) for row in rows],
    }


@router.put("/{release_id}/phases/{phase_id}")
async def update_phase(
    release_id: str,
    phase_id: str,
    body: PhaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    # The actor travels with the call: a gate override recorded against nobody
    # is an audit row that answers "what" and not "who", which is the half that
    # matters when somebody asks later why a phase shipped un-gated.
    phase, all_done = await release_service.update_phase(
        db, release_id, phase_id, body, actor=current_user
    )
    await db.commit()
    await db.refresh(phase)
    result = serialize_model(phase)
    result["all_phases_completed"] = all_done
    return result


@router.post("/{release_id}/activate")
async def activate_release_endpoint(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    """Make this the project's active release.

    The active release is where a run lands when nothing else claims it — the
    attribution ladder's terminal rung. It has been a real, enforced concept
    since S0 (one per project, guarded by ``ix_releases_project_active``, with
    activation history behind it), and until now it could only ever be chosen
    FOR the user by the ingestion ladder and the rotation beat.
    ``activate_release`` was written complete, with its ``reason="manual"``
    default, for a caller that never arrived.

    Both guards, not one: ``require_role`` gates by ROLE and knows nothing about
    which project this release belongs to, so a QA lead of one project could
    otherwise redirect another project's attribution.

    The swap itself stays in ``release_lifecycle_service`` — it contains a
    load-bearing flush between the demote and the promote, because SQLAlchemy
    orders persistent UPDATEs by primary key and ``Release.id`` is a random
    uuid4, so without it half of all orderings present two active rows to a
    partial unique index. Writing ``is_active`` here instead would route around
    that.
    """
    release = await release_service.get_release_or_404(db, release_id)

    if release.status in release_lifecycle_service.TERMINAL_STATUSES:
        # A finished release must not collect new runs: the next unlabelled run
        # would be attributed to something already shipped, and the misdated
        # evidence would sit in its gate decision looking legitimate.
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{release.name}' is {release.status} and cannot be made "
                "active — a finished release must not collect new runs."
            ),
        )

    if release.is_active:
        # Idempotent by design. A double-click, or two people acting on the
        # same stale page, must not be an error — the requested state is the
        # state.
        return {
            "release_id": str(release.id),
            "is_active": True,
            "deactivated": None,
            "changed": False,
        }

    previous = await release_lifecycle_service.activate_release(
        db, release, reason="manual"
    )

    # REL-04. The access-audit row below is for an auditor; the activity
    # ledger is what the release page's "Recent activity" and ``/activity``
    # read, and nothing put this there. Outcome mode: staged on this session,
    # so it commits with the swap or not at all. Only on a real change -- the
    # idempotent no-op above returned before reaching here.
    await record_activity(
        db,
        project_id=release.project_id,
        event_type="release.activated",
        actor=ActorRef.from_user(current_user),
        entity_id=release.id,
        entity_label=release.name,
        release_id=release.id,
        before={"active_release": previous.name if previous is not None else None},
        after={"active_release": release.name},
        changed_fields=["active_release"],
    )

    db.add(
        AccessAuditLog(
            actor_user_id=current_user.id,
            actor_name=current_user.username,
            project_id=release.project_id,
            action="release.activated",
            before_value=(
                {"release_id": str(previous.id), "name": previous.name}
                if previous is not None
                else None
            ),
            after_value={"release_id": str(release.id), "name": release.name},
        )
    )

    # The router owns the transaction, so the demote and the promote commit
    # together — there is never a committed state with two actives or none.
    await db.commit()
    return {
        "release_id": str(release.id),
        "is_active": True,
        "deactivated": str(previous.id) if previous is not None else None,
        "changed": True,
    }


@router.get("/{release_id}/phases/gate")
async def get_release_phase_gate(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    """Every phase of this release, with a verdict each, and whether it may
    advance.

    Read-only: ``evaluate_all_phases`` defaults to ``record=False`` because
    looking at the state of a release is routine, and appending a decision row
    per phase per look would bury the real decisions in noise.

    The summary is three-valued on purpose. A release blocked by a FAILING
    phase and one blocked by an UNEVALUATED phase need different actions — fix
    the tests, or go run some — and collapsing both into "cannot advance" sends
    a release manager to do the wrong one half the time. ``NO_PHASES`` is a
    fourth state and not a pass: phases are optional, so having none is not a
    failure, but nothing was gated either.
    """
    phases = await release_phase_gate_service.evaluate_all_phases(db, release_id)
    return {
        "release_id": release_id,
        **release_phase_gate_service.summarise_gate(phases),
        "phases": phases,
    }


@router.post("/{release_id}/phases/{phase_id}/gate/evaluate")
async def evaluate_release_phase_gate(
    release_id: str,
    phase_id: str,
    record: bool = Query(True, description="Append the verdict to the audit history"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    """Evaluate ONE phase and, by default, record the verdict.

    ``record=false`` previews without appending. The history is the point of
    that table, so writing to it is a deliberate act rather than a side effect
    of looking — which is also why the read endpoint above never records.
    """
    result = await release_phase_gate_service.evaluate_phase(
        db, release_id, phase_id, record=record, created_by_id=current_user.id
    )
    # The service stages the decision row; the router owns the transaction.
    await db.commit()
    return result


@router.delete("/{release_id}/phases/{phase_id}", status_code=204)
async def delete_phase(
    release_id: str,
    phase_id: str,
    db: AsyncSession = Depends(get_db),
    # N20: the require_*_access guards below confine a project-bound key to
    # its own project.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    await release_service.delete_phase(db, release_id, phase_id, actor=current_user)
    await db.commit()


@router.post("/{release_id}/test-runs")
async def link_test_run(
    release_id: str,
    body: LinkRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_release_access()),
):
    link, is_new = await release_service.link_test_run(
        db, release_id, body, linked_by_id=current_user.id, actor=current_user
    )
    if is_new:
        await db.commit()
        # VIZ-212: the new link became the run's primary release.
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(link.project_id)
        await db.refresh(link)
        return serialize_model(link)
    # Idempotent: link already existed, service returned the existing row.
    return {"message": "Already linked", "id": str(link.id)}


@router.delete("/{release_id}/test-runs/{run_id}", status_code=204)
async def unlink_test_run(
    release_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    # N20: the require_*_access guards below confine a project-bound key to
    # its own project.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    __: User = Depends(require_run_access()),
    # The route carries TWO scoped path params and only the run was checked.
    # The authz ratchet stops at the first scoped param it can satisfy, so it
    # cannot see the gap — a caller with access to the run but not the release
    # could unlink it from a release in a project they cannot reach.
    ___: User = Depends(require_release_access()),
):
    await _refuse_while_held(db, release_id)
    # Read before the unlink: the run's project is what the bump invalidates.
    project_id = await db.scalar(
        select(TestRun.project_id).where(TestRun.id == uuid.UUID(run_id))
    )
    await release_service.unlink_test_run(db, release_id, run_id, actor=current_user)
    await db.commit()
    # VIZ-212: the unlink promoted a survivor (or cleared the primary release).
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(project_id)
