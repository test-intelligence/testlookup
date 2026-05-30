"""RAG batch review service — accept/reject generated cases (RAG-10)."""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids
from app.models.postgres import (
    GenerationBatch,
    GenerationCaseSource,
    ManagedTestCase,
    User,
)

logger = structlog.get_logger(__name__)


async def _get_batch_or_404(
    db: AsyncSession,
    batch_id: uuid.UUID,
    user: User,
) -> GenerationBatch:
    result = await db.execute(
        select(GenerationBatch).where(GenerationBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Generation batch not found")

    accessible = await get_accessible_project_ids(db, user)
    if accessible is not None and batch.project_id not in accessible:
        raise HTTPException(status_code=403, detail="You do not have access to this project")
    return batch


async def get_batch_preview(
    db: AsyncSession,
    batch_id: uuid.UUID,
    user: User,
) -> dict:
    """Return batch + all pending generated cases with their citations."""
    batch = await _get_batch_or_404(db, batch_id, user)

    cases_result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.generation_batch_id == batch_id,
        ).order_by(ManagedTestCase.created_at)
    )
    cases = cases_result.scalars().all()

    citations_result = await db.execute(
        select(GenerationCaseSource).where(
            GenerationCaseSource.batch_id == batch_id,
        )
    )
    citations = citations_result.scalars().all()

    return {
        "batch": batch,
        "cases": cases,
        "citations": citations,
    }


async def accept_case(
    db: AsyncSession,
    batch_id: uuid.UUID,
    case_id: uuid.UUID,
    edits: Optional[dict],
    user: User,
) -> ManagedTestCase:
    """Stage accept of a generated case. Handler commits.

    Applies optional edits, flips status to draft, increments the batch
    accept counter atomically with the case mutation.
    """
    batch = await _get_batch_or_404(db, batch_id, user)

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.id == case_id,
            ManagedTestCase.generation_batch_id == batch_id,
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Generated case not found in this batch")

    # Apply edits
    if edits:
        for field, value in edits.items():
            if hasattr(case, field) and field not in ("id", "project_id", "generation_batch_id", "created_at"):
                setattr(case, field, value)

    case.status = "draft"
    batch.cases_accepted = (batch.cases_accepted or 0) + 1

    logger.info("Case accepted: %s (batch=%s)", case_id, batch_id)
    return case


async def reject_case(
    db: AsyncSession,
    batch_id: uuid.UUID,
    case_id: uuid.UUID,
    reason: Optional[str],
    user: User,
) -> None:
    """Stage reject of a generated case. Handler commits."""
    batch = await _get_batch_or_404(db, batch_id, user)

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.id == case_id,
            ManagedTestCase.generation_batch_id == batch_id,
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Generated case not found in this batch")

    case.status = "rejected"
    if reason:
        case.description = f"[Rejected: {reason}]\n\n{case.description or ''}"
    batch.cases_rejected = (batch.cases_rejected or 0) + 1

    logger.info("Case rejected: %s (batch=%s, reason=%s)", case_id, batch_id, reason)


async def bulk_accept(
    db: AsyncSession,
    batch_id: uuid.UUID,
    case_ids: list[uuid.UUID],
    user: User,
) -> list[ManagedTestCase]:
    """Stage acceptance of multiple cases. Handler commits once for the
    whole batch, so bulk-accept costs one transaction instead of N.
    """
    accepted = []
    for cid in case_ids:
        case = await accept_case(db, batch_id, cid, edits=None, user=user)
        accepted.append(case)
    return accepted
