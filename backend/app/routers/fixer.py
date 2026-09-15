"""Fixer API (Agentic plan AI-2 — the Fixer).

Pinned wire contract the frontend is built against verbatim — do not rename
keys:

* ``GET  /api/v1/projects/{project_id}/fixer/config`` keeps the pinned
  read-only FixerConfig projection for one release. Writes use
  ``/agent-configs/fixer``.
* ``POST /api/v1/projects/{project_id}/fixer/run`` → 202 ``{"fixer_run_id"}``;
  403 disabled / 409 already running / 422 runner-required-for-suggest.
* ``GET  /api/v1/projects/{project_id}/fixer/attempts?limit=&offset=`` →
  ``{"items":[FixAttempt],"total":N}``.
* ``GET  /api/v1/fixer/attempts/{attempt_id}`` → FixAttempt + patch /
  runner_log_digest / ledger_run_id.

Authorization (per the ratchet): the ``{project_id}`` routes depend on
``require_project_access``; the config PUT additionally requires QA_LEAD, the
run POST QA_ENGINEER. The ``{attempt_id}`` route resolves the attempt to its
project and verifies membership on the PROVIDED id (IDOR discipline).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.fixer.state import (
    VALID_FIXER_MODES,
    VALID_RUNNER_TYPES,
    VALID_SCHEDULES,
    is_valid_runner_image,
)
from app.core.deps import (
    _enforce_api_key_project_binding,
    get_current_active_user,
    get_db,
    require_project_access,
    require_project_role,
)
from app.models.postgres import FixAttempt, ProjectMember, User, UserRole
from app.services import fixer_service as svc

router = APIRouter(prefix="/api/v1", tags=["Fixer"])
logger = structlog.get_logger("routers.fixer")


# ── Guard: attempt → project membership (IDOR discipline) ────────────────────


def require_attempt_access():
    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        attempt_id_str = request.path_params.get("attempt_id")
        if not attempt_id_str:
            return current_user
        try:
            attempt_uuid = uuid.UUID(attempt_id_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid attempt ID")
        project_id = (
            await db.execute(
                select(FixAttempt.project_id).where(FixAttempt.id == attempt_uuid)
            )
        ).scalar_one_or_none()
        if not project_id:
            raise HTTPException(status_code=404, detail="Fix attempt not found")
        # A project-bound API key reaches only its own project's attempts,
        # whatever its owner's role: the ADMIN return below used to hand a CI
        # key every tenant's (re-audit N20; compare core/deps.py
        # _make_project_scoped_guard).
        _enforce_api_key_project_binding(current_user, project_id)
        role_value = getattr(current_user.role, "value", current_user.role)
        if str(role_value) == UserRole.ADMIN.value:
            return current_user
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == current_user.id,
                ProjectMember.project_id == project_id,
            )
        )
        if not membership.scalar_one_or_none():
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this attempt's project",
            )
        return current_user

    return _check


# ── Request models (PUT body = FixerConfig sans server fields) ───────────────


class FixerRunner(BaseModel):
    type: str = "none"
    runner_image: Optional[str] = None
    command_template: Optional[str] = None
    workflow_ref: Optional[str] = None

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in VALID_RUNNER_TYPES:
            raise ValueError(f"runner.type must be one of {list(VALID_RUNNER_TYPES)}")
        return v

    @field_validator("runner_image")
    @classmethod
    def _valid_image(cls, v: Optional[str]) -> Optional[str]:
        # Security: the image lands in ``docker run``'s argv — reject anything
        # that isn't a well-formed image reference (e.g. ``--privileged``).
        if v is not None and not is_valid_runner_image(v):
            raise ValueError(
                "runner_image must be a docker image reference "
                "(lowercase repo path, optional :tag / @sha256 digest)"
            )
        return v


class FixerBudgets(BaseModel):
    max_tests_per_run: int = Field(default=3, ge=0, le=1000)
    max_attempts_per_test: int = Field(default=2, ge=0, le=100)
    validation_reruns: int = Field(default=5, ge=1, le=100)
    max_concurrent_open_prs: int = Field(default=2, ge=0, le=100)


class FixerConfigUpdate(BaseModel):
    enabled: bool = False
    mode: str = "shadow"
    runner: FixerRunner = Field(default_factory=FixerRunner)
    test_globs: list[str] = Field(default_factory=lambda: ["tests/**", "**/*.spec.*", "**/*.test.*"])
    budgets: FixerBudgets = Field(default_factory=FixerBudgets)
    schedule: str = "off"

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        if v not in VALID_FIXER_MODES:
            raise ValueError(f"mode must be one of {list(VALID_FIXER_MODES)} (act is reserved)")
        return v

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, v: str) -> str:
        if v not in VALID_SCHEDULES:
            raise ValueError(f"schedule must be one of {list(VALID_SCHEDULES)}")
        return v


# ── Config ───────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/fixer/config", deprecated=True)
async def get_fixer_config(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    """The project's effective FixerConfig (defaults when unconfigured)."""
    return await svc.get_effective_config(db, project_id)


