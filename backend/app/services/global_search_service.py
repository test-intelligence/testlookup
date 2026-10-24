"""
Global Search Service — multi-entity search across the system.

Fans out a query to entity-specific adapters (test cases, runs, suites,
defects, flaky tests, releases), merges results, sorts by relevance,
and returns a paginated GlobalSearchResponse.

Usage:
    from app.services.global_search_service import global_search
    response = await global_search(db, q="payment", project_id=None)

Structured filters (W12 SRCH-04): the router hands ``global_search`` the
``ParsedQuery`` of ``services/search_query_parser``. Its free text is what the
adapters match; its filters become one :class:`AdapterFilters`, and each
adapter ANDs the filters it supports INSIDE its own statement -- inside EACH
half of the test-case UNION, never as an OR across tables (re-audit H9: a
cross-table OR defeats every trigram index). With no filter given, every
statement is byte-identical to the one built before W12. An entity type that
cannot apply a given filter (``core/search_vocab.FILTER_SUPPORT``) is NOT
searched, and the ``parsed`` block says so.
"""
import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence
from urllib.parse import urlencode

import structlog
from sqlalchemy import any_, bindparam, case, cast, func, select, or_, String, Text, union
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.search_vocab import FILTER_SUPPORT, RUN_STATUS_VALUES
from app.models.postgres import (
    Defect,
    Project,
    Release,
    TestCase,
    TestCaseHistory,
    TestRun,
)
from app.services.analytics_scope import effective_suite_clause, run_in_suite_scope_clause
from app.services.search_query_parser import (
    STATUS_NAME_TO_DB,
    ParsedQuery,
    parsed_block,
    type_outside_scope_note,
)
from app.services.sql_utils import like_contains
logger = structlog.get_logger("services.global_search")

# Supported entity types
ALL_ENTITY_TYPES = {"test_case", "test_run", "suite", "defect", "flaky_test", "release"}
_DEFAULT_ADAPTER_LIMITS = {
    "test_case": 50,
    "test_run": 20,
    "suite": 15,
    "defect": 20,
    "flaky_test": 15,
    "release": 10,
}

#: At most this many releases are read to resolve the ``release:`` values of one query.
_RELEASE_RESOLVE_LIMIT = 50


@dataclass(frozen=True, slots=True)
class AdapterFilters:
    """The structured filters one adapter ANDs into its statement (only those given).

    ``statuses`` holds the normalised ``status:`` names (``passed`` /
    ``failed`` / ``error`` / ``skipped``). ``release_ids`` is ``None`` when no
    ``release:`` was typed and ``()`` when one was typed but resolved to no
    release in the caller's scope -- then every adapter that supports it
    returns ``[]`` without a statement (never a broadened search).
    """

    statuses: frozenset[str] = frozenset()
    suites: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    envs: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    release_ids: tuple[uuid.UUID, ...] | None = None

    @property
    def nothing_to_find(self) -> bool:
        return self.release_ids is not None and not self.release_ids


def _filters_from(parsed: ParsedQuery, release_ids: Sequence[uuid.UUID] | None) -> AdapterFilters:
    resolved: tuple[uuid.UUID, ...] | None = None
    if parsed.releases:
        # A release: that the caller did not resolve finds nothing (fail closed).
        resolved = tuple(dict.fromkeys(release_ids or ()))
    return AdapterFilters(
        statuses=parsed.statuses,
        suites=parsed.suites,
        branches=parsed.branches,
        envs=parsed.envs,
        owners=parsed.owners,
        release_ids=resolved,
    )


def unsupported_keys(entity_type: str, parsed: ParsedQuery) -> list[str]:
    """The applied filter keys ``entity_type`` cannot apply (it is then NOT searched)."""
    keys = set(parsed.keys()) - FILTER_SUPPORT.get(entity_type, frozenset())
    if entity_type == "test_run" and parsed.statuses - set(RUN_STATUS_VALUES):
        # LaunchStatus has PASSED / FAILED only: status:error / skipped cannot apply to a run.
        keys.add("status")
    return sorted(keys)


