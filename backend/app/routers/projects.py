"""Project CRUD endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user, require_project_access, require_role
from app.db.postgres import get_db
from app.services.activity.service import ActorRef, record as record_activity
from app.models.postgres import (
    Project,
    ProjectMember,
    ProjectRetentionPolicy,
    User,
    UserRole,
)
from app.models.schemas import (
    ProjectCreate,
    ProjectResetRequest,
    ProjectResetResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.project_reset_service import (
    ConfirmationMismatch,
    ProjectNotFound,
    reset_project,
)

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


def _retention_status(enabled: bool | None) -> str:
    """Map the LEFT-JOINed policy flag onto the three-state posture.

    ``None`` is not "disabled": the column is NOT NULL, so a NULL can only
    come from the outer join finding no row — i.e. retention was never
    configured for this project at all. That is the population S1's nudge
    targets, and folding it into "disabled" would make it invisible.
    """
    if enabled is None:
        return "unconfigured"
    return "enabled" if enabled else "disabled"


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # S1: the retention posture rides along on a LEFT JOIN rather than a
    # per-project lookup. `enabled` is NOT NULL on the policy table, so a NULL
    # here means exactly one thing — no policy row has ever existed — which is
    # the state the activation nudge exists to catch.
    stmt = (
        select(Project, ProjectRetentionPolicy.enabled)
        .outerjoin(
            ProjectRetentionPolicy,
            ProjectRetentionPolicy.project_id == Project.id,
        )
        .where(Project.is_active.is_(True))
        .order_by(Project.name)
    )
    # Tenant isolation: non-admin users only see projects they belong to
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        stmt = stmt.where(Project.id.in_(accessible))
    result = await db.execute(stmt)
    return [
        ProjectResponse.model_validate(project).model_copy(
            update={"retention_status": _retention_status(enabled)}
        )
        for project, enabled in result.all()
    ]


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.QA_LEAD))],
)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    existing = await db.execute(select(Project).where(Project.slug == payload.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Project with slug '{payload.slug}' already exists")
    # Identity assigned at construction so the row is addressable before a
    # flush — the activity event is staged in this same transaction.
    project = Project(id=uuid.uuid4(), **payload.model_dump())
    db.add(project)
    await db.flush()

    # Auto-add the creator as a member so they can access the project
    # without an admin having to add them manually. Creator inherits their
    # global role (ADMIN stays ADMIN, QA_LEAD stays QA_LEAD, etc.).
    db.add(ProjectMember(
        user_id=current_user.id,
        project_id=project.id,
        role=current_user.role,
    ))

    # 0079: validate default_qa_lead_user_id, if provided, is permitted to
    # own suites. Run AFTER the creator's ProjectMember is staged so the
    # creator themselves (a QA_LEAD or higher per the dependency above)
    # can be set as default in a single create-then-default flow.
    if payload.default_qa_lead_user_id is not None:
        await db.flush()
        from app.services.suite_review_service import assert_user_is_qa_lead_on_project
        await assert_user_is_qa_lead_on_project(
            db, payload.default_qa_lead_user_id, project.id,
        )
    else:
        # Auto-provision a synthetic QA-lead user so the project has a
        # deterministic default failure assignee from day one. Without
        # this, every fresh project's /my-failures inbox would stay
        # empty until an admin manually configures an owner.
        from app.services.default_qa_lead_service import ensure_default_qa_lead
        await db.flush()
        await ensure_default_qa_lead(db, project)

    # Machine accounts (the MCP server) see projects through the same
    # membership mechanism as everyone else, so a project created after they
    # were provisioned would otherwise be invisible to them forever.
    from app.services.service_account_service import enroll_service_accounts_in_project
    enrolled_service_accounts = await enroll_service_accounts_in_project(db, project.id)

    # Every project must have exactly one active release at every moment
    # (migration 0150) — it is where a run with no release from the client
    # lands. Created in THIS transaction on purpose: doing it lazily at first
    # ingest would make the invariant true-after-the-first-repair rather than
    # true from t=0, and the reconciliation sweep could not then distinguish
    # "never provisioned" from "something broke it".
    from app.services.release_lifecycle_service import (
        ensure_active_release_for_new_project,
    )
    await ensure_active_release_for_new_project(db, project)

    # Epic ACT — the ledger's own first row for this project. Staged on this
    # session so it shares the create transaction: if the project does not
    # exist, neither does the event saying it was created.
    await record_activity(
        db,
        project_id=project.id,
        event_type="project.created",
        actor=ActorRef.from_user(current_user),
        entity_id=project.id,
        entity_label=project.name,
        context={"slug": project.slug},
    )

    await db.commit()
    await db.refresh(project)

    # Invalidate the creator's cached membership set so the new project
    # shows up in their accessible-project queries immediately instead of
    # after the 5-minute Redis TTL. Service accounts need the same treatment
    # for the same reason — a stale "no projects" cache is indistinguishable
    # from never having been enrolled.
    from app.core.deps import invalidate_membership_cache
    await invalidate_membership_cache(current_user.id)
    for account_id in enrolled_service_accounts:
        await invalidate_membership_cache(account_id)

    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[
        Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
        Depends(require_project_access()),
    ],
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update a project's attributes. Only non-None fields are applied. Requires QA_LEAD or higher."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    updates = payload.model_dump(exclude_none=True)

    # 0079: validate default_qa_lead_user_id, if changing, before persisting.
    # ``exclude_none=True`` means the key is only present when the caller
    # explicitly sent a UUID — passing ``null`` to clear is not possible via
    # this update path (current schema convention; revisit if clearing is
    # needed).
    if "default_qa_lead_user_id" in updates:
        from app.services.suite_review_service import assert_user_is_qa_lead_on_project
        await assert_user_is_qa_lead_on_project(
            db, updates["default_qa_lead_user_id"], project.id,
        )

    before = {field: getattr(project, field, None) for field in updates}
    # What actually DIFFERS, not merely what was sent. `if updates:` was true
    # for a PUT that re-sent the current value, so a no-op recorded a
    # "settings changed" row whose own diff said changed_fields: [] while the
    # summary named the field.
    changed = {f: v for f, v in updates.items() if before.get(f) != v}

    for field, value in updates.items():
        setattr(project, field, value)

    # Epic ACT. Staged on THIS session, before the commit, so the ledger row
    # and the change it describes land together or not at all — a
    # "settings changed" row for an update that rolled back would be a lie.
    if changed:
        await record_activity(
            db,
            project_id=project.id,
            event_type="project.updated",
            actor=ActorRef.from_user(current_user),
            entity_id=project.id,
            entity_label=project.name,
            before={f: before[f] for f in changed},
            after=changed,
            context={"changed": ", ".join(sorted(changed))},
        )

    await db.commit()
    await db.refresh(project)
    return project


@router.delete(
    "/{project_id}",
    status_code=204,
    dependencies=[
        Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
        Depends(require_project_access()),
    ],
)
async def delete_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Soft-delete a project and revoke everything that grants access to it.

    The soft delete used to be the whole handler, which meant "deleted"
    revoked nothing: the auto-provisioned QA-lead account stayed live, and
    any API key bound to the project kept authenticating and kept accepting
    ingestion. See ``project_access_revocation_service`` for the measurements.
    """
    from app.services.project_access_revocation_service import (
        revoke_project_credentials,
    )

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.is_active = False
    await revoke_project_credentials(db, project)
    await db.commit()
    # VIZ-212: all-projects analytics filter on is_active, so they must drop
    # this project's rows now rather than at TTL. The project bump also bumps
    # the all-projects epoch.
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(project_id)


