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
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics_scope import (
    ReleaseArg,
    release_filter_sql,
    scoped_text,
    suite_label_eq_sql,
    suite_label_in_sql,
)

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
    release_id: ReleaseArg = None,
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
    release_id:
        VIZ-202: only runs whose primary release is one of these (the
        ``unattributed`` sentinel for none). ``None`` leaves the statement
        unchanged.

    Notes
    -----
    * The query uses a UNION ALL of run-level and per-row paths (the two
      branches are disjoint by construction, so no dedup is needed) and
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
    release_clause = release_filter_sql(params, release_id)
    if release_clause:
        # The shared fragment is ``AND ...``; this builder joins bare clauses.
        where_clauses.append(release_clause.removeprefix("AND ").strip())
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
        # The clause is built by ``analytics_scope`` (VIZ-201): every
        # suite-match clause lives there.
        where_clauses.append(suite_label_in_sql(params, "effective_suite", suite_names))

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

    query = scoped_text(f"""
        WITH run_effective AS (
            -- Path A: run-level primary_suite_name.
            SELECT
                tr.id AS run_id,
                NULLIF(TRIM(tr.primary_suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at,
                -- This row IS the run-level suite, so a NULL-suite
                -- test_case belongs to it. Carrying the fact lets the
                -- join below drop a correlated EXISTS that only
                -- re-derived it (measured: 290ms -> 53ms).
                TRUE AS from_primary
            FROM test_runs tr
            {pre_where}
            AND NULLIF(TRIM(tr.primary_suite_name), '') IS NOT NULL

            UNION ALL

            -- Path B: per-row suite_name distinct from run-level.
            SELECT DISTINCT
                tr.id AS run_id,
                NULLIF(TRIM(tc.suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at,
                FALSE AS from_primary
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
                     AND re.from_primary
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
    """, params)

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
    release_id: ReleaseArg = None,
) -> list[dict]:
    """Return per-day trend points for one suite.

    Each point is ``{date, run_count, total_tests, passed_count,
    failed_count, skipped_count, broken_count}`` ordered ascending
    by date. Days with no runs are emitted with all-zero counts so
    the frontend chart has a continuous x-axis. ``release_id`` (VIZ-202)
    keeps the runs of those releases; ``None`` leaves the statement unchanged.
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
    # ``release_filter_sql`` binds ``release_id`` / ``release_ids``; the
    # suite here binds ``suite_name``, so the names cannot collide.
    release_clause = release_filter_sql(params, release_id)
    if release_clause:
        pre_where += f" {release_clause.strip()}"

    query = scoped_text(f"""
        WITH run_effective AS (
            SELECT
                tr.id AS run_id,
                NULLIF(TRIM(tr.primary_suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at,
                -- This row IS the run-level suite, so a NULL-suite
                -- test_case belongs to it. Carrying the fact lets the
                -- join below drop a correlated EXISTS that only
                -- re-derived it (measured: 290ms -> 53ms).
                TRUE AS from_primary
            FROM test_runs tr
            WHERE {pre_where}
              AND NULLIF(TRIM(tr.primary_suite_name), '') IS NOT NULL
            UNION ALL
            SELECT DISTINCT
                tr.id AS run_id,
                NULLIF(TRIM(tc.suite_name), '') AS effective_suite,
                tr.created_at AS run_created_at,
                FALSE AS from_primary
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
            WHERE {suite_label_eq_sql("effective_suite")}
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
                     AND f.from_primary
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
    """, params)

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