async def resolve_release_values(
    db: AsyncSession,
    values: Sequence[str],
    *,
    project_id: uuid.UUID | str | None,
    allowed_project_ids: set[uuid.UUID] | None,
) -> tuple[tuple[uuid.UUID, ...], str | None]:
    """Resolve typed ``release:`` values to release ids INSIDE the caller's resolved scope.

    ONE statement: ``releases`` of active projects in the scope (the pinned
    project, the caller's memberships, or -- ADMIN -- every project) whose id,
    name or version (case-insensitive) is one of ``values``; an OR on ONE
    table. A foreign release id or name matches nothing, so unknown, malformed
    and foreign values all read the same (no oracle). Returns the matched ids
    and the first value that matched nothing (``None`` when every value
    matched). No values, or an empty membership set: no statement.
    """
    wanted = [str(v).strip() for v in values if str(v).strip()]
    if not wanted:
        return (), None
    if project_id is None and allowed_project_ids is not None and not allowed_project_ids:
        return (), wanted[0]

    ids: list[uuid.UUID] = []
    for value in wanted:
        try:
            ids.append(uuid.UUID(value))
        except ValueError:
            continue
    names = list(dict.fromkeys(value.lower() for value in wanted))
    names_param = bindparam("release_names", value=names, type_=ARRAY(String()))
    match = [
        func.lower(Release.name) == any_(names_param),
        func.lower(Release.version) == any_(names_param),
    ]
    if ids:
        match.insert(0, Release.id == any_(bindparam(
            "release_ids", value=list(dict.fromkeys(ids)), type_=ARRAY(PG_UUID(as_uuid=True)),
        )))
    stmt = (
        select(Release.id, Release.name, Release.version)
        .join(Project, Project.id == Release.project_id)
        .where(Project.is_active.is_(True))
    )
    if project_id is not None:
        stmt = stmt.where(Release.project_id == uuid.UUID(str(project_id)))
    elif allowed_project_ids is not None:
        stmt = stmt.where(Release.project_id == any_(bindparam(
            "release_scope_ids", value=sorted(allowed_project_ids, key=str), type_=ARRAY(PG_UUID(as_uuid=True)),
        )))
    stmt = stmt.where(or_(*match)).limit(_RELEASE_RESOLVE_LIMIT)

    rows = (await db.execute(stmt)).all()
    matched: list[uuid.UUID] = []
    unresolved: str | None = None
    for value in wanted:
        lowered = value.lower()
        hits = [
            row.id for row in rows
            if str(row.id) == lowered
            or (row.name or "").lower() == lowered
            or (row.version or "").lower() == lowered
        ]
        if hits:
            matched.extend(hits)
        elif unresolved is None:
            unresolved = value
    return tuple(dict.fromkeys(matched)), unresolved


