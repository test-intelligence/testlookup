"""Duplicate authored-test-case review router (Phase 4 of granular test detail).

Thin HTTP surface over ``services.duplicate_detection_service`` for the
per-project duplicate review queue:

  * ``GET  /api/v1/projects/{project_id}/duplicate-candidates`` — list banded
    candidates (filter by ``band`` / ``status``, paginated), each joined to the
    two ``ManagedTestCase`` refs and carrying its explainable score breakdown.
  * ``POST /api/v1/projects/{project_id}/duplicate-candidates/detect`` — run a
    detection sweep over the project's authored cases.
  * ``POST .../{candidate_id}/dismiss`` — suppress a pair (flips status +
    records a ``dismissed_duplicate_pairs`` row so re-detection never resurfaces
    it).
  * ``POST .../{candidate_id}/merge`` — NON-DESTRUCTIVE: flips status to
    ``merged`` and optionally soft-deprecates the losing case. Never deletes a
    case or redirects a fingerprint.

Authorisation: every route depends on ``require_project_access()`` which reads
``project_id`` from the path and verifies the PROVIDED id against project
membership (ADMIN bypass) — the IDOR ratchet. The detection SERVICE stages only;
this router OWNS the ``await db.commit()`` for the detect / dismiss / merge
mutations (the read endpoint does not commit).
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access
from app.db.postgres import get_db
from app.models.postgres import DuplicateTestCaseCandidate, ManagedTestCase, User
from app.models.schemas import (
    DuplicateActionResponse,
    DuplicateCandidateListResponse,
    DuplicateCandidateResponse,
    DuplicateCaseRef,
    DuplicateDetectionRunResponse,
    DuplicateMergeRequest,
)
from app.services import duplicate_detection_service as dup_svc

router = APIRouter(prefix="/api/v1/projects", tags=["Duplicate Detection"])


def _ref(case: Optional[ManagedTestCase], case_id: uuid.UUID) -> DuplicateCaseRef:
    """Build a minimal case ref, tolerating a case row that vanished (CASCADE
    leaves the candidate only momentarily — defensive fallback to the id)."""
    if case is None:
        return DuplicateCaseRef(id=case_id, title="(deleted)", suite_name=None, status=None)
    return DuplicateCaseRef(
        id=case.id,
        title=case.title,
        suite_name=case.suite_name,
        status=case.status,
    )


def _candidate_response(
    cand: DuplicateTestCaseCandidate,
    cases_by_id: dict[uuid.UUID, ManagedTestCase],
) -> DuplicateCandidateResponse:
    return DuplicateCandidateResponse(
        id=cand.id,
        project_id=cand.project_id,
        band=cand.band,
        score=cand.score,
        reason=cand.reason,
        method=cand.method,
        component_scores=cand.component_scores,
        status=cand.status,
        detected_at=cand.detected_at,
        case_a=_ref(cases_by_id.get(cand.case_a_id), cand.case_a_id),
        case_b=_ref(cases_by_id.get(cand.case_b_id), cand.case_b_id),
    )


@router.get(
    "/{project_id}/duplicate-candidates",
    response_model=DuplicateCandidateListResponse,
)
async def list_duplicate_candidates(
    project_id: uuid.UUID,
    band: Optional[str] = Query(None, description="Filter: exact|strong|possible"),
    status_filter: Optional[str] = Query(
        "open", alias="status", description="Filter: open|merged|dismissed (default open)"
    ),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """List a project's duplicate-candidate review queue (paginated, filterable).

    ``status`` defaults to ``open`` so the review queue shows actionable pairs;
    pass ``status=`` (empty) is not supported — use an explicit value to widen.
    """
    base = select(DuplicateTestCaseCandidate).where(
        DuplicateTestCaseCandidate.project_id == project_id
    )
    if band:
        base = base.where(DuplicateTestCaseCandidate.band == band)
    if status_filter:
        base = base.where(DuplicateTestCaseCandidate.status == status_filter)

    total = (
        await db.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()

    open_count = (
        await db.execute(
            select(func.count())
            .select_from(DuplicateTestCaseCandidate)
            .where(
                DuplicateTestCaseCandidate.project_id == project_id,
                DuplicateTestCaseCandidate.status == "open",
            )
        )
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(
                DuplicateTestCaseCandidate.score.desc(),
                DuplicateTestCaseCandidate.detected_at.desc(),
            )
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    # Resolve the two case refs for the page in one query.
    case_ids: set[uuid.UUID] = set()
    for c in rows:
        case_ids.add(c.case_a_id)
        case_ids.add(c.case_b_id)
    cases_by_id: dict[uuid.UUID, ManagedTestCase] = {}
    if case_ids:
        case_rows = (
            await db.execute(
                select(ManagedTestCase).where(ManagedTestCase.id.in_(case_ids))
            )
        ).scalars().all()
        cases_by_id = {c.id: c for c in case_rows}

    return DuplicateCandidateListResponse(
        items=[_candidate_response(c, cases_by_id) for c in rows],
        total=int(total),
        open_count=int(open_count),
    )


@router.post(
    "/{project_id}/duplicate-candidates/detect",
    response_model=DuplicateDetectionRunResponse,
)
async def run_duplicate_detection(
    project_id: uuid.UUID,
    enable_semantic: bool = Query(
        True, description="Run the optional local-embedder semantic tier (best-effort)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Run a tiered detection sweep over the project's authored cases.

    The service stages candidate rows + lazily backfills ``dup_fingerprint``;
    this router owns the commit. ``semantic_used`` returned by the service is an
    extra key the response model ignores.
    """
    result = await dup_svc.detect_duplicates_for_project(
        db, project_id, enable_semantic=enable_semantic
    )
    await db.commit()
    return DuplicateDetectionRunResponse(
        project_id=result["project_id"],
        candidates_created=result["candidates_created"],
        candidates_total=result["candidates_total"],
        cases_scanned=result["cases_scanned"],
        sampled=result["sampled"],
        note=result.get("note"),
    )


