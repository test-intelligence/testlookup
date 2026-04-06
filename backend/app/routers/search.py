"""Search endpoint — keyword, semantic (ChromaDB), hybrid, and global modes."""
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.core.metrics import semantic_search_duration_seconds, semantic_search_total
from app.db.postgres import get_db
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
    actual_type = search_type
    start = time.monotonic()

    if search_type == "semantic":
        from app.services.semantic_search import semantic_search
        items, total, pages = await semantic_search(
            db, q=q, page=page, size=size,
            project_id=project_id, status=status, days=days,
        )
        if total == 0:
            # ChromaDB returned nothing — fall back to keyword so users always get results
            items, total, pages = await search_test_cases_query(
                db, q=q, page=page, size=size,
                project_id=project_id, status=status, days=days,
            )
            actual_type = "keyword"
            semantic_search_total.labels(search_type="semantic", status="fallback").inc()
        else:
            semantic_search_total.labels(search_type="semantic", status="success").inc()

    elif search_type == "hybrid":
        from app.services.semantic_search import hybrid_search
        items, total, pages = await hybrid_search(
            db, q=q, page=page, size=size,
            project_id=project_id, status=status, days=days,
        )
        if total == 0:
            items, total, pages = await search_test_cases_query(
                db, q=q, page=page, size=size,
                project_id=project_id, status=status, days=days,
            )
            actual_type = "keyword"
            semantic_search_total.labels(search_type="hybrid", status="fallback").inc()
        else:
            semantic_search_total.labels(search_type="hybrid", status="success").inc()

    else:
        items, total, pages = await search_test_cases_query(
            db, q=q, page=page, size=size,
            project_id=project_id, status=status, days=days,
        )
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
    _=Depends(get_current_active_user),
):
    """
    System-wide global search across multiple entity types.

    Searches test cases, test runs, suites, defects, flaky tests, and releases.
    Returns mixed results with entity badges and navigation URLs.
    """
    from app.services.global_search_service import global_search, ALL_ENTITY_TYPES

    types: set[str] | None = None
    if entity_types:
        types = {t.strip() for t in entity_types.split(",") if t.strip()} & ALL_ENTITY_TYPES

    return await global_search(
        db=db,
        q=q,
        project_id=project_id,
        entity_types=types or None,
        days=days,
        page=page,
        size=size,
    )
