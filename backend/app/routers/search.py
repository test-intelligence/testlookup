"""Search endpoint — keyword, semantic (ChromaDB), hybrid, and global modes.

W12 adds: the structured query syntax (``status:failed suite:"API" since:7d``,
``services/search_query_parser``) on ``/search`` and ``/search/global``, with a
``parsed`` block saying what was applied, ignored and not searched; the
honest semantic fallback (``fallback_reason``); Similar failures' FAILED /
BROKEN filter and ``semantic_status``; the index status's semantic
availability; recent searches (Redis, per user), suggested searches, and the
search usage summary.

The raw search text is never logged, never put in an exception message or a
metric label, and never stored outside the reader's own Redis recents
(``tests/test_search_query_text_never_logged.py`` scans this module).
"""
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    _api_key_bound_project,
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
    resolve_project_scope,
)
from app.core.metrics import (
    global_search_duration_seconds,
    global_search_zero_results_total,
    search_after_response_writes_total,
    semantic_search_duration_seconds,
    semantic_search_total,
)
from app.core.search_vocab import (
    FALLBACK_TEXT,
    MAX_QUERY_CHARS,
    QUERY_TOO_LONG,
    RECENT_MAX,
    RECENT_TTL_DAYS,
    SCOPE_ENTITY,
    SEMANTIC_FILTER_KEYS,
    SEMANTIC_MODES,
    SEMANTIC_REASON_TEXT,
    STATUS_VALUES,
    USAGE_DAYS,
    semantic_availability,
)
from app.db.postgres import get_db
from app.models.postgres import (
    Defect,
    FlakyQuarantineRequest,
    Project,
    Release,
    SearchQueryEvent,
    TestCase,
    TestRun,
    TestSuite,
    User,
    UserRole,
)
from app.models.schemas import (
    RecentSearchesResponse,
    SearchSuggestionsResponse,
    SearchUsageRead,
)
from app.services.resilience import with_fallback
from app.services.search_query_parser import ParsedQuery, parse_query, parsed_block, whole_days_since
from app.services.search_recents import RecentsUnavailable, clear_recents, push_recent, read_recents
from app.services.search_service import search_test_cases_query
from app.services.search_suggestions import suggestions_for_project
from app.services.search_usage_service import build_event, usage_summary

router = APIRouter(prefix="/api/v1/search", tags=["Search"])

#: stdlib logger: every line formats the exception TYPE only (``%s`` of
#: ``type(exc).__name__``), never the query or the exception's message.
logger = logging.getLogger("routers.search")

#: Reverse of ``SCOPE_ENTITY``: the scope a one-type global search was run in.
_ENTITY_SCOPE = {entity: scope for scope, entity in SCOPE_ENTITY.items()}
#: The index-status keys of the ACTIVE embedding generation (W12 SRCH-02, G-C).
_GENERATION_KEYS = ("collection", "generation", "embedder", "embedder_available", "reembed")
_SIMILAR_STATUSES = ("FAILED", "BROKEN")
_SIMILAR_CANDIDATE_CAP = 40
USAGE_DAYS_SENTENCE = "days must be 1, 7 or 30."
RECENTS_UNAVAILABLE = "Could not clear recent searches: the cache is unavailable."


# Search is deliberately NOT release-scoped, and says so.
#
# Every other windowed read honours the header's release filter. This one must
# not: search is a DISCOVERY tool, and scoping it would return nothing for a
# test that exists but last ran in another release — which reads as "that test
# does not exist". A user hunting for a test they know they wrote would
# conclude the product had lost it.
#
# Saying so in the payload is the other half of the S4b decision: a surface
# that ignores a filter the header is showing has to declare it, or the user
# reasonably assumes it applied. The UI renders this as the all-releases badge.
#
# ONE definition for both search endpoints. It was inlined in `/search` only,
# and `/global` — the endpoint behind the default "All" chip, so the path most
# searches actually take — declared nothing at all. The two had the same
# behaviour and different honesty, and a second copy would drift the same way.
RELEASE_SCOPE_DECLARATION: dict[str, str] = {
    "release": "not_applicable",
    "note": (
        "Search spans every release in the project by design — a test "
        "you are looking for may have last run in a different one."
    ),
}


