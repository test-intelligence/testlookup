from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _period_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _row_dict(row) -> dict:
    data = dict(row._mapping)
    for key, value in data.items():
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
            data[key] = float(value)
    return data


async def flaky_tests(db: AsyncSession, project_id: str | None, days: int, limit: int) -> dict:
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    query = text(
        f"""
        SELECT
            tch.test_fingerprint,
            MAX(tc.test_name)   AS test_name,
            MAX(tc.suite_name)  AS suite_name,
            MAX(tc.class_name)  AS class_name,
            MAX(p.name)         AS project_name,
            COUNT(*)            AS total_runs,
            COUNT(*) FILTER (WHERE tch.status IN ('FAILED', 'BROKEN')) AS fail_count,
            COUNT(*) FILTER (WHERE tch.status = 'PASSED')              AS pass_count,
            ROUND(
                COUNT(*) FILTER (WHERE tch.status IN ('FAILED', 'BROKEN')) * 100.0 / COUNT(*), 1
            ) AS failure_rate_pct,
            MAX(tch.created_at) AS last_seen
        FROM test_case_history tch
        JOIN test_cases tc ON tc.id = tch.test_case_id
        JOIN test_runs tr   ON tr.id = tch.test_run_id
        LEFT JOIN projects p ON p.id = tr.project_id
        WHERE tch.created_at >= :period_start
          {project_filter}
        GROUP BY tch.test_fingerprint
        HAVING COUNT(*) >= 3
           AND COUNT(*) FILTER (WHERE tch.status IN ('FAILED', 'BROKEN')) * 1.0 / COUNT(*) BETWEEN 0.05 AND 0.95
        ORDER BY failure_rate_pct DESC
        LIMIT :limit
        """
    )
    params: dict = {"period_start": _period_start(days), "limit": limit}
    if project_id:
        params["project_id"] = str(project_id)
    result = await db.execute(query, params)
    rows = result.fetchall()
    return {"items": [dict(row._mapping) for row in rows], "period_days": days, "total": len(rows)}


async def failure_categories(db: AsyncSession, project_id: str | None, days: int) -> dict:
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    query = text(
        f"""
        SELECT
            COALESCE(tc.failure_category, 'UNKNOWN') AS category,
            COUNT(*) AS count
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.status IN ('FAILED', 'BROKEN')
          AND tc.created_at >= :period_start
          {project_filter}
        GROUP BY category
        ORDER BY count DESC
        """
    )
    params: dict = {"period_start": _period_start(days)}
    if project_id:
        params["project_id"] = str(project_id)
    result = await db.execute(query, params)
    return {"items": [dict(row._mapping) for row in result.fetchall()], "period_days": days}


async def top_failing_tests(db: AsyncSession, project_id: str | None, days: int, limit: int) -> dict:
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    query = text(
        f"""
        SELECT
            tc.test_fingerprint,
            MAX(tc.test_name)   AS test_name,
            MAX(tc.suite_name)  AS suite_name,
            MAX(tc.class_name)  AS class_name,
            MAX(tc.failure_category) AS failure_category,
            COUNT(*) AS fail_count,
            MAX(tc.created_at)  AS last_failed
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.status IN ('FAILED', 'BROKEN')
          AND tc.created_at >= :period_start
          {project_filter}
        GROUP BY tc.test_fingerprint
        ORDER BY fail_count DESC
        LIMIT :limit
        """
    )
    params: dict = {"period_start": _period_start(days), "limit": limit}
    if project_id:
        params["project_id"] = str(project_id)
    result = await db.execute(query, params)
    return {"items": [dict(row._mapping) for row in result.fetchall()], "period_days": days}


async def coverage_stats(db: AsyncSession, project_id: str | None, days: int) -> dict:
    period_start = _period_start(days)
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    suite_query = text(
        f"""
        SELECT
            COALESCE(tc.suite_name, 'Unknown Suite') AS suite_name,
            MAX(p.name)                              AS project_name,
            COUNT(DISTINCT tc.test_fingerprint)      AS unique_tests,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED') AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN')) AS failed,
            COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped,
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0 / NULLIF(COUNT(*), 0), 1
            ) AS pass_rate
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        LEFT JOIN projects p ON p.id = tr.project_id
        WHERE tc.created_at >= :period_start
          {project_filter}
        GROUP BY tc.suite_name
        ORDER BY unique_tests DESC
        LIMIT 50
        """
    )
    total_query = text(
        f"""
        SELECT
            COUNT(DISTINCT tc.test_fingerprint)  AS unique_tests,
            COUNT(DISTINCT tc.suite_name)        AS suite_count,
            COUNT(*)                            AS total_executions,
            ROUND(AVG(tr.pass_rate)::numeric, 1) AS avg_pass_rate,
            COUNT(DISTINCT DATE_TRUNC('day', tr.created_at)) AS days_with_runs
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.created_at >= :period_start
          {project_filter}
        """
    )
    params: dict = {"period_start": period_start}
    if project_id:
        params["project_id"] = str(project_id)
    suites = (await db.execute(suite_query, params)).fetchall()
    total = (await db.execute(total_query, params)).one()
    return {
        "summary": dict(total._mapping),
        "suites": [dict(row._mapping) for row in suites],
        "period_days": days,
    }


