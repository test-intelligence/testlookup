"""Search endpoint — keyword, semantic (ChromaDB), hybrid, and global modes."""
import asyncio
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, resolve_project_scope
from app.core.metrics import semantic_search_duration_seconds, semantic_search_total
from app.db.postgres import get_db
from app.models.postgres import (
    Defect,
    FlakyQuarantineRequest,
    Release,
    TestCase,
    TestRun,
    TestSuite,
    User,
)
from app.services.resilience import with_fallback
from app.services.search_service import search_test_cases_query

router = APIRouter(prefix="/api/v1/search", tags=["Search"])


@router.get("/index-status")
async def get_index_status():
    """Return ChromaDB collection health: document count, last indexed timestamp."""
    from app.services.semantic_search import get_index_status as _get_index_status
    return await _get_index_status()


@router.post("/reindex")
async def trigger_reindex(project_id: str | None = None, full: bool = False):
    """Manually trigger a search reindex via Celery task.
    Pass full=true for a complete rebuild; default is incremental."""
    from app.worker.tasks import reindex_search
    task = reindex_search.apply_async(kwargs={"project_id": project_id, "full": full})
    return {"task_id": task.id, "status": "queued", "mode": "full" if full else "incremental"}


@router.get("/entity-counts")
async def get_entity_counts(
    project_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Project-scoped totals for the /search page's chip + Index Health panels.

    The page previously sourced these counts from ``response.entity_counts`` —
    the per-query result counts — so the user saw 0 everywhere before they
    typed anything. The chip count for ``Tests`` was meant to read "how many
    test cases exist in this project", not "how many match the empty query".
    This endpoint fills that gap with one round trip of cheap COUNT queries.

    Filtering:
      * If ``project_id`` is provided, scope to that single project (after
        tenant-access validation by ``resolve_project_scope``).
      * If ``project_id`` is omitted (or the All-Projects sentinel resolved
        to ``None``), scope to the union of projects the caller can see;
        ADMIN gets the full instance.

    Notes:
      * ``test_case`` joins against ``test_runs`` because test_cases hold a
        ``test_run_id`` FK, not a direct ``project_id`` column.
      * ``flaky_test`` counts ``flaky_quarantine_requests`` rows in *any*
        state — proposed/approved/quarantined/re_quarantined — because the
        /search page's ``Flaky`` chip is "everything the system has seen
        flagged as flaky", not just live quarantines. Released rows are
        excluded since those are no longer flagged-as-flaky.
    """
    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db, current_user, project_id,
    )

    # When a specific project is requested, scope every count to that single
    # project. Otherwise scope by accessible-project set (None = ADMIN-all).
    def _apply_project_filter(stmt, column):
        if scoped_project_id is not None:
            return stmt.where(column == uuid.UUID(str(scoped_project_id)))
        if allowed_project_ids is not None:
            if not allowed_project_ids:
                # Caller has no project memberships — return 0 instead of all rows.
                return stmt.where(False)
            return stmt.where(column.in_(allowed_project_ids))
        return stmt

    async def _count(stmt) -> int:
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    test_runs_q = _apply_project_filter(
        select(func.count(TestRun.id)), TestRun.project_id,
    )
    # ``test_cases`` has no project_id column; join via TestRun.
    test_cases_q = _apply_project_filter(
        select(func.count(TestCase.id)).join(TestRun, TestCase.test_run_id == TestRun.id),
        TestRun.project_id,
    )
    suites_q = _apply_project_filter(
        select(func.count(TestSuite.id)), TestSuite.project_id,
    )
    defects_q = _apply_project_filter(
        select(func.count(Defect.id)), Defect.project_id,
    )
    flaky_q = _apply_project_filter(
        select(func.count(FlakyQuarantineRequest.id)).where(
            FlakyQuarantineRequest.status != "RELEASED",
        ),
        FlakyQuarantineRequest.project_id,
    )
    releases_q = _apply_project_filter(
        select(func.count(Release.id)), Release.project_id,
    )

    test_run, test_case, suite, defect, flaky_test, release = await asyncio.gather(
        _count(test_runs_q),
        _count(test_cases_q),
        _count(suites_q),
        _count(defects_q),
        _count(flaky_q),
        _count(releases_q),
    )

    return {
        "test_case":  test_case,
        "test_run":   test_run,
        "suite":      suite,
        "defect":     defect,
        "flaky_test": flaky_test,
        "release":    release,
    }


@router.get("/similar/{test_case_id}")
async def find_similar_failures(
    test_case_id: str,
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Find historically similar failures for a given test case."""
    from sqlalchemy import select
    from app.models.postgres import TestCase
    import uuid as _uuid

    try:
        tc_result = await db.execute(
            select(TestCase.test_name, TestCase.error_message)
            .where(TestCase.id == _uuid.UUID(test_case_id))
        )
        row = tc_result.first()
    except Exception:
        row = None

    if not row:
        return {"items": [], "total": 0, "query": test_case_id}

    query_text = f"{row.test_name} {(row.error_message or '')[:300]}".strip()
    if not query_text:
        return {"items": [], "total": 0, "query": test_case_id}

    from app.services.semantic_search import semantic_search
    items, total, _ = await semantic_search(
        db, q=query_text, page=1, size=limit + 1,
    )
    # Exclude the source test case itself
    items = [i for i in items if str(i.get("test_case_id", "")) != test_case_id]
    return {"items": items[:limit], "total": len(items), "query": query_text[:100]}


@router.get("")
async def search_test_cases(
    q: str = Query(..., min_length=1),
    project_id: str | None = None,
    status: str | None = None,
    days: int = Query(None, ge=1, le=365),
    search_type: str = Query("keyword", pattern="^(keyword|semantic|hybrid)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Search test cases by name, suite, or error message.

    search_type options:
    - keyword  (default): ILIKE substring matching across name/suite/error columns.
    - semantic: ChromaDB vector-similarity search — finds conceptually similar failures.
    - hybrid:   Merges keyword + semantic results, deduplicates, and re-ranks by relevance.

    The response always includes search_type to reflect the mode actually used
    (may fall back to keyword if ChromaDB is unavailable).
    """
    # Tenant isolation: resolve the effective project scope. ADMIN gets
    # unrestricted access; non-admin is pinned to their accessible set (and
    # receives 403 when requesting a specific project they don't belong to).
    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db, current_user, project_id,
    )
    scoped_project_id_str = str(scoped_project_id) if scoped_project_id else None

    actual_type = search_type
    start = time.monotonic()

    # Keyword search is the source-of-truth fallback for every mode: it
    # reads straight from Postgres and never depends on ChromaDB. The
    # ``with_fallback`` helper routes around a ChromaDB outage (primary
    # raises) and a zero-result response (primary returned ``[]``,
    # which today is also how ``semantic_search`` reports a ChromaDB
    # failure) so the user always sees data from the DB instead of an
    # empty page. Promote this pattern to other ChromaDB-dependent
    # endpoints as they appear.
    common_kwargs = dict(
        q=q, page=page, size=size,
        project_id=scoped_project_id_str, status=status, days=days,
        allowed_project_ids=allowed_project_ids,
    )

    async def _keyword():
        return await search_test_cases_query(db, **common_kwargs)

    def _is_empty(result):
        # ``semantic_search`` returns (items, total, pages); fall back when
        # total is 0 — this covers both a legitimate no-match query (where
        # keyword will also legitimately return 0) and a ChromaDB outage
        # (where keyword may have results).
        return result[1] == 0

    if search_type == "semantic":
        from app.services.semantic_search import semantic_search

        async def _semantic():
            return await semantic_search(db, **common_kwargs)

        items, total, pages = await with_fallback(
            primary=_semantic,
            fallback=_keyword,
            name="search.semantic",
            is_empty=_is_empty,
        )
        # Tag the metric based on whether the fallback fired.
        used_fallback = total == 0 or (
            # If primary succeeded and was non-empty, with_fallback returned it as-is;
            # the only way total can be non-zero after this is success. Treat 0 as
            # "fallback fired" since the helper would have called keyword in that case.
            False
        )
        actual_type = "keyword" if used_fallback else "semantic"
        semantic_search_total.labels(
            search_type="semantic",
            status="fallback" if used_fallback else "success",
        ).inc()

    elif search_type == "hybrid":
        from app.services.semantic_search import hybrid_search

        async def _hybrid():
            return await hybrid_search(db, **common_kwargs)

        items, total, pages = await with_fallback(
            primary=_hybrid,
            fallback=_keyword,
            name="search.hybrid",
            is_empty=_is_empty,
        )
        used_fallback = total == 0
        actual_type = "keyword" if used_fallback else "hybrid"
        semantic_search_total.labels(
            search_type="hybrid",
            status="fallback" if used_fallback else "success",
        ).inc()

    else:
        items, total, pages = await _keyword()
        actual_type = "keyword"
        semantic_search_total.labels(search_type="keyword", status="success").inc()

    semantic_search_duration_seconds.labels(search_type=actual_type).observe(time.monotonic() - start)

    return {
        "items": items,
        "total": total,
        "query": q,
        "search_type": actual_type,
        "page": page,
        "size": size,
        "pages": pages,
    }


@router.get("/global")
async def global_search_endpoint(
    q: str = Query(..., min_length=1),
    project_id: Optional[str] = None,
    entity_types: Optional[str] = Query(None, description="Comma-separated entity types to search"),
    days: Optional[int] = Query(None, ge=1, le=365),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    System-wide global search across multiple entity types.

    Searches test cases, test runs, suites, defects, flaky tests, and releases.
    Returns mixed results with entity badges and navigation URLs.
    """
    # Tenant isolation: resolve the effective scope. Non-admin users without a
    # specific project get fanned out across their membership set; non-admin
    # users requesting a project they don't belong to get 403 from the helper.
    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db, current_user, project_id,
    )

    from app.services.global_search_service import global_search, ALL_ENTITY_TYPES

    types: set[str] | None = None
    if entity_types:
        types = {t.strip() for t in entity_types.split(",") if t.strip()} & ALL_ENTITY_TYPES

    return await global_search(
        db=db,
        q=q,
        project_id=str(scoped_project_id) if scoped_project_id else None,
        entity_types=types or None,
        days=days,
        page=page,
        size=size,
        allowed_project_ids=allowed_project_ids,
    )
