"""Knowledge source registry, connector test, and governance endpoints (RAG-1 / RAG-2 / RAG-3)."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_knowledge_source_access,
    require_role,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    ConnectorConfigTestRequest,
    ConnectorTestResult,
    KnowledgeDomainAllowlistUpdate,
    KnowledgeSourceCreate,
    KnowledgeSourceFreshnessResponse,
    KnowledgeSourceListResponse,
    KnowledgeSourceResponse,
    KnowledgeSourceSyncResponse,
    KnowledgeSourceUpdate,
    KnowledgeSyncEventResponse,
)
from app.services import knowledge_source_service as svc

router = APIRouter(prefix="/api/v1/knowledge-sources", tags=["Knowledge Sources"])


# ── RAG-1: Source CRUD ────────────────────────────────────────────────────────


@router.get("", response_model=KnowledgeSourceListResponse)
async def list_knowledge_sources(
    project_id: uuid.UUID = Query(...),
    source_type: Optional[str] = None,
    sync_status: Optional[str] = None,
    classification: Optional[str] = None,
    include_archived: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await svc.list_sources(
        db, current_user, project_id,
        source_type=source_type,
        sync_status=sync_status,
        classification=classification,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=KnowledgeSourceResponse,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def create_knowledge_source(
    payload: KnowledgeSourceCreate,
    project_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await svc.create_source(db, project_id, payload.model_dump(), current_user)


@router.get("/{source_id}", response_model=KnowledgeSourceResponse)
async def get_knowledge_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_knowledge_source_access()),
):
    return await svc.get_source_or_404(db, source_id, current_user)


@router.patch(
    "/{source_id}",
    response_model=KnowledgeSourceResponse,
    dependencies=[
        Depends(require_role(UserRole.QA_ENGINEER)),
        Depends(require_knowledge_source_access()),
    ],
)
async def update_knowledge_source(
    source_id: uuid.UUID,
    payload: KnowledgeSourceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await svc.update_source(db, source_id, payload.model_dump(exclude_none=True), current_user)


@router.delete(
    "/{source_id}",
    status_code=204,
    dependencies=[
        Depends(require_role(UserRole.QA_ENGINEER)),
        Depends(require_knowledge_source_access()),
    ],
)
async def delete_knowledge_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await svc.delete_source(db, source_id, current_user)


# ── RAG-1: Sync trigger ──────────────────────────────────────────────────────


@router.post(
    "/{source_id}/sync",
    response_model=KnowledgeSourceSyncResponse,
    dependencies=[
        Depends(require_role(UserRole.QA_ENGINEER)),
        Depends(require_knowledge_source_access()),
    ],
)
async def trigger_sync(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await svc.trigger_sync(db, source_id, current_user)


# ── RAG-4: Sync history ───────────────────────────────────────────────────────


@router.get("/{source_id}/sync-history", response_model=list[KnowledgeSyncEventResponse])
async def get_sync_history(
    source_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_knowledge_source_access()),
):
    await svc.require_rag_enabled_async(db)
    await svc.get_source_or_404(db, source_id, current_user)
    from app.services.knowledge_sync_service import get_sync_history as _get_history
    return await _get_history(db, source_id, limit=limit)


# ── RAG-6: Freshness ─────────────────────────────────────────────────────────


@router.get("/{source_id}/freshness", response_model=KnowledgeSourceFreshnessResponse)
async def get_freshness(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_knowledge_source_access()),
):
    await svc.require_rag_enabled_async(db)
    source = await svc.get_source_or_404(db, source_id, current_user)
    from app.services.knowledge_sync_service import get_freshness as _get_freshness
    return await _get_freshness(db, source)


# ── RAG-2: Connector test ────────────────────────────────────────────────────


@router.post(
    "/connectors/test",
    response_model=ConnectorTestResult,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def test_connector(
    payload: ConnectorConfigTestRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    await svc.require_rag_enabled_async(db)
    from app.services.connectors.registry import get_connector
    try:
        connector = get_connector(payload.source_type)
    except ValueError as e:
        return ConnectorTestResult(success=False, error=str(e))
    result = await connector.test_connection()
    return ConnectorTestResult(**result)


# ── RAG-3: Domain allowlist ───────────────────────────────────────────────────


@router.get("/governance/domain-allowlist")
async def get_domain_allowlist(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    await svc.require_rag_enabled_async(db)
    domains = await svc.get_domain_allowlist(db)
    return {"domains": domains}


@router.put(
    "/governance/domain-allowlist",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_domain_allowlist(
    payload: KnowledgeDomainAllowlistUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    domains = await svc.set_domain_allowlist(db, payload.domains)
    return {"domains": domains}
