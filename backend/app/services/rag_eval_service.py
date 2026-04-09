"""RAG evaluation and rollout status service (RAG-14)."""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import (
    GenerationBatch,
    GenerationCaseSource,
    KnowledgeChunk,
    KnowledgeSource,
)

logger = structlog.get_logger(__name__)


async def get_rag_status(db: AsyncSession) -> dict:
    """Return overall RAG feature status and adoption metrics.

    Checks Redis cache → DB AppSetting → env-var fallback to determine
    whether RAG is enabled (mirrors the logic in require_rag_enabled_async).
    """
    # Resolve effective enabled state: Redis → DB → env var
    enabled: bool | None = None
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cached = await redis.get("config:knowledge_rag_enabled")
        if cached is not None:
            val = cached.decode() if isinstance(cached, bytes) else str(cached)
            enabled = val == "1"
    except Exception:
        pass

    if enabled is None:
        from app.services.knowledge_source_service import _is_rag_enabled_from_db
        enabled = await _is_rag_enabled_from_db(db)

    total_sources = (await db.execute(
        select(func.count(KnowledgeSource.id)).where(KnowledgeSource.is_archived.is_(False))
    )).scalar() or 0

    total_batches = (await db.execute(
        select(func.count(GenerationBatch.id))
    )).scalar() or 0

    total_chunks = (await db.execute(
        select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.is_active.is_(True))
    )).scalar() or 0

    return {
        "enabled": enabled,
        "feature_flag": "KNOWLEDGE_RAG_ENABLED",
        "total_sources": total_sources,
        "total_batches": total_batches,
        "total_chunks": total_chunks,
    }


async def run_eval_for_batch(
    db: AsyncSession,
    batch_id: uuid.UUID,
) -> dict:
    """
    Compute quality metrics for a generation batch:
    - citation_presence: % of cases with at least one citation
    - acceptance_rate: cases_accepted / cases_generated
    - coverage_score: from batch.coverage_score
    """
    result = await db.execute(
        select(GenerationBatch).where(GenerationBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return {"error": "Batch not found"}

    generated = batch.cases_generated or 0
    accepted = batch.cases_accepted or 0
    rejected = batch.cases_rejected or 0

    # Citation presence
    citation_result = await db.execute(
        select(func.count(func.distinct(GenerationCaseSource.case_id))).where(
            GenerationCaseSource.batch_id == batch_id,
        )
    )
    cases_with_citations = citation_result.scalar() or 0

    return {
        "batch_id": str(batch_id),
        "generation_mode": batch.generation_mode,
        "cases_generated": generated,
        "cases_accepted": accepted,
        "cases_rejected": rejected,
        "acceptance_rate": round(accepted / generated * 100, 1) if generated > 0 else 0,
        "citation_presence_rate": round(cases_with_citations / generated * 100, 1) if generated > 0 else 0,
        "coverage_score": batch.coverage_score,
        "status": batch.status,
    }
