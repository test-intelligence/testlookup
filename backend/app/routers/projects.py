"""Project CRUD endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user, require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import Project, ProjectMember, User, UserRole
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


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    stmt = select(Project).where(Project.is_active.is_(True)).order_by(Project.name)
    # Tenant isolation: non-admin users only see projects they belong to
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        stmt = stmt.where(Project.id.in_(accessible))
    result = await db.execute(stmt)
    return result.scalars().all()


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
    project = Project(**payload.model_dump())
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

    await db.commit()
    await db.refresh(project)

    # Invalidate the creator's cached membership set so the new project
    # shows up in their accessible-project queries immediately instead of
    # after the 5-minute Redis TTL.
    from app.core.deps import invalidate_membership_cache
    await invalidate_membership_cache(current_user.id)

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
        Depends(require_role(UserRole.QA_LEAD)),
        Depends(require_project_access()),
    ],
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
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

    for field, value in updates.items():
        setattr(project, field, value)

    await db.commit()
    await db.refresh(project)
    return project


@router.delete(
    "/{project_id}",
    status_code=204,
    dependencies=[
        Depends(require_role(UserRole.QA_LEAD)),
        Depends(require_project_access()),
    ],
)
async def delete_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.is_active = False
    await db.commit()


@router.post(
    "/{project_id}/reset",
    response_model=ProjectResetResponse,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
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
