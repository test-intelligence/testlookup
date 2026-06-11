from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Defect, Project, TestCase, TestRun, TriageStatus


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


def _normalise_suite_name(suite_name: str | None) -> str:
    return (suite_name or "").strip().lower()


def _effective_suite_sql() -> str:
    """SQL expression for a test_case row's *effective* suite name.

    For ``live_stream`` runs we trust the run-level
    ``tr.primary_suite_name`` (the session label the SDK supplied —
    e.g. "API Regression Multi-Class"), because those SDKs commonly
    stamp the test class name on every per-event ``tc.suite_name``.
    For everything else (file uploads) the per-event ``tc.suite_name``
    is authoritative — multi-``<testsuite>`` XML inputs need each test
    bucketed by its own suite. Shared by the filter + grouping helpers
    so "which suite does this test belong to" is answered identically
    everywhere.
    """
    return (
        "COALESCE("
        "CASE WHEN tr.trigger_source = 'live_stream' "
        "THEN NULLIF(TRIM(tr.primary_suite_name), '') ELSE NULL END, "
        "NULLIF(TRIM(tc.suite_name), '')"
        ")"
    )


def _suite_filter_sql() -> str:
    # Match by the *effective* suite, not a loose OR. An earlier OR-based
    # filter (``tc.suite_name = :s OR tr.primary_suite_name = :s``)
    # over-returned: a multi-suite run whose ``primary_suite_name``
    # matched leaked EVERY test of that run, so e.g. an Order test
    # surfaced under "Smoke suite". Equality on the effective suite
    # attributes each test to exactly one suite — the session label for
    # live_stream rows, the per-event suite otherwise. (Bug 2026-05-20.)
    return f"AND LOWER({_effective_suite_sql()}) = :suite_name"


def _add_suite_param(params: dict, suite_name: str | None) -> str:
    suite_key = _normalise_suite_name(suite_name)
    if not suite_key:
        return ""
    params["suite_name"] = suite_key
    return _suite_filter_sql()


def _tenant_filter(
    params: dict,
    *,
    project_id: str | uuid.UUID | None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]],
    table_alias: str = "tr",
    column: str = "project_id",
) -> str:
    """Defence-in-depth tenant scoping for raw-SQL analytics queries.

    Each analytics function previously built ``project_filter = "AND
    tr.project_id = :project_id" if project_id else ""`` inline. When the
    caller passed ``project_id=None`` (any caller — agent, task, future
    router) the WHERE clause silently dropped the tenant filter and the
    query returned cross-project rows. That was guarded at the router
    layer (returns empty for non-admin with no project_id), but the
    service trusted the gate. This helper hardens the service:

    * ``project_id`` set  → ``AND {alias}.{column} = :project_id`` (pin)
    * ``project_id`` None + ``allowed_project_ids`` non-empty set →
      ``AND {alias}.{column} IN (:pid_0, :pid_1, ...)`` (membership scope)
    * ``project_id`` None + ``allowed_project_ids`` empty set →
      ``AND FALSE`` (zero results, no cross-tenant leak)
    * ``project_id`` None + ``allowed_project_ids`` None →
      ``""`` (no filter; unrestricted — admin-only callers must opt in
      explicitly by passing ``None``; new non-admin callers should
      always pass a set, even empty)

    Mutates ``params`` in place to bind the placeholder values.
    """
    if project_id:
        params["project_id"] = str(project_id)
        return f"AND {table_alias}.{column} = :project_id"
    if allowed_project_ids is None:
        # Unrestricted scope — admin path. Callers that don't intend this
        # should pass an empty set, which fails closed.
        return ""
    ids = list(allowed_project_ids)
    if not ids:
        # Empty membership set — fail closed.
        return "AND FALSE"
    placeholders = ", ".join(f":pid_{i}" for i, _ in enumerate(ids))
    for i, pid in enumerate(ids):
        params[f"pid_{i}"] = str(pid)
    return f"AND {table_alias}.{column} IN ({placeholders})"