@router.post(
    "/{project_id}/duplicate-candidates/{candidate_id}/dismiss",
    response_model=DuplicateActionResponse,
)
async def dismiss_duplicate_candidate(
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """Dismiss a candidate pair (status → ``dismissed`` + suppression record).

    The suppression row keeps the pair from resurfacing on the next detection
    run. STAGE in the service; commit here.
    """
    try:
        cand = await dup_svc.dismiss_candidate(
            db, project_id, candidate_id, current_user.id
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="duplicate candidate not found in project",
        )
    await db.commit()
    return DuplicateActionResponse(
        candidate_id=cand.id,
        status=cand.status,
        deprecated_case_id=None,
    )


@router.post(
    "/{project_id}/duplicate-candidates/{candidate_id}/merge",
    response_model=DuplicateActionResponse,
)
async def merge_duplicate_candidate(
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    payload: DuplicateMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """NON-DESTRUCTIVE merge: status → ``merged`` + optional soft-deprecate of the
    losing case. Never deletes a case or redirects a fingerprint.

    ``payload.candidate_id`` must match the path ``candidate_id`` (the path is
    authoritative — a mismatch is a 400). ``keep_case_id`` must be one of the
    pair's two cases (service raises → 400).
    """
    if payload.candidate_id != candidate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="candidate_id in body does not match the path",
        )
    try:
        cand, deprecated_id = await dup_svc.merge_candidate(
            db,
            project_id,
            candidate_id,
            payload.keep_case_id,
            deprecate_loser=payload.deprecate_loser,
        )
    except ValueError as exc:
        msg = str(exc)
        # "not found in project" → 404; "keep_case_id must be ..." → 400.
        if "not found" in msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    await db.commit()
    return DuplicateActionResponse(
        candidate_id=cand.id,
        status=cand.status,
        deprecated_case_id=deprecated_id,
    )
