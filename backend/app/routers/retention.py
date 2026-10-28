"""
Per-project retention policy API — PMF US-11.4.

Endpoints (contract — frontend built in parallel):

* ``GET /api/v1/projects/{project_id}/retention-policy`` — any project
  member: effective policy (+``source``) and the latest execute-mode
  purge summary (``last_purge``).
* ``PUT /api/v1/projects/{project_id}/retention-policy`` — ADMIN: partial
  update of ``enabled`` + the four day windows + the two AI-history caps
  (``ai_versions_per_run`` / ``ai_versions_per_test``, 1-100, AIH-06). Bounds
  422 at the schema; the audit≥runs cross-check 422s from the service on
  merged values.
* ``POST /api/v1/projects/{project_id}/retention-policy/preview`` — ADMIN:
  runs the purge service in preview mode NOW, synchronously. Read-only —
  works even while the policy is disabled (preview is how admins decide
  whether to enable).
* ``POST /api/v1/projects/{project_id}/retention-policy/purge`` — ADMIN:
  typed-name confirmation, 409 while the policy is disabled; enqueues the
  Celery purge for THIS project in execute mode → 202 ``{queued: true}``.

Guard pattern mirrors ``projects.py``'s reset endpoint: ``require_role``
+ ``require_project_access()`` (authorization ratchet — ADMIN bypasses the
membership check, the project guard ties the route to its ``{project_id}``
scope). The service stages; the router session owns the commit
(transaction ratchet — ``get_db`` commits on successful return).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CREDENTIAL_KIND_JWT,
    credential_kind,
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import Project, TestRun, User, UserRole
from app.models.schemas import (
    DeletionExecuteAcceptedResponse,
    DeletionExecuteRequest,
    DeletionPreviewResponse,
    DeletionJobListResponse,
    DeletionJobResponse,
    ProjectStorageResponse,
    ReferenceBrokenRead,
    RetentionHoldCreate,
    RetentionHoldListResponse,
    RetentionHoldRead,
    RetentionHoldRelease,
    RetentionPolicyRead,
    RetentionPolicyWrite,
    RetentionPreviewResponse,
    RetentionPurgeQueued,
    RetentionPurgeRequest,
)
from app.services import retention_service as svc
from app.services import deletion_job_service
from app.services import retention_hold_service as holds
from app.services.activity.service import ActorRef, record as record_activity
from app.services.deletion_criteria import RetentionCriteria
from app.services import storage_accounting_service
from app.services.retention_vocab import (
    HOLD_ERROR_SENTENCES,
    HOLD_LIST_DEFAULT_LIMIT,
    HOLD_LIST_MAX_LIMIT,
)

router = APIRouter(prefix="/api/v1/projects", tags=["Retention"])
logger = structlog.get_logger("routers.retention")


@router.get(
    "/{project_id}/retention-policy",
    response_model=RetentionPolicyRead,
)
async def get_retention_policy(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Effective retention policy for a project (defaults when no row
    exists — the UI always renders the form) plus the latest purge."""
    effective = await svc.get_effective_policy(db, project_id)
    last_purge = await svc.get_last_purge(db, project_id)
    return RetentionPolicyRead(
        **effective.as_dict(), source=effective.source, last_purge=last_purge,
    )


