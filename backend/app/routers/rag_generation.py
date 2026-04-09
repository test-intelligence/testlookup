"""RAG grounded test case generation endpoints (RAG-7 through RAG-14)."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    AcceptCaseRequest,
    BatchAcceptRequest,
    GenerationBatchResponse,
    ManagedTestCaseResponse,
    RagGenerateRequest,
    RagGenerateResponse,
    RagRetrieveRequest,
    RagRetrieveResponse,
    RagStatusResponse,
    RejectCaseRequest,
    RequirementCoverageSchema,
    RetrievedChunkSchema,
)

router = APIRouter(prefix="/api/v1/test-management", tags=["RAG Generation"])


# ── RAG-7: Retrieval preview ─────────────────────────────────────────────────


@router.post(
    "/cases/rag-retrieve",
    response_model=RagRetrieveResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def rag_retrieve(
    payload: RagRetrieveRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from app.services.rag_retrieval_service import retrieve_chunks
    chunks = await retrieve_chunks(
        db, payload.project_id, payload.query_text,
        source_ids=payload.source_ids,
        top_k=payload.top_k,
        min_score=payload.min_score,
    )
    return RagRetrieveResponse(
        chunks=[RetrievedChunkSchema(
            vector_id=c.vector_id,
            source_id=c.source_id,
            source_title=c.source_title,
            section_heading=c.section_heading,
            chunk_text=c.chunk_text,
            relevance_score=c.relevance_score,
            requirement_id=c.requirement_id,
        ) for c in chunks],
        total=len(chunks),
    )


# ── RAG-8: Grounded generation ───────────────────────────────────────────────


@router.post(
    "/cases/rag-generate",
    response_model=RagGenerateResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def rag_generate(
    payload: RagGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.services.rag_generation_service import grounded_generate
    result = await grounded_generate(
        db,
        project_id=payload.project_id,
        prompt_text=payload.prompt_text,
        source_ids=payload.source_ids,
        generation_config=payload.generation_config,
        current_user=current_user,
        persist=payload.persist,
    )
    return RagGenerateResponse(
        batch_id=result.batch_id,
        generation_mode=result.generation_mode,
        test_cases=result.test_cases,
        citations=result.citations,
        coverage_summary=result.coverage_summary,
        gaps_noted=result.gaps_noted,
        created_ids=result.created_ids,
    )


# ── RAG-9: Coverage ──────────────────────────────────────────────────────────


@router.get(
    "/batches/{batch_id}/coverage",
    response_model=list[RequirementCoverageSchema],
)
async def get_batch_coverage(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from sqlalchemy import select
    from app.models.postgres import RequirementCoverage
    result = await db.execute(
        select(RequirementCoverage).where(RequirementCoverage.batch_id == batch_id)
    )
    return result.scalars().all()


# ── RAG-10: Batch review ─────────────────────────────────────────────────────


@router.get("/batches/{batch_id}", response_model=GenerationBatchResponse)
async def get_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.services.rag_review_service import get_batch_preview
    preview = await get_batch_preview(db, batch_id, current_user)
    return preview["batch"]


@router.post(
    "/batches/{batch_id}/accept",
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def batch_accept(
    batch_id: uuid.UUID,
    payload: BatchAcceptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.services.rag_review_service import bulk_accept
    cases = await bulk_accept(db, batch_id, payload.case_ids, current_user)
    return [{"id": str(c.id), "title": c.title, "status": c.status} for c in cases]


@router.post(
    "/batches/{batch_id}/cases/{case_id}/accept",
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def accept_case(
    batch_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: AcceptCaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.services.rag_review_service import accept_case as _accept
    case = await _accept(db, batch_id, case_id, payload.edits, current_user)
    return {"id": str(case.id), "title": case.title, "status": case.status}


@router.post(
    "/batches/{batch_id}/cases/{case_id}/reject",
    status_code=204,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def reject_case(
    batch_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: RejectCaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.services.rag_review_service import reject_case as _reject
    await _reject(db, batch_id, case_id, payload.reason, current_user)


# ── RAG-11: Citations / Traceability ─────────────────────────────────────────


@router.get("/cases/{case_id}/citations")
async def get_case_citations(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from sqlalchemy import select
    from app.models.postgres import GenerationCaseSource
    result = await db.execute(
        select(GenerationCaseSource).where(GenerationCaseSource.case_id == case_id)
    )
    citations = result.scalars().all()
    return [
        {
            "source_id": str(c.source_id),
            "chunk_vector_id": c.chunk_vector_id,
            "section_heading": c.section_heading,
            "chunk_text_preview": c.chunk_text_preview,
            "relevance_score": c.relevance_score,
            "is_stale": c.is_stale,
        }
        for c in citations
    ]


# ── RAG-12: Stale cases ──────────────────────────────────────────────────────


@router.get("/cases/stale")
async def list_stale_cases(
    project_id: uuid.UUID = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from app.services.rag_staleness_service import get_stale_cases
    items, total = await get_stale_cases(db, project_id, page, size)
    return {
        "items": [{"id": str(c.id), "title": c.title, "stale_reason": c.stale_reason} for c in items],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post(
    "/cases/{case_id}/dismiss-stale",
    status_code=204,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def dismiss_stale(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from app.services.rag_staleness_service import dismiss_stale as _dismiss
    await _dismiss(db, case_id)
    await db.commit()


# ── RAG-14: Status and eval ──────────────────────────────────────────────────


@router.get("/rag/status", response_model=RagStatusResponse)
async def rag_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from app.services.rag_eval_service import get_rag_status
    return await get_rag_status(db)


@router.get("/batches/{batch_id}/eval")
async def batch_eval(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    from app.services.rag_eval_service import run_eval_for_batch
    return await run_eval_for_batch(db, batch_id)