async def suite_detail(db: AsyncSession, project_id: str | None, suite_name: str, days: int) -> dict:
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    params: dict = {
        "suite_name": suite_name,
        "period_start": _period_start(days),
    }
    if project_id:
        params["project_id"] = str(project_id)
    summary_query = text(
        f"""
        SELECT
            COUNT(DISTINCT tc.test_fingerprint)                              AS unique_tests,
            COUNT(*)                                                         AS total_executions,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED')                     AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN'))        AS failed,
            COUNT(*) FILTER (WHERE tc.status = 'SKIPPED')                    AS skipped,
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(COUNT(*), 0), 1
            ) AS pass_rate,
            ROUND(AVG(tc.duration_ms)::numeric, 0)                           AS avg_duration_ms,
            MAX(tc.created_at)                                               AS last_run_at
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE COALESCE(tc.suite_name, 'Unknown Suite') = :suite_name
          AND tc.created_at >= :period_start
          {project_filter}
        """
    )
    cases_query = text(
        f"""
        SELECT
            tc.test_fingerprint,
            MAX(tc.test_name)                                             AS test_name,
            MAX(tc.class_name)                                            AS class_name,
            COUNT(*)                                                      AS total_executions,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED')                  AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN'))     AS failed,
            COUNT(*) FILTER (WHERE tc.status = 'SKIPPED')                 AS skipped,
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(COUNT(*), 0), 1
            ) AS pass_rate,
            ROUND(AVG(tc.duration_ms)::numeric, 0)                       AS avg_duration_ms,
            (array_agg(tc.status ORDER BY tc.created_at DESC))[1]        AS last_status,
            (array_agg(tc.error_message ORDER BY tc.created_at DESC))[1] AS last_error,
            MAX(tc.created_at)                                            AS last_run_at,
            (
                COUNT(*) >= 3
                AND COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN'))
                    * 1.0 / NULLIF(COUNT(*), 0) BETWEEN 0.05 AND 0.95
            ) AS is_flaky
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE COALESCE(tc.suite_name, 'Unknown Suite') = :suite_name
          AND tc.created_at >= :period_start
          {project_filter}
        GROUP BY tc.test_fingerprint
        ORDER BY failed DESC, total_executions DESC
        LIMIT 200
        """
    )
    runs_query = text(
        f"""
        SELECT
            tr.id::text                                                   AS test_run_id,
            tr.build_number,
            tr.created_at                                                 AS run_date,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED')                  AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN'))     AS failed,
            COUNT(*) FILTER (WHERE tc.status = 'SKIPPED')                 AS skipped,
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(COUNT(*), 0), 1
            ) AS pass_rate
        FROM test_runs tr
        JOIN test_cases tc ON tc.test_run_id = tr.id
        WHERE COALESCE(tc.suite_name, 'Unknown Suite') = :suite_name
          AND tr.created_at >= :period_start
          {project_filter}
        GROUP BY tr.id, tr.build_number, tr.created_at
        ORDER BY tr.created_at DESC
        LIMIT 15
        """
    )
    summary_row = (await db.execute(summary_query, params)).one()
    cases_rows = (await db.execute(cases_query, params)).fetchall()
    runs_rows = (await db.execute(runs_query, params)).fetchall()
    return {
        "suite_name": suite_name,
        "summary": _row_dict(summary_row),
        "test_cases": [_row_dict(row) for row in cases_rows],
        "recent_runs": [_row_dict(row) for row in runs_rows],
        "period_days": days,
    }


async def list_defects(
    db: AsyncSession,
    project_id: str | None,
    resolution_status: str | None,
    page: int,
    size: int,
) -> dict:
    project_filter = "AND d.project_id = :project_id" if project_id else ""
    status_filter = "AND d.resolution_status = :resolution_status" if resolution_status else ""
    query = text(
        f"""
        SELECT
            d.id,
            d.jira_ticket_id,
            d.jira_ticket_url,
            d.jira_status,
            d.failure_category,
            d.resolution_status,
            d.ai_confidence_score,
            d.created_at,
            d.resolved_at,
            tc.test_name,
            tc.suite_name
        FROM defects d
        JOIN test_cases tc ON tc.id = d.test_case_id
        WHERE 1=1
        {project_filter}
        {status_filter}
        ORDER BY d.created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    params: dict = {"limit": size, "offset": (page - 1) * size}
    if project_id:
        params["project_id"] = str(project_id)
    if resolution_status:
        params["resolution_status"] = resolution_status.upper()

    rows = (await db.execute(query, params)).fetchall()
    count_query = text(
        f"""
        SELECT COUNT(*) FROM defects d
        WHERE 1=1
        {project_filter}
        {status_filter}
        """
    )
    count_params = {key: value for key, value in params.items() if key not in ("limit", "offset")}
    total = (await db.execute(count_query, count_params)).scalar() or 0
    return {"items": [dict(row._mapping) for row in rows], "total": total, "page": page, "size": size, "pages": -(-total // size)}


async def ai_analysis_summary(db: AsyncSession, project_id: str | None, days: int) -> dict:
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    query = text(
        f"""
        SELECT
            COUNT(*)                                           AS total_analysed,
            COUNT(*) FILTER (WHERE ai.confidence_score >= 80)  AS high_confidence,
            COUNT(*) FILTER (WHERE ai.is_flaky)                AS flaky_detected,
            COUNT(*) FILTER (WHERE ai.backend_error_found)     AS backend_errors,
            COUNT(*) FILTER (WHERE ai.pod_issue_found)         AS pod_issues,
            COUNT(*) FILTER (WHERE ai.requires_human_review)   AS needs_review,
            ROUND(AVG(ai.confidence_score), 1)                 AS avg_confidence
        FROM ai_analysis ai
        JOIN test_cases tc ON tc.id = ai.test_case_id
        JOIN test_runs tr  ON tr.id = tc.test_run_id
        WHERE ai.created_at >= :period_start
          {project_filter}
        """
    )
    params: dict = {"period_start": _period_start(days)}
    if project_id:
        params["project_id"] = str(project_id)
    result = await db.execute(query, params)
    return dict(result.one()._mapping)
