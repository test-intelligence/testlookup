"""Human review endpoints for the generic agent action ledger."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import AgentActionLedger, User, UserRole
from app.services.agent_action_ledger_service import transition_action

router = APIRouter(prefix="/api/v1/projects", tags=["Agent Actions"])


class ActionTransitionRequest(BaseModel):
    status: Literal["approved", "rejected", "rolled_back"]
    error_code: str | None = Field(default=None, max_length=100)


def _action_response(row: AgentActionLedger) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "test_run_id": str(row.test_run_id) if row.test_run_id else None,
        "pipeline_run_id": str(row.pipeline_run_id) if row.pipeline_run_id else None,
        "action_type": row.action_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "status": row.status,
        "approval_required": bool(row.approval_required),
        "idempotency_key": row.idempotency_key,
        "request_sha256": row.request_sha256,
        "request_payload": row.request_payload,
        "approved_by": str(row.approved_by) if row.approved_by else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "execution_started_at": row.execution_started_at.isoformat() if row.execution_started_at else None,
        "execution_completed_at": row.execution_completed_at.isoformat() if row.execution_completed_at else None,
        "result_payload": row.result_payload,
        "rollback_payload": row.rollback_payload,
        "error_code": row.error_code,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{project_id}/agent-actions")
async def list_agent_actions(
    project_id: uuid.UUID,
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_project_access()),
):
    query = (
        select(AgentActionLedger)
        .where(AgentActionLedger.project_id == project_id)
        .order_by(AgentActionLedger.created_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(AgentActionLedger.status == status)
    rows = (await db.execute(query)).scalars().all()
    return {"items": [_action_response(row) for row in rows], "count": len(rows)}


@router.patch("/{project_id}/agent-actions/{action_id}")
async def transition_agent_action(
    project_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ActionTransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    try:
        row = await transition_action(
            db,
            project_id=project_id,
            action_id=action_id,
            to_status=body.status,
            actor_user_id=current_user.id,
            error_code=body.error_code,
        )
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code == "action_not_found" else 409
        raise HTTPException(status_code=status_code, detail=code) from None
    await db.commit()
    return _action_response(row)