async def flaky_tests(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    limit: int,
    suite_name: str | None = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    params: dict = {"period_start": _period_start(days), "limit": limit}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
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
          {suite_filter}
        GROUP BY tch.test_fingerprint
        HAVING COUNT(*) >= 3
           AND COUNT(*) FILTER (WHERE tch.status IN ('FAILED', 'BROKEN')) * 1.0 / COUNT(*) BETWEEN 0.05 AND 0.95
        ORDER BY failure_rate_pct DESC
        LIMIT :limit
        """
    )
    result = await db.execute(query, params)
    items = [dict(row._mapping) for row in result.fetchall()]
    for it in items:
        it["source"] = "auto"
    seen = {it["test_fingerprint"] for it in items}

    # Merge tests humans have manually triaged as FLAKY_TEST (via /my-failures),
    # so /failures agrees with /flaky-coach. The auto-detector above only fires
    # on >=3-run intermittents (both pass AND fail in the window); a test a human
    # recognises as flaky before the detector has signal — or one that currently
    # always fails — is invisible to it. Without this merge, /failures returned 0
    # flakes and rendered "flake detector found zero intermittents — treat as a
    # hard regression", directly contradicting the /flaky-coach list (which has
    # merged manual triage since 2026-05-18). Same tenant + suite scoping; auto
    # rows win on dedup (they carry a real ratio). ``source`` lets the UI render
    # manual entries distinctly; it's additive, so existing consumers are unchanged.
    if len(items) < limit:
        manual_params: dict = {
            "period_start": _period_start(days),
            "flaky_status": TriageStatus.FLAKY_TEST.value,
            "limit": limit,
        }
        m_project_filter = _tenant_filter(
            manual_params, project_id=project_id, allowed_project_ids=allowed_project_ids,
        )
        m_suite_filter = _add_suite_param(manual_params, suite_name)
        manual_query = text(
            f"""
            SELECT
                tc.test_fingerprint,
                MAX(tc.test_name)  AS test_name,
                MAX(tc.suite_name) AS suite_name,
                MAX(tc.class_name) AS class_name,
                MAX(p.name)        AS project_name,
                COUNT(*)           AS total_runs,
                COUNT(*)           AS fail_count,
                0                  AS pass_count,
                -- 100.0 mirrors flaky-coach's 1.0 "human-flagged" marker (manual
                -- triage has no measured ratio); ``source='manual'`` is the real
                -- signal the UI keys on.
                100.0              AS failure_rate_pct,
                MAX(tc.triage_updated_at) AS last_seen
            FROM test_cases tc
            JOIN test_runs tr   ON tr.id = tc.test_run_id
            LEFT JOIN projects p ON p.id = tr.project_id
            WHERE tc.triage_status = :flaky_status
              AND tc.triage_updated_at >= :period_start
              AND tc.test_fingerprint IS NOT NULL
              {m_project_filter}
              {m_suite_filter}
            GROUP BY tc.test_fingerprint
            ORDER BY last_seen DESC
            LIMIT :limit
            """
        )
        for row in (await db.execute(manual_query, manual_params)).fetchall():
            data = dict(row._mapping)
            # Defensive: the SQL already filters NULL fingerprints, but never
            # let a null/empty fingerprint through the Python merge either (it
            # would dedup-collide and isn't routable to a test).
            if not data["test_fingerprint"] or data["test_fingerprint"] in seen:
                continue
            seen.add(data["test_fingerprint"])
            data["source"] = "manual"
            items.append(data)
            if len(items) >= limit:
                break

    return {"items": items, "period_days": days, "total": len(items)}


async def failure_categories(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    suite_name: str | None = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
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
          {suite_filter}
        GROUP BY category
        ORDER BY count DESC
        """
    )
    result = await db.execute(query, params)
    return {"items": [dict(row._mapping) for row in result.fetchall()], "period_days": days}


