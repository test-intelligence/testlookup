"""Project workflow definition API (architecture E3.1)."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_project_access, require_project_role
from app.models.postgres import User, UserRole, WorkflowDefinition
from app.services import workflow_definition_service as svc
from app.services.activity.service import ActorRef, record as record_activity

router = APIRouter(prefix="/api/v1/projects/{project_id}/workflows", tags=["Workflows"])


def _read(item: WorkflowDefinition | dict[str, Any]) -> dict[str, Any]:
    return item if isinstance(item, dict) else svc.serialize(item)


async def _get(
    db: AsyncSession, project_id: uuid.UUID, workflow_id: str, version: Optional[int] = None
) -> WorkflowDefinition | dict[str, Any]:
    try:
        return await svc.get_definition(db, project_id, workflow_id, version)
    except svc.WorkflowNotFound:
        raise HTTPException(status_code=404, detail="Workflow definition not found") from None


def _mutable(workflow_id: str) -> None:
    if svc.is_builtin(workflow_id):
        raise HTTPException(status_code=405, detail="Built-in workflows are read-only")


def _conflict(exc: svc.WorkflowConflict) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


async def _activity(
    db: AsyncSession, *, project_id: uuid.UUID, event_type: str,
    user: User, item: dict[str, Any], source: Optional[str] = None,
) -> None:
    await record_activity(
        db,
        project_id=project_id,
        event_type=event_type,
        actor=ActorRef.from_user(user),
        entity_id=uuid.UUID(item["id"]),
        entity_label=item["name"],
        context={
            "workflow_id": item["workflow_id"],
            "version": item["version"],
            "source": source or "",
        },
    )


@router.get("")
async def list_workflows(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    return {"workflows": await svc.list_definitions(db, project_id)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    project_id: uuid.UUID,
    body: svc.WorkflowBodyV1,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    try:
        row = await svc.create_definition(db, project_id, body, actor_id=current_user.id)
    except svc.WorkflowConflict as exc:
        raise _conflict(exc) from None
    item = svc.serialize(row)
    await _activity(db, project_id=project_id, event_type="workflow.created", user=current_user, item=item)
    await db.commit()
    return item


@router.get("/{workflow_id}")
async def get_workflow(
    project_id: uuid.UUID,
    workflow_id: str,
    version: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    return _read(await _get(db, project_id, workflow_id, version))


@router.put("/{workflow_id}")
async def update_workflow(
    project_id: uuid.UUID,
    workflow_id: str,
    body: svc.WorkflowBodyV1,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    _mutable(workflow_id)
    try:
        row, created_version = await svc.update_definition(
            db, project_id, workflow_id, body, actor_id=current_user.id
        )
    except svc.WorkflowNotFound:
        raise HTTPException(status_code=404, detail="Workflow definition not found") from None
    except svc.WorkflowConflict as exc:
        raise _conflict(exc) from None
    item = svc.serialize(row)
    await _activity(db, project_id=project_id, event_type="workflow.updated", user=current_user, item=item)
    await db.commit()
    item["created_version"] = created_version
    return item


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    project_id: uuid.UUID,
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> Response:
    _mutable(workflow_id)
    row = await _get(db, project_id, workflow_id)
    assert isinstance(row, WorkflowDefinition)
    item = svc.serialize(row)
    try:
        await svc.delete_definition(db, row)
    except svc.WorkflowConflict as exc:
        raise _conflict(exc) from None
    await _activity(db, project_id=project_id, event_type="workflow.deleted", user=current_user, item=item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# activity: none - structural validation is read-only
@router.post("/{workflow_id}/validate")
async def validate_workflow(
    project_id: uuid.UUID,
    workflow_id: str,
    version: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
) -> dict[str, Any]:
    return svc.validation_result(await _get(db, project_id, workflow_id, version))


@router.post("/{workflow_id}/publish")
async def publish_workflow(
    project_id: uuid.UUID,
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    _mutable(workflow_id)
    row = await _get(db, project_id, workflow_id)
    assert isinstance(row, WorkflowDefinition)
    row = await svc.publish_definition(db, row)
    item = svc.serialize(row)
    await _activity(db, project_id=project_id, event_type="workflow.published", user=current_user, item=item)
    await db.commit()
    return item


@router.post("/{workflow_id}/fork", status_code=status.HTTP_201_CREATED)
async def fork_workflow(
    project_id: uuid.UUID,
    workflow_id: str,
    body: svc.WorkflowForkV1,
    version: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
    _lead: User = Depends(require_project_role(UserRole.QA_LEAD)),
) -> dict[str, Any]:
    source = await _get(db, project_id, workflow_id, version)
    try:
        row = await svc.fork_definition(db, project_id, source, body, actor_id=current_user.id)
    except svc.WorkflowConflict as exc:
        raise _conflict(exc) from None
    item = svc.serialize(row)
    await _activity(
        db, project_id=project_id, event_type="workflow.forked",
        user=current_user, item=item, source=workflow_id,
    )
    await db.commit()
    return item
