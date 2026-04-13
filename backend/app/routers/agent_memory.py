"""Agent Memory router — unified memory queries and similarity recall (P3)."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role, require_run_access
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    AgentMemoryEntryResponse,
    AgentMemoryListResponse,
    MemoryTimelineResponse,
    SimilarMemoryRecallRequest,
    SimilarMemoryRecallResponse,
    SimilarMemoryResponse,
)
from app.services import agent_memory_service

logger = logging.getLogger("routers.agent_memory")

router = APIRouter(prefix="/api/v1/memory", tags=["Agent Memory"])


# ── Project-scoped memory list ───────────────────────────────────────────────


@router.get("/projects/{project_id}", response_model=AgentMemoryListResponse)
async def list_project_memories(
    project_id: uuid.UUID,
    entity_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_role(UserRole.VIEWER)),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """List memory entries for a project, optionally filtered by entity type."""
    entries, total = await agent_memory_service.list_project_memories(
        db, project_id, entity_type=entity_type, page=page, size=size,
    )
    return AgentMemoryListResponse(
        total=total,
        items=[AgentMemoryEntryResponse.model_validate(e) for e in entries],
        page=page,
        size=size,
    )


# ── Run memory timeline ─────────────────────────────────────────────────────


@router.get("/runs/{run_id}/timeline", response_model=MemoryTimelineResponse)
async def get_run_memory_timeline(
    run_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.VIEWER)),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Get all memory entries for a run, grouped by entity type."""
    entries_by_type, total = await agent_memory_service.get_run_memory_timeline(
        db, run_id,
    )

    # Serialize grouped entries
    serialized: dict[str, list[dict]] = {}
    project_id: uuid.UUID | None = None
    for etype, entries in entries_by_type.items():
        serialized[etype] = [
            AgentMemoryEntryResponse.model_validate(e).model_dump(mode="json")
            for e in entries
        ]
        if entries and not project_id:
            project_id = entries[0].project_id

    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No memory entries found for this run",
        )

    return MemoryTimelineResponse(
        run_id=run_id,
        project_id=project_id,
        entries_by_type=serialized,
        total_entries=total,
    )


# ── Semantic similarity recall ───────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/recall",
    response_model=SimilarMemoryRecallResponse,
)
async def recall_similar_memories(
    project_id: uuid.UUID,
    body: SimilarMemoryRecallRequest,
    current_user: User = Depends(require_role(UserRole.VIEWER)),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Find historically similar failures in the same project via semantic search."""
    matches = await agent_memory_service.recall_similar(
        db,
        project_id=project_id,
        error_signature=body.error_signature,
        entity_type=body.entity_type,
        limit=body.limit,
    )

    results = [
        SimilarMemoryResponse(
            memory=AgentMemoryEntryResponse.model_validate(m["memory"]),
            similarity=m["similarity"],
        )
        for m in matches
    ]

    return SimilarMemoryRecallResponse(
        query_signature=body.error_signature[:200],
        results=results,
        total_found=len(results),
    )