# ── After-response writes (W12 SRCH-03 recents, SRCH-05 usage; D-4) ─────────
#
# The ONE exception to "a GET never writes": a /search page search with words
# that the page asked to record (``record=true``, page 1). The writes run as a
# ``BackgroundTasks`` job AFTER the response: on FastAPI 0.115 the request's
# ``get_db`` session has already exited by then, so the usage row's own short
# session never sits on top of the request's connection. Best effort: a
# failure is counted (``search_after_response_writes_total``) and logged by
# its exception type, never raised, never read back by the same request. The
# typeahead, the MCP tools, the CLI and pagination never pass ``record``.


async def _after_search(*, user_id: str, recent: Optional[dict], event: Optional[dict]) -> None:
    if recent is not None:
        try:
            ok = await push_recent(user_id, recent)
        except Exception as exc:  # noqa: BLE001 -- push_recent never raises; belt and braces
            logger.warning("search_recent_write_failed error_type=%s", type(exc).__name__)
            ok = False
        search_after_response_writes_total.labels(
            store="recent", outcome="written" if ok else "failed",
        ).inc()
    if event is not None:
        from app.db.postgres import AsyncSessionLocal  # imported here: tests patch the PEP-562 name

        try:
            async with AsyncSessionLocal() as session:
                session.add(SearchQueryEvent(**event))
                await session.commit()
            search_after_response_writes_total.labels(store="usage", outcome="written").inc()
        except Exception as exc:  # noqa: BLE001 -- telemetry never fails a search
            search_after_response_writes_total.labels(store="usage", outcome="failed").inc()
            logger.warning("search_usage_write_failed error_type=%s", type(exc).__name__)


def _schedule_after_search(
    background_tasks: Optional[BackgroundTasks],
    *,
    record: bool,
    page: int,
    current_user: User,
    stripped: str,
    parsed: ParsedQuery,
    project_id: Optional[uuid.UUID],
    entity_scope: str,
    mode_requested: str,
    mode_used: str,
    fallback_reason: Optional[str],
    result_count: int,
    counts_exact: bool,
    started: float,
    now: datetime,
) -> None:
    """Schedule ONE recent + ONE usage row, only for a recorded page-1 search with words."""
    if not record or background_tasks is None or page != 1 or not stripped:
        return
    recent = {
        "q": stripped,
        "scope": entity_scope,
        "mode": mode_used,
        "project_id": str(project_id) if project_id else None,
        "result_count": int(result_count),
        "counts_exact": bool(counts_exact),
        "ts": now.isoformat(),
    }
    event = build_event(
        q=stripped,
        word_count=parsed.word_count,
        filter_count=parsed.filter_count,
        project_id=project_id,
        entity_scope=entity_scope,
        mode_requested=mode_requested,
        mode_used=mode_used,
        fallback_reason=fallback_reason,
        result_count=int(result_count),
        counts_exact=bool(counts_exact),
        latency_ms=int((time.monotonic() - started) * 1000),
        now=now,
    )
    background_tasks.add_task(_after_search, user_id=str(current_user.id), recent=recent, event=event)


class _PrimaryFailed(RuntimeError):
    """A semantic / hybrid primary failed. Carries the original exception's TYPE only.

    ``with_fallback`` logs the primary's exception text, and a database
    error's text carries the statement's bound parameters -- the search text
    (hybrid runs the keyword query too). Re-raised as this, the log line says
    which kind of failure it was and nothing a person typed.
    """


def _refuse_long_query(stripped: str) -> None:
    if len(stripped) > MAX_QUERY_CHARS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=QUERY_TOO_LONG)