async def global_search(
    db: AsyncSession,
    q: str,
    project_id: Optional[str] = None,
    entity_types: Optional[set[str]] = None,
    days: Optional[int] = None,
    page: int = 1,
    size: int = 20,
    allowed_project_ids: Optional[set[uuid.UUID]] = None,
    *,
    parsed: ParsedQuery | None = None,
    release_ids: Sequence[uuid.UUID] | None = None,
    release_unresolved: str | None = None,
) -> dict[str, Any]:
    """Execute a global search across multiple entity types.

    Tenant isolation is enforced by passing one of:

    - ``project_id`` — pin results to a single project (caller must verify access)
    - ``allowed_project_ids`` — fan out across this membership set (non-admin)
    - Neither — admin only, unrestricted

    An empty ``allowed_project_ids`` set short-circuits to zero results without
    touching the database.

    ``parsed`` (W12 SRCH-04, the router always passes one): its free text is
    matched instead of ``q``; ``since:`` overrides ``days``; ``type:`` narrows
    the entity types; every other filter is ANDed by the adapters that
    support it, and an entity type that cannot apply one is not searched. The
    result then carries a ``parsed`` block (``SearchParsed``). ``release_ids``
    are the router's ``resolve_release_values`` answer for ``parsed.releases``
    (``release_unresolved`` its first unmatched value). ``parsed`` None: the
    statements and the dict are exactly what they were before W12.

    Returns a dict matching GlobalSearchResponse shape.
    """
    # ``None`` means the caller omitted the filter. An explicit empty set
    # means "search none" and must never broaden into every adapter.
    types = ALL_ENTITY_TYPES if entity_types is None else entity_types
    period_start = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    filters: AdapterFilters | None = None
    match_text = q
    block: dict[str, Any] | None = None
    if parsed is not None:
        match_text = parsed.text
        if parsed.since is not None:
            period_start = parsed.since
        extra_notes: list[str] = []
        if parsed.types:
            narrowed = set(types) & set(parsed.types)
            if not narrowed and entity_types is not None:
                extra_notes.append(type_outside_scope_note(parsed, sorted(entity_types)))
            types = narrowed
        unsupported = {
            entity: keys for entity in sorted(types) if (keys := unsupported_keys(entity, parsed))
        }
        types = {entity for entity in types if entity not in unsupported}
        filters = _filters_from(parsed, release_ids)
        empty_scope = project_id is None and allowed_project_ids is not None and not allowed_project_ids
        block = parsed_block(
            parsed,
            unsupported_by=unsupported,
            searched=[] if empty_scope else sorted(types),
            release_unresolved=release_unresolved,
            extra_notes=extra_notes,
        )

    # Short-circuit: non-admin with no project memberships cannot see anything.
    if project_id is None and allowed_project_ids is not None and not allowed_project_ids:
        empty: dict[str, Any] = {
            "items": [], "total": 0, "query": q, "search_type": "keyword",
            "entity_counts": {}, "page": page, "size": size, "pages": 0,
            "result_status": "complete", "failed_entity_types": [],
            "counts_are_exact": True,
        }
        if block is not None:
            empty["parsed"] = block
        return empty

    # When the caller narrows to a single entity type (Tests, Runs, …),
    # bump the adapter's per-type cap so the user can paginate through
    # the real project total. Fan-out mode keeps the small per-adapter
    # caps because the global view shows "top N relevant per type" by
    # design — we'd otherwise pull thousands of rows just to render one
    # page. 2026-05-15: the user reported the Tests chip said 84 but
    # only 25 results rendered with no way to see the rest; the cause
    # was ``_search_test_cases.limit(50)`` capping the dataset under
    # the slice, so pagination had nothing to paginate over.
    narrowed_to_one = entity_types is not None and len(entity_types) == 1
    if parsed is not None:
        # A "type:" filter (or a filter only one kind applies) leaves one kind to page through.
        narrowed_to_one = len(types) == 1
    # Cover up to ``page * size`` with headroom so the last page renders
    # in full. ``min(..., 1000)`` is a sanity guard against pathological
    # callers asking for page 200; if needed, the project is large
    # enough to justify a proper per-type endpoint.
    adapter_override_limit: Optional[int] = (
        min(max(page * size + size, 200), 1000) if narrowed_to_one else None
    )

    # Fan out to adapters
    all_results: list[dict] = []
    failed_entity_types: list[str] = []
    capped_entity_types: list[str] = []
    for entity_type in sorted(types):
        adapter = _ADAPTERS.get(entity_type)
        if adapter:
            try:
                if filters is None:
                    results = await adapter(
                        db, q, project_id, period_start, allowed_project_ids,
                        override_limit=adapter_override_limit,
                    )
                else:
                    results = await adapter(
                        db, match_text, project_id, period_start, allowed_project_ids,
                        override_limit=adapter_override_limit, filters=filters,
                    )
                all_results.extend(results)
                effective_limit = adapter_override_limit or _DEFAULT_ADAPTER_LIMITS.get(
                    entity_type
                )
                if effective_limit is not None and len(results) >= effective_limit:
                    capped_entity_types.append(entity_type)
            except Exception as exc:
                # The exception's TYPE only: a database error's text carries the
                # statement's bound parameters, i.e. the search text (W12).
                logger.warning("search_adapter_failed", entity_type=entity_type, error_type=type(exc).__name__)
                failed_entity_types.append(entity_type)

    # Sort by relevance descending
    all_results.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)

    # Entity counts
    entity_counts = dict(Counter(r["entity_type"] for r in all_results))

    # Paginate
    total = len(all_results)
    start = (page - 1) * size
    page_results = all_results[start:start + size]
    counts_are_exact = not failed_entity_types and not capped_entity_types

    result: dict[str, Any] = {
        "items": page_results,
        "total": total,
        "query": q,
        "search_type": "keyword",
        "entity_counts": entity_counts,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if size > 0 else 0,
        # Adapters intentionally use bounded samples. Disclose failures and
        # cap hits so consumers never present a lower bound as an exact total.
        "result_status": "complete" if counts_are_exact else "partial",
        "failed_entity_types": failed_entity_types,
        "counts_are_exact": counts_are_exact,
    }
    if block is not None:
        result["parsed"] = block
    return result


