"""
Semantic search service using ChromaDB for vector-similarity retrieval
over test case error messages, names, and suite labels.

Used by the /search endpoint when search_type=semantic or search_type=hybrid.
Falls back to keyword-only results when ChromaDB is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import TestCase, TestRun

logger = logging.getLogger("services.semantic_search")

_COLLECTION_NAME = "test_case_search"


def _get_chroma_client():
    import chromadb
    return chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)


async def _get_or_create_collection():
    client = await asyncio.to_thread(_get_chroma_client)
    return await asyncio.to_thread(
        client.get_or_create_collection,
        _COLLECTION_NAME,
    )


def _doc_text(test_name: str, suite_name: Optional[str], error_message: Optional[str]) -> str:
    """Combine fields into a single document string for embedding."""
    parts = [test_name]
    if suite_name:
        parts.append(suite_name)
    if error_message:
        parts.append(error_message[:500])
    return " | ".join(parts)


_REDIS_CURSOR_KEY = "testlookup:search:last_indexed_id"
_REDIS_TIMESTAMP_KEY = "testlookup:search:last_indexed_at"


def _get_redis():
    """Get a Redis client for cursor management."""
    import redis as _redis
    return _redis.Redis.from_url(settings.CELERY_BROKER_URL)


async def index_test_cases(db: AsyncSession, project_id: Optional[str] = None) -> int:
    """
    Full re-index of test cases into ChromaDB.

    Use this for:
    - Manual reindex requests (POST /search/reindex)
    - Project-scoped rebuilds

    For incremental indexing after ingestion, use `index_incremental` instead.
    """
    try:
        collection = await _get_or_create_collection()
    except Exception as exc:
        logger.warning("ChromaDB unavailable — semantic indexing skipped: %s", exc)
        return 0

    q = select(
        TestCase.id,
        TestCase.test_name,
        TestCase.suite_name,
        TestCase.error_message,
        TestCase.status,
        TestCase.test_run_id,
        TestRun.project_id,
        TestCase.created_at,
    ).join(TestRun, TestRun.id == TestCase.test_run_id)

    if project_id:
        q = q.where(TestRun.project_id == project_id)

    rows = (await db.execute(q)).all()
    if not rows:
        return 0

    count = await _upsert_rows_to_collection(collection, rows)

    # Update cursor to the latest ID so incremental picks up from here
    _update_cursor(rows)

    logger.info("Full-indexed %d test cases into ChromaDB collection '%s'", count, _COLLECTION_NAME)
    return count


async def index_incremental(db: AsyncSession, project_id: Optional[str] = None) -> int:
    """
    Incremental index: only processes test cases created AFTER the last indexed ID.

    Uses a Redis-stored cursor (last_indexed_id) to avoid re-scanning the full table.
    Called by:
    - Celery beat schedule (hourly)
    - Post-ingestion trigger
    - Post-pipeline-completion trigger
    """
    try:
        collection = await _get_or_create_collection()
    except Exception as exc:
        logger.warning("ChromaDB unavailable — incremental indexing skipped: %s", exc)
        return 0

    # Read cursor from Redis
    last_id = None
    try:
        r = _get_redis()
        raw = r.get(_REDIS_CURSOR_KEY)
        if raw:
            import uuid as _uuid
            last_id = _uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw))
    except Exception:
        pass

    q = select(
        TestCase.id,
        TestCase.test_name,
        TestCase.suite_name,
        TestCase.error_message,
        TestCase.status,
        TestCase.test_run_id,
        TestRun.project_id,
        TestCase.created_at,
    ).join(TestRun, TestRun.id == TestCase.test_run_id)

    if project_id:
        q = q.where(TestRun.project_id == project_id)

    if last_id:
        # Only index records created after the last cursor
        # Using ID comparison as a cursor (UUIDs are v4, so we use created_at ordering)
        q = q.where(TestCase.created_at > (
            select(TestCase.created_at).where(TestCase.id == last_id).scalar_subquery()
        ))

    q = q.order_by(TestCase.created_at.asc()).limit(5000)

    rows = (await db.execute(q)).all()
    if not rows:
        logger.debug("No new test cases to index (cursor up to date)")
        return 0

    count = await _upsert_rows_to_collection(collection, rows)
    _update_cursor(rows)

    logger.info("Incrementally indexed %d new test cases", count)
    return count


async def _upsert_rows_to_collection(collection, rows) -> int:
    """Upsert rows into ChromaDB collection in batches."""
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for row in rows:
        ids.append(str(row.id))
        documents.append(_doc_text(row.test_name, row.suite_name, row.error_message))
        metadatas.append({
            "status": row.status or "",
            "project_id": str(row.project_id) if row.project_id else "",
            "test_run_id": str(row.test_run_id) if row.test_run_id else "",
            "created_at": row.created_at.isoformat() if row.created_at else "",
        })

    batch_size = 200
    for i in range(0, len(ids), batch_size):
        await asyncio.to_thread(
            collection.upsert,
            ids=ids[i:i + batch_size],
            documents=documents[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
        )

    return len(ids)


def _update_cursor(rows) -> None:
    """Update the Redis cursor to the last row's ID and timestamp."""
    if not rows:
        return
    last_row = rows[-1]
    try:
        r = _get_redis()
        r.set(_REDIS_CURSOR_KEY, str(last_row.id))
        r.set(_REDIS_TIMESTAMP_KEY, datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


async def get_index_status() -> dict:
    """Return ChromaDB collection health metrics."""
    status = {"status": "unknown", "document_count": 0, "last_indexed_at": None}
    try:
        collection = await _get_or_create_collection()
        count = await asyncio.to_thread(collection.count)
        status["document_count"] = count
        status["status"] = "healthy"
    except Exception as exc:
        logger.debug("ChromaDB unavailable for status check: %s", exc)
        status["status"] = "unavailable"

    try:
        import redis as _redis
        from app.core.config import settings as _settings
        r = _redis.Redis.from_url(_settings.CELERY_BROKER_URL)
        ts = r.get("testlookup:search:last_indexed_at")
        if ts:
            status["last_indexed_at"] = ts.decode() if isinstance(ts, bytes) else str(ts)
    except Exception:
        pass

    return status


async def semantic_search(
    db: AsyncSession,
    q: str,
    page: int,
    size: int,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    days: Optional[int] = None,
) -> tuple[list[dict], int, int]:
    """
    Vector-similarity search against ChromaDB.
    Returns (items, total, pages) with the same shape as keyword search.
    Falls back to empty results on ChromaDB errors.
    """
    try:
        collection = await _get_or_create_collection()

        # Build ChromaDB where clause
        where: dict | None = None
        conditions: list[dict] = []
        if project_id:
            conditions.append({"project_id": {"$eq": project_id}})
        if status:
            conditions.append({"status": {"$eq": status.upper()}})

        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        query_kwargs: dict = {
            "query_texts": [q],
            "n_results": min((page + 1) * size, 200),
            "include": ["documents", "distances", "metadatas"],
        }
        if where:
            query_kwargs["where"] = where

        results = await asyncio.to_thread(collection.query, **query_kwargs)

    except Exception as exc:
        logger.warning("ChromaDB query failed — returning empty semantic results: %s", exc)
        return [], 0, 0

    ids_list = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas_list = results.get("metadatas", [[]])[0]

    if not ids_list:
        return [], 0, 0

    # Apply days filter in Python (ChromaDB metadata comparison on ISO strings is unreliable)
    cutoff: Optional[datetime] = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch full test case details from Postgres for matched IDs
    import uuid as _uuid
    valid_uuids: list[_uuid.UUID] = []
    distance_map: dict[str, float] = {}
    for tc_id, dist, meta in zip(ids_list, distances, metadatas_list):
        if cutoff and meta.get("created_at"):
            try:
                created = datetime.fromisoformat(meta["created_at"].replace("Z", "+00:00"))
                if created < cutoff:
                    continue
            except ValueError:
                pass
        try:
            valid_uuids.append(_uuid.UUID(tc_id))
            distance_map[tc_id] = float(dist)
        except ValueError:
            pass

    if not valid_uuids:
        return [], 0, 0

    from sqlalchemy import func
    from sqlalchemy.orm import aliased

    history_case = aliased(TestCase)
    pg_query = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at.label("last_run_date"),
            func.count().filter(history_case.status == "FAILED").label("failure_count"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .outerjoin(history_case, history_case.test_fingerprint == TestCase.test_fingerprint)
        .where(TestCase.id.in_(valid_uuids))
        .group_by(
            TestCase.id, TestCase.test_run_id, TestCase.test_name,
            TestCase.suite_name, TestCase.status, TestCase.created_at,
        )
    )

    rows = (await db.execute(pg_query)).all()

    # Attach relevance score (convert distance → similarity: 1 / (1 + dist))
    from app.services.search_ranking import build_match_reasons

    items: list[dict] = []
    for row in rows:
        dist = distance_map.get(str(row.test_case_id), 1.0)
        relevance = round(1.0 / (1.0 + dist), 4)
        item = dict(row._mapping)
        item["relevance_score"] = relevance
        item["match_reasons"] = build_match_reasons(
            has_keyword_match=False,
            semantic_score=relevance,
            failure_count=item.get("failure_count", 0),
            status=str(item.get("status", "")),
            source_mode="semantic",
        )
        item["source_mode_used"] = "semantic"
        items.append(item)

    # Sort by relevance descending, then paginate
    items.sort(key=lambda x: x["relevance_score"], reverse=True)
    total = len(items)
    start = (page - 1) * size
    page_items = items[start:start + size]
    pages = -(-total // size) if total else 0

    return page_items, total, pages


async def hybrid_search(
    db: AsyncSession,
    q: str,
    page: int,
    size: int,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    days: Optional[int] = None,
) -> tuple[list[dict], int, int]:
    """
    Hybrid search: merge keyword + semantic results, deduplicate by test_case_id,
    and re-rank by max(keyword_present, semantic_score).
    """
    from app.services.search_service import search_test_cases_query

    # Run both in parallel
    semantic_task = asyncio.create_task(
        semantic_search(db, q, 1, size * 2, project_id, status, days)
    )
    keyword_results, kw_total, _ = await search_test_cases_query(
        db, q=q, page=1, size=size * 2,
        project_id=project_id, status=status, days=days,
    )
    sem_results, _, _ = await semantic_task

    from app.services.search_ranking import compute_hybrid_score, build_match_reasons

    seen: dict[str, dict] = {}
    # Keyword results: mark as keyword match
    for item in keyword_results:
        key = str(item["test_case_id"])
        seen[key] = {**item, "_keyword_match": True, "relevance_score": 1.0}

    # Semantic results: keep highest relevance, don't downgrade keyword hits
    for item in sem_results:
        key = str(item["test_case_id"])
        if key not in seen:
            seen[key] = {**item, "_keyword_match": False}
        else:
            existing = seen[key].get("relevance_score", 0.0)
            seen[key]["relevance_score"] = max(existing, item.get("relevance_score", 0.0))

    # Re-rank using hybrid scoring
    items = list(seen.values())
    for item in items:
        kw_match = item.get("_keyword_match", False)
        sem_score = item.get("relevance_score", 0.0)
        item["relevance_score"] = compute_hybrid_score(
            has_keyword_match=kw_match,
            semantic_score=sem_score,
            last_run_date=str(item.get("last_run_date", "")),
            failure_count=item.get("failure_count", 0),
            status=str(item.get("status", "")),
        )
        item["match_reasons"] = build_match_reasons(
            has_keyword_match=kw_match,
            semantic_score=sem_score,
            failure_count=item.get("failure_count", 0),
            status=str(item.get("status", "")),
        )
        item["source_mode_used"] = "hybrid"
        item.pop("_keyword_match", None)

    merged = sorted(items, key=lambda x: x.get("relevance_score", 0.0), reverse=True)
    total = len(merged)
    start = (page - 1) * size
    page_items = merged[start:start + size]
    pages = -(-total // size) if total else 0
    return page_items, total, pages
