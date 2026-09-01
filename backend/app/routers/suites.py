"""TestSuite + CanonicalTestCase HTTP API (Phase 3).

Two route groups served from one file because they share schemas and a
service module:

  * ``/api/v1/suites`` — CRUD for the first-class suite entity introduced in
    migration 0075. The default suite (``is_default=true``) cannot be
    deleted; promote a different suite to default via the dedicated
    ``set-default`` endpoint so the partial-unique index can never reject a
    direct ``is_default`` write race.
  * ``/api/v1/canonical-test-cases`` — read + relink for the project-scoped
    canonical catalog. Moving a case between suites is the primary write;
    delete/create happen via ingestion or the legacy suite_sync path during
    the dual-write window.

Authorization mirrors the rest of the protected API: list/read for any
authenticated user (filtered by ``get_accessible_project_ids``), mutations
gated by ``QA_ENGINEER``. The default-suite promotion is ``QA_LEAD``+ since
it's a project-wide configuration change.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    CanonicalManagedUnlinkRequest,
    CanonicalPromotionResponse,
    CanonicalRetirementConfirmRequest,
    CanonicalTestCaseBulkLinkRequest,
    CanonicalTestCaseBulkLinkResponse,
    CanonicalTestCaseLinkRequest,
    CanonicalTestCaseListResponse,
    CanonicalTestCaseResponse,
    ManagedTestCaseResponse,
    TestSuiteCreate,
    TestSuiteListResponse,
    TestSuiteResponse,
    TestSuiteUpdate,
)
from app.services.test_case_lifecycle_service import lifecycle_actions_for
from app.services.test_management_metrics_service import emit_staged_test_management_metrics
from app.services import test_suite_service as svc
from app.services.run_environment import resolve_environment

router = APIRouter(tags=["Test Suites"])


# ── helpers ────────────────────────────────────────────────────────────────


async def _enforce_project_access(
    db: AsyncSession,
    user: User,
    project_id: uuid.UUID,
) -> None:
    """Raise 403 if the user can't see this project. ADMIN bypasses (returns None)."""
    accessible = await get_accessible_project_ids(db, user)
    if accessible is None:
        return  # ADMIN — sees everything.
    if project_id not in accessible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _canonical_to_response(
    c,
    *,
    suite_name: Optional[str] = None,
    last_seen_test_case_id: Optional[uuid.UUID] = None,
) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "test_suite_id": c.test_suite_id,
        "test_suite_name": suite_name,
        "test_fingerprint": c.test_fingerprint,
        "test_name": c.test_name,
        "class_name": c.class_name,
        "status": c.status,
        "source": c.source,
        "first_seen_run_id": c.first_seen_run_id,
        "last_seen_run_id": c.last_seen_run_id,
        # Optional deep-link target: when the caller resolved the per-run
        # TestCase.id that matches this canonical row's fingerprint in its
        # last-seen run, surface it so the UI can navigate directly to
        # ``/runs/<run>/tests/<case>``. Falls back to None when unresolved.
        "last_seen_test_case_id": last_seen_test_case_id,
        "deleted_at_run_id": c.deleted_at_run_id,
        "managed_test_case_id": c.managed_test_case_id,
        "retirement_confirmed_at": c.retirement_confirmed_at,
        "retirement_confirmed_by_id": c.retirement_confirmed_by_id,
        "retirement_reason": c.retirement_reason,
        "deleted_observed_at": c.deleted_observed_at,
        "review_tag": c.review_tag,
        "tags": c.tags,
        "run_count": None,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


async def _resolve_last_seen_test_case_ids(
    db: AsyncSession,
    canonicals: list,
) -> dict:
    """For each canonical with a ``last_seen_run_id``, find the matching
    per-run ``TestCase.id`` so the UI can deep-link to the test detail page.

    Returns ``{canonical.id: TestCase.id}``. One query for the whole list —
    matches on ``(test_fingerprint, test_run_id)``.
    """
    from sqlalchemy import select, tuple_
    from app.models.postgres import TestCase

    keys: list[tuple[str, uuid.UUID]] = []
    canonical_by_key: dict[tuple[str, str], uuid.UUID] = {}
    for c in canonicals:
        if c.last_seen_run_id and c.test_fingerprint:
            keys.append((c.test_fingerprint, c.last_seen_run_id))
            canonical_by_key[(c.test_fingerprint, str(c.last_seen_run_id))] = c.id
    if not keys:
        return {}
    rows = (
        await db.execute(
            select(TestCase.id, TestCase.test_fingerprint, TestCase.test_run_id).where(
                tuple_(TestCase.test_fingerprint, TestCase.test_run_id).in_(keys)
            )
        )
    ).all()
    result: dict = {}
    for tc_id, fp, tr_id in rows:
        cid = canonical_by_key.get((fp, str(tr_id)))
        if cid is not None:
            result[cid] = tc_id
    return result