@router.get("/index-status")
async def get_index_status(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the semantic (ChromaDB) index's health within the caller's active project scope.

    ``status``: ``healthy`` (with ``document_count`` and ``last_indexed_at``),
    ``not_configured`` (no ChromaDB server answers at the configured address,
    e.g. a ``make dev`` stack without the ``local-llm`` profile) or
    ``unavailable`` (a server answered but the read failed). The index holds
    test cases only; global search is keyword over every entity either way.

    W12 SRCH-01/-02: also the active collection / generation / embedder (from
    ``get_index_status``) and whether Hybrid / Semantic can answer here:
    ``semantic_available``, ``semantic_reason`` and its sentence
    (``core/search_vocab.semantic_availability``). Every key is always present.
    """
    from app.services.semantic_search import get_index_status as _get_index_status

    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db, current_user, project_id,
    )
    active_projects = select(Project.id).where(Project.is_active.is_(True))
    if scoped_project_id is not None:
        active_projects = active_projects.where(Project.id == scoped_project_id)
    elif allowed_project_ids is not None:
        active_projects = active_projects.where(
            Project.id.in_(list(allowed_project_ids)),
        )

    active_project_ids = set((await db.execute(active_projects)).scalars().all())
    index_status = await _get_index_status(
        project_id=str(scoped_project_id) if scoped_project_id else None,
        allowed_project_ids=active_project_ids,
    )
    # Annotated in place: the body IS the service's dict, plus these keys.
    # The service answers its three keys by default (the call shape above is
    # pinned by tests/regression/test_search_index_status_scope.py); the five
    # generation keys come from G-C's own reader, which never raises and reads
    # the cached generation only. Without them ``embedder_missing`` could
    # never be reported and the page could not say which embedder is in use.
    missing = [key for key in _GENERATION_KEYS if key not in index_status]
    if missing:
        from app.services.semantic_search import _generation_status_fields

        fields = await _generation_status_fields()
        for key in missing:
            index_status[key] = fields.get(key)
    available, reason = semantic_availability(index_status)
    index_status["semantic_available"] = available
    index_status["semantic_reason"] = reason
    index_status["semantic_reason_text"] = SEMANTIC_REASON_TEXT.get(reason) if reason else None
    return index_status


@router.post("/reindex")
async def trigger_reindex(
    project_id: str | None = None,
    full: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Manually trigger a search reindex via Celery task.

    Pass ``full=true`` for a complete rebuild; default is incremental.

    Authorization mirrors the blast radius rather than one flat role. This
    endpoint previously took no user at all — every other endpoint in this
    module depends on ``get_current_active_user`` — so a VIEWER holding zero
    project memberships could queue an instance-wide rebuild.

    * A named project needs QA_LEAD **and** membership: reindexing burns
      worker capacity, which VIEWER (read-only) has no business spending.
    * No project named means *every* tenant's index, so that variant is
      ADMIN-only — otherwise a lead in one project rebuilds everyone else's.
    """
    # The role check is in this body, not require_role, so the scope rule is
    # applied here: a scoped key without project:admin (a CI streaming key)
    # does not spend worker capacity on a reindex (review of QA-R4-1).
    from app.core.deps import _refuse_scoped_key_without_project_admin  # noqa: PLC0415

    _refuse_scoped_key_without_project_admin(current_user)
    role = getattr(current_user.role, "value", current_user.role)
    is_admin = role == UserRole.ADMIN.value
    # A project-bound API key is not an instance admin, whatever its owner's
    # role (re-audit N20): it may reindex its own project, never every tenant.
    # A named project is already confined by resolve_project_scope below.
    bound_project_id = _api_key_bound_project(current_user)

    if project_id is None:
        if not is_admin or bound_project_id is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "An instance-wide reindex requires an instance ADMIN, not a "
                    "project-bound API key. Pass project_id to reindex one project."
                ),
            )
    else:
        if not is_admin and role != UserRole.QA_LEAD.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Triggering a reindex requires QA_LEAD or higher",
            )
        # Verifies the *provided* project, raising 403 for a non-member.
        await resolve_project_scope(db, current_user, project_id)

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
        select(func.count(TestRun.id))
        .join(Project, TestRun.project_id == Project.id)
        .where(Project.is_active.is_(True)),
        TestRun.project_id,
    )
    # ``test_cases`` has no project_id column; join via TestRun.
    test_cases_q = _apply_project_filter(
        select(func.count(TestCase.id))
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .join(Project, TestRun.project_id == Project.id)
        .where(Project.is_active.is_(True)),
        TestRun.project_id,
    )
    suites_q = _apply_project_filter(
        select(func.count(TestSuite.id))
        .join(Project, TestSuite.project_id == Project.id)
        .where(Project.is_active.is_(True)),
        TestSuite.project_id,
    )
    defects_q = _apply_project_filter(
        select(func.count(Defect.id))
        .join(Project, Defect.project_id == Project.id)
        .where(Project.is_active.is_(True)),
        Defect.project_id,
    )
    flaky_q = _apply_project_filter(
        select(func.count(FlakyQuarantineRequest.id))
        .join(Project, FlakyQuarantineRequest.project_id == Project.id)
        .where(
            Project.is_active.is_(True),
            FlakyQuarantineRequest.status != "RELEASED",
        ),
        FlakyQuarantineRequest.project_id,
    )
    releases_q = _apply_project_filter(
        select(func.count(Release.id))
        .join(Project, Release.project_id == Project.id)
        .where(Project.is_active.is_(True)),
        Release.project_id,
    )

    # AsyncSession permits only one operation at a time. These counts share
    # the request-scoped session, so execute them sequentially; ``gather`` can
    # overlap driver operations and fail with "another operation is in
    # progress" under real network latency.
    test_run = await _count(test_runs_q)
    test_case = await _count(test_cases_q)
    suite = await _count(suites_q)
    defect = await _count(defects_q)
    flaky_test = await _count(flaky_q)
    release = await _count(releases_q)

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
    current_user: User = Depends(get_current_active_user),
):
    """Find similar FAILURES within the source test case's project.

    The source's project is authorised (``resolve_project_scope``) before any
    Chroma read; a malformed, unknown or textless id answers an empty list.
    The semantic read asks for ``min(limit * 4, 40)`` candidates and keeps
    FAILED / BROKEN results only, the source excluded, the top ``limit`` in
    relevance order. ``semantic_status`` says whether the index answered:
    ``ok``, ``not_configured`` (no ChromaDB server answers) or ``unavailable``
    -- an outage is never shown as "nothing similar".
    """
    from sqlalchemy import select
    from app.models.postgres import TestCase
    import uuid as _uuid

    try:
        source_id = _uuid.UUID(test_case_id)
    except (TypeError, ValueError, AttributeError):
        return {"items": [], "total": 0, "query": test_case_id}

    tc_result = await db.execute(
        select(
            TestCase.test_name,
            TestCase.error_message,
            TestRun.project_id,
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(TestCase.id == source_id)
    )
    row = tc_result.first()

    if not row:
        return {"items": [], "total": 0, "query": test_case_id}

    query_text = f"{row.test_name} {(row.error_message or '')[:300]}".strip()
    if not query_text:
        return {"items": [], "total": 0, "query": test_case_id}

    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db,
        current_user,
        str(row.project_id),
    )

    from app.services.semantic_search import semantic_search

    semantic_status = "ok"
    try:
        candidates, _, _ = await semantic_search(
            db,
            q=query_text,
            page=1,
            size=min(limit * 4, _SIMILAR_CANDIDATE_CAP),
            project_id=str(scoped_project_id),
            allowed_project_ids=allowed_project_ids,
            raise_on_provider_error=True,
        )
    except SQLAlchemyError:
        raise  # a database failure is not "the semantic index is down"
    except Exception as exc:  # noqa: BLE001 -- the index answered badly or not at all
        from app.services.semantic_search import classify_chroma_error

        candidates = []
        semantic_status = classify_chroma_error(exc)
        logger.warning("similar_failures_semantic_unavailable error_type=%s", type(exc).__name__)

    source = str(source_id)
    items = [
        item for item in candidates
        if str(item.get("test_case_id", "")) != source
        and str(getattr(item.get("status"), "value", item.get("status")) or "").upper() in _SIMILAR_STATUSES
    ][:limit]
    return {
        "items": items,
        "total": len(items),
        "query": query_text[:100],
        "semantic_status": semantic_status,
    }


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
    record: bool = False,
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
):
    """
    Search test cases by name, suite, or error message.

    search_type options:
    - keyword  (default): ILIKE substring matching across name/suite/error columns.
    - semantic: ChromaDB vector-similarity search — finds conceptually similar failures.
    - hybrid:   Merges keyword + semantic results, deduplicates, and re-ranks by relevance.

    The response always includes search_type to reflect the mode actually used
    (may fall back to keyword if ChromaDB is unavailable), plus
    ``requested_search_type`` and, when they differ, WHY (``fallback_reason``:
    ``semantic_unavailable`` -- the index raised; ``no_semantic_match`` -- it
    answered nothing; ``no_text`` -- the query is filters only) with the
    sentence the page shows (``fallback_message``).

    ``q`` takes the structured syntax; this test-result path applies
    ``status:`` (one value), ``since:`` and ``type:`` naming test results.
    Any other filter means nothing is searched, and ``parsed.notes`` says so.
    ``record=true`` (the /search page's own page-1 searches) records the
    search after the response (a recent + a usage row, no query text in the
    row).
    """
    started = time.monotonic()
    stripped = q.strip()
    _refuse_long_query(stripped)
    # Tenant isolation: resolve the effective project scope. ADMIN gets
    # unrestricted access; non-admin is pinned to their accessible set (and
    # receives 403 when requesting a specific project they don't belong to).
    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db, current_user, project_id,
    )
    scoped_project_id_str = str(scoped_project_id) if scoped_project_id else None

    now = datetime.now(timezone.utc)
    parsed = parse_query(stripped, now=now)
    semantic_mode = search_type in SEMANTIC_MODES
    record_kwargs: dict[str, Any] = dict(
        record=record, page=page, current_user=current_user, stripped=stripped, parsed=parsed,
        project_id=scoped_project_id, entity_scope="tests", mode_requested=search_type,
        started=started, now=now,
    )

    # This path applies status: (ONE value), since: and type: naming test
    # results. Anything else cannot be applied here, so nothing is searched
    # (rather than silently dropping a filter the person typed).
    blocked = sorted(
        (set(parsed.keys()) - SEMANTIC_FILTER_KEYS)
        | ({"status"} if len(parsed.statuses) > 1 else set())
        | ({"type"} if parsed.types and "test_case" not in parsed.types else set())
    )
    if blocked:
        response: dict[str, Any] = {
            "items": [], "total": 0, "query": q, "search_type": search_type,
            "page": page, "size": size, "pages": 0,
            "result_status": "complete", "counts_are_exact": True, "failed_entity_types": [],
            "scope": dict(RELEASE_SCOPE_DECLARATION),
            "requested_search_type": search_type, "fallback_reason": None, "fallback_message": None,
            "parsed": parsed_block(
                parsed, unsupported_by={"test_case": blocked}, searched=[],
                release_unresolved=None, semantic_mode=semantic_mode,
            ),
        }
        _schedule_after_search(
            background_tasks, **record_kwargs, mode_used=search_type, fallback_reason=None,
            result_count=0, counts_exact=True,
        )
        return response

    effective_status = STATUS_VALUES[next(iter(parsed.statuses))] if parsed.statuses else status
    effective_days = whole_days_since(parsed.since, now) if parsed.since is not None else days

    actual_type = search_type
    start = time.monotonic()

    # Keyword search is the source-of-truth fallback for every mode: it
    # reads straight from Postgres and never depends on ChromaDB. The
    # ``with_fallback`` helper routes around a ChromaDB outage (primary
    # raises: ``raise_on_provider_error=True``) and a zero-result response
    # (the primary answered nothing) so the user always sees data from the
    # DB instead of an empty page -- and ``fallback_reason`` says which.
    common_kwargs = dict(
        q=parsed.text, page=page, size=size,
        project_id=scoped_project_id_str, status=effective_status, days=effective_days,
        allowed_project_ids=allowed_project_ids,
    )

    async def _keyword():
        return await search_test_cases_query(db, **common_kwargs)

    fallback_used = False
    outage = False

    async def _keyword_fallback():
        nonlocal fallback_used
        fallback_used = True
        return await _keyword()

    def _is_empty(result):
        # ``semantic_search`` returns (items, total, pages); fall back when
        # total is 0 — a legitimate no-match query (``no_semantic_match``,
        # where keyword may still find rows by substring).
        return result[1] == 0

    fallback_reason: Optional[str] = None
    if semantic_mode and not parsed.text:
        # Filters only: there are no words to embed. Keyword answers directly.
        items, total, pages = await _keyword()
        actual_type = "keyword"
        fallback_reason = "no_text"
        semantic_search_total.labels(search_type=search_type, status="fallback").inc()

    elif search_type == "semantic":
        from app.services.semantic_search import semantic_search

        async def _semantic():
            nonlocal outage
            try:
                return await semantic_search(db, **common_kwargs, raise_on_provider_error=True)
            except Exception as exc:
                outage = True
                raise _PrimaryFailed(type(exc).__name__) from None

        items, total, pages = await with_fallback(
            primary=_semantic,
            fallback=_keyword_fallback,
            name="search.semantic",
            is_empty=_is_empty,
        )
        # The final result count cannot reveal which callable produced it: a
        # successful keyword fallback commonly returns non-zero rows. Track the
        # fallback invocation itself so response metadata and metrics stay true.
        actual_type = "keyword" if fallback_used else "semantic"
        semantic_search_total.labels(
            search_type="semantic",
            status="fallback" if fallback_used else "success",
        ).inc()

    elif search_type == "hybrid":
        from app.services.semantic_search import hybrid_search

        async def _hybrid():
            # hybrid_search raises on a provider error itself.
            nonlocal outage
            try:
                return await hybrid_search(db, **common_kwargs)
            except Exception as exc:
                outage = True
                raise _PrimaryFailed(type(exc).__name__) from None

        items, total, pages = await with_fallback(
            primary=_hybrid,
            fallback=_keyword_fallback,
            name="search.hybrid",
            is_empty=_is_empty,
        )
        actual_type = "keyword" if fallback_used else "hybrid"
        semantic_search_total.labels(
            search_type="hybrid",
            status="fallback" if fallback_used else "success",
        ).inc()

    else:
        items, total, pages = await _keyword()
        actual_type = "keyword"
        semantic_search_total.labels(search_type="keyword", status="success").inc()

    if fallback_used:
        fallback_reason = "semantic_unavailable" if outage else "no_semantic_match"

    semantic_search_duration_seconds.labels(search_type=actual_type).observe(time.monotonic() - start)

    response = {
        "items": items,
        "total": total,
        "query": q,
        "search_type": actual_type,
        "page": page,
        "size": size,
        "pages": pages,
        # Semantic and hybrid totals are derived from a bounded Chroma
        # candidate window. Keep the wire shape additive while making that
        # lower-bound contract explicit to API consumers.
        "result_status": "complete" if actual_type == "keyword" else "partial",
        "counts_are_exact": actual_type == "keyword",
        "failed_entity_types": [],
        "scope": dict(RELEASE_SCOPE_DECLARATION),
        "requested_search_type": search_type,
        "fallback_reason": fallback_reason,
        "fallback_message": FALLBACK_TEXT.get(fallback_reason) if fallback_reason else None,
        "parsed": parsed_block(
            parsed, unsupported_by={}, searched=["test_case"], release_unresolved=None,
            semantic_mode=semantic_mode,
        ),
    }
    _schedule_after_search(
        background_tasks, **record_kwargs, mode_used=actual_type, fallback_reason=fallback_reason,
        result_count=total, counts_exact=response["counts_are_exact"],
    )
    return response


