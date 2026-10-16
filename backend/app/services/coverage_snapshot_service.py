"""W08 TRND-07 -- daily coverage snapshots, the Coverage History tab, and the
tests behind the "Untagged executions" callout.

**A snapshot** of project P for the UTC day D describes the 30 UTC days
ending D (``[D - 29 00:00, D + 1 00:00)``, by the RUN's ``created_at``):

* ``catalogue_tests`` -- canonical tests of P created before D ended and not
  deleted by then (``status <> 'deleted'``, or deleted after D:
  ``deleted_observed_at >= D + 1``). For a past D this is an approximation:
  the catalogue keeps no history, only a test's creation and the moment its
  deletion was observed (``meta.definitions`` says so).
* ``exercised_tests`` -- distinct test fingerprints executed in the window.
* ``suites`` -- distinct non-null effective suites executed in the window.
* ``suite_coverage`` -- up to 50 per-suite rows, the ``coverage_stats`` suite
  rule (the effective suite, ``Unknown Suite`` for none; evaluated pass rate).

It is stored in ``coverage_snapshots`` (0001's column names: ``total_tests``
:= catalogue, ``automated_count`` := exercised, ``total_suites`` := suites,
``snapshot_day`` := D, ``snapshot_date`` := when it was written). One row a
project a day (``uq_coverage_snapshots_project_day``): the daily beat UPSERTS
yesterday and every missing day of the last 30, so a second run of the beat
rewrites a day with its newer values and never duplicates it. Today's partial
day is never stored. A snapshot is a row of its own, so the history survives a
run purge.

**The untagged tests** are the distinct tests whose executions in the window
carry no suite label: no effective suite, or one of the labels the Coverage
page reads as untagged (``unknown suite``, ``unknown``, ``untagged``),
matched through the shared ``analytics_scope`` helper. Same clock, scope and
window as the callout's own read (``analytics_service.coverage_stats``), so
the list is the callout's number, test by test. Each carries its CURRENT
catalogue suite (most read ``Default Suite (...)``); moving one files it under
another suite in the catalogue and never relabels an execution.

Services flush; the worker task and the routers commit.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Final, Optional

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import CoverageSnapshot
from app.services.analytics_scope import (
    AnalyticsScope,
    effective_suite_sql,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
    suite_label_in_sql,
)
from app.services.analytics_service import _period_start, _row_dict
from app.services.analytics_service import _tenant_filter as tenant_filter_sql

logger = structlog.get_logger(__name__)

SNAPSHOT_WINDOW_DAYS: Final = 30
BACKFILL_DAYS: Final = 30
HISTORY_MAX_DAYS: Final = 365
UNTAGGED_PAGE_MAX: Final = 200
#: Per-suite rows a snapshot keeps (the ``coverage_stats`` page size).
SUITE_ROWS_MAX: Final = 50
#: The labels the Coverage page reads as "untagged" besides no label at all
#: (``CoveragePage.tsx`` ``isUntaggedRow``; ``trend_summary_service``'s list
#: minus the empty string, which the effective suite already reads as NULL).
UNTAGGED_LABELS: Final = ("unknown suite", "unknown", "untagged")
NO_SNAPSHOT_REASON: Final = "No coverage snapshot yet: the first is taken at 00:05 UTC."

_UTC = timezone.utc


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_UTC)


def snapshot_window(day: date) -> tuple[datetime, datetime]:
    """``[D - 29 00:00, D + 1 00:00)`` UTC: the 30 days ending ``day``."""
    return _midnight(day - timedelta(days=SNAPSHOT_WINDOW_DAYS - 1)), _midnight(day + timedelta(days=1))


def utc_today(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(_UTC)).astimezone(_UTC).date()


def exercised_pct(catalogue: int, exercised: int) -> Optional[float]:
    """Exercised / catalogue x 100, 1 dp; ``None`` with an empty catalogue
    (not measured, never 0 %). Above 100 when tests ran that the catalogue
    does not hold (yet): stated, not clamped."""
    if catalogue <= 0:
        return None
    return round(exercised / catalogue * 100, 1)


# ── The snapshot ───────────────────────────────────────────────────────────


async def snapshot_values(db: AsyncSession, project_id: uuid.UUID, day: date) -> dict:
    """The four values of (project, day): three bounded statements."""
    start, end = snapshot_window(day)
    catalogue = (
        await db.execute(
            scoped_text(
                """
                SELECT COUNT(*) AS catalogue_tests
                FROM canonical_test_cases c
                WHERE c.project_id = :snap_project
                  AND c.created_at < :snap_end
                  AND (c.status <> 'deleted' OR c.deleted_observed_at >= :snap_end)
                """,
                {},
            ),
            {"snap_project": project_id, "snap_end": end},
        )
    ).scalar_one()

    params: dict[str, Any] = {"snap_start": start, "snap_end": end}
    tenant = tenant_filter_sql(params, project_id=str(project_id), allowed_project_ids=None)
    exercised = (
        await db.execute(
            scoped_text(
                f"""
                SELECT
                    COUNT(DISTINCT tc.test_fingerprint) AS exercised_tests,
                    COUNT(DISTINCT {effective_suite_sql()}) AS suites
                FROM test_cases tc
                JOIN test_runs tr ON tr.id = tc.test_run_id
                WHERE tr.created_at >= :snap_start
                  AND tr.created_at < :snap_end
                  {tenant}
                """,
                params,
            ),
            params,
        )
    ).one()

    # The ``coverage_stats`` suite rule, over the snapshot's window.
    effective_suite = f"COALESCE({effective_suite_sql()}, 'Unknown Suite')"
    suite_rows = (
        await db.execute(
            scoped_text(
                f"""
                SELECT
                    {effective_suite} AS suite_name,
                    COUNT(DISTINCT tc.test_fingerprint) AS unique_tests,
                    COUNT(*) AS executions,
                    COUNT(*) FILTER (WHERE tc.status = 'PASSED') AS passed,
                    COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN')) AS failed,
                    COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped,
                    ROUND(
                        COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                        / NULLIF(COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')), 0), 1
                    ) AS pass_rate
                FROM test_cases tc
                JOIN test_runs tr ON tr.id = tc.test_run_id
                WHERE tr.created_at >= :snap_start
                  AND tr.created_at < :snap_end
                  {tenant}
                GROUP BY {effective_suite}
                ORDER BY unique_tests DESC, suite_name
                LIMIT {SUITE_ROWS_MAX}
                """,
                params,
            ),
            params,
        )
    ).fetchall()
    return {
        "catalogue_tests": int(catalogue or 0),
        "exercised_tests": int(exercised.exercised_tests or 0),
        "suites": int(exercised.suites or 0),
        "suite_coverage": [_row_dict(row) for row in suite_rows],
    }


async def stage_snapshot(db: AsyncSession, project_id: uuid.UUID, day: date, values: dict) -> None:
    """UPSERT the (project, day) row: a day already stored is rewritten with
    these values (the newest run of the beat wins). Flush only."""
    statement = pg_insert(CoverageSnapshot).values(
        id=uuid.uuid4(),
        project_id=project_id,
        snapshot_day=day,
        snapshot_date=func.now(),
        total_tests=values["catalogue_tests"],
        automated_count=values["exercised_tests"],
        total_suites=values["suites"],
        suite_coverage=values["suite_coverage"],
    )
    statement = statement.on_conflict_do_update(
        index_elements=["project_id", "snapshot_day"],
        set_={
            "snapshot_date": func.now(),
            "total_tests": statement.excluded.total_tests,
            "automated_count": statement.excluded.automated_count,
            "total_suites": statement.excluded.total_suites,
            "suite_coverage": statement.excluded.suite_coverage,
        },
    )
    await db.execute(statement)
    await db.flush()


async def missing_days(
    db: AsyncSession, project_id: uuid.UUID, *, today: date, back: int = BACKFILL_DAYS,
) -> list[date]:
    """The days of ``[today - back, today - 1]`` with no row, ascending."""
    first, last = today - timedelta(days=back), today - timedelta(days=1)
    stored = {
        row.snapshot_day
        for row in (
            await db.execute(
                scoped_text(
                    """
                    SELECT snapshot_day FROM coverage_snapshots
                    WHERE project_id = :snap_project
                      AND snapshot_day >= :snap_first AND snapshot_day <= :snap_last
                    """,
                    {},
                ),
                {"snap_project": project_id, "snap_first": first, "snap_last": last},
            )
        ).fetchall()
    }
    return [first + timedelta(days=n) for n in range(back) if first + timedelta(days=n) not in stored]


def days_to_snapshot(missing: list[date], today: date) -> list[date]:
    """What one beat stores for a project: every missing day plus yesterday
    (rewritten, so a late upload of yesterday's runs is counted), ascending.
    Today's partial day never."""
    yesterday = today - timedelta(days=1)
    return sorted({*missing, yesterday})


# ── The history ────────────────────────────────────────────────────────────


def history_point(row: Any) -> dict:
    catalogue, exercised = int(row.total_tests or 0), int(row.automated_count or 0)
    return {
        "day": row.snapshot_day.isoformat(),
        "catalogue_tests": catalogue,
        "exercised_tests": exercised,
        "exercised_pct": exercised_pct(catalogue, exercised),
        "suites": int(row.total_suites or 0),
    }


def history_window_start(days: int, today: date) -> date:
    """The first day the history reads: ``today - days`` (so ``days`` stored
    days end yesterday)."""
    return today - timedelta(days=days)


async def coverage_history(db: AsyncSession, scope: AnalyticsScope, *, today: Optional[date] = None) -> dict:
    """GET /analytics/coverage/history's body (contract §1.3), without meta."""
    if scope.project_id is None:  # pragma: no cover - the route's policy requires one
        raise ValueError("coverage history is per project")
    today = today or utc_today()
    days = scope.window_days
    params = {"snap_project": scope.project_id, "snap_first": history_window_start(days, today)}
    rows = (
        await db.execute(
            scoped_text(
                """
                SELECT snapshot_day, total_tests, automated_count, total_suites
                FROM coverage_snapshots
                WHERE project_id = :snap_project
                  AND snapshot_day >= :snap_first
                ORDER BY snapshot_day
                """,
                {},
            ),
            params,
        )
    ).fetchall()
    first = (
        await db.execute(
            scoped_text(
                "SELECT MIN(snapshot_day) FROM coverage_snapshots WHERE project_id = :snap_project", {},
            ),
            {"snap_project": scope.project_id},
        )
    ).scalar_one_or_none()
    return {
        "window_days": days,
        "measured": first is not None,
        "reason": None if first is not None else NO_SNAPSHOT_REASON,
        "first_snapshot_day": first.isoformat() if first is not None else None,
        "points": [history_point(row) for row in rows],
    }


HISTORY_DEFINITIONS: Final = {
    "snapshot": (
        "One snapshot a project a UTC day, taken by the daily job at 00:05 UTC for the "
        "day before (and for every missing day of the last 30). A day with no snapshot "
        "is absent from points: a gap, never 0."
    ),
    "catalogue_tests": (
        "Canonical tests of the project that existed on that day and were not deleted "
        "by its end. For a day before the job first ran this is an approximation: the "
        "catalogue keeps a test's creation and when its deletion was observed, not its "
        "full history."
    ),
    "exercised_tests": "Distinct tests executed in the 30 UTC days ending that day (by the run's time).",
    "exercised_pct": "exercised_tests / catalogue_tests x 100; null with an empty catalogue, never 0.",
    "suites": "Distinct suites (the effective suite) executed in those 30 days.",
    "scope": "A snapshot is project-wide: release and suite filters do not apply.",
}


# ── The untagged tests ─────────────────────────────────────────────────────


async def untagged_tests(db: AsyncSession, scope: AnalyticsScope, *, page: int, size: int) -> dict:
    """GET /analytics/coverage/untagged-tests's body (contract §1.3), without meta:
    one page of the distinct tests whose executions in the window carry no
    suite label, busiest first, with their current catalogue suite."""
    if scope.project_id is None:  # pragma: no cover - the route's policy requires one
        raise ValueError("untagged tests are per project")
    if not 1 <= size <= UNTAGGED_PAGE_MAX or page < 1:
        raise ValueError("page >= 1 and 1 <= size <= UNTAGGED_PAGE_MAX")
    effective = effective_suite_sql()
    params: dict[str, Any] = {
        # ``coverage_stats``' window and clock (the row's own created_at), so
        # the list is the callout's number.
        "period_start": _period_start(scope.window_days),
        "ut_limit": size,
        "ut_offset": (page - 1) * size,
        "ut_project": scope.project_id,
    }
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=None)
    release = release_filter_sql(params, scope.release_arg)
    suites = suite_filter_sql(params, scope.suite_arg)
    labelled_untagged = suite_label_in_sql(params, effective, UNTAGGED_LABELS)
    statement = scoped_text(
        f"""
        WITH untagged AS (
            SELECT
                tc.test_fingerprint AS fp,
                COUNT(*) AS executions,
                MAX(tr.created_at) AS last_executed_at,
                MIN(tc.test_name) AS test_name,
                MIN(tc.class_name) AS class_name
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tc.created_at >= :period_start
              {tenant}
              {release}
              {suites}
              AND ({effective} IS NULL OR {labelled_untagged})
            GROUP BY tc.test_fingerprint
        ), counted AS (
            SELECT COUNT(*) AS total FROM untagged
        ), paged AS (
            SELECT * FROM untagged
            ORDER BY executions DESC, test_name, fp
            LIMIT :ut_limit OFFSET :ut_offset
        )
        SELECT
            counted.total,
            paged.fp, paged.executions, paged.last_executed_at, paged.test_name, paged.class_name,
            c.id AS canonical_id, ts.id AS catalogue_suite_id, ts.name AS catalogue_suite
        FROM counted
        LEFT JOIN paged ON TRUE
        LEFT JOIN canonical_test_cases c
               ON c.project_id = :ut_project AND c.test_fingerprint = paged.fp
        LEFT JOIN test_suites ts ON ts.id = c.test_suite_id
        ORDER BY paged.executions DESC, paged.test_name, paged.fp
        """,
        params,
    )
    rows = (await db.execute(statement, params)).fetchall()
    total = int(rows[0].total or 0) if rows else 0
    items = [
        {
            "canonical_id": str(row.canonical_id) if row.canonical_id is not None else None,
            "test_fingerprint": row.fp,
            "test_name": row.test_name,
            "class_name": row.class_name,
            "catalogue_suite_id": str(row.catalogue_suite_id) if row.catalogue_suite_id is not None else None,
            "catalogue_suite": row.catalogue_suite,
            "executions": int(row.executions or 0),
            "last_executed_at": (
                row.last_executed_at.astimezone(_UTC).isoformat() if row.last_executed_at is not None else None
            ),
        }
        for row in rows
        if row.fp is not None
    ]
    return {"items": items, "total": total, "page": page, "size": size, "window_days": scope.window_days}


UNTAGGED_DEFINITIONS: Final = {
    "untagged": (
        "A test whose executions in the window carry no suite label: no suite at all, or "
        "a label of 'unknown suite', 'unknown' or 'untagged'. The same window and scope as "
        "the Coverage page's Untagged executions callout."
    ),
    "catalogue_suite": (
        "The suite the test is filed under in the catalogue now (most read 'Default Suite "
        "(...)'). canonical_id is null for a test not in the catalogue yet (it is added "
        "when its run is finalised)."
    ),
    "moving": (
        "Moving a test (POST /canonical-test-cases/bulk-link) files it under another suite "
        "in the catalogue. Its executions -- past and future -- keep the suite label the "
        "test reporter sends, so this list only shrinks once the reporter sends a label."
    ),
    "total": "Distinct untagged tests in the window, beyond this page.",
}


__all__ = [
    "BACKFILL_DAYS",
    "HISTORY_DEFINITIONS",
    "HISTORY_MAX_DAYS",
    "NO_SNAPSHOT_REASON",
    "SNAPSHOT_WINDOW_DAYS",
    "UNTAGGED_DEFINITIONS",
    "UNTAGGED_LABELS",
    "UNTAGGED_PAGE_MAX",
    "coverage_history",
    "days_to_snapshot",
    "exercised_pct",
    "history_point",
    "history_window_start",
    "missing_days",
    "snapshot_values",
    "snapshot_window",
    "stage_snapshot",
    "untagged_tests",
    "utc_today",
]