@router.post(
    "/{project_id}/reset",
    response_model=ProjectResetResponse,
    dependencies=[
        # N20: a project-bound key may reset its own project (CI);
        # require_project_access() below refuses it every other project.
        Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
        # Project-scope guard alongside the ADMIN role gate. An unbound ADMIN
        # bypasses its membership check, so for them it changes nothing; for a
        # project-bound key it is the check that keeps the key inside its own
        # project. It also ties the route to its ``{project_id}`` scope so the
        # architectural authorization ratchet
        # (test_architectural_authorization.py) recognises it as guarded.
        Depends(require_project_access()),
    ],
)
async def reset_project_data(
    project_id: uuid.UUID,
    payload: ProjectResetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Wipe project-scoped data. ADMIN-only.

    ``mode="runs"`` deletes every TestRun in the project (cascades to
    test_cases, ai_analyses, failure_clusters, agent_pipeline_runs,
    release_decisions, etc.) but keeps the test_suites and canonical
    catalog so the project's authored structure survives.

    ``mode="full"`` runs the ``runs`` deletes and then also wipes
    test_suites, canonical_test_cases, releases, release_gate_policies,
    perf_baselines, flaky_quarantine_requests, managed_test_cases,
    test_plans, test_strategies, and knowledge_sources. The Project
    row, its members, API keys, and AI/SSO config survive.

    Two-step confirmation: ``payload.confirmation_name`` must equal
    the project's ``name`` exactly (case-sensitive). A mismatch
    returns 422 and nothing is deleted.
    """
    try:
        result = await reset_project(
            db,
            project_id=project_id,
            mode=payload.mode,
            confirmation_name=payload.confirmation_name,
            actor_id=current_user.id,
            actor_name=current_user.full_name or current_user.email,
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfirmationMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@router.post(
    "/{project_id}/default-qa-lead/reset-password",
    dependencies=[Depends(require_project_access())],
)
async def reset_default_qa_lead_password_endpoint(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Reset the project's auto-provisioned QA-lead account password.

    Any user with access to the project can call this — the account is a
    shared, per-project triage inbox, not a personal user, so rotating
    its password is a routine project-admin task.

    Idempotently provisions the QA-lead user first if it doesn't exist
    yet (covers projects created before this feature shipped). Returns
    the new password so the operator can hand it off; the response is
    not persisted anywhere else.
    """
    from app.services.default_qa_lead_service import (
        reset_default_qa_lead_password,
    )

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    user, password = await reset_default_qa_lead_password(db, project)
    await db.commit()
    return {
        "user_id": str(user.id),
        "email": user.email,
        "username": user.username,
        "password": password,
        "project_id": str(project.id),
        "actor_id": str(current_user.id),
    }