@router.get("/global")
async def global_search_endpoint(
    q: str = Query("", description="Search query — empty browses the most-recent items in scope"),
    project_id: Optional[str] = None,
    entity_types: Optional[str] = Query(None, description="Comma-separated entity types to search"),
    days: Optional[int] = Query(None, ge=1, le=365),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    record: bool = False,
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
):
    """
    System-wide global search across multiple entity types.

    Searches test cases, test runs, suites, defects, flaky tests, and releases.
    Returns mixed results with entity badges and navigation URLs.

    An empty ``q`` acts as a browse: the chips on the /search page show
    project-scoped totals from ``/entity-counts``, and selecting a chip with
    no query should surface real records of that type rather than a blank
    panel. Adapters fall back to "most recent N" when ``q`` is empty.

    ``q`` takes the structured syntax (``status:`` ``suite:`` ``branch:``
    ``env:`` ``type:`` ``since:`` ``release:`` ``owner:``); the ``parsed``
    block lists the applied filters, the ignored tokens, the entity types
    that could not apply a filter (and so were not searched) and a sentence
    for each. A ``release:`` value is resolved only inside the caller's
    scope. ``record=true`` (the /search page's own page-1 searches) records
    the search after the response.
    """
    started = time.monotonic()
    stripped = q.strip()
    _refuse_long_query(stripped)
    # Tenant isolation: resolve the effective scope. Non-admin users without a
    # specific project get fanned out across their membership set; non-admin
    # users requesting a project they don't belong to get 403 from the helper.
    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db, current_user, project_id,
    )

    from app.services.global_search_service import (
        ALL_ENTITY_TYPES,
        global_search,
        resolve_release_values,
    )

    types: set[str] | None = None
    if entity_types:
        requested_types = {t.strip() for t in entity_types.split(",") if t.strip()}
        invalid_types = requested_types - ALL_ENTITY_TYPES
        if invalid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported entity types: {', '.join(sorted(invalid_types))}",
            )
        types = requested_types or None

    now = datetime.now(timezone.utc)
    parsed = parse_query(stripped, now=now)
    release_ids: Optional[tuple[uuid.UUID, ...]] = None
    release_unresolved: Optional[str] = None
    if parsed.releases:
        # Inside the caller's resolved scope only: a foreign release resolves
        # to nothing, and reads exactly like an unknown one (no oracle).
        release_ids, release_unresolved = await resolve_release_values(
            db, parsed.releases, project_id=scoped_project_id, allowed_project_ids=allowed_project_ids,
        )

    result = await global_search(
        db=db,
        q=q,
        project_id=str(scoped_project_id) if scoped_project_id else None,
        entity_types=types,
        days=days,
        page=page,
        size=size,
        allowed_project_ids=allowed_project_ids,
        parsed=parsed,
        release_ids=release_ids,
        release_unresolved=release_unresolved,
    )
    # Same behaviour as `/search`, so the same declaration. Attached here
    # rather than in the service because it describes the HTTP contract, and
    # `global_search` is also called from places that are not this endpoint.
    result["scope"] = dict(RELEASE_SCOPE_DECLARATION)

    # SRCH-05: every call is timed; an exact zero for a query with words is
    # counted. Labels carry the scope's SHAPE only (all | one), never text.
    one_type = types is not None and len(types) == 1
    scope_label = "one" if one_type else "all"
    global_search_duration_seconds.labels(scope=scope_label).observe(time.monotonic() - started)
    exact = bool(result.get("counts_are_exact", True))
    if stripped and result.get("total", 0) == 0 and exact:
        global_search_zero_results_total.labels(scope=scope_label).inc()

    entity_scope = _ENTITY_SCOPE.get(next(iter(types)), "all") if (types and one_type) else "all"
    _schedule_after_search(
        background_tasks, record=record, page=page, current_user=current_user, stripped=stripped,
        parsed=parsed, project_id=scoped_project_id, entity_scope=entity_scope,
        mode_requested="keyword", mode_used="keyword", fallback_reason=None,
        result_count=int(result.get("total", 0)), counts_exact=exact, started=started, now=now,
    )
    return result


