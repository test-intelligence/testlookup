"""RAG staleness detection — mark cases stale when sources change (RAG-12)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    GenerationCaseSource,
    KnowledgeSource,
    ManagedTestCase,
)

logger = structlog.get_logger(__name__)


async def mark_cases_stale_for_source(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> int:
    """
    Called after a successful sync that changed content_hash.
    Marks GenerationCaseSource rows as stale, propagates to ManagedTestCase.is_stale.
    Returns count of cases marked.
    """
    # Get current content hash
    src_result = await db.execute(
        select(KnowledgeSource.content_hash).where(KnowledgeSource.id == source_id)
    )
    current_hash = src_result.scalar_one_or_none()
    if not current_hash:
        return 0

    # Find citation rows where the hash at generation differs from current
    gcs_result = await db.execute(
        select(GenerationCaseSource).where(
            GenerationCaseSource.source_id == source_id,
            GenerationCaseSource.is_stale.is_(False),
            GenerationCaseSource.source_content_hash_at_generation != current_hash,
        )
    )
    stale_rows = gcs_result.scalars().all()

    if not stale_rows:
        return 0

    now = datetime.now(timezone.utc)
    case_ids = set()

    for row in stale_rows:
        row.is_stale = True
        row.stale_detected_at = now
        case_ids.add(row.case_id)

    # Propagate to ManagedTestCase
    if case_ids:
        await db.execute(
            update(ManagedTestCase)
            .where(ManagedTestCase.id.in_(case_ids))
            .values(is_stale=True, stale_reason="Source content changed since generation")
        )

    await db.flush()
    logger.info("marked_cases_stale_for_source", case_ids_count=len(case_ids), source_id=source_id)
    return len(case_ids)


async def check_batch_staleness(
    db: AsyncSession,
    batch_id: uuid.UUID,
) -> list[dict]:
    """Check staleness for all citations in a batch."""
    gcs_result = await db.execute(
        select(GenerationCaseSource).where(
            GenerationCaseSource.batch_id == batch_id,
        )
    )
    citations = gcs_result.scalars().all()

    results = []
    for cit in citations:
        src_result = await db.execute(
            select(KnowledgeSource.content_hash).where(
                KnowledgeSource.id == cit.source_id,
            )
        )
        current_hash = src_result.scalar_one_or_none() or ""
        was_stale = cit.is_stale
        is_now_stale = (
            cit.source_content_hash_at_generation is not None
            and cit.source_content_hash_at_generation != current_hash
        )

        if is_now_stale and not was_stale:
            cit.is_stale = True
            cit.stale_detected_at = datetime.now(timezone.utc)

        results.append({
            "case_id": str(cit.case_id),
            "source_id": str(cit.source_id),
            "was_already_stale": was_stale,
            "marked_stale": is_now_stale and not was_stale,
        })

    await db.flush()
    return results


async def dismiss_stale(
    db: AsyncSession,
    case_id: uuid.UUID,
) -> None:
    """User acknowledges staleness — clear the flag."""
    result = await db.execute(
        select(ManagedTestCase).where(ManagedTestCase.id == case_id)
    )
    case = result.scalar_one_or_none()
    if case:
        case.is_stale = False
        case.stale_reason = None
        await db.flush()


async def get_stale_cases(
    db: AsyncSession,
    project_id: uuid.UUID,
    page: int = 1,
    size: int = 20,
) -> tuple[list, int]:
    """Return paginated stale cases for a project."""
    count_result = await db.execute(
        select(func.count(ManagedTestCase.id)).where(
            ManagedTestCase.project_id == project_id,
            ManagedTestCase.is_stale.is_(True),
        )
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.project_id == project_id,
            ManagedTestCase.is_stale.is_(True),
        )
        .order_by(ManagedTestCase.updated_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = result.scalars().all()
    return items, total
