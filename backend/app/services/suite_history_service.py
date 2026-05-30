"""Suite-level run history aggregates and trend data.

Every page that surfaces "test suites" — /test-management Test Suites tab,
/suites, /coverage/suite, /reports/summary — needs the same columns:

* ``run_count``      — how many distinct TestRuns included this suite.
* ``total_tests``    — total per-execution test rows across those runs.
* ``passed_count``   — total PASSED rows.
* ``failed_count``   — total FAILED rows.
* ``skipped_count``  — total SKIPPED rows.
* ``broken_count``   — total BROKEN rows.
* ``last_run_at``    — most recent run timestamp.
* ``last_run_id``    — most recent run id (for navigation).

Previously each page rolled its own SQL. Counts disagreed across pages
because each query made slightly different assumptions about NULL
suite_name handling, the SDK's run-level ``primary_suite_name`` vs
per-row ``suite_name`` split (see ``feedback_live_stream_suite_name_nulls``),
and time-window semantics. This service is the single source of truth:
one query path, one set of semantics, one cache point if we ever need
to memoise.

The shape mirrors what the SDK actually emits:

* Path A — run-level suite: ``test_runs.primary_suite_name`` is set
  (stamped by the SDK at session create via ``testlookup.suite``).
* Path B — per-row suite: ``test_cases.suite_name`` differs from the
  run-level value (often a Java class name from a TestNG framework).

A single (run, suite_name) pair is counted ONCE even when it appears in
both paths. ``run_count`` counts distinct ``test_run_id``s; the
test-row counts come from each suite's per-test rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


SUITE_HISTORY_FIELDS = (
    "run_count",
    "total_tests",
    "passed_count",
    "failed_count",
    "skipped_count",
    "broken_count",
    "last_run_at",
    "last_run_id",
)


def _zero_row(suite_name: str) -> dict:
    return {
        "suite_name": suite_name,
        "run_count": 0,
        "total_tests": 0,
        "passed_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "broken_count": 0,
        "last_run_at": None,
        "last_run_id": None,
    }


async def compute_suite_history(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    suite_names: Optional[list[str]] = None,
    days: Optional[int] = None,
) -> dict[str, dict]:
    """Return one history row per suite as ``{suite_name: dict}``.

    Parameters
    ----------
    project_id:
        Scope the query to one project. ``None`` means cross-project
        (caller is responsible for tenant filtering).
    suite_names:
        Filter to a set of suite names. ``None`` returns every suite
        the project knows about. Useful for the list endpoint where
        we want all suites; pass a list when hydrating a single page.
    days:
        Window for ``test_runs.created_at >= now - days``. ``None``
        means lifetime — used by the catalog list endpoint where the
        user expects "how many runs ever".

    Notes
    -----
    * The query uses a UNION ALL of run-level and per-row paths and
      DISTINCT on (run_id, suite_name) so a run that has the same
      suite stamped at both levels is counted once.
    * Counts come from a join to ``test_cases`` filtered on the
      effective suite_name. ``run_count`` is ``COUNT(DISTINCT
      test_run_id)`` over the effective set.
    """
    where_clauses: list[str] = []
    params: dict = {}
    if project_id is not None:
        where_clauses.append("tr.project_id = :project_id")
        params["project_id"] = project_id
    if days is not None:
        where_clauses.append("tr.created_at >= :period_start")
        params["period_start"] = (
            datetime.now(timezone.utc) - timedelta(days=int(days))
        )
    if suite_names is not None:
        if not suite_names:
            return {}
        # IN-list expansion via SQLAlchemy text params; SQLAlchemy
        # handles the bind-parameter expansion when ``expanding=True``
        # — but for sa_text we use a named param + literal_binds in
        # postgres-friendly form.
        # Simpler shape: build the IN clause inline. Names are bounded
        # by the caller (page size). Bind via :suite_X parameters to
        # keep escaping safe.
        in_params = []
        for idx, name in enumerate(suite_names):
            key = f"suite_name_{idx}"
            params[key] = name
            in_params.append(f":{key}")
        where_clauses.append(
            "LOWER(TRIM(coalesce(effective_suite, ''))) IN ("
            + ", ".join(f"LOWER(TRIM({p}))" for p in in_params)
            + ")"
        )

    suite_where = ""
    if where_clauses:
        # Some clauses reference ``effective_suite`` (post-projection),
        # others reference ``tr.*`` (pre-projection). Split: project-
        # /day filters go into the WHERE of the inner CTE; suite-name
        # filter goes after the projection.
        pre_clauses = [
            c for c in where_clauses
            if "effective_suite" not in c
        ]
        post_clauses = [
            c for c in where_clauses
            if "effective_suite" in c
        ]
        pre_where = ("WHERE " + " AND ".join(pre_clauses)) if pre_clauses else "WHERE TRUE"
        suite_where = (
            "WHERE " + " AND ".join(post_clauses)
        ) if post_clauses else ""
    else:
        pre_where = "WHERE TRUE"

    query = sa_text(f"""
        WITH run_effective AS (
            -- Path A: run-level primary_suite_name.
            SELECT
                tr.id AS run_id,
                NULLIF(TRIM(tr.primary_suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at
            FROM test_runs tr
            {pre_where}
            AND NULLIF(TRIM(tr.primary_suite_name), '') IS NOT NULL

            UNION

            -- Path B: per-row suite_name distinct from run-level.
            SELECT DISTINCT
                tr.id AS run_id,
                NULLIF(TRIM(tc.suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at
            FROM test_runs tr
            JOIN test_cases tc ON tc.test_run_id = tr.id
            {pre_where}
            AND NULLIF(TRIM(tc.suite_name), '') IS NOT NULL
            AND NULLIF(TRIM(tr.primary_suite_name), '')
                IS DISTINCT FROM NULLIF(TRIM(tc.suite_name), '')
        ),
        per_run AS (
            SELECT
                re.effective_suite,
                re.run_id,
                MAX(re.run_created_at) AS run_created_at,
                COUNT(tc.id) AS total_tests,
                COUNT(*) FILTER (WHERE tc.status = 'PASSED')  AS passed_count,
                COUNT(*) FILTER (WHERE tc.status = 'FAILED')  AS failed_count,
                COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped_count,
                COUNT(*) FILTER (WHERE tc.status = 'BROKEN')  AS broken_count
            FROM run_effective re
            LEFT JOIN test_cases tc
              ON tc.test_run_id = re.run_id
             AND (
                 NULLIF(TRIM(tc.suite_name), '') = re.effective_suite
                 OR (
                     NULLIF(TRIM(tc.suite_name), '') IS NULL
                     AND EXISTS (
                         SELECT 1 FROM test_runs tr2
                         WHERE tr2.id = re.run_id
                           AND NULLIF(TRIM(tr2.primary_suite_name), '') = re.effective_suite
                     )
                 )
             )
            GROUP BY re.effective_suite, re.run_id
        )
        SELECT
            effective_suite AS suite_name,
            COUNT(DISTINCT run_id)             AS run_count,
            COALESCE(SUM(total_tests),    0)   AS total_tests,
            COALESCE(SUM(passed_count),   0)   AS passed_count,
            COALESCE(SUM(failed_count),   0)   AS failed_count,
            COALESCE(SUM(skipped_count),  0)   AS skipped_count,
            COALESCE(SUM(broken_count),   0)   AS broken_count,
            MAX(run_created_at)                AS last_run_at,
            (array_agg(run_id ORDER BY run_created_at DESC))[1] AS last_run_id
        FROM per_run
        {suite_where}
        GROUP BY effective_suite
        HAVING effective_suite IS NOT NULL
    """)

    try:
        rows = (await db.execute(query, params)).fetchall()
    except Exception as exc:
        logger.warning(
            "suite_history_query_failed",
            project_id=str(project_id) if project_id else None,
            error=str(exc),
        )
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        out[r.suite_name] = {
            "suite_name": r.suite_name,
            "run_count": int(r.run_count or 0),
            "total_tests": int(r.total_tests or 0),
            "passed_count": int(r.passed_count or 0),
            "failed_count": int(r.failed_count or 0),
            "skipped_count": int(r.skipped_count or 0),
            "broken_count": int(r.broken_count or 0),
            "last_run_at": r.last_run_at,
            "last_run_id": r.last_run_id,
        }

    if suite_names is not None:
        for name in suite_names:
            out.setdefault(name, _zero_row(name))

    return out


async def compute_suite_trend(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    suite_name: str,
    days: int = 30,
) -> list[dict]:
    """Return per-day trend points for one suite.

    Each point is ``{date, run_count, total_tests, passed_count,
    failed_count, skipped_count, broken_count}`` ordered ascending
    by date. Days with no runs are emitted with all-zero counts so
    the frontend chart has a continuous x-axis.
    """
    if days <= 0:
        return []
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    params: dict = {
        "suite_name": suite_name,
        "period_start": period_start,
    }
    pre_where = "tr.created_at >= :period_start"
    if project_id is not None:
        params["project_id"] = project_id
        pre_where += " AND tr.project_id = :project_id"

    query = sa_text(f"""
        WITH run_effective AS (
            SELECT
                tr.id AS run_id,
                NULLIF(TRIM(tr.primary_suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at
            FROM test_runs tr
            WHERE {pre_where}
              AND NULLIF(TRIM(tr.primary_suite_name), '') IS NOT NULL
            UNION
            SELECT DISTINCT
                tr.id AS run_id,
                NULLIF(TRIM(tc.suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at
            FROM test_runs tr
            JOIN test_cases tc ON tc.test_run_id = tr.id
            WHERE {pre_where}
              AND NULLIF(TRIM(tc.suite_name), '') IS NOT NULL
              AND NULLIF(TRIM(tr.primary_suite_name), '')
                  IS DISTINCT FROM NULLIF(TRIM(tc.suite_name), '')
        ),
        filtered AS (
            SELECT *
            FROM run_effective
            WHERE LOWER(TRIM(effective_suite)) = LOWER(TRIM(:suite_name))
        ),
        per_run AS (
            SELECT
                date_trunc('day', f.run_created_at) AS day,
                f.run_id,
                COUNT(tc.id) AS total_tests,
                COUNT(*) FILTER (WHERE tc.status = 'PASSED')  AS passed_count,
                COUNT(*) FILTER (WHERE tc.status = 'FAILED')  AS failed_count,
                COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped_count,
                COUNT(*) FILTER (WHERE tc.status = 'BROKEN')  AS broken_count
            FROM filtered f
            LEFT JOIN test_cases tc
              ON tc.test_run_id = f.run_id
             AND (
                 NULLIF(TRIM(tc.suite_name), '') = f.effective_suite
                 OR (
                     NULLIF(TRIM(tc.suite_name), '') IS NULL
                     AND EXISTS (
                         SELECT 1 FROM test_runs tr2
                         WHERE tr2.id = f.run_id
                           AND NULLIF(TRIM(tr2.primary_suite_name), '') = f.effective_suite
                     )
                 )
             )
            GROUP BY day, f.run_id
        )
        SELECT
            day,
            COUNT(DISTINCT run_id) AS run_count,
            COALESCE(SUM(total_tests),   0) AS total_tests,
            COALESCE(SUM(passed_count),  0) AS passed_count,
            COALESCE(SUM(failed_count),  0) AS failed_count,
            COALESCE(SUM(skipped_count), 0) AS skipped_count,
            COALESCE(SUM(broken_count),  0) AS broken_count
        FROM per_run
        GROUP BY day
        ORDER BY day
    """)

    try:
        rows = (await db.execute(query, params)).fetchall()
    except Exception as exc:
        logger.warning(
            "suite_trend_query_failed",
            suite_name=suite_name, days=days,
            error=str(exc),
        )
        return []

    by_day: dict[str, dict] = {}
    for r in rows:
        key = r.day.date().isoformat() if r.day else None
        if not key:
            continue
        by_day[key] = {
            "date": key,
            "run_count": int(r.run_count or 0),
            "total_tests": int(r.total_tests or 0),
            "passed_count": int(r.passed_count or 0),
            "failed_count": int(r.failed_count or 0),
            "skipped_count": int(r.skipped_count or 0),
            "broken_count": int(r.broken_count or 0),
        }

    # Fill gaps with zero so the chart has a continuous x-axis.
    out: list[dict] = []
    cursor = period_start.date()
    today = datetime.now(timezone.utc).date()
    while cursor <= today:
        key = cursor.isoformat()
        if key in by_day:
            out.append(by_day[key])
        else:
            out.append({
                "date": key,
                "run_count": 0,
                "total_tests": 0,
                "passed_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "broken_count": 0,
            })
        cursor = cursor + timedelta(days=1)
    return out
