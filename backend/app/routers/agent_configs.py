"""Per-project agent configuration API (architecture E4.1).

* ``GET /api/v1/projects/{project_id}/agent-configs`` -- every configurable
  agent's configuration, defaults included.
* ``GET /api/v1/projects/{project_id}/agent-configs/{agent_id}``
* ``PUT /api/v1/projects/{project_id}/agent-configs/{agent_id}`` (QA_LEAD+) --
  replaces the document and bumps ``config_version``.

A response carries ``source`` (``default`` when no row exists, with
``config_version`` 0), ``valid`` and ``errors`` (a stored row can stop
validating when an environment ceiling is lowered), and the ``config``
document itself.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_project_access, require_project_role
from app.models.postgres import User, UserRole
from app.services import agent_config_service as svc
from app.services.activity.service import ActorRef, record as record_activity
from app.services.ai_config_resolver import get_effective_ai_config

router = APIRouter(prefix="/api/v1", tags=["Agent Configs"])
logger = structlog.get_logger("routers.agent_configs")


def _require_known(agent_id: str) -> None:
    if agent_id not in svc.configurable_agents():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown agent_id {agent_id!r}; configurable agents: {list(svc.configurable_agents())}",
        )


@router.get("/projects/{project_id}/agent-configs")
async def list_agent_configs(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    """Every configurable agent, with the defaults for agents nobody has configured.

    ``tools`` maps every agent tool to the permission it needs (E4.3), so a
    client can offer tools that are not in an allowlist today.
    """
    rows = await svc.list_config_rows(db, project_id)
    return {
        "configs": [svc.serialize(agent_id, rows.get(agent_id)) for agent_id in sorted(svc.configurable_capabilities())],
        "tools": dict(sorted(svc.AGENT_TOOL_PERMISSIONS.items())),
    }


@router.get("/projects/{project_id}/agent-configs/{agent_id}")
async def get_agent_config(
    project_id: uuid.UUID,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    _require_known(agent_id)
    return svc.serialize(agent_id, await svc.get_config_row(db, project_id, agent_id))


@router.put("/projects/{project_id}/agent-configs/{agent_id}")
async def put_agent_config(
    project_id: uuid.UUID,
    agent_id: str,
    body: svc.AgentConfigV1,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    """Replace this project's configuration of one agent (QA_LEAD+)."""
    _require_known(agent_id)
    if body.agent_id != agent_id:
        raise HTTPException(
            status_code=422,
            detail=f"body agent_id {body.agent_id!r} does not match the path agent_id {agent_id!r}",
        )
    ai_config = await get_effective_ai_config()
    errors = svc.provider_environment_errors(body, offline=bool(ai_config.get("offline_mode", True)))
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    before = await svc.get_config_row(db, project_id, agent_id)
    before_doc = svc.serialize(agent_id, before)["config"]
    try:
        before_config = svc.AgentConfigV1.model_validate(before_doc)
    except ValidationError:
        # A lowered environment ceiling can invalidate a stored row. Preserve
        # its valid model block for G2 while the PUT repairs another field.
        try:
            before_model = svc.ModelConfig.model_validate(before_doc.get("model", {}))
        except ValidationError:
            before_model = body.model
        before_config = body.model_copy(update={"model": before_model})
    if agent_id not in svc.COMPATIBILITY_AGENT_IDS:
        from app.services.tier_comparison_service import (
            TierComparisonRejected,
            enforce_config_tier_gate,
        )

        try:
            await enforce_config_tier_gate(
                db,
                project_id=project_id,
                agent_id=agent_id,
                before=before_config,
                after=body,
            )
        except TierComparisonRejected as exc:
            raise HTTPException(status_code=422, detail=exc.report) from None
    row = await svc.put_config(db, project_id, body, updated_by=getattr(current_user, "id", None))
    after = svc.serialize(agent_id, row)
    changed = sorted(key for key in after["config"] if after["config"].get(key) != before_doc.get(key))
    await record_activity(
        db,
        project_id=project_id,
        event_type="agent_config.updated",
        actor=ActorRef.from_user(current_user),
        entity_id=project_id,
        entity_label=agent_id,
        changed_fields=changed,
        context={
            "agent_id": agent_id,
            "config_version": after["config_version"],
            "changed": ", ".join(changed) or "nothing",
        },
    )
    await db.commit()
    logger.info(
        "agent_config_updated",
        project_id=str(project_id),
        agent_id=agent_id,
        config_version=after["config_version"],
        changed=changed,
    )
    return after