# ── Recent and suggested searches (W12 SRCH-03) ──────────────────────────────


def _recent_visible(entry: dict, accessible: Optional[set[uuid.UUID]]) -> bool:
    """An entry of All Projects, or of a project the reader can see NOW."""
    if entry.get("project_id") is None or accessible is None:
        return True
    try:
        return uuid.UUID(str(entry["project_id"])) in accessible
    except ValueError:
        return False


@router.get("/recent", response_model=RecentSearchesResponse)
async def list_recent_searches(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """The caller's own last searches (Redis, newest first, at most 20, kept 30 days).

    Keyed by the CALLER's id. Each entry's project is re-checked against the
    projects the reader can access NOW: an entry of a project they lost is
    left out of the answer (this GET writes nothing, so it is filtered, not
    deleted). A Redis fault answers an empty list.
    """
    entries = await read_recents(str(current_user.id))
    accessible: Optional[set[uuid.UUID]] = None
    if any(entry.get("project_id") is not None for entry in entries):
        accessible = await get_accessible_project_ids(db, current_user)
    return {
        "items": [entry for entry in entries if _recent_visible(entry, accessible)],
        "max": RECENT_MAX,
        "retention_days": RECENT_TTL_DAYS,
    }


# activity: none - per-user search history in Redis; no project, nothing a reader would call an activity
@router.delete("/recent", status_code=status.HTTP_204_NO_CONTENT)
async def clear_recent_searches(
    current_user: User = Depends(get_current_active_user),
):
    """Forget the caller's recent searches (their own Redis list only)."""
    try:
        await clear_recents(str(current_user.id))
    except RecentsUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=RECENTS_UNAVAILABLE)
    return None