@router.put(
    "/{project_id}/retention-policy",
    response_model=RetentionPolicyRead,
)
async def put_retention_policy(
    project_id: uuid.UUID,
    payload: RetentionPolicyWrite,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Upsert the project's retention policy. ADMIN-only — retention drives
    a destructive scheduled purge. Omitted fields keep their current (or
    default) value; returns the effective policy."""
    try:
        effective = await svc.upsert_policy(
            db,
            project_id=project_id,
            actor_id=current_user.id,
            actor_name=current_user.full_name or current_user.email,
            enabled=payload.enabled,
            raw_events_days=payload.raw_events_days,
            runs_days=payload.runs_days,
            artifacts_days=payload.artifacts_days,
            audit_days=payload.audit_days,
            ai_versions_per_run=payload.ai_versions_per_run,
            ai_versions_per_test=payload.ai_versions_per_test,
            keep_last_runs=payload.keep_last_runs,
            actor=current_user,
        )
    except svc.RetentionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    last_purge = await svc.get_last_purge(db, project_id)
    return RetentionPolicyRead(
        **effective.as_dict(), source=effective.source, last_purge=last_purge,
    )


@router.get(
    "/{project_id}/storage",
    response_model=ProjectStorageResponse,
)
async def get_project_storage(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Storage footprint for this project, per store (S3).

    Read-only. ADMIN-gated like the rest of this router's writes: the figure
    is the blast radius of a purge, so it is not a general-membership read.

    A store that cannot be reached comes back ``measured=False`` with null
    figures rather than a zero — the endpoint degrades per store instead of
    500-ing, because a page whose whole job is reporting is more useful
    partially right than absent.
    """
    project_exists = await db.scalar(
        select(Project.id).where(
            Project.id == project_id,
            Project.is_active.is_(True),
        )
    )
    if project_exists is None:
        # ADMIN bypasses the membership lookup in require_project_access(), so
        # the route itself must still distinguish a missing/deleted project
        # from a real project that happens to contain no data.
        raise HTTPException(status_code=404, detail="Project not found")

    footprint = await storage_accounting_service.project_storage_footprint(
        db, project_id
    )
    return ProjectStorageResponse(**footprint.as_payload())



@router.get(
    "/{project_id}/deletion/jobs",
    response_model=DeletionJobListResponse,
)
async def list_deletion_jobs(
    project_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Deletions that have run for this project, newest first.

    The purge already writes a never-purged record to ``settings_audit_log``,
    but that row is written after the fact — it cannot say a job is running
    now, and it cannot exist for one that failed. This is that view.
    """
    jobs = await deletion_job_service.list_jobs(db, project_id, limit=limit)
    return DeletionJobListResponse(
        jobs=[DeletionJobResponse.model_validate(j) for j in jobs]
    )


@router.get(
    "/{project_id}/deletion/jobs/{job_id}",
    response_model=DeletionJobResponse,
)
async def get_deletion_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """One deletion job — the endpoint a 202 caller polls.

    404s when the job belongs to another project, rather than leaking that the
    id exists somewhere else.
    """
    job = await deletion_job_service.get_job(db, job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="Deletion job not found")
    return DeletionJobResponse.model_validate(job)


@router.post(
    "/{project_id}/retention-policy/preview",
    response_model=RetentionPreviewResponse,
)
async def preview_retention_purge(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Dry-run the purge NOW (synchronous, read-only): per-class cutoffs and
    candidate counts. Deliberately available while the policy is disabled.

    W22a: also what the purge would KEEP — the live holds and what each pins
    (``holds``), the keep-last floor (``floor``) — and the links the deletion
    would break (``references_broken``, a warning; ``references_measured``
    false = not measured, never "none"). A hold read that fails fails the
    preview (500) rather than under-reporting what is kept."""
    # analytics-epoch: none — mode="preview" only counts candidates; nothing is deleted
    out = await svc.run_purge(db, project_id=project_id, mode="preview")
    grown: dict[str, Any] = {}
    if out.get("holds") is not None:
        grown["holds"] = out["holds"]
    if out.get("floor") is not None:
        grown["floor"] = out["floor"]
    if "references_broken" in out:
        grown["references_broken"] = out["references_broken"]
        grown["references_measured"] = bool(out.get("references_measured", True))
    return RetentionPreviewResponse(
        cutoffs=out["cutoffs"],
        candidates=out["candidates"],
        unmeasured=out.get("unmeasured", []),
        **grown,
    )


@router.post(
    "/{project_id}/retention-policy/purge",
    response_model=RetentionPurgeQueued,
    status_code=202,
)
async def enqueue_retention_purge(
    project_id: uuid.UUID,
    payload: RetentionPurgeRequest,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Enqueue an execute-mode purge for THIS project (202).

    Two-step confirmation: ``confirmation_name`` must equal the project's
    name exactly (422 on mismatch — the project-reset convention). 409
    while the policy is disabled.
    """
    try:
        project = await svc.validate_purge_request(
            db, project_id=project_id, confirmation_name=payload.confirmation_name,
        )
    except svc.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except svc.ConfirmationMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except svc.PolicyDisabled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Lazy import — routers must not pull the Celery app at import time.
    from app.worker.tasks import run_retention_purges

    run_retention_purges.apply_async(kwargs={"project_id": str(project_id)})
    logger.info(
        "retention_purge_enqueued",
        project_id=str(project_id),
        project_name=project.name,
        actor_id=str(current_user.id),
    )
    return RetentionPurgeQueued(queued=True)


@router.post(
    "/{project_id}/deletion/preview",
    response_model=DeletionPreviewResponse,
)
async def preview_criteria_deletion(
    project_id: uuid.UUID,
    criteria: RetentionCriteria,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Resolve a criteria set and FREEZE it for execution.

    The freeze is the whole point (N3/N4). The nightly purge's candidate set is
    a pure function of ``(policy, now)``, so re-resolving gives the same answer.
    Criteria are not: they read columns other code rewrites while the job sits
    queued — ``TestRun.status`` by ``_update_run_aggregates`` and by
    live-session close, ``primary_suite_name`` at session close. Execute is
    asynchronous, so re-resolving there would delete a different set from the
    one an ADMIN reviewed. This materializes the ids and hashes them; execute
    replays that set.

    Runs that cannot be deleted are reported HERE rather than discovered
    mid-execution, so the count an ADMIN authorises is the count that will go.
    """
    from app.db.mongo import get_mongo_db
    from app.services import deletion_criteria, retention_references, run_deletion_service

    foreign = await deletion_criteria.foreign_run_ids(
        db, project_id=project_id, run_ids=criteria.run_ids or []
    )
    if foreign:
        # The whole request fails. Filtering to the caller's own runs would act
        # on a request they got wrong, and naming which id was foreign would
        # confirm it exists in another project.
        raise HTTPException(
            status_code=403,
            detail="one or more run_ids do not belong to this project",
        )

    run_ids = await deletion_criteria.resolve_criteria_candidates(
        db, project_id=project_id, criteria=criteria
    )
    truncated = len(run_ids) > deletion_criteria.MAX_CANDIDATES
    run_ids = run_ids[: deletion_criteria.MAX_CANDIDATES]

    mongo = get_mongo_db()
    blocked: list[dict] = []
    deletable: list[uuid.UUID] = []
    refused_prefixes: list[str] = []

    rows = (
        await db.execute(
            select(TestRun.id, TestRun.status, TestRun.minio_prefix).where(
                TestRun.id.in_(run_ids)
            )
        )
    ).all() if run_ids else []
    by_id = {r[0]: r for r in rows}

    for run_id in run_ids:
        row = by_id.get(run_id)
        if row is None:
            continue
        reasons: list[str] = []
        if run_deletion_service.status_blocks_deletion(row[1]):
            reasons.append("run is still executing")
        reasons.extend(
            await run_deletion_service.citation_blockers(
                db, run_id=run_id, mongo=mongo
            )
        )
        if reasons:
            # A live retention hold is named FIRST (W22a): the operator learns
            # why before any other blocker.
            blocked.append(
                {"run_id": str(run_id), "reasons": run_deletion_service.held_first(reasons)}
            )
            continue
        _safe, refused = run_deletion_service.safe_artifact_prefixes(
            project_id, run_id, row[2]
        )
        refused_prefixes.extend(refused)
        deletable.append(run_id)

    # W22a F-6: the links deleting the deletable runs would break — a WARNING,
    # counted in a SAVEPOINT and failing soft (``measured: false``), never a
    # blocker. Explicit deletions are not limited by the keep-last floor.
    references = await retention_references.references_broken(db, deletable)

    job_id = deletion_job_service.stage_frozen_candidate_set(
        db,
        project_id=project_id,
        run_ids=deletable,
        criteria=criteria.model_dump(mode="json", exclude_none=True),
        requested_by_id=current_user.id,
    )
    # The router owns the commit, per the transaction-boundary rule. A preview
    # that failed after this point must NOT leave an executable job behind.
    await db.commit()

    return DeletionPreviewResponse(
        job_id=job_id,
        project_id=project_id,
        run_count=len(deletable),
        run_ids=deletable,
        candidate_hash=deletion_job_service.candidate_hash(
            [str(r) for r in deletable]
        ),
        truncated=truncated,
        refused_prefixes=sorted(set(refused_prefixes)),
        blocked=blocked,
        references_broken=[ReferenceBrokenRead(**item) for item in references.items],
        references_measured=references.measured,
        floor_applies=False,
    )


@router.post(
    "/{project_id}/deletion/execute",
    status_code=202,
    response_model=DeletionExecuteAcceptedResponse,
)
async def execute_criteria_deletion(
    project_id: uuid.UUID,
    body: DeletionExecuteRequest,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Execute a previewed set. Takes a JOB ID, never a criteria body.

    Accepting criteria here would re-resolve them, which is exactly the bug the
    freeze exists to prevent: the set executed would not be the set reviewed.

    Refusals come from :func:`claim_frozen_set` — 404 for a missing or foreign
    job, 409 for one that is not ``previewed`` (which is what stops a
    double-submitted form deleting twice) and 409 on hash drift. The queued
    transition is committed before dispatch so a second request cannot race
    through the same preview.
    """
    from app.worker.tasks import execute_criteria_deletion_task

    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.confirmation_name != project.name:
        # Typed confirmation, matching the project-reset endpoint's guard.
        raise HTTPException(
            status_code=422,
            detail="confirmation_name must match the project name exactly",
        )

    try:
        run_ids = await deletion_job_service.claim_frozen_set(
            db, job_id=body.job_id, project_id=project_id
        )
    except deletion_job_service.FrozenSetRejected as rejected:
        raise HTTPException(
            status_code=rejected.status_code, detail=rejected.detail
        ) from rejected

    await db.commit()
    try:
        execute_criteria_deletion_task.delay(
            str(body.job_id), str(project_id), str(current_user.id)
        )
    except Exception as exc:
        # QUEUED is the durable outbox state. The minute relay republishes it;
        # an ambiguous broker error may also have accepted this delivery, and
        # the worker's QUEUED -> RUNNING claim makes that duplicate harmless.
        logger.warning(
            "criteria_deletion_fast_dispatch_failed",
            job_id=str(body.job_id),
            error_type=type(exc).__name__,
        )

    return DeletionExecuteAcceptedResponse(
        job_id=body.job_id, run_count=len(run_ids)
    )


# ── Retention holds (W22a ACT-05) ────────────────────────────────────────────
#
# A live hold is ABSOLUTE: every purge and deletion path refuses what it
# covers, and the only way past one is an ADMIN releasing it here, with a
# reason. Place is QA_LEAD+, release ADMIN, both JWT-only: neither opts a
# project-bound key in (the role guard refuses it) and the handler refuses a
# user-scoped key too, so a CI credential can never place or release a hold.
# The service stages the hold and its ``settings_audit_log`` row; the handler
# stages the ledger row beside them and commits once.


def _hold_error(status_code: int, code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": HOLD_ERROR_SENTENCES[code]},
    )


def _require_interactive(current_user: User) -> None:
    if credential_kind(current_user) != CREDENTIAL_KIND_JWT:
        raise _hold_error(403, "interactive_login_required")


def _hold_read(hold: Any, *, now: datetime) -> RetentionHoldRead:
    return RetentionHoldRead(
        id=hold.id,
        project_id=hold.project_id,
        entity_type=hold.entity_type,
        entity_id=hold.entity_id,
        entity_label=hold.entity_label,
        reason=hold.reason,
        expires_at=hold.expires_at,
        state=holds.hold_state(hold, now=now),
        placed_by_name=hold.placed_by_name,
        placed_at=hold.placed_at,
        released_at=hold.released_at,
        released_by_name=hold.released_by_name,
        release_reason=hold.release_reason,
    )


@router.get(
    "/{project_id}/retention-holds",
    response_model=RetentionHoldListResponse,
)
async def list_retention_holds(
    project_id: uuid.UUID,
    state: str = Query("live"),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(HOLD_LIST_DEFAULT_LIMIT, ge=1, le=HOLD_LIST_MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """The project's retention holds — any member (and a key bound to the
    project, read only). Writes nothing.

    Every statement carries ``project_id``, so a QUERY ``entity_id`` of another
    project matches nothing (200, empty) — no existence oracle. ``total`` is a
    COUNT of the filter; ``counts`` the per-state totals (state filter ignored).
    """
    now = datetime.now(timezone.utc)
    try:
        items, total = await holds.list_holds(
            db, project_id=project_id, state=state, entity_type=entity_type,
            entity_id=entity_id, limit=limit, now=now,
        )
        counts = await holds.count_holds_by_state(
            db, project_id=project_id, entity_type=entity_type, entity_id=entity_id, now=now,
        )
    except holds.HoldValidationError as exc:
        raise _hold_error(422, exc.code) from exc
    return RetentionHoldListResponse(
        items=[_hold_read(h, now=now) for h in items], total=total, counts=counts,
    )


@router.post(
    "/{project_id}/retention-holds",
    response_model=RetentionHoldRead,
    status_code=201,
)
async def place_retention_hold(
    project_id: uuid.UUID,
    payload: RetentionHoldCreate,
    db: AsyncSession = Depends(get_db),
    # No allow_project_key: a project-bound key gets the role guard's 403.
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Place a retention hold on the project, a release, a run or a compliance pack.

    The BODY ``entity_id`` is resolved ``WHERE id AND project_id``: a release,
    run or pack of another project is the same 404 as a missing one. One
    ``retention_hold.placed`` ledger row and one ``settings_audit_log`` row are
    committed with the hold (a rolled-back place records nothing).
    """
    _require_interactive(current_user)
    now = datetime.now(timezone.utc)
    try:
        hold = await holds.place_hold(
            db,
            project_id=project_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            reason=payload.reason,
            expires_at=payload.expires_at,
            actor=current_user,
            now=now,
        )
    except holds.HoldValidationError as exc:
        raise _hold_error(422, exc.code) from exc
    except holds.HoldEntityNotFound as exc:
        if exc.args and exc.args[0] == "project":
            raise HTTPException(status_code=404, detail="Project not found") from exc
        raise _hold_error(404, "entity_not_found") from exc
    await record_activity(
        db,
        project_id=project_id,
        event_type="retention_hold.placed",
        actor=ActorRef.from_user(current_user),
        entity_id=hold.id,
        entity_label=hold.entity_label,
        release_id=hold.entity_id if hold.entity_type == "release" else None,
        target_type=hold.entity_type,
        target_id=str(hold.entity_id) if hold.entity_id is not None else None,
        context={
            "reason": hold.reason[:200],
            "expires_at": hold.expires_at.isoformat() if hold.expires_at else None,
        },
        source_table="retention_holds",
        source_id=hold.id,
    )
    await db.commit()
    await db.refresh(hold)
    logger.info(
        "retention_hold_place_committed",
        project_id=str(project_id),
        hold_id=str(hold.id),
        entity_type=hold.entity_type,
    )
    return _hold_read(hold, now=now)


@router.post(
    "/{project_id}/retention-holds/{hold_id}/release",
    response_model=RetentionHoldRead,
)
async def release_retention_hold(
    project_id: uuid.UUID,
    hold_id: uuid.UUID,
    payload: RetentionHoldRelease,
    db: AsyncSession = Depends(get_db),
    # No allow_project_key: a project-bound key gets the role guard's 403.
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_project_access()),
):
    """Release a live hold — ADMIN only, with a reason, audited.

    ``hold_id`` is loaded ``WHERE id AND project_id … FOR UPDATE``: another
    project's hold is the same 404. A released or expired hold is 409 (an
    expired hold needs no release). One ``retention_hold.released`` ledger row
    and one ``settings_audit_log`` row are committed with the release.
    """
    _require_interactive(current_user)
    now = datetime.now(timezone.utc)
    try:
        hold = await holds.release_hold(
            db,
            project_id=project_id,
            hold_id=hold_id,
            reason=payload.reason,
            actor=current_user,
            now=now,
        )
    except holds.HoldNotFound as exc:
        raise _hold_error(404, "hold_not_found") from exc
    except holds.HoldNotLive as exc:
        raise _hold_error(409, "hold_not_live") from exc
    except holds.HoldValidationError as exc:
        raise _hold_error(422, exc.code) from exc
    await record_activity(
        db,
        project_id=project_id,
        event_type="retention_hold.released",
        actor=ActorRef.from_user(current_user),
        entity_id=hold.id,
        entity_label=hold.entity_label,
        release_id=hold.entity_id if hold.entity_type == "release" else None,
        target_type=hold.entity_type,
        target_id=str(hold.entity_id) if hold.entity_id is not None else None,
        context={"reason": (hold.release_reason or "")[:200]},
        source_table="retention_holds",
        source_id=hold.id,
    )
    await db.commit()
    await db.refresh(hold)
    logger.info(
        "retention_hold_release_committed",
        project_id=str(project_id),
        hold_id=str(hold.id),
        entity_type=hold.entity_type,
    )
    return _hold_read(hold, now=now)
