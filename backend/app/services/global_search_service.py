"""
Global Search Service — multi-entity search across the system.

Fans out a query to entity-specific adapters (test cases, runs, suites,
defects, flaky tests, releases), merges results, sorts by relevance,
and returns a paginated GlobalSearchResponse.

Usage:
    from app.services.global_search_service import global_search
    response = await global_search(db, q="payment", project_id=None)
"""
import math
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import case, cast, func, select, or_, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    Project,
    Release,
    TestCase,
    TestCaseHistory,
    TestRun,
)
from app.services.sql_utils import like_contains
logger = structlog.get_logger("services.global_search")

# Supported entity types
ALL_ENTITY_TYPES = {"test_case", "test_run", "suite", "defect", "flaky_test", "release"}


async def global_search(
    db: AsyncSession,
    q: str,
    project_id: Optional[str] = None,
    entity_types: Optional[set[str]] = None,
    days: Optional[int] = None,
    page: int = 1,
    size: int = 20,
    allowed_project_ids: Optional[set[uuid.UUID]] = None,
) -> dict[str, Any]:
    """Execute a global search across multiple entity types.

    Tenant isolation is enforced by passing one of:

    - ``project_id`` — pin results to a single project (caller must verify access)
    - ``allowed_project_ids`` — fan out across this membership set (non-admin)
    - Neither — admin only, unrestricted

    An empty ``allowed_project_ids`` set short-circuits to zero results without
    touching the database.

    Returns a dict matching GlobalSearchResponse shape.
    """
    # ``None`` means the caller omitted the filter. An explicit empty set
    # means "search none" and must never broaden into every adapter.
    types = ALL_ENTITY_TYPES if entity_types is None else entity_types
    period_start = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    # Short-circuit: non-admin with no project memberships cannot see anything.
    if project_id is None and allowed_project_ids is not None and not allowed_project_ids:
        return {
            "items": [], "total": 0, "query": q, "search_type": "keyword",
            "entity_counts": {}, "page": page, "size": size, "pages": 0,
        }

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
    # Cover up to ``page * size`` with headroom so the last page renders
    # in full. ``min(..., 1000)`` is a sanity guard against pathological
    # callers asking for page 200; if needed, the project is large
    # enough to justify a proper per-type endpoint.
    adapter_override_limit: Optional[int] = (
        min(max(page * size + size, 200), 1000) if narrowed_to_one else None
    )

    # Fan out to adapters
    all_results: list[dict] = []
    for entity_type in types:
        adapter = _ADAPTERS.get(entity_type)
        if adapter:
            try:
                results = await adapter(
                    db, q, project_id, period_start, allowed_project_ids,
                    override_limit=adapter_override_limit,
                )
                all_results.extend(results)
            except Exception as exc:
                logger.warning("search_adapter_failed", entity_type=entity_type, error=str(exc))

    # Sort by relevance descending
    all_results.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)

    # Entity counts
    entity_counts = dict(Counter(r["entity_type"] for r in all_results))

    # Paginate
    total = len(all_results)
    start = (page - 1) * size
    page_results = all_results[start:start + size]

    return {
        "items": page_results,
        "total": total,
        "query": q,
        "search_type": "keyword",
        "entity_counts": entity_counts,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if size > 0 else 0,
    }


# ── Entity Adapters ────────────────────────────────────────────────────────


def _apply_tenant_filter(stmt, column, project_id, allowed_project_ids):
    """Apply either a single-project pin or an accessible-set ``IN`` filter."""
    if project_id:
        return stmt.where(column == project_id)
    if allowed_project_ids is not None:
        return stmt.where(column.in_(list(allowed_project_ids)))
    return stmt


async def _search_test_cases(
    db: AsyncSession,
    q: str,
    project_id: Optional[str],
    period_start: Optional[datetime],
    allowed_project_ids: Optional[set] = None,
    override_limit: Optional[int] = None,
) -> list[dict]:
    pattern = like_contains(q)
    stmt = (
        select(TestCase, TestRun.project_id)
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .join(Project, Project.id == TestRun.project_id)
        .where(or_(
            TestCase.test_name.ilike(pattern, escape="\\"),
            TestCase.suite_name.ilike(pattern, escape="\\"),
            # Run-level label so a query for the session-supplied suite
            # name (e.g. "API Regression Multi-Class") still surfaces
            # tests of live_stream runs whose per-event ``tc.suite_name``
            # is the test class name.
            TestRun.primary_suite_name.ilike(pattern, escape="\\"),
            TestCase.error_message.ilike(pattern, escape="\\"),
            cast(TestCase.tags, String).ilike(pattern, escape="\\"),
        ), Project.is_active.is_(True))
        .order_by(TestCase.created_at.desc())
        .limit(override_limit or 50)
    )
    stmt = _apply_tenant_filter(stmt, TestRun.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(TestCase.created_at >= period_start)

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
) -> list[dict]:
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

    runs = (await db.execute(stmt)).scalars().all()
    return [
        {
            "entity_type": "test_run",
            "entity_id": str(r.id),
            "title": f"Build {r.build_number}",
            "subtitle": f"{r.branch or 'no branch'} · {r.status} · {r.pass_rate or 0:.0f}% pass rate",
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
) -> list[dict]:
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
        .group_by(effective_suite)
        .order_by(func.count().desc())
        .limit(override_limit or 15)
    )
    stmt = _apply_tenant_filter(stmt, TestRun.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(TestCase.created_at >= period_start)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "entity_type": "suite",
            "entity_id": row.suite_name,
            "title": row.suite_name,
            "subtitle": f"{row.test_count} test executions",
            "navigation_url": f"/coverage/suite?name={row.suite_name}",
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
) -> list[dict]:
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
    stmt = (
        select(
            TestCaseHistory.test_fingerprint,
            TestRun.project_id.label("project_id"),
            func.max(TestCase.test_name).label("test_name"),
            func.max(TestCase.suite_name).label("suite_name"),
            func.count().label("total_runs"),
            func.count().filter(TestCaseHistory.status.in_(["FAILED", "BROKEN"])).label("fail_count"),
        )
        .join(TestCase, TestCaseHistory.test_case_id == TestCase.id)
        .join(TestRun, TestCaseHistory.test_run_id == TestRun.id)
        .join(Project, Project.id == TestRun.project_id)
        .where(
            TestCase.test_name.ilike(pattern, escape="\\"),
            Project.is_active.is_(True),
        )
        .group_by(TestCaseHistory.test_fingerprint, TestRun.project_id)
        .having(func.count() >= 5)
        .limit(override_limit or 15)
    )
    stmt = _apply_tenant_filter(stmt, TestRun.project_id, project_id, allowed_project_ids)
    if period_start:
        stmt = stmt.where(TestCaseHistory.created_at >= period_start)

    rows = (await db.execute(stmt)).all()
    results = []
    for row in rows:
        if row.total_runs > 0:
            rate = (row.fail_count / row.total_runs) * 100
            if 10 <= rate <= 90:  # flaky range
                results.append({
                    "entity_type": "flaky_test",
                    "entity_id": row.test_fingerprint,
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
) -> list[dict]:
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