@router.get("/suggested", response_model=SearchSuggestionsResponse)
async def suggested_searches(
    project_id: str = Query(..., description="The ONE project the suggestions are for"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """What the /search page offers while its box is empty, for one project.

    The tests failing most in the last 7 days (top 5, each with its failure
    count), the newest open defects with a Jira key (top 5) and the active
    release. The project is authorised FIRST (400 / 403).
    """
    scoped_project_id, _ = await resolve_project_scope(db, current_user, project_id)
    if scoped_project_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid project ID")
    return await suggestions_for_project(db, scoped_project_id, now=datetime.now(timezone.utc))


# ── Search usage (W12 SRCH-05) ───────────────────────────────────────────────


@router.get("/usage", response_model=SearchUsageRead)
async def search_usage(
    project_id: Optional[str] = Query(None),
    days: int = Query(1, description="1 (today, UTC), 7 or 30"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Searches, the zero-result rate and the latency percentiles of recorded searches.

    QA lead and up. The project is authorised FIRST; without one a QA lead
    sees the projects they belong to and an ADMIN the whole deployment. No
    search in the window answers ``measured: false`` (never ``0 %``).
    """
    scoped_project_id, allowed_project_ids = await resolve_project_scope(db, current_user, project_id)
    if days not in USAGE_DAYS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=USAGE_DAYS_SENTENCE)
    return await usage_summary(
        db,
        project_id=scoped_project_id,
        allowed_project_ids=allowed_project_ids,
        days=days,
        now=datetime.now(timezone.utc),
    )