# ── Entity Adapters ────────────────────────────────────────────────────────


def _apply_tenant_filter(stmt, column, project_id, allowed_project_ids):
    """Apply either a single-project pin or an accessible-set ``IN`` filter."""
    if project_id:
        return stmt.where(column == project_id)
    if allowed_project_ids is not None:
        return stmt.where(column.in_(list(allowed_project_ids)))
    return stmt


def _lowered_in(column, values: Sequence[str]):
    return func.lower(column).in_(list(dict.fromkeys(v.lower() for v in values)))


def _contains_any(column, values: Sequence[str]):
    """An OR on ONE column: ``column ILIKE %v%`` for each value (bind parameters only)."""
    return or_(*(column.ilike(like_contains(v), escape="\\") for v in values))


def _run_terms(filters: AdapterFilters | None) -> list:
    """The ``branch:`` / ``env:`` / ``release:`` terms on ``TestRun`` (given ones only)."""
    if filters is None:
        return []
    terms: list[Any] = []
    if filters.branches:
        terms.append(_lowered_in(TestRun.branch, filters.branches))
    if filters.envs:
        terms.append(_lowered_in(TestRun.environment, filters.envs))
    if filters.release_ids:
        terms.append(TestRun.primary_release_id.in_(list(filters.release_ids)))
    return terms


def _test_case_terms(filters: AdapterFilters | None) -> list:
    """Every filter a test-case row applies, as AND terms (given ones only)."""
    if filters is None:
        return []
    terms: list[Any] = []
    if filters.statuses:
        terms.append(TestCase.status.in_(sorted(STATUS_NAME_TO_DB[s] for s in filters.statuses)))
    suite = effective_suite_clause(filters.suites) if filters.suites else None
    if suite is not None:
        terms.append(suite)
    terms.extend(_run_terms(filters))
    if filters.owners:
        terms.append(_contains_any(TestCase.owner, filters.owners))
    return terms


def _with_terms(stmt, terms: list):
    """AND the given terms into ``stmt``'s WHERE; no terms -> ``stmt`` unchanged (byte-identical SQL)."""
    return stmt.where(*terms) if terms else stmt


