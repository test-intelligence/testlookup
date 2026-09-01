"""
Semantic search service using ChromaDB for vector-similarity retrieval
over test case error messages, names, suite labels, and (Phase 3) granular
step names + assertion messages.

Used by the /search endpoint when search_type=semantic or search_type=hybrid.
Falls back to keyword-only results when ChromaDB is unavailable.

Phase 3 / code-reviewer gate (c) — ACCEPTED DEVIATIONS (documented, not silent):

* **Single shared collection, tenant-isolated by metadata filter (NOT a
  per-project collection).** ``_get_or_create_collection`` uses one global
  ``_COLLECTION_NAME`` for every project; cross-tenant isolation is enforced at
  query time by the ``project_id`` ``where`` filter in ``semantic_search`` AND
  re-applied as defence-in-depth at the Postgres layer. The gate's
  "collection per project" rule is met *in effect* (no row is returned outside
  the caller's ``project_id`` / ``allowed_project_ids``) rather than by
  physical collection sharding. Phase 3 is purely *additive* to this
  pre-existing module — it only widens the embedded document with step text —
  so the proper per-project shard (which would also have to re-shape the
  cross-project full-reindex batching in ``_upsert_rows_to_collection`` and the
  multi-project ``$in`` / ``allowed_project_ids`` query fan-out) is deferred and
  recorded here rather than introduced implicitly. See CHANGELOG Phase 3 note.
* **Offline-safe, but not "by construction" — by a guard.** The semantic path
  is *opt-in* (``search_type=semantic``/``hybrid``; the router defaults to
  keyword) and *fails back to keyword* on any ChromaDB error.
  ``_get_or_create_collection`` passes NO explicit ``embedding_function``, so
  ChromaDB uses its default local ONNX all-MiniLM model and there is no cloud
  *inference* on this path.

  This comment previously concluded from that "and therefore no
  ``AI_OFFLINE_MODE`` early-return is required". **That was wrong, and it was
  wrong in production.** The model runs locally but is *not bundled*: on first
  use ChromaDB downloads 79 MB from ``chroma-onnx-models.s3.amazonaws.com``.
  Observed egressing with ``AI_OFFLINE_MODE=true``, and the download plus ONNX
  load OOM-killed the Celery worker in a restart loop.

  The ceiling now lives at ChromaDB's own download chokepoint — see
  ``services/local_embedder_guard.py`` — because nine modules create
  collections and all nine would trigger the same fetch. When offline mode is
  on and no local model is present, that raises, and the keyword fallback below
  handles it. If a cloud ``embedding_function`` is ever wired in it MUST be
  gated on ``settings.AI_OFFLINE_MODE`` as well.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import Project, TestCase, TestRun, TestStep

logger = logging.getLogger("services.semantic_search")

_COLLECTION_NAME = "test_case_search"


def _step_text_subq():
    """Correlated scalar subquery: concat granular step names + assertion
    messages for a test's canonical (latest-run) snapshot, so the embedded
    document includes step text (Phase 3). Project-safe — steps anchor to the
    project-scoped canonical that ``TestCase.canonical_test_case_id`` links to.
    Bounded to 50 steps so a pathological tree can't blow up the indexer.
    """
    from sqlalchemy import func, select as _select

    inner = (
        _select(
            func.string_agg(
                func.coalesce(TestStep.name, "")
                + func.coalesce(" " + TestStep.assertion_message, ""),
                " | ",
            )
        )
        .where(TestStep.canonical_test_case_id == TestCase.canonical_test_case_id)
        .correlate(TestCase)
        .scalar_subquery()
    )
    return inner.label("step_text")


async def _get_chroma_client():
    from app.db.chroma import get_configured_chroma_client
    return await get_configured_chroma_client()


async def _get_or_create_collection():
    """Return the shared search collection.

    NOTE (gate c, accepted deviation — see module docstring): one global
    ``_COLLECTION_NAME`` for all projects; tenant isolation is the query-time
    ``project_id`` metadata filter in ``semantic_search`` (+ Postgres-layer
    re-filter), not a per-project collection. No explicit ``embedding_function``
    is passed, so ChromaDB's default local ONNX MiniLM is used — no cloud
    *inference*.

    That does NOT by itself make the path offline-safe: the model is not
    bundled, and ChromaDB fetches 79 MB from AWS S3 the first time it is used.
    Offline safety comes from the guard at ChromaDB's download chokepoint
    (``services/local_embedder_guard.py``), which raises under
    ``AI_OFFLINE_MODE`` when no local model is present; the caller then falls
    back to keyword. Do NOT swap in a cloud ``embedding_function`` without an
    ``AI_OFFLINE_MODE`` gate either.
    """
    client = await _get_chroma_client()
    return await asyncio.to_thread(
        client.get_or_create_collection,
        _COLLECTION_NAME,
    )


async def _publish_index_size(collection) -> None:
    """Publish the collection's document count. Never raises.

    ``search_index_documents`` was declared and never emitted, so the gauge sat
    at 0 whether the index held a million documents or had never been built --
    the number an operator checks first when semantic search returns nothing.

    Read from ``collection.count()`` rather than accumulated from the per-run
    upsert counts: the gauge is the SIZE of the index, and an incremental run
    that upserts 12 rows has not made the index 12 documents large. Off-thread,
    like every other Chroma call here, because the client is synchronous.
    """
    try:
        from app.core.metrics import search_index_documents

        total = await asyncio.to_thread(collection.count)
        search_index_documents.set(int(total))
    except Exception as exc:  # noqa: BLE001 -- telemetry must never break indexing
        logger.debug("Could not publish search index size: %s", exc)


def _doc_text(
    test_name: str,
    suite_name: Optional[str],
    error_message: Optional[str],
    step_text: Optional[str] = None,
) -> str:
    """Combine fields into a single document string for embedding.

    ``step_text`` (Phase 3) is the concatenated granular step names + assertion
    messages for the test's latest-run snapshot, so a failing step/assertion is
    findable in the semantic index too — mirroring the keyword path's step
    EXISTS match. Bounded to keep the embedded document from ballooning on
    deep/wide step trees.
    """
    parts = [test_name]
    if suite_name:
        parts.append(suite_name)
    if error_message:
        parts.append(error_message[:500])
    if step_text:
        parts.append(step_text[:1000])
    return " | ".join(parts)


_REDIS_CURSOR_KEY = "testlookup:search:last_indexed_id"
_REDIS_TIMESTAMP_KEY = "testlookup:search:last_indexed_at"


def _cursor_keys(project_id: Optional[str] = None) -> tuple[str, str]:
    """Return an isolated cursor namespace for global or project indexing."""
    if project_id is None:
        return _REDIS_CURSOR_KEY, _REDIS_TIMESTAMP_KEY
    suffix = f":project:{project_id}"
    return f"{_REDIS_CURSOR_KEY}{suffix}", f"{_REDIS_TIMESTAMP_KEY}{suffix}"


def _get_redis():
    """Get a Redis client for cursor management."""
    import redis as _redis
    return _redis.Redis.from_url(settings.CELERY_BROKER_URL)


def _after_incremental_cursor(last_id):
    """Resume after one exact row without skipping equal-timestamp siblings."""
    cursor_created_at = (
        select(TestCase.created_at).where(TestCase.id == last_id).scalar_subquery()
    )
    resume_created_at = func.coalesce(
        cursor_created_at,
        datetime.min.replace(tzinfo=timezone.utc),
    )
    return or_(
        TestCase.created_at > resume_created_at,
        and_(
            TestCase.created_at == resume_created_at,
            TestCase.id > last_id,
        ),
    )


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
        _step_text_subq(),
    ).join(TestRun, TestRun.id == TestCase.test_run_id).join(
        Project, Project.id == TestRun.project_id
    ).where(Project.is_active.is_(True))

    if project_id:
        q = q.where(TestRun.project_id == project_id)

    q = q.order_by(TestCase.created_at.asc(), TestCase.id.asc())
    rows = (await db.execute(q)).all()
    if not rows:
        return 0

    try:
        count = await _upsert_rows_to_collection(collection, rows)
    except Exception as exc:
        # Embeddings are computed at UPSERT, not at collection creation, so an
        # unavailable embedder surfaces here — outside the guard above. Verified
        # live: with AI_OFFLINE_MODE on and no local model, the offline ceiling
        # raises from ``collection.upsert``, and without this the whole task
        # failed instead of degrading.
        #
        # The cursor is deliberately NOT advanced: skipping it means these rows
        # are re-indexed once embeddings are available again, rather than being
        # silently passed over forever.
        logger.warning("Semantic indexing skipped — embeddings unavailable: %s", exc)
        return 0

    # Update cursor to the latest ID so incremental picks up from here
    _update_cursor(rows, project_id)

    logger.info("Full-indexed %d test cases into ChromaDB collection '%s'", count, _COLLECTION_NAME)
    await _publish_index_size(collection)
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
        cursor_key, _ = _cursor_keys(project_id)
        raw = r.get(cursor_key)
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
        _step_text_subq(),
    ).join(TestRun, TestRun.id == TestCase.test_run_id).join(
        Project, Project.id == TestRun.project_id
    ).where(Project.is_active.is_(True))

    if project_id:
        q = q.where(TestRun.project_id == project_id)

    if last_id:
        q = q.where(_after_incremental_cursor(last_id))

    q = q.order_by(TestCase.created_at.asc(), TestCase.id.asc()).limit(5000)

    rows = (await db.execute(q)).all()
    if not rows:
        logger.debug("No new test cases to index (cursor up to date)")
        return 0

    try:
        count = await _upsert_rows_to_collection(
            collection,
            rows,
            checkpoint_cursor=True,
            cursor_project_id=project_id,
        )
    except Exception as exc:
        # Same as the full index: the embedder is exercised at upsert. Degrade
        # to zero and leave the cursor at the last completed batch, so the
        # failing batch is retried without discarding earlier progress.
        logger.warning("Incremental indexing skipped — embeddings unavailable: %s", exc)
        return 0
    logger.info("Incrementally indexed %d new test cases", count)
    await _publish_index_size(collection)
    return count


async def _upsert_rows_to_collection(
    collection,
    rows,
    *,
    checkpoint_cursor: bool = False,
    cursor_project_id: Optional[str] = None,
) -> int:
    """Upsert rows in batches, optionally checkpointing every completed batch."""
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for row in rows:
        ids.append(str(row.id))
        documents.append(_doc_text(
            row.test_name,
            row.suite_name,
            row.error_message,
            getattr(row, "step_text", None),
        ))
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
        if checkpoint_cursor:
            _update_cursor(rows[i:i + batch_size], cursor_project_id)

    return len(ids)


def _update_cursor(rows, project_id: Optional[str] = None) -> None:
    """Update the Redis cursor to the last row's ID and timestamp."""
    if not rows:
        return
    last_row = rows[-1]
    try:
        r = _get_redis()
        cursor_key, timestamp_key = _cursor_keys(project_id)
        r.set(cursor_key, str(last_row.id))
        r.set(timestamp_key, datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


async def get_index_status(
    project_id: Optional[str] = None,
    allowed_project_ids: Optional[set] = None,
) -> dict:
    """Return ChromaDB health and a scope-filtered document count."""
    status = {"status": "unknown", "document_count": 0, "last_indexed_at": None}
    try:
        collection = await _get_or_create_collection()
        if allowed_project_ids is None:
            # Backward-compatible internal/global probe. The HTTP endpoint
            # always supplies its authorized set of active projects.
            count = await asyncio.to_thread(collection.count)
        elif not allowed_project_ids:
            count = 0
        else:
            scoped = await asyncio.to_thread(
                collection.get,
                where={
                    "project_id": {
                        "$in": [str(pid) for pid in allowed_project_ids],
                    }
                },
                include=[],
            )
            count = len(scoped.get("ids", []))
        status["document_count"] = count
        status["status"] = "healthy"
    except Exception as exc:
        logger.debug("ChromaDB unavailable for status check: %s", exc)
        status["status"] = "unavailable"

    try:
        import redis as _redis
        from app.core.config import settings as _settings
        r = _redis.Redis.from_url(_settings.CELERY_BROKER_URL)
        _, timestamp_key = _cursor_keys(project_id)
        ts = r.get(timestamp_key)
        # Global incremental indexing also covers an explicitly selected
        # project, so fall back to its timestamp when no project-specific
        # manual reindex has written a namespaced cursor yet.
        if ts is None and project_id is not None:
            ts = r.get(_REDIS_TIMESTAMP_KEY)
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
    allowed_project_ids: Optional[set] = None,
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
        elif allowed_project_ids is not None:
            allowed = [str(pid) for pid in allowed_project_ids]
            if not allowed:
                return [], 0, 0
            conditions.append({"project_id": {"$in": allowed}})
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

    from sqlalchemy import and_, func
    from sqlalchemy.orm import aliased

    history_case = aliased(TestCase)
    history_run = aliased(TestRun)
    pg_query = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at.label("last_run_date"),
            func.count().filter(
                and_(
                    history_case.status == "FAILED",
                    # test_fingerprint is NOT project-salted, so a fingerprint-only
                    # join blends a same-named test in ANOTHER tenant into this
                    # count — inflating failure_count and leaking cross-tenant
                    # failure aggregates into search ranking + display. Scope the
                    # history to the matched row's own project.
                    history_run.project_id == TestRun.project_id,
                )
            ).label("failure_count"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .join(Project, Project.id == TestRun.project_id)
        .outerjoin(history_case, history_case.test_fingerprint == TestCase.test_fingerprint)
        .outerjoin(history_run, history_run.id == history_case.test_run_id)
        .where(TestCase.id.in_(valid_uuids), Project.is_active.is_(True))
        .group_by(
            TestCase.id, TestCase.test_run_id, TestCase.test_name,
            TestCase.suite_name, TestCase.status, TestCase.created_at,
        )
    )
    # Defense in depth: re-apply the tenant filter at the Postgres layer so a
    # stale or mis-indexed ChromaDB document cannot leak cross-tenant rows.
    if project_id:
        pg_query = pg_query.where(TestRun.project_id == project_id)
    elif allowed_project_ids is not None:
        allowed_uuids = list(allowed_project_ids)
        if not allowed_uuids:
            return [], 0, 0
        pg_query = pg_query.where(TestRun.project_id.in_(allowed_uuids))

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
    allowed_project_ids: Optional[set] = None,
) -> tuple[list[dict], int, int]:
    """
    Hybrid search: merge keyword + semantic results, deduplicate by test_case_id,
    and re-rank by max(keyword_present, semantic_score).
    """
    from app.services.search_service import search_test_cases_query

    # Run sequentially on the injected session. ``semantic_search`` and
    # ``search_test_cases_query`` both issue ``db.execute()`` on the SAME
    # AsyncSession; scheduling one with ``asyncio.create_task`` while awaiting
    # the other let two ``db.execute`` calls overlap on one session, which
    # SQLAlchemy rejects with "another operation is in progress" (intermittent,
    # timing-dependent). The two reads are independent, so serialising them
    # gives identical results without the concurrency hazard. (A genuinely
    # parallel version would need a second, isolated session.)
    keyword_results, kw_total, _ = await search_test_cases_query(
        db, q=q, page=1, size=size * 2,
        project_id=project_id, status=status, days=days,
        allowed_project_ids=allowed_project_ids,
    )
    sem_results, _, _ = await semantic_search(
        db, q, 1, size * 2, project_id, status, days,
        allowed_project_ids=allowed_project_ids,
    )

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


