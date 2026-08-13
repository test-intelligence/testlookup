"""Investigator + agent-governance API (Agentic plan AI-1 / AI-3).

Endpoints (all shapes are the PINNED wire contract the frontend is built
against verbatim — do not rename keys):

* ``POST /api/v1/runs/{run_id}/investigations`` → 202
  ``{"investigation_id": "<uuid>"}`` — 409 when one is already active for
  the run; 403 with an actionable message when the agent policy disables
  the investigator; 429 when max_runs_per_day is exhausted.
* ``GET /api/v1/investigations/{investigation_id}`` → InvestigationDetail.
* ``POST /api/v1/investigations/{investigation_id}/cancel`` → 202
  ``{"status": "cancelling"}`` (cooperative cancel).
* ``GET /api/v1/projects/{project_id}/investigations?limit=&offset=`` →
  ``{"items": [InvestigationSummary], "total": N}``.
* ``GET /api/v1/projects/{project_id}/agent-policies`` →
  ``{"policies": [AgentPolicy]}``; ``PUT .../agent-policies/{agent_id}``
  (QA_LEAD+) → AgentPolicy.
* ``GET /api/v1/projects/{project_id}/agent-runs?agent_id=&limit=&offset=``
  → ``{"items": [AgentRunEntry], "total": N}``.

Authorization: project/run-scoped guards per the ratchet
(``require_run_access`` / ``require_project_access``); the
``{investigation_id}`` routes resolve the investigation to its project and
verify membership on the PROVIDED id (IDOR discipline). Write operations
require QA_ENGINEER+; the policy PUT requires QA_LEAD+.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_project_access,
    require_project_role,
    require_role,
    require_run_access,
)
from app.models.postgres import (
    AgentInvestigation,
    AgentRun,
    ProjectMember,
    TestRun,
    User,
    UserRole,
)
from app.services import agent_investigation_service as svc

router = APIRouter(prefix="/api/v1", tags=["Agent Investigations"])
logger = structlog.get_logger("routers.agent_investigations")


# ── Guard: investigation → project membership (IDOR discipline) ─────────────


def require_investigation_access():
    """Resolve ``{investigation_id}`` to its project and check membership on
    the PROVIDED id (ADMIN bypasses after the row is confirmed to exist)."""

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        investigation_id_str = request.path_params.get("investigation_id")
        if not investigation_id_str:
            return current_user
        try:
            investigation_uuid = uuid.UUID(investigation_id_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid investigation ID",
            )
        result = await db.execute(
            select(AgentInvestigation.project_id).where(
                AgentInvestigation.id == investigation_uuid
            )
        )
        project_id = result.scalar_one_or_none()
        if not project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Investigation not found",
            )
        role = current_user.role
        role_value = getattr(role, "value", role)
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
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this investigation's project",
            )
        return current_user

    return _check


# ── Request models ───────────────────────────────────────────────────────────


class AgentPolicyBudgets(BaseModel):
    max_runs_per_day: int = Field(default=10, ge=0, le=10000)
    max_llm_calls_per_run: int = Field(default=30, ge=0, le=100000)
    max_tokens_per_run: int = Field(default=60000, ge=0, le=100_000_000)
    max_cost_usd_per_run: float = Field(default=5.0, ge=0, le=1_000_000)
    max_seconds_per_run: int = Field(default=300, ge=0, le=86400)
    max_cluster_children_per_run: int = Field(default=1, ge=0, le=20)
    max_cluster_members_per_child: int = Field(default=50, ge=1, le=500)
    max_cluster_child_llm_calls_per_parent: int = Field(default=6, ge=0, le=1000)
    max_cluster_child_tokens_per_parent: int = Field(default=12000, ge=0, le=10_000_000)
    max_cluster_child_cost_usd_per_parent: float = Field(default=2.0, ge=0, le=1_000_000)
    max_cluster_child_seconds_per_parent: int = Field(default=180, ge=0, le=86400)
    max_active_cluster_children_per_project: int = Field(default=2, ge=0, le=20)
    max_cluster_children_per_day: int = Field(default=20, ge=0, le=1000)


class AgentPolicyPromotion(BaseModel):
    # shadow_runs_completed is server-maintained; accepted-but-ignored on PUT.
    shadow_runs_completed: int = Field(default=0, ge=0)
    note: Optional[str] = Field(default=None, max_length=2000)


class AgentPolicyUpdate(BaseModel):
    """PUT body — AgentPolicy sans ``agent_id`` (pinned contract)."""

    enabled: bool = True
    mode: str = "shadow"
    budgets: AgentPolicyBudgets = Field(default_factory=AgentPolicyBudgets)
    promotion: Optional[AgentPolicyPromotion] = None

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in svc.VALID_MODES:
            raise ValueError(f"mode must be one of {list(svc.VALID_MODES)}")
        return v


# ── Investigations ───────────────────────────────────────────────────────────


@router.post(
    "/runs/{run_id}/investigations",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_investigation(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_run_access()),
    _writer: User = Depends(require_role(UserRole.QA_ENGINEER)),
) -> dict[str, Any]:
    """Trigger a manual investigation for a run (QA_ENGINEER+)."""
    run = (
        await db.execute(select(TestRun).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")

    try:
        investigation = await svc.start_investigation(db, run, triggered_by="manual")
    except svc.InvestigationPolicyDisabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The Investigator agent is disabled for this project by its "
                "agent policy. A QA Lead can enable it via "
                f"PUT /api/v1/projects/{run.project_id}/agent-policies/investigator."
            ),
        )
    except svc.InvestigationAlreadyActive as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "An investigation is already running for this run "
                f"(investigation_id={exc.investigation_id})."
            ),
        )
    except svc.InvestigationDailyBudgetExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"The project's max_runs_per_day budget "
                f"({exc.max_runs_per_day}) is exhausted for today. A QA Lead "
                "can raise it via the agent policy."
            ),
        )

    await db.commit()
    svc.enqueue_investigation_task(investigation.id)
    return {"investigation_id": str(investigation.id)}


@router.get("/investigations/{investigation_id}")
async def get_investigation(
    investigation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_investigation_access()),
) -> dict[str, Any]:
    """InvestigationDetail (pinned wire shape). The UI polls this."""
    investigation = (
        await db.execute(
            select(AgentInvestigation).where(AgentInvestigation.id == investigation_id)
        )
    ).scalar_one_or_none()
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return svc.serialize_investigation_detail(investigation)


@router.post(
    "/investigations/{investigation_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_investigation(
    investigation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_investigation_access()),
    _writer: User = Depends(require_role(UserRole.QA_ENGINEER)),
) -> dict[str, Any]:
    """Request cooperative cancellation (QA_ENGINEER+). The workflow checks
    the flag between nodes and finalizes the row as ``cancelled``."""
    investigation = (
        await db.execute(
            select(AgentInvestigation).where(AgentInvestigation.id == investigation_id)
        )
    ).scalar_one_or_none()
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    if investigation.status not in svc.INVESTIGATION_ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Investigation is already {investigation.status} — nothing to cancel.",
        )
    actor = getattr(current_user, "username", None) or getattr(current_user, "email", None)
    await svc.request_cancel(db, investigation, cancelled_by=actor)
    await db.commit()
    return {"status": "cancelling"}


@router.get("/projects/{project_id}/investigations")
async def list_investigations(
    project_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    """Paged InvestigationSummary list, newest first."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    total = (
        await db.execute(
            select(func.count(AgentInvestigation.id)).where(
                AgentInvestigation.project_id == project_id
            )
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(AgentInvestigation, TestRun.build_number)
            .join(TestRun, TestRun.id == AgentInvestigation.run_id)
            .where(AgentInvestigation.project_id == project_id)
            .order_by(AgentInvestigation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "items": [
            svc.serialize_investigation_summary(row[0], row[1]) for row in rows
        ],
        "total": int(total),
    }


# ── Agent policies ───────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/agent-policies")
async def list_agent_policies(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    """Every known agent's effective policy (defaults when no row exists)."""
    policies = []
    for agent_id in svc.KNOWN_AGENT_IDS:
        row = await svc.get_policy_row(db, project_id, agent_id)
        policies.append(svc.serialize_policy(agent_id, row))
    return {"policies": policies}


@router.put("/projects/{project_id}/agent-policies/{agent_id}")
async def update_agent_policy(
    project_id: uuid.UUID,
    agent_id: str,
    body: AgentPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    """Upsert a project's agent policy (QA_LEAD+). ``shadow_runs_completed``
    is server-maintained and ignored on write."""
    if agent_id not in svc.KNOWN_AGENT_IDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown agent_id {agent_id!r} — known agents: {list(svc.KNOWN_AGENT_IDS)}",
        )
    row = await svc.upsert_policy(
        db,
        project_id,
        agent_id,
        enabled=body.enabled,
        mode=body.mode,
        budgets=body.budgets.model_dump(),
        promotion_note=body.promotion.note if body.promotion else None,
    )
    await db.commit()
    return svc.serialize_policy(agent_id, row)


# ── Agent-runs ledger ────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/agent-runs")
async def list_agent_runs(
    project_id: uuid.UUID,
    agent_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    """Paged AgentRunEntry ledger, newest first (AI-3)."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    filters = [AgentRun.project_id == project_id]
    if agent_id:
        filters.append(AgentRun.agent_id == agent_id)
    total = (
        await db.execute(select(func.count(AgentRun.id)).where(*filters))
    ).scalar() or 0
    rows = (
        await db.execute(
            select(AgentRun)
            .where(*filters)
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "items": [svc.serialize_agent_run(row) for row in rows],
        "total": int(total),
    }