async def top_failing_tests(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    limit: int,
    suite_name: str | None = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    params: dict = {"period_start": _period_start(days), "limit": limit}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
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
          {suite_filter}
        GROUP BY tc.test_fingerprint
        ORDER BY fail_count DESC
        LIMIT :limit
        """
    )
    result = await db.execute(query, params)
    items = [dict(row._mapping) for row in result.fetchall()]

    # Granular enrichment (Phase 5): FAILURE LOCATION — the first FAILED/BROKEN
    # step name for each failing test, read from the LATEST-RUN-ONLY snapshot
    # anchored to the test's canonical id. Batched once for all rows → no N+1.
    # Only resolvable when a single project is in scope (the snapshot anchor is
    # project-scoped and ``test_fingerprint`` is not salted); unscoped / multi-
    # tenant views leave ``failure_step`` as None to avoid cross-tenant reads.
    if project_id and items:
        from app.services.runs_service import first_failed_step_by_fingerprint

        fingerprints = [i["test_fingerprint"] for i in items if i.get("test_fingerprint")]
        step_by_fp = await first_failed_step_by_fingerprint(db, project_id, fingerprints)
        for i in items:
            i["failure_step"] = step_by_fp.get(i.get("test_fingerprint"))
    else:
        for i in items:
            i["failure_step"] = None

    return {"items": items, "period_days": days}


async def coverage_stats(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    suite_name: str | None = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    period_start = _period_start(days)
    params: dict = {"period_start": period_start}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
    # Effective suite — preferred over raw ``tc.suite_name`` for
    # GROUP BY so live_stream runs whose SDK stamped the test class
    # name on every event still bucket under their session-level
    # ``primary_suite_name`` (e.g. "API Regression Multi-Class")
    # instead of splitting into per-class buckets that the user
    # never asked for. Shares ``_effective_suite_sql`` with the filter
    # helper so grouping and filtering can never drift; the extra
    # 'Unknown Suite' fallback keeps NULL-suite rows in one labelled
    # bucket rather than dropping them.
    effective_suite = f"COALESCE({_effective_suite_sql()}, 'Unknown Suite')"
    suite_query = text(
        f"""
        SELECT
            {effective_suite} AS suite_name,
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
          {suite_filter}
        GROUP BY {effective_suite}
        ORDER BY unique_tests DESC
        LIMIT 50
        """
    )
    total_query = text(
        f"""
        SELECT
            COUNT(DISTINCT tc.test_fingerprint)  AS unique_tests,
            COUNT(DISTINCT {effective_suite})    AS suite_count,
            COUNT(*)                             AS total_executions,
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(COUNT(*), 0), 1
            ) AS avg_pass_rate,
            COUNT(DISTINCT DATE_TRUNC('day', tr.created_at)) AS days_with_runs
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.created_at >= :period_start
          {project_filter}
          {suite_filter}
        """
    )
    suites = (await db.execute(suite_query, params)).fetchall()
    total = (await db.execute(total_query, params)).one()
    return {
        "summary": _row_dict(total),
        "suites": [_row_dict(row) for row in suites],
        "period_days": days,
    }


async def suite_detail(
    db: AsyncSession,
    project_id: str | None,
    suite_name: str,
    days: int,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    params: dict = {
        "suite_name": suite_name,
        "suite_key": (suite_name or "").strip().lower(),
        "period_start": _period_start(days),
    }
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    # Match by the EFFECTIVE suite (equality), NOT a loose OR. The OR form
    # (``tc.suite_name = :s OR tr.primary_suite_name = :s``) over-returns:
    # for a multi-suite run whose ``primary_suite_name`` matches, the second
    # clause is true for EVERY test of that run, so an Order test surfaces
    # under "Smoke" and ``/coverage/suite`` then contradicts ``/coverage``
    # (which groups by this same effective suite). Equality on the effective
    # suite attributes each test to exactly one suite: the session label for
    # live_stream rows (where per-event ``tc.suite_name`` is often the test
    # class name), the per-event suite for file uploads. Shares
    # ``_effective_suite_sql`` with ``coverage_stats`` + ``_suite_filter_sql``
    # so attribution can never drift. (Same fix as Bug 2026-05-20, which
    # migrated the other analytics queries but missed this one.)
    suite_match = f"LOWER({_effective_suite_sql()}) = :suite_key"
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
        WHERE {suite_match}
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
        WHERE {suite_match}
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
        WHERE {suite_match}
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
    summary = _row_dict(summary_row)

    # Run-level aggregate fallback. When the per-test rows didn't land
    # (live-stream Redis buffer eviction, or a write-path bug that left
    # tc.suite_name NULL for runs whose TestRun.primary_suite_name is
    # set), the summary above reads zero from test_cases. The /reports/
    # summary page in this same window would still show the suite via
    # its runs_missing_cases UNION-ALL path — so /coverage/suite would
    # contradict it ("0 tests" vs "100 tests"). Mirror that fallback so
    # the two surfaces agree.
    needs_fallback = (
        not summary.get("total_executions")
        and not cases_rows
        and not runs_rows
    )
    if needs_fallback:
        run_fallback_params: dict = {"period_start": _period_start(days)}
        run_fallback_project_filter = _tenant_filter(
            run_fallback_params,
            project_id=project_id,
            allowed_project_ids=allowed_project_ids,
        )
        run_fallback_params["suite_name"] = _normalise_suite_name(suite_name)
        run_fallback_query = text(
            f"""
            SELECT
                COALESCE(SUM(tr.total_tests),   0)  AS unique_tests,
                COALESCE(SUM(tr.total_tests),   0)  AS total_executions,
                COALESCE(SUM(tr.passed_tests),  0)  AS passed,
                COALESCE(SUM(tr.failed_tests),  0)
                  + COALESCE(SUM(tr.broken_tests), 0) AS failed,
                COALESCE(SUM(tr.skipped_tests), 0)  AS skipped,
                CASE
                    WHEN COALESCE(SUM(tr.total_tests), 0) = 0 THEN 0.0
                    ELSE ROUND(
                        SUM(tr.passed_tests) * 100.0
                        / NULLIF(SUM(tr.total_tests), 0), 1
                    )
                END                                  AS pass_rate,
                COALESCE(ROUND(AVG(tr.duration_ms)::numeric, 0), 0) AS avg_duration_ms,
                MAX(tr.created_at)                   AS last_run_at
            FROM test_runs tr
            WHERE LOWER(TRIM(COALESCE(tr.primary_suite_name, ''))) = :suite_name
              AND tr.created_at >= :period_start
              AND NOT EXISTS (
                  SELECT 1 FROM test_cases tc2
                  WHERE tc2.test_run_id = tr.id
              )
              {run_fallback_project_filter}
            """
        )
        recent_runs_fallback_query = text(
            f"""
            SELECT
                tr.id::text                                  AS test_run_id,
                tr.build_number,
                tr.created_at                                AS run_date,
                COALESCE(tr.passed_tests, 0)                 AS passed,
                COALESCE(tr.failed_tests, 0)
                  + COALESCE(tr.broken_tests, 0)             AS failed,
                COALESCE(tr.skipped_tests, 0)                AS skipped,
                CASE
                    WHEN COALESCE(tr.total_tests, 0) = 0 THEN 0.0
                    ELSE ROUND(
                        COALESCE(tr.passed_tests, 0) * 100.0
                        / NULLIF(tr.total_tests, 0), 1
                    )
                END                                          AS pass_rate
            FROM test_runs tr
            WHERE LOWER(TRIM(COALESCE(tr.primary_suite_name, ''))) = :suite_name
              AND tr.created_at >= :period_start
              AND NOT EXISTS (
                  SELECT 1 FROM test_cases tc2
                  WHERE tc2.test_run_id = tr.id
              )
              {run_fallback_project_filter}
            ORDER BY tr.created_at DESC
            LIMIT 15
            """
        )
        fallback_summary = (
            await db.execute(run_fallback_query, run_fallback_params)
        ).one()
        fallback_runs = (
            await db.execute(recent_runs_fallback_query, run_fallback_params)
        ).fetchall()
        fallback_total = int(fallback_summary.total_executions or 0)
        if fallback_total > 0:
            summary = _row_dict(fallback_summary)
            runs_rows = fallback_runs

    return {
        "suite_name": suite_name,
        "summary": summary,
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
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    params: dict = {"limit": size, "offset": (page - 1) * size}
    # defects table is queried directly here (alias ``d``), so scope on
    # ``d.project_id`` rather than going through ``tr``.
    project_filter = _tenant_filter(
        params,
        project_id=project_id,
        allowed_project_ids=allowed_project_ids,
        table_alias="d",
    )
    status_filter = "AND d.resolution_status = :resolution_status" if resolution_status else ""
    if resolution_status:
        params["resolution_status"] = resolution_status.upper()
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
            tc.suite_name,
            r.name AS release_name
        FROM defects d
        JOIN test_cases tc ON tc.id = d.test_case_id
        LEFT JOIN test_runs tr ON tr.id = tc.test_run_id
        LEFT JOIN release_test_run_links rtrl ON rtrl.test_run_id = tr.id
        LEFT JOIN releases r ON r.id = rtrl.release_id
        WHERE 1=1
        {project_filter}
        {status_filter}
        ORDER BY d.created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )

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


# Maps the UI's P0–P3 vocabulary onto the defects.severity column's CRITICAL/HIGH/MEDIUM/LOW values.
_SEVERITY_FROM_PRIORITY = {
    "P0": "CRITICAL",
    "P1": "HIGH",
    "P2": "MEDIUM",
    "P3": "LOW",
}

# Jira keys look like `ABC-123` — extract from a pasted browse URL.
_JIRA_KEY_RE = re.compile(r"([A-Z][A-Z0-9]+-\d+)")


def _jira_key_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = _JIRA_KEY_RE.search(url)
    return match.group(1) if match else None


async def _find_recent_test_case_id(
    db: AsyncSession,
    project_id: uuid.UUID,
    test_name: str | None,
    suite_name: str | None,
) -> uuid.UUID | None:
    """Best-effort attach: find the most recent matching TestCase in the project.

    Returns None when no match is found — the caller stores the defect with a
    NULL test_case_id rather than failing the intake.
    """
    if not test_name:
        return None
    # TestCase has no direct project_id — scope through TestRun.project_id and
    # order by run recency (the most recent run's matching test case wins).
    stmt = (
        select(TestCase.id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.test_name == test_name)
        .where(TestRun.project_id == project_id)
    )
    if suite_name:
        stmt = stmt.where(TestCase.suite_name == suite_name)
    stmt = stmt.order_by(desc(TestRun.created_at)).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_manual_defect(db: AsyncSession, project_id: uuid.UUID, payload: dict) -> Defect:
    """Insert a manually-intaken defect. Caller commits the session."""
    # Project-existence guard. ``_find_recent_test_case_id`` below
    # silently returns None when no test case matches the project, so a
    # bogus project_id wouldn't fail until commit-time as an opaque FK
    # violation. Surfacing the 404 here makes the FE error toast actionable.
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found — refresh the page or pick a different project.",
        )
    test_case_id = await _find_recent_test_case_id(
        db,
        project_id,
        payload.get("test_name"),
        payload.get("suite_name"),
    )
    severity_label = _SEVERITY_FROM_PRIORITY.get(payload["severity"], "MEDIUM")
    defect = Defect(
        project_id=project_id,
        test_case_id=test_case_id,
        title=payload["title"][:255],
        description=payload.get("description"),
        severity=severity_label,
        failure_category=payload.get("failure_category"),
        component=payload.get("component"),
        jira_ticket_url=payload.get("jira_ticket_url"),
        jira_ticket_id=_jira_key_from_url(payload.get("jira_ticket_url")),
        resolution_status="OPEN",
        promotion_source="manual",
    )
    db.add(defect)
    await db.flush()
    return defect


async def ai_analysis_summary(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> dict:
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
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
    result = await db.execute(query, params)
    return dict(result.one()._mapping)
