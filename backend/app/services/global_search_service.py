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
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    Release,
    TestCase,
    TestCaseHistory,
    TestRun,
)
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
) -> dict[str, Any]:
    """Execute a global search across multiple entity types.

    Returns a dict matching GlobalSearchResponse shape.
    """
    types = entity_types or ALL_ENTITY_TYPES
    period_start = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    # Fan out to adapters
    all_results: list[dict] = []
    for entity_type in types:
        adapter = _ADAPTERS.get(entity_type)
        if adapter:
            try:
                results = await adapter(db, q, project_id, period_start)
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


async def _search_test_cases(
    db: AsyncSession, q: str, project_id: Optional[str], period_start: Optional[datetime],
) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(TestCase, TestRun.project_id)
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(or_(
            TestCase.test_name.ilike(pattern),
            TestCase.suite_name.ilike(pattern),
            TestCase.error_message.ilike(pattern),
        ))
        .order_by(TestCase.created_at.desc())
        .limit(50)
    )
    if project_id:
        stmt = stmt.where(TestRun.project_id == project_id)
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
            "navigation_url": f"/test-runs/{tc.test_run_id}",
            "relevance_score": 0.8,
            "match_reasons": ["Matched test name, suite, or error message"],
            "metadata": {"status": tc.status, "suite_name": tc.suite_name},
        }
        for tc, pid in rows
    ]


async def _search_test_runs(
    db: AsyncSession, q: str, project_id: Optional[str], period_start: Optional[datetime],
) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(TestRun)
        .where(or_(
            TestRun.build_number.ilike(pattern),
            TestRun.branch.ilike(pattern),
            TestRun.jenkins_job.ilike(pattern),
        ))
        .order_by(TestRun.created_at.desc())
        .limit(20)
    )
    if project_id:
        stmt = stmt.where(TestRun.project_id == project_id)
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
            "navigation_url": f"/test-runs/{r.id}",
            "relevance_score": 0.7,
            "match_reasons": ["Matched build number, branch, or job name"],
            "metadata": {"status": str(r.status), "pass_rate": r.pass_rate},
        }
        for r in runs
    ]


async def _search_suites(
    db: AsyncSession, q: str, project_id: Optional[str], period_start: Optional[datetime],
) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(
            TestCase.suite_name,
            func.count().label("test_count"),
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(
            TestCase.suite_name.ilike(pattern),
            TestCase.suite_name.isnot(None),
            TestCase.suite_name != "",
        )
        .group_by(TestCase.suite_name)
        .order_by(func.count().desc())
        .limit(15)
    )
    if project_id:
        stmt = stmt.where(TestRun.project_id == project_id)

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
    db: AsyncSession, q: str, project_id: Optional[str], period_start: Optional[datetime],
) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(Defect)
        .where(or_(
            Defect.jira_ticket_id.ilike(pattern),
        ))
        .order_by(Defect.created_at.desc())
        .limit(20)
    )
    if project_id:
        stmt = stmt.where(Defect.project_id == project_id)
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
    db: AsyncSession, q: str, project_id: Optional[str], period_start: Optional[datetime],
) -> list[dict]:
    pattern = f"%{q}%"
    # Find test fingerprints with intermittent pass/fail that match the query
    stmt = (
        select(
            TestCaseHistory.test_fingerprint,
            func.max(TestCase.test_name).label("test_name"),
            func.max(TestCase.suite_name).label("suite_name"),
            func.count().label("total_runs"),
            func.count().filter(TestCaseHistory.status.in_(["FAILED", "BROKEN"])).label("fail_count"),
        )
        .join(TestCase, TestCaseHistory.test_case_id == TestCase.id)
        .join(TestRun, TestCaseHistory.test_run_id == TestRun.id)
        .where(TestCase.test_name.ilike(pattern))
        .group_by(TestCaseHistory.test_fingerprint)
        .having(func.count() >= 5)
        .limit(15)
    )
    if project_id:
        stmt = stmt.where(TestRun.project_id == project_id)

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
                    "navigation_url": "/failures",
                    "relevance_score": 0.55,
                    "match_reasons": ["Flaky test matching query"],
                    "metadata": {"failure_rate": round(rate, 1), "total_runs": row.total_runs, "suite_name": row.suite_name},
                })
    return results


async def _search_releases(
    db: AsyncSession, q: str, project_id: Optional[str], period_start: Optional[datetime],
) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(Release)
        .where(or_(
            Release.name.ilike(pattern),
            Release.version.ilike(pattern),
        ))
        .order_by(Release.created_at.desc())
        .limit(10)
    )
    if project_id:
        stmt = stmt.where(Release.project_id == project_id)

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