# ── /api/v1/suites ─────────────────────────────────────────────────────────


@router.get("/api/v1/suites", response_model=TestSuiteListResponse)
async def list_suites(
    project_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List test suites. ``project_id`` scopes to one project; otherwise the
    response covers every project the caller can see (ADMIN: all)."""
    accessible = await get_accessible_project_ids(db, current_user)
    if project_id is not None:
        if accessible is not None and project_id not in accessible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        project_ids: Optional[list[uuid.UUID]] = [project_id]
    else:
        project_ids = None if accessible is None else list(accessible)

    items = await svc.list_test_suites(db, project_ids)
    return {"items": items, "total": len(items)}


@router.post(
    "/api/v1/suites",
    response_model=TestSuiteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def create_suite(
    payload: TestSuiteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _enforce_project_access(db, current_user, payload.project_id)
    suite = await svc.create_test_suite(
        db,
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
    )

    # If the caller picked an explicit owner, write the TestSuiteOwner
    # row now. ``set_suite_owner`` enforces the QA_LEAD role gate; the
    # 400 propagates back to the client. Skipping this branch leaves
    # the suite to inherit the project's default QA lead via the
    # read-time fallback in ``resolve_suite_owner`` (or to be seeded
    # later by ``_maybe_seed_default_owner`` on next ingest).
    if payload.owner_user_id is not None:
        from app.services.suite_review_service import set_suite_owner
        await set_suite_owner(
            db,
            project_id=payload.project_id,
            suite_name=suite.name,
            owner_user_id=payload.owner_user_id,
        )

    await db.commit()
    await db.refresh(suite)
    return TestSuiteResponse.model_validate({
        **suite.__dict__, "test_case_count": 0,
    })


@router.get("/api/v1/suites/{suite_id}", response_model=TestSuiteResponse)
async def get_suite(
    suite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    suite = await svc.get_suite_or_404(db, suite_id)
    await _enforce_project_access(db, current_user, suite.project_id)
    items = await svc.list_test_suites(db, [suite.project_id])
    payload = next((s for s in items if s["id"] == suite.id), None)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test suite not found")
    return payload


@router.patch(
    "/api/v1/suites/{suite_id}",
    response_model=TestSuiteResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def update_suite(
    suite_id: uuid.UUID,
    payload: TestSuiteUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    suite = await svc.get_suite_or_404(db, suite_id)
    await _enforce_project_access(db, current_user, suite.project_id)
    await svc.update_test_suite(
        db,
        suite,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
    )
    await db.commit()
    await db.refresh(suite)
    return TestSuiteResponse.model_validate({**suite.__dict__, "test_case_count": None})


@router.delete(
    "/api/v1/suites/{suite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(UserRole.QA_LEAD))],
)
async def delete_suite(
    suite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    suite = await svc.get_suite_or_404(db, suite_id)
    await _enforce_project_access(db, current_user, suite.project_id)
    await svc.delete_test_suite(db, suite)
    await db.commit()


@router.post(
    "/api/v1/suites/{suite_id}/set-default",
    response_model=TestSuiteResponse,
    dependencies=[Depends(require_role(UserRole.QA_LEAD))],
)
async def set_default(
    suite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Promote this suite to ``is_default`` within its project (demoting the
    prior default). New ingested test cases without an explicit suite land
    in whichever suite is currently default.
    """
    suite = await svc.get_suite_or_404(db, suite_id)
    await _enforce_project_access(db, current_user, suite.project_id)
    await svc.set_default_suite(db, suite)
    await db.commit()
    await db.refresh(suite)
    return TestSuiteResponse.model_validate({**suite.__dict__, "test_case_count": None})


@router.get(
    "/api/v1/suites/{suite_id}/test-cases",
    response_model=CanonicalTestCaseListResponse,
)
async def list_suite_test_cases(
    suite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    suite = await svc.get_suite_or_404(db, suite_id)
    await _enforce_project_access(db, current_user, suite.project_id)
    rows = await svc.list_canonical_test_cases(
        db,
        project_ids=[suite.project_id],
        suite_id=suite.id,
    )
    if rows:
        last_seen_map = await _resolve_last_seen_test_case_ids(db, rows)
        items = [
            _canonical_to_response(
                c,
                suite_name=suite.name,
                last_seen_test_case_id=last_seen_map.get(c.id),
            )
            for c in rows
        ]
        return {"items": items, "total": len(items)}

    # Legacy fallback — when ``canonical_test_cases`` has nothing linked to
    # this suite (pre-0075 ingests, or sync_canonical_test_cases hasn't
    # caught up), derive the list from per-run ``test_cases`` joined on the
    # suite's name. Keeps /suites/{id} usable for older or partially-synced
    # data; new ingests populate canonical and short-circuit this branch.
    legacy = await svc.list_legacy_suite_test_cases(db, suite)
    return {"items": legacy, "total": len(legacy)}


# ── /api/v1/canonical-test-cases ───────────────────────────────────────────


@router.get(
    "/api/v1/canonical-test-cases",
    response_model=CanonicalTestCaseListResponse,
)
async def list_canonical_cases(
    project_id: Optional[uuid.UUID] = Query(None),
    suite_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    accessible = await get_accessible_project_ids(db, current_user)
    if project_id is not None:
        if accessible is not None and project_id not in accessible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        project_ids: Optional[list[uuid.UUID]] = [project_id]
    else:
        project_ids = None if accessible is None else list(accessible)

    rows = await svc.list_canonical_test_cases(
        db,
        project_ids=project_ids,
        suite_id=suite_id,
        status_filter=status_filter,
    )
    return {
        "items": [_canonical_to_response(c) for c in rows],
        "total": len(rows),
    }


@router.get(
    "/api/v1/canonical-test-cases/orphaned",
    response_model=CanonicalTestCaseListResponse,
)
async def list_orphaned_canonical_cases(
    project_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    accessible = await get_accessible_project_ids(db, current_user)
    if project_id is not None:
        if accessible is not None and project_id not in accessible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        project_ids: Optional[list[uuid.UUID]] = [project_id]
    else:
        project_ids = None if accessible is None else list(accessible)
    rows, total = await svc.list_orphaned_canonical_cases(
        db, project_ids, page=page, size=size
    )
    return {"items": [_canonical_to_response(c) for c in rows], "total": total}


@router.get(
    "/api/v1/canonical-test-cases/{canonical_id}",
    response_model=CanonicalTestCaseResponse,
)
async def get_canonical_case(
    canonical_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    canonical = await svc.get_canonical_or_404(db, canonical_id)
    await _enforce_project_access(db, current_user, canonical.project_id)
    return _canonical_to_response(canonical)


@router.post(
    "/api/v1/canonical-test-cases/{canonical_id}/promote",
    response_model=CanonicalPromotionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def promote_canonical_case(
    canonical_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    canonical = await svc.get_canonical_or_404(db, canonical_id)
    await _enforce_project_access(db, current_user, canonical.project_id)
    canonical, managed = await svc.promote_canonical_test_case(
        db, canonical_id, current_user
    )
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(canonical)
    await db.refresh(managed)
    managed_response = ManagedTestCaseResponse.model_validate(managed)
    managed_response.allowed_actions = await lifecycle_actions_for(
        db, managed, current_user
    )
    return {
        "canonical": _canonical_to_response(canonical),
        "managed_case": managed_response,
    }


@router.delete(
    "/api/v1/canonical-test-cases/{canonical_id}/managed-link",
    response_model=CanonicalTestCaseResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def unlink_canonical_managed_case(
    canonical_id: uuid.UUID,
    payload: CanonicalManagedUnlinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    canonical = await svc.get_canonical_or_404(db, canonical_id)
    await _enforce_project_access(db, current_user, canonical.project_id)
    canonical = await svc.unlink_canonical_managed_case(
        db, canonical_id, current_user, reason=payload.reason
    )
    await db.commit()
    await db.refresh(canonical)
    return _canonical_to_response(canonical)


@router.post(
    "/api/v1/canonical-test-cases/{canonical_id}/confirm-retirement",
    response_model=CanonicalTestCaseResponse,
    dependencies=[Depends(require_role(UserRole.QA_LEAD))],
)
async def confirm_canonical_retirement(
    canonical_id: uuid.UUID,
    payload: CanonicalRetirementConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    canonical = await svc.get_canonical_or_404(db, canonical_id)
    await _enforce_project_access(db, current_user, canonical.project_id)
    canonical = await svc.confirm_canonical_retirement(
        db, canonical_id, current_user, reason=payload.reason
    )
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(canonical)
    return _canonical_to_response(canonical)


@router.post(
    "/api/v1/canonical-test-cases/{canonical_id}/link",
    response_model=CanonicalTestCaseResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def link_canonical_to_suite(
    canonical_id: uuid.UUID,
    payload: CanonicalTestCaseLinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Move a canonical test case to a different suite within the same
    project. Refuses cross-project moves (different project's suites are
    isolated by design)."""
    canonical = await svc.get_canonical_or_404(db, canonical_id)
    await _enforce_project_access(db, current_user, canonical.project_id)
    target = await svc.get_suite_or_404(db, payload.test_suite_id)
    await svc.link_canonical_to_suite(db, canonical, target)
    await db.commit()
    await db.refresh(canonical)
    return _canonical_to_response(canonical)


@router.post(
    "/api/v1/canonical-test-cases/bulk-link",
    response_model=CanonicalTestCaseBulkLinkResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def bulk_link_canonicals_to_suite(
    payload: CanonicalTestCaseBulkLinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Move a batch of canonical test cases to a single target suite.

    Companion to the single-id ``/canonical-test-cases/{id}/link`` route —
    unblocks the SuiteCasesPage multi-select bulk-move UX. Same auth and
    cross-project semantics: caller must have access to the target
    suite's project, and any id from a different project rejects the
    entire batch with 400 (no partial moves).

    Ids that don't resolve to an existing canonical are surfaced in
    ``missing_ids`` so the UI can clear stale rows from its selection
    without re-fetching the whole page.
    """
    target = await svc.get_suite_or_404(db, payload.target_test_suite_id)
    # Project-access check uses the target suite's project — the service
    # layer's cross-project guard then ensures every id also belongs
    # there, so we can't be tricked into moving an inaccessible project's
    # cases via a target suite the caller does have access to.
    await _enforce_project_access(db, current_user, target.project_id)
    result = await svc.bulk_link_canonicals_to_suite(
        db, target, payload.canonical_ids
    )
    await db.commit()
    return result


@router.get("/api/v1/canonical-test-cases/{canonical_id}/runs")
async def list_canonical_run_history(
    canonical_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Every per-run ``TestCase`` row currently linked to this canonical.
    Newest run first. Returned as bare dicts (no full RunTestCase schema —
    that lives in ``runs`` router and would create a cycle here).

    Carries the run's environment/branch/build and the per-run retry evidence
    so one screen can answer "has this test been unstable, on which
    environments, and how long has it taken?" — the question the per-test
    history timeline exists to answer (roadmap Phase 1).

    ``environment`` is resolved, not raw: a run that never recorded one yields
    ``None`` with ``environment_source: "unknown"`` rather than being folded
    into a synthetic default group.
    """
    canonical = await svc.get_canonical_or_404(db, canonical_id)
    await _enforce_project_access(db, current_user, canonical.project_id)
    rows = await svc.list_runs_for_canonical(db, canonical)
    return {
        "items": [
            {
                "test_case_id": case.id,
                "test_run_id": case.test_run_id,
                "status": case.status,
                "duration_ms": case.duration_ms,
                "suite_name": case.suite_name,
                "created_at": case.created_at,
                # Run context — what makes this a timeline rather than a list.
                "build_number": run.build_number,
                "branch": run.branch,
                **resolve_environment(run).to_dict(),
                # Retry evidence. A retry is evidence, not a way to make the
                # build green, so it is reported rather than absorbed.
                "retry_count": case.retry_count,
                "is_flaky_run": case.is_flaky_run,
            }
            for case, run in rows
        ],
        "total": len(rows),
    }
