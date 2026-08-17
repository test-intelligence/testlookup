"""Knowledge source CRUD, governance, and sync orchestration (RAG-1 / RAG-2 / RAG-3)."""
from __future__ import annotations

import json
import uuid
from typing import Optional
from urllib.parse import urlparse

import structlog

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_accessible_project_ids
from app.models.postgres import (
    AppSetting,
    KnowledgeClassification,
    KnowledgeSource,
    KnowledgeSourceType,
    KnowledgeSyncStatus,
    Project,
    User,
)

logger = structlog.get_logger(__name__)

ALLOWLIST_SETTINGS_KEY = "knowledge_rag.domain_allowlist"

VALID_SOURCE_TYPES = {e.value for e in KnowledgeSourceType}
VALID_CLASSIFICATIONS = {e.value for e in KnowledgeClassification}


# ── Feature gate ──────────────────────────────────────────────────────────────
#
# ONE gate. There used to be three, reading three different stores, and on a
# live deployment they disagreed:
#
#   feature_flags row 'knowledge_rag'    false   <- gated all four endpoints
#   app_settings ai_config.knowledge_rag_enabled  true   <- what the UI toggled
#   env KNOWLEDGE_RAG_ENABLED            false
#
# So /settings/ai displayed "Enable Knowledge RAG — Active" while every
# knowledge-source endpoint answered 503 "Knowledge RAG feature is not enabled.
# Enable it from Settings > AI Configuration" — sending the operator to the
# switch they had already turned on. A third gate (a sync, env-only
# ``require_rag_enabled``) had no callers at all.
#
# ``feature_flags.is_enabled`` is the survivor because the flag service is the
# declared successor here: see LEGACY_ENV_VAR_MAP in services/feature_flags.py,
# which exists "during the one-release cutover from hand-rolled RAG flag to the
# new service". The AppSetting was the hand-rolled flag. It is gone, and
# ``PUT /settings/ai`` now writes the flag row instead, so both settings pages
# drive the same switch.


async def require_rag_enabled_async(db: AsyncSession) -> None:
    """The only Knowledge-RAG gate.

    Resolves the ``knowledge_rag`` flag in the ``feature_flags`` table (seeded
    by migration 0062), falling back to the legacy ``KNOWLEDGE_RAG_ENABLED``
    env var via ``LEGACY_ENV_VAR_MAP``. Anything that needs to *read* the state
    without raising should call ``is_enabled("knowledge_rag", db=db)`` directly
    rather than growing a second resolver.
    """
    from app.services.feature_flags import is_enabled
    if await is_enabled("knowledge_rag", db=db):
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Knowledge RAG feature is not enabled. Enable it from "
            "Settings > AI Configuration."
        ),
    )


# ── Project access ────────────────────────────────────────────────────────────