# activity: none — deprecated write alias always returns 405
@router.put("/projects/{project_id}/fixer/config", deprecated=True)
async def put_fixer_config(
    project_id: uuid.UUID,
    body: FixerConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    """Retired write alias; agent-configs is the single writable resource."""
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="fixer/config is read-only; write /agent-configs/fixer",
        headers={"Location": f"/api/v1/projects/{project_id}/agent-configs/fixer"},
    )


# ── Manual run ───────────────────────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/fixer/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_fixer_run(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    # Project-scoped role (not the global one): a project VIEWER with a
    # global QA_ENGINEER role must not be able to trigger runs here —
    # mirrors the config PUT's require_project_role(QA_LEAD).
    _writer: User = Depends(require_project_role(UserRole.QA_ENGINEER)),
) -> dict[str, Any]:
    """Trigger a manual fixer run (project QA_ENGINEER+)."""
    try:
        await svc.gate_fixer_run(db, project_id)
    except svc.FixerDisabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The Fixer is disabled for this project. A QA Lead can enable it via "
                f"PUT /api/v1/projects/{project_id}/fixer/config."
            ),
        )
    except svc.FixerRunnerRequiredForSuggest:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Suggest mode requires a runner (docker or workflow_dispatch). "
                "Configure fixer.runner.type before running in suggest mode."
            ),
        )
    except svc.FixerAlreadyRunning:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A fixer run is already in flight for this project.",
        )

    fixer_run_id = uuid.uuid4()
    if not svc.enqueue_fixer_run(project_id, fixer_run_id, triggered_by="manual"):
        # The gate acquired the dispatch lock; nothing was queued, so free it.
        await svc.release_fixer_run_lock(project_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The fixer run could not be queued (task broker unavailable). Try again shortly.",
        )
    return {"fixer_run_id": str(fixer_run_id)}


# ── Attempts ─────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/fixer/attempts")
async def list_fixer_attempts(
    project_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    """Paged FixAttempt list, newest first."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    total = (
        await db.execute(
            select(func.count(FixAttempt.id)).where(FixAttempt.project_id == project_id)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(FixAttempt)
            .where(FixAttempt.project_id == project_id)
            .order_by(FixAttempt.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "items": [svc.serialize_attempt(r) for r in rows],
        "total": int(total),
    }


@router.get("/fixer/attempts/{attempt_id}")
async def get_fixer_attempt(
    attempt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_attempt_access()),
) -> dict[str, Any]:
    """A single FixAttempt with patch + runner_log_digest + ledger_run_id."""
    row = (
        await db.execute(select(FixAttempt).where(FixAttempt.id == attempt_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Fix attempt not found")
    return svc.serialize_attempt(row, detail=True)
