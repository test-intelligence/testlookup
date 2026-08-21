"""RAG evaluation and rollout status service (RAG-14)."""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import false as sa_false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    GenerationBatch,
    GenerationCaseSource,
    KnowledgeChunk,
    KnowledgeSource,
)

logger = structlog.get_logger(__name__)


async def get_rag_status(
    db: AsyncSession,
    accessible_project_ids: set[uuid.UUID] | None = None,
) -> dict:
    """Return overall RAG feature status and adoption metrics.

    ``enabled`` comes from the same resolver the gate uses, so this cannot
    report the feature on while the endpoints answer 503. The docstring here
    used to claim it "mirrors the logic in require_rag_enabled_async"; it did
    not — it read a Redis key and an AppSetting that the gate never consulted,
    which is how a workspace ended up displaying "Active" for a feature that
    was off everywhere that mattered.
    """
    from app.services.feature_flags import is_enabled

    enabled = await is_enabled("knowledge_rag", db=db)

    # The three counts used to be unscoped ``COUNT(*)`` over every tenant, so
    # any authenticated user — the endpoint carries no role or project guard —
    # learned how many knowledge sources, batches and chunks existed across
    # the whole install, and every call paid for three full-table counts.
    #
    # ``accessible_project_ids`` follows the convention in core/deps: ``None``
    # means ADMIN (or an unscoped internal caller) and sees everything; a set
    # restricts to the caller's projects. An EMPTY set is not the same as
    # ``None`` — it means "a member of nothing", which must count zero rather
    # than silently widen back to the whole install.
    def _scoped(stmt, model):
        if accessible_project_ids is None:
            return stmt
        if not accessible_project_ids:
            return stmt.where(sa_false())
        return stmt.where(model.project_id.in_(accessible_project_ids))

    total_sources = (await db.execute(_scoped(
        select(func.count(KnowledgeSource.id)).where(KnowledgeSource.is_archived.is_(False)),
        KnowledgeSource,
    ))).scalar() or 0

    total_batches = (await db.execute(_scoped(
        select(func.count(GenerationBatch.id)),
        GenerationBatch,
    ))).scalar() or 0

    total_chunks = (await db.execute(_scoped(
        select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.is_active.is_(True)),
        KnowledgeChunk,
    ))).scalar() or 0

    return {
        "enabled": enabled,
        "feature_flag": "knowledge_rag",
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