async def _check_project_access(db: AsyncSession, user: User, project_id: uuid.UUID) -> None:
    accessible = await get_accessible_project_ids(db, user)
    if accessible is not None and project_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def list_sources(
    db: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    source_type: Optional[str] = None,
    sync_status: Optional[str] = None,
    classification: Optional[str] = None,
    include_archived: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    await require_rag_enabled_async(db)
    await _check_project_access(db, user, project_id)

    stmt = select(KnowledgeSource).where(KnowledgeSource.project_id == project_id)
    count_stmt = select(func.count(KnowledgeSource.id)).where(KnowledgeSource.project_id == project_id)

    if not include_archived:
        stmt = stmt.where(KnowledgeSource.is_archived.is_(False))
        count_stmt = count_stmt.where(KnowledgeSource.is_archived.is_(False))
    if source_type:
        stmt = stmt.where(KnowledgeSource.source_type == source_type)
        count_stmt = count_stmt.where(KnowledgeSource.source_type == source_type)
    if sync_status:
        stmt = stmt.where(KnowledgeSource.sync_status == sync_status)
        count_stmt = count_stmt.where(KnowledgeSource.sync_status == sync_status)
    if classification:
        stmt = stmt.where(KnowledgeSource.classification == classification)
        count_stmt = count_stmt.where(KnowledgeSource.classification == classification)

    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(KnowledgeSource.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def create_source(
    db: AsyncSession,
    project_id: uuid.UUID,
    payload: dict,
    user: User,
) -> KnowledgeSource:
    await require_rag_enabled_async(db)
    await _check_project_access(db, user, project_id)
    # Project-existence guard fires AFTER the access check so non-admins
    # never receive a "this project doesn't exist" signal for a project
    # outside their membership (existence-leak via 404-vs-403). Admins
    # bypass access and get a clean 404 for genuinely-missing ids
    # instead of an opaque FK-violation 500 on commit.
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found — refresh the page or pick a different project.",
        )

    source_type = payload["source_type"]
    if source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid source_type: {source_type}")

    classification = payload.get("classification", "internal")
    if classification not in VALID_CLASSIFICATIONS:
        raise HTTPException(status_code=422, detail=f"Invalid classification: {classification}")

    canonical_url = payload["canonical_url"]

    # RAG-3: Enforce domain allowlist for external URLs
    if source_type == KnowledgeSourceType.EXTERNAL_URL.value:
        allowlist = await get_domain_allowlist(db)
        _validate_url_domain(canonical_url, allowlist)

    # Enforce per-project source cap
    count_result = await db.execute(
        select(func.count(KnowledgeSource.id)).where(
            KnowledgeSource.project_id == project_id,
            KnowledgeSource.is_archived.is_(False),
        )
    )
    if (count_result.scalar() or 0) >= settings.KNOWLEDGE_MAX_SOURCES_PER_PROJECT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project source limit ({settings.KNOWLEDGE_MAX_SOURCES_PER_PROJECT}) reached",
        )

    # Check duplicate canonical_url within project
    dup = await db.execute(
        select(KnowledgeSource.id).where(
            KnowledgeSource.project_id == project_id,
            KnowledgeSource.canonical_url == canonical_url,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A source with this URL already exists in the project",
        )

    source = KnowledgeSource(
        project_id=project_id,
        source_type=source_type,
        title=payload["title"],
        canonical_url=canonical_url,
        external_id=payload.get("external_id"),
        owner_id=user.id,
        classification=classification,
        sync_status=KnowledgeSyncStatus.PENDING.value,
    )
    db.add(source)
    await db.flush()  # materialize source.id for the log line and handler refresh
    logger.info(
        "knowledge_source_created_project_type",
        id=source.id,
        project_id=project_id,
        source_type=source_type,
    )
    return source


async def get_source_or_404(
    db: AsyncSession,
    source_id: uuid.UUID,
    user: User,
) -> KnowledgeSource:
    await require_rag_enabled_async(db)
    result = await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    await _check_project_access(db, user, source.project_id)
    return source


async def update_source(
    db: AsyncSession,
    source_id: uuid.UUID,
    payload: dict,
    user: User,
) -> KnowledgeSource:
    """Stage updates to a knowledge source. Handler commits + refreshes."""
    source = await get_source_or_404(db, source_id, user)
    for field, value in payload.items():
        if value is not None:
            if field == "classification" and value not in VALID_CLASSIFICATIONS:
                raise HTTPException(status_code=422, detail=f"Invalid classification: {value}")
            setattr(source, field, value)
    logger.info("knowledge_source_updated", source_id=source_id)
    return source


async def delete_source(
    db: AsyncSession,
    source_id: uuid.UUID,
    user: User,
) -> None:
    """Stage deletion of a knowledge source. Handler commits."""
    source = await get_source_or_404(db, source_id, user)
    await db.delete(source)
    logger.info("knowledge_source_deleted", source_id=source_id)


async def trigger_sync(
    db: AsyncSession,
    source_id: uuid.UUID,
    user: User,
) -> dict:
    """Stage a sync state transition and enqueue the Celery task.

    Mutates ``sync_status`` to SYNCING (or FAILED if enqueue fails) and
    returns the response payload. The handler owns the commit so both the
    status transition and any fallback state land atomically.
    """
    source = await get_source_or_404(db, source_id, user)

    if source.is_archived:
        raise HTTPException(status_code=400, detail="Cannot sync an archived source")

    # RAG-3: Block sync on restricted sources unless user is the owner
    if source.classification == KnowledgeClassification.RESTRICTED.value and source.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the source owner can sync restricted sources")

    source.sync_status = KnowledgeSyncStatus.SYNCING.value
    source.sync_error = None

    # Enqueue Celery task — stub for now, real task in Epic 2 (RAG-4)
    task_id = str(uuid.uuid4())
    try:
        from app.worker.tasks import sync_knowledge_source
        result = sync_knowledge_source.apply_async(
            kwargs={"source_id": str(source_id)},
            task_id=task_id,
        )
        task_id = result.id
    except Exception:
        # Celery not available — mark as failed. The handler's single commit
        # will land this terminal state instead of the SYNCING one above.
        source.sync_status = KnowledgeSyncStatus.FAILED.value
        source.sync_error = "Task queue unavailable"

    logger.info("knowledge_source_sync_triggered_task", source_id=source_id, task_id=task_id)
    return {"source_id": source.id, "task_id": task_id, "sync_status": source.sync_status}


# ── Domain allowlist (RAG-3) ──────────────────────────────────────────────────


async def get_domain_allowlist(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == ALLOWLIST_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        try:
            return json.loads(row)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


async def set_domain_allowlist(db: AsyncSession, domains: list[str]) -> list[str]:
    """Stage domain allowlist changes. Handler commits."""
    cleaned = sorted({d.strip().lower() for d in domains if d.strip()})
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == ALLOWLIST_SETTINGS_KEY)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = json.dumps(cleaned)
    else:
        db.add(AppSetting(key=ALLOWLIST_SETTINGS_KEY, value=json.dumps(cleaned)))
    logger.info("domain_allowlist_updated_domains", cleaned_count=len(cleaned))
    return cleaned


def _validate_url_domain(url: str, allowlist: list[str]) -> None:
    if not allowlist:
        return  # empty allowlist = permissive
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not any(hostname == d or hostname.endswith(f".{d}") for d in allowlist):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Domain '{hostname}' is not in the approved domain allowlist",
        )
