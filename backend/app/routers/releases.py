"""Release management endpoints — CRUD for releases, phases, and test-run links.

Authorization policy:
  - Read endpoints (GET): any authenticated user
  - Mutation endpoints (POST, PUT, DELETE): QA_LEAD or ADMIN
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_release_access,
    require_role,
    require_run_access,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, User, UserRole
from app.services.activity.service import ActorRef, record as record_activity
from app.models.serializers import serialize_model
from app.services import (
    github_release_sync,
    release_lifecycle_service,
    jira_release_sync,
    release_gate_service,
    release_phase_gate_service,
    release_service,
)

router = APIRouter(prefix="/api/v1/releases", tags=["Releases"])


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


class LinkRunRequest(BaseModel):
    test_run_id: str
    phase_id: Optional[str] = None


class ReleaseSyncIn(BaseModel):
    project_id: str
    #: A closed set, so an unknown source is a 422 naming the valid ones rather
    #: than a silent no-op that reports "0 created" and looks like an empty
    #: milestone list.
    source: Literal["github", "jira"]
    #: Jira only. Falls back to the deployment's configured default key.
    jira_project_key: Optional[str] = None


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
        # Non-admin with no memberships, all-projects view → nothing to show.
        return []
    # Pass the accessible set so the service confines results to the caller's
    # projects (fan-out in all-projects mode; defence-in-depth when pinned).
    return await release_service.list_releases(
        db, project_id, status, accessible_project_ids=accessible,
    )


@router.get("/{release_id}")
async def get_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_release_access()),
):
    return await release_service.get_release_details(db, release_id)


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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    # ``require_role(QA_LEAD)`` gates by ROLE, never by project membership, and
    # ``project_id`` arrives in the BODY -- which the architectural ratchet
    # matched only in the path, so this route was auto-declared protected. A QA
    # lead of one project could therefore create a release, with its phases,
    # inside any other project on the deployment. Note the sibling
    # ``PUT /{release_id}`` immediately below already carries
    # ``require_release_access()``: create was the odd one out.
    await resolve_project_scope(db, current_user, str(body.project_id))
    release = await release_service.create_release(db, body)

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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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

    try:
        if body.source == "github":
            summary = await github_release_sync.sync_milestones(db, scoped)
        else:
            summary = await jira_release_sync.sync_fix_versions(
                db, scoped, body.jira_project_key
            )
    except (
        github_release_sync.GitHubSyncUnavailable,
        jira_release_sync.JiraSyncUnavailable,
    ) as exc:
        # 503 with the gate's own words. "AI_OFFLINE_MODE is enabled" and "No
        # Jira domain configured" are different problems with different fixes,
        # and collapsing them into one message is how an operator ends up
        # re-checking credentials that were never the issue.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    # Snapshot BEFORE the service applies the update. update_release mutates
    # the row and returns it, so comparing afterwards would find every field
    # equal and record nothing at all — a worse bug than the no-op events this
    # is fixing.
    submitted = body.model_dump(exclude_none=True)
    existing = await release_service.get_release_or_404(db, release_id)
    before = {f: getattr(existing, f, None) for f in submitted}

    release = await release_service.update_release(db, release_id, body)

    # A release's name, dates and status are what a gate decision is read
    # against later, so "who changed this release, and to what" is exactly the
    # question the feed exists to answer. Only fields that actually DIFFER
    # count: a PUT that re-sends the current name is not a rename.
    changed = sorted(f for f, v in submitted.items() if before.get(f) != v)
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
        )

    await db.commit()
    await db.refresh(release)
    return serialize_model(release)


@router.delete("/{release_id}", status_code=204)
async def delete_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    __: User = Depends(require_release_access()),
):
    # Read BEFORE the delete: afterwards the name is gone and the row could
    # only say that "something" was removed. release_id is deliberately NOT
    # set on the event - the FK is ON DELETE SET NULL, so it would be nulled
    # moments later; the label snapshot is what survives.
    doomed = await release_service.get_release_or_404(db, release_id)
    project_id = getattr(doomed, "project_id", None)
    label = getattr(doomed, "name", None)

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


@router.post("/{release_id}/gate/evaluate")
async def evaluate_release_gate(
    release_id: str,
    record: bool = Query(
        True,
        description="Append the verdict to the release's audit history. "
        "Pass false to preview without recording.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    phase = await release_service.add_phase(db, release_id, body)
    await db.commit()
    await db.refresh(phase)
    return serialize_model(phase)


@router.put("/{release_id}/phases/{phase_id}")
async def update_phase(
    release_id: str,
    phase_id: str,
    body: PhaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
    _: User = Depends(require_role(UserRole.ADMIN)),
    __: User = Depends(require_release_access()),
):
    await release_service.delete_phase(db, release_id, phase_id)
    await db.commit()


@router.post("/{release_id}/test-runs")
async def link_test_run(
    release_id: str,
    body: LinkRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    __: User = Depends(require_release_access()),
):
    link, is_new = await release_service.link_test_run(
        db, release_id, body, linked_by_id=current_user.id
    )
    if is_new:
        await db.commit()
        await db.refresh(link)
        return serialize_model(link)
    # Idempotent: link already existed, service returned the existing row.
    return {"message": "Already linked", "id": str(link.id)}


@router.delete("/{release_id}/test-runs/{run_id}", status_code=204)
async def unlink_test_run(
    release_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
    __: User = Depends(require_run_access()),
    # The route carries TWO scoped path params and only the run was checked.
    # The authz ratchet stops at the first scoped param it can satisfy, so it
    # cannot see the gap — a caller with access to the run but not the release
    # could unlink it from a release in a project they cannot reach.
    ___: User = Depends(require_release_access()),
):
    await release_service.unlink_test_run(db, release_id, run_id)
    await db.commit()