async def _search_test_cases(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
    *,
    filters: AdapterFilters | None = None,
) -> list[dict]:
    if filters is not None and filters.nothing_to_find:
        return []
    pattern = like_contains(q)
    limit = override_limit or 50
    terms = _test_case_terms(filters)

    # Re-audit H9. This used to be ONE OR across five predicates, and two of
    # them each defeated the trigram indexes on test_cases (measured on the
    # homelab, 50,510 test_cases -- see migration 0166 for the plans):
    #
    #   * TestRun.primary_suite_name is a column on ANOTHER table. A bitmap OR
    #     only combines indexes on one table, so that branch forced the whole
    #     OR to be checked row by row. Dropping the tags branch alone left the
    #     plan identical.
    #   * CAST(tags AS VARCHAR) had no index, so even a one-table OR collapsed.
    #
    # So the run-level branch is its own half of a UNION, and tags are cast to
    # TEXT to match ix_test_cases_tags_trgm exactly (an expression index is
    # only used for a predicate written identically; VARCHAR is a different
    # expression). The first half is then a four-way BitmapOr.
    #
    # Each half applies the tenant, active-project and period filters itself
    # and takes its own top ``limit`` by recency. The global top ``limit`` is
    # always inside the union of each half's top ``limit``, and bounding each
    # half keeps a broad term from materialising every match before sorting.
    # Ids only ever come from a filtered half, so the tenant filter cannot be
    # bypassed by the outer query.
    #
    # W12 SRCH-04: the structured filters are AND terms of EACH half (after
    # the match OR, never inside it), so the own-text half stays a one-table
    # BitmapOr and a filter cannot leak rows in through the other half.
    def _half(match):
        stmt = (
            select(TestCase.id.label("id"), TestCase.created_at.label("created_at"))
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .join(Project, Project.id == TestRun.project_id)
            .where(match, Project.is_active.is_(True))
        )
        stmt = _apply_tenant_filter(
            stmt, TestRun.project_id, project_id, allowed_project_ids
        )
        if period_start:
            stmt = stmt.where(TestCase.created_at >= period_start)
        stmt = _with_terms(stmt, terms)
        return stmt.order_by(TestCase.created_at.desc()).limit(limit)

    own_text = _half(or_(
        TestCase.test_name.ilike(pattern, escape="\\"),
        TestCase.suite_name.ilike(pattern, escape="\\"),
        TestCase.error_message.ilike(pattern, escape="\\"),
        cast(TestCase.tags, Text).ilike(pattern, escape="\\"),
    )).subquery("own_text")
    # Run-level label so a query for the session-supplied suite name (e.g.
    # "API Regression Multi-Class") still surfaces tests of live_stream runs
    # whose per-event ``tc.suite_name`` is the test class name.
    run_suite = _half(
        TestRun.primary_suite_name.ilike(pattern, escape="\\")
    ).subquery("run_suite")

    hits = union(
        select(own_text.c.id, own_text.c.created_at),
        select(run_suite.c.id, run_suite.c.created_at),
    ).subquery("hits")

    stmt = (
        select(TestCase, TestRun.project_id)
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .join(hits, hits.c.id == TestCase.id)
        .order_by(TestCase.created_at.desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    return [
        {
            "entity_type": "test_case",
            "entity_id": str(tc.id),
            "title": tc.test_name,
            "subtitle": f"{tc.suite_name or 'No suite'} · {tc.status}",
            "project_id": str(pid) if pid else None,
            "navigation_url": f"/runs/{tc.test_run_id}/tests/{tc.id}",
            "relevance_score": 0.8,
            "match_reasons": ["Matched test name, suite, or error message"],
            "metadata": {"status": tc.status, "suite_name": tc.suite_name},
        }
        for tc, pid in rows
    ]


async def _search_test_runs(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
    *,
    filters: AdapterFilters | None = None,
) -> list[dict]:
    if filters is not None and filters.nothing_to_find:
        return []
    pattern = like_contains(q)
    stmt = (
        select(TestRun)
        .join(Project, Project.id == TestRun.project_id)
        .where(or_(
            TestRun.build_number.ilike(pattern, escape="\\"),
            TestRun.branch.ilike(pattern, escape="\\"),
            TestRun.jenkins_job.ilike(pattern, escape="\\"),
            cast(TestRun.tags, String).ilike(pattern, escape="\\"),
        ), Project.is_active.is_(True))
        .order_by(TestRun.created_at.desc())
        .limit(override_limit or 20)
    )
    stmt = _apply_tenant_filter(stmt, TestRun.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(TestRun.created_at >= period_start)
    if filters is not None:
        terms: list[Any] = []
        if filters.statuses:
            # status:error / skipped never reach here (unsupported_keys): runs are PASSED / FAILED.
            terms.append(TestRun.status.in_(sorted(
                RUN_STATUS_VALUES[s] for s in filters.statuses if s in RUN_STATUS_VALUES
            )))
        suite = run_in_suite_scope_clause(filters.suites) if filters.suites else None
        if suite is not None:
            terms.append(suite)
        terms.extend(_run_terms(filters))
        stmt = _with_terms(stmt, terms)

    runs = (await db.execute(stmt)).scalars().all()
    return [
        {
            "entity_type": "test_run",
            "entity_id": str(r.id),
            "title": f"Build {r.build_number}",
            # No pass rate (a run in progress, or one that measured nothing) is
            # not 0%: it read "IN_PROGRESS · 0% pass rate" (browser E2E pass).
            "subtitle": f"{r.branch or 'no branch'} · {r.status} · "
            + (f"{r.pass_rate:.0f}% pass rate" if r.pass_rate is not None else "no pass rate yet"),
            "project_id": str(r.project_id),
            "navigation_url": f"/runs/{r.id}",
            "relevance_score": 0.7,
            "match_reasons": ["Matched build number, branch, or job name"],
            "metadata": {"status": str(r.status), "pass_rate": r.pass_rate},
        }
        for r in runs
    ]


async def _search_suites(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
    *,
    filters: AdapterFilters | None = None,
) -> list[dict]:
    if filters is not None and filters.nothing_to_find:
        return []
    pattern = like_contains(q)
    # Group by effective suite name — preferring ``tr.primary_suite_name``
    # for live_stream runs so a session label like "API Regression
    # Multi-Class" is searchable even when the SDK stamped the test
    # class name on every per-event ``tc.suite_name``. File uploads
    # keep their per-event suite (the COALESCE falls through to
    # ``tc.suite_name`` when ``primary_suite_name`` isn't set or the
    # run isn't a live_stream). The ILIKE filter is applied to the
    # effective name so the query string finds run-level labels even
    # when no ``tc.suite_name`` row contains the substring.
    effective_suite = func.coalesce(
        case(
            (TestRun.trigger_source == "live_stream",
             func.nullif(func.trim(TestRun.primary_suite_name), "")),
            else_=None,
        ),
        func.nullif(func.trim(TestCase.suite_name), ""),
    )
    stmt = (
        select(
            effective_suite.label("suite_name"),
            TestRun.project_id.label("project_id"),
            func.count().label("test_count"),
        )
        # The selected expression references both tables, so make TestCase
        # the explicit FROM root before joining TestRun. Without this,
        # SQLAlchemy can raise an ambiguous-join error at runtime and the
        # global-search fan-out silently drops suite results.
        .select_from(TestCase)
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .join(Project, Project.id == TestRun.project_id)
        .where(
            effective_suite.ilike(pattern, escape="\\"),
            effective_suite.isnot(None),
            Project.is_active.is_(True),
        )
        .group_by(effective_suite, TestRun.project_id)
        .order_by(func.count().desc())
        .limit(override_limit or 15)
    )
    stmt = _apply_tenant_filter(stmt, TestRun.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(TestCase.created_at >= period_start)
    if filters is not None:
        # suite: is ANDed with the ILIKE above on the same effective-suite expression.
        suite = effective_suite_clause(filters.suites) if filters.suites else None
        stmt = _with_terms(stmt, ([suite] if suite is not None else []) + _run_terms(filters))

    rows = (await db.execute(stmt)).all()
    return [
        {
            "entity_type": "suite",
            # Suite names are unique only within a project. The composite key
            # keeps same-name rows distinct in API consumers and React lists.
            "entity_id": f"{row.project_id}:{row.suite_name}",
            "title": row.suite_name,
            "subtitle": f"{row.test_count} test executions",
            "project_id": str(row.project_id),
            "navigation_url": "/coverage/suite?" + urlencode(
                {"name": row.suite_name, "project_id": str(row.project_id)}
            ),
            "relevance_score": 0.6,
            "match_reasons": ["Matched suite name"],
            "metadata": {"test_count": row.test_count},
        }
        for row in rows
    ]


async def _search_defects(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
    *,
    filters: AdapterFilters | None = None,
) -> list[dict]:
    pattern = like_contains(q)
    stmt = (
        select(Defect)
        .join(Project, Project.id == Defect.project_id)
        .where(or_(
            Defect.jira_ticket_id.ilike(pattern, escape="\\"),
        ), Project.is_active.is_(True))
        .order_by(Defect.created_at.desc())
        .limit(override_limit or 20)
    )
    stmt = _apply_tenant_filter(stmt, Defect.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(Defect.created_at >= period_start)
    if filters is not None and filters.owners:
        stmt = stmt.where(_contains_any(Defect.owner_team, filters.owners))

    defects = (await db.execute(stmt)).scalars().all()
    return [
        {
            "entity_type": "defect",
            "entity_id": str(d.id),
            "title": d.jira_ticket_id or f"Defect {str(d.id)[:8]}",
            "subtitle": f"{d.failure_category or 'Unknown'} · {d.resolution_status or 'Open'}",
            "project_id": str(d.project_id) if d.project_id else None,
            "navigation_url": "/defects",
            "relevance_score": 0.65,
            "match_reasons": ["Matched Jira ticket ID"],
            "metadata": {"resolution_status": d.resolution_status, "failure_category": d.failure_category},
        }
        for d in defects
    ]


async def _search_flaky_tests(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
    *,
    filters: AdapterFilters | None = None,
) -> list[dict]:
    if filters is not None and filters.nothing_to_find:
        return []
    pattern = like_contains(q)
    # Find test fingerprints with intermittent pass/fail that match the query.
    # ``make_test_fingerprint`` is NOT project-salted, so grouping by
    # fingerprint ALONE blends a same-named test across projects (in fan-out
    # mode across a user's memberships, or unrestricted for an admin) into one
    # flaky entry with summed runs + a ``func.max`` name picked from whichever
    # project — wrong stats and an ambiguous result. Group by
    # ``(fingerprint, project_id)`` so each project's flaky test is its own,
    # correctly-scoped entry. (For a single pinned project the tenant filter
    # already restricts to one project, so this is a no-op there.)
    total_runs = func.count()
    fail_count = func.count().filter(
        TestCaseHistory.status.in_(["FAILED", "BROKEN"])
    )
    stmt = (
        select(
            TestCaseHistory.test_fingerprint,
            TestRun.project_id.label("project_id"),
            func.max(TestCase.test_name).label("test_name"),
            func.max(TestCase.suite_name).label("suite_name"),
            total_runs.label("total_runs"),
            fail_count.label("fail_count"),
        )
        .join(TestCase, TestCaseHistory.test_case_id == TestCase.id)
        .join(TestRun, TestCaseHistory.test_run_id == TestRun.id)
        .join(Project, Project.id == TestRun.project_id)
        .where(
            TestCase.test_name.ilike(pattern, escape="\\"),
            Project.is_active.is_(True),
        )
        .group_by(TestCaseHistory.test_fingerprint, TestRun.project_id)
        # Filter the flaky-rate range before LIMIT. Filtering in Python after
        # LIMIT could return fewer than the cap while eligible groups existed
        # below it, making global search falsely mark the sample exact.
        .having(
            total_runs >= 5,
            fail_count * 100 >= total_runs * 10,
            fail_count * 100 <= total_runs * 90,
        )
        .limit(override_limit or 15)
    )
    stmt = _apply_tenant_filter(stmt, TestRun.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(TestCaseHistory.created_at >= period_start)
    if filters is not None:
        suite = effective_suite_clause(filters.suites) if filters.suites else None
        stmt = _with_terms(stmt, ([suite] if suite is not None else []) + _run_terms(filters))

    rows = (await db.execute(stmt)).all()
    results = []
    for row in rows:
        if row.total_runs > 0:
            rate = (row.fail_count / row.total_runs) * 100
            results.append({
                "entity_type": "flaky_test",
                # Fingerprints are not project-salted. Keep same-test rows
                # from two authorized projects distinct for consumer keys.
                "entity_id": f"{row.project_id}:{row.test_fingerprint}",
                "title": row.test_name or row.test_fingerprint,
                "subtitle": f"{rate:.0f}% failure rate over {row.total_runs} runs",
                "project_id": str(row.project_id) if row.project_id else None,
                "navigation_url": "/failures",
                "relevance_score": 0.55,
                "match_reasons": ["Flaky test matching query"],
                "metadata": {"failure_rate": round(rate, 1), "total_runs": row.total_runs, "suite_name": row.suite_name},
            })
    return results


async def _search_releases(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
    *,
    filters: AdapterFilters | None = None,
) -> list[dict]:
    if filters is not None and filters.nothing_to_find:
        return []
    pattern = like_contains(q)
    stmt = (
        select(Release)
        .join(Project, Project.id == Release.project_id)
        .where(or_(
            Release.name.ilike(pattern, escape="\\"),
            Release.version.ilike(pattern, escape="\\"),
        ), Project.is_active.is_(True))
        .order_by(Release.created_at.desc())
        .limit(override_limit or 10)
    )
    stmt = _apply_tenant_filter(stmt, Release.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(Release.created_at >= period_start)
    if filters is not None and filters.release_ids:
        stmt = stmt.where(Release.id.in_(list(filters.release_ids)))

    releases = (await db.execute(stmt)).scalars().all()
    return [
        {
            "entity_type": "release",
            "entity_id": str(r.id),
            "title": r.name,
            "subtitle": f"{r.version or 'no version'} · {r.status}",
            "project_id": str(r.project_id),
            "navigation_url": f"/releases/{r.id}",
            "relevance_score": 0.5,
            "match_reasons": ["Matched release name or version"],
            "metadata": {"status": str(r.status), "version": r.version},
        }
        for r in releases
    ]


# Adapter registry
_ADAPTERS = {
    "test_case": _search_test_cases,
    "test_run": _search_test_runs,
    "suite": _search_suites,
    "defect": _search_defects,
    "flaky_test": _search_flaky_tests,
    "release": _search_releases,
}