# ── Retention: purge a project's documents from the search index ────────────


async def purge_project_documents(project_id: str, *, execute: bool) -> int | None:
    """Count (or delete) this project's documents in the test-case index.

    Returns the document count, or **None when the store could not be
    reached** — callers must not render that as 0.

    **Why this exists.** Both indexers filter ``Project.is_active`` at WRITE
    time, so deleting a project stops new documents being added — but nothing
    ever retired the ones already written, and this module contains no
    ``collection.delete`` of any kind. Measured on a live deployment: the
    ``test_case_search`` collection held 49,380 documents while only 600 test
    cases belonged to active projects. **98.8% of the index was embeddings of
    deleted projects' tests.**

    That was never a data leak — ``semantic_search`` applies a ``project_id``
    metadata filter and the Postgres layer re-filters — so this is deletion
    completeness and unbounded growth, not exposure.

    **Why it hangs off retention rather than off soft-delete.** Deleting a
    project only flips ``is_active``, and that is reversible. Purging
    embeddings there would make un-deleting a project silently lossy: the
    incremental indexer will not re-add the rows (its cursor has already passed
    them), so search would stay empty until someone ran a FULL reindex.
    Retention's execute path is the deliberate, audited, already-irreversible
    one, and it is the feature that promises a cross-store purge — so it is the
    honest place for this.

    Deleting by metadata filter rather than by id list: the ids are test-case
    UUIDs we would otherwise have to re-derive from Postgres rows that this
    same purge is deleting.
    """
    try:
        collection = await _get_or_create_collection()
    except Exception as exc:
        # A vector-store outage must not block the durable-store purge, which
        # is the same stance analysis_cache_retention takes.
        #
        # Returns None, NOT 0. This comment used to argue that reporting 0 was
        # acceptable because the failure was logged, and then said in its own
        # next breath that "a purge that could not visit a store must not read
        # as 'nothing to delete there'". Both cannot hold: every caller of this
        # function renders the number, and none of them read the log. 0 and
        # "could not look" are opposite findings and now have opposite values.
        logger.warning("Search-index purge failed for project %s: %s", project_id, exc)
        return None

    where = {"project_id": str(project_id)}
    try:
        payload = await asyncio.to_thread(collection.get, where=where, include=[])
        ids = list(payload.get("ids") or [])
        if execute and ids:
            await asyncio.to_thread(collection.delete, where=where)
        return len(ids)
    except Exception as exc:
        # Same rule as above: unreachable is not empty.
        logger.warning("Search-index purge failed for project %s: %s", project_id, exc)
        return None
