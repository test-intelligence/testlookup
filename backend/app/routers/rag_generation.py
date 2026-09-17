"""RAG grounded test case generation endpoints (RAG-7 through RAG-14)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_generation_batch_access,
    require_role,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    AcceptCaseRequest,
    BatchAcceptRequest,
    GenerationBatchResponse,
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


async def _require_case_project_access(db: AsyncSession, current_user, case_id: uuid.UUID):
    """Fetch a ManagedTestCase and verify the caller can access its project.

    Case-id RAG endpoints (citations, dismiss-stale) fetch by PK only; without
    this the case's project_id is never access-checked, letting any
    authenticated user read/mutate another tenant's generated cases.
    """
    from app.models.postgres import ManagedTestCase

    case = await db.get(ManagedTestCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    # Raises 403 when the (non-admin) caller isn't a member of the project.
    await resolve_project_scope(db, current_user, str(case.project_id))
    return case


# ── RAG-7: Retrieval preview ─────────────────────────────────────────────────


@router.post(
    "/cases/rag-retrieve",
    response_model=RagRetrieveResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def rag_retrieve(
    payload: RagRetrieveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Tenant isolation: verify access to the requested project (403 if the
    # non-admin caller isn't a member) before retrieving its RAG chunks.
    await resolve_project_scope(db, current_user, str(payload.project_id))
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
            canonical_url=c.canonical_url,
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
    # Tenant isolation: a QA_ENGINEER must not generate/persist test cases into
    # — or read RAG evidence from — a project they can't access. Verify before
    # the (write-capable) generation runs.
    await resolve_project_scope(db, current_user, str(payload.project_id))
    from app.services.rag_generation_service import (
        RagEvidenceUnavailable,
        RagGenerationUnavailable,
        grounded_generate,
    )
    try:
        result = await grounded_generate(
            db,
            project_id=payload.project_id,
            prompt_text=payload.prompt_text,
            source_ids=payload.source_ids,
            generation_config=payload.generation_config,
            current_user=current_user,
            persist=payload.persist,
        )
    except RagEvidenceUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RagGenerationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
    _: User = Depends(require_generation_batch_access()),
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
    _: User = Depends(require_generation_batch_access()),
):
    from app.services.rag_review_service import get_batch_preview
    preview = await get_batch_preview(db, batch_id, current_user)
    return preview["batch"]


@router.get("/cases/needs-review")
async def cases_needing_review(
    project_id: str = Query(..., description="Project to list — never a fleet view"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Generated cases held back for human review, lowest faithfulness first.

    Populated by the faithfulness gate (Tier 2 item 9). When the
    ``rag_faithfulness_gate`` flag is off nothing sets ``needs_review_reason``,
    so this returns an empty list rather than an error — "nothing is queued"
    and "the gate is not running" look the same here on purpose, because the
    queue itself cannot tell them apart. `/settings/ai` is where the flag state
    is reported.
    """
    scoped, _allowed = await resolve_project_scope(db, current_user, project_id)
    if scoped is None:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    from app.services.rag_faithfulness_service import list_needs_review

    rows = await list_needs_review(db, scoped, limit=limit)
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "status": r.status,
            "faithfulness_score": r.faithfulness_score,
            "faithfulness_evaluator": r.faithfulness_evaluator,
            "faithfulness_evaluated_at": (
                r.faithfulness_evaluated_at.isoformat()
                if r.faithfulness_evaluated_at else None
            ),
            "needs_review_reason": r.needs_review_reason,
            "generation_batch_id": (
                str(r.generation_batch_id) if r.generation_batch_id else None
            ),
        }
        for r in rows
    ]


@router.post(
    "/batches/{batch_id}/accept",
    dependencies=[
        Depends(require_role(UserRole.QA_ENGINEER)),
        Depends(require_generation_batch_access()),
    ],
)
async def batch_accept(
    batch_id: uuid.UUID,
    payload: BatchAcceptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.services.rag_review_service import bulk_accept
    cases = await bulk_accept(
        db,
        batch_id,
        payload.case_ids,
        current_user,
        edits_by_case=payload.edits,
    )
    return [{"id": str(c.id), "title": c.title, "status": c.status} for c in cases]


@router.post(
    "/batches/{batch_id}/cases/{case_id}/accept",
    dependencies=[
        Depends(require_role(UserRole.QA_ENGINEER)),
        Depends(require_generation_batch_access()),
    ],
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
    dependencies=[
        Depends(require_role(UserRole.QA_ENGINEER)),
        Depends(require_generation_batch_access()),
    ],
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
    current_user: User = Depends(get_current_active_user),
):
    await _require_case_project_access(db, current_user, case_id)
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
    current_user: User = Depends(get_current_active_user),
):
    await resolve_project_scope(db, current_user, str(project_id))
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
    current_user: User = Depends(get_current_active_user),
):
    await _require_case_project_access(db, current_user, case_id)
    from app.services.rag_staleness_service import dismiss_stale as _dismiss
    await _dismiss(db, case_id)
    await db.commit()


# ── RAG-14: Status and eval ──────────────────────────────────────────────────


@router.get("/rag/status", response_model=RagStatusResponse)
async def rag_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """RAG feature status plus adoption counts **for the caller's projects**.

    The counts were previously unscoped ``COUNT(*)`` across every tenant, on an
    endpoint with no role or project guard, so any authenticated user could
    read the whole install's totals. ``get_accessible_project_ids`` returns
    ``None`` for an ADMIN (sees everything) and a membership set otherwise.
    """
    from app.core.deps import get_accessible_project_ids
    from app.services.rag_eval_service import get_rag_status

    scope = await get_accessible_project_ids(db, current_user)
    return await get_rag_status(db, accessible_project_ids=scope)


@router.get("/batches/{batch_id}/eval")
async def batch_eval(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_generation_batch_access()),
):
    from app.services.rag_eval_service import run_eval_for_batch
    return await run_eval_for_batch(db, batch_id)
