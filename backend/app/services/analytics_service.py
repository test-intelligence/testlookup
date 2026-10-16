from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import bindparam, desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Defect, Project, ServiceOwnershipRule, TestCase, TestRun, TriageStatus
from app.services.analytics_scope import (
    AnalyticsScope,
    ReleaseArg,
    SuiteArg,
    effective_suite_sql,
    release_filter_sql,
    run_label_match_sql,
    scoped_text,
    suite_filter_sql,
    suite_match_sql,
)
from app.services.defect_status import ACTIVE_QUERY_VALUE, ACTIVE_STATUSES, ESCAPE_RATE_MIN_SAMPLE, priority_sql
from app.services.flaky_investigator import determine_likely_cause
from app.services.flaky_signals import (
    MIN_FLIPS_FOR_INTERMITTENCY as _FLAKY_MIN_FLIPS,
    compute_intermittency_signals,
)


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


# The effective-suite rule and every suite / release fragment live in
# ``analytics_scope`` (VIZ-201): five copies of the suite rule had drifted
# apart, and ``tests/test_analytics_scope_ratchet.py`` now fails on a sixth.
# These names stay as thin delegates for existing importers.
_effective_suite_sql = effective_suite_sql


def _suite_filter_sql() -> str:
    """The single-suite filter fragment (``AND ... = :suite_name``)."""
    return suite_filter_sql({}, "_")


def _add_suite_param(params: dict, suite_name: SuiteArg) -> str:
    """Suite scoping: ``""`` when none, one name exactly as before VIZ-201,
    several as an expanding ``IN`` (OR within the dimension)."""
    return suite_filter_sql(params, suite_name)


def _add_release_param(
    params: dict,
    release_id: ReleaseArg,
    *,
    table_alias: str = "tr",
) -> str:
    """Release scoping for raw-SQL analytics queries (S4a, VIZ-201).

    Returns ``""`` when no release is requested, so a caller that omits
    ``release_id`` produces byte-identical SQL to before this existed (NFR1).
    Deliberately a conditional fragment, never
    ``AND (:release_id IS NULL OR tr.primary_release_id = :release_id)``: the
    planner cannot resolve that at plan time and stops using
    ``ix_test_runs_project_release_created`` for every call. See
    ``analytics_scope.release_filter_sql``.
    """
    return release_filter_sql(params, release_id, table_alias=table_alias)


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
      unrestricted *tenancy* (admin path) — but still life-cycle filtered,
      see below. Admin-only callers must opt in explicitly by passing
      ``None``; new non-admin callers should always pass a set, even empty.

    **Every branch also excludes soft-deleted projects.** Tenancy answers
    "who may see this project"; it does not answer "does this project still
    exist". ``DELETE /projects/{id}`` only flips ``is_active``, so on the
    admin path — where the tenancy clause is empty by design — every
    analytics query aggregated over deleted projects. Measured live, the
    unscoped Coverage response was built *entirely* from them: 27 suites
    across 13 deleted projects, 47,105 executions where the live projects
    account for 672. The two real projects did not appear at all.

    Mutates ``params`` in place to bind the placeholder values.
    """
    # Life-cycle scope, applied on top of whichever tenancy clause is chosen.
    # A subquery rather than a join: the callers are ~20 hand-written SQL
    # strings with their own FROM lists, and adding a join to each is both
    # more invasive and easy to get subtly wrong (row multiplication).
    live = f"AND {table_alias}.{column} IN (SELECT id FROM projects WHERE is_active)"
    if project_id:
        params["project_id"] = str(project_id)
        return f"AND {table_alias}.{column} = :project_id {live}"
    if allowed_project_ids is None:
        # Unrestricted tenancy — admin path. Callers that don't intend this
        # should pass an empty set, which fails closed.
        return live
    ids = list(allowed_project_ids)
    if not ids:
        # Empty membership set — fail closed.
        return "AND FALSE"
    placeholders = ", ".join(f":pid_{i}" for i, _ in enumerate(ids))
    for i, pid in enumerate(ids):
        params[f"pid_{i}"] = str(pid)
    return f"AND {table_alias}.{column} IN ({placeholders}) {live}"


async def flaky_tests(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    limit: int,
    suite_name: SuiteArg = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    # S4a. None = no release filter, and the SQL is then byte-identical
    # to before this parameter existed (NFR1). VIZ-201: a sequence is OR.
    release_id: ReleaseArg = None,
) -> dict:
    # No LIMIT in either query: ``limit`` is the length of the list, and
    # ``total`` is the count of every flaky test. /failures read the list's
    # length as the count -- "20 tests intermittent", "Flaky tests 20" -- for a
    # project with 30 (browser E2E pass, 2026-10-08). The rows are one per
    # flaky fingerprint, so the full set stays small.
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
    release_filter = _add_release_param(params, release_id)
    # The /failures headline states this list as fact -- "N tests show pass/fail
    # oscillation on the same SHA. Re-runs may pass without fixing the underlying
    # race" -- so membership must actually BE oscillation. A failure-ratio band
    # alone is order-blind and admitted stable regressions: a test broken in four
    # consecutive builds sits at 0.8 and became the headline example of flakiness,
    # telling the reader to re-run a test that will never pass.
    #
    # Same rule and threshold as metrics_service._FLAKY_MIN_FLIPS (#461) and
    # test_health_coach_service._MIN_FLIPS_FOR_QUARANTINE (#462), and the same one
    # the agents already enforce ("Flaky classification requires status
    # transitions (oscillation, not regression)" -- anomaly_agent).
    query = scoped_text(
        f"""
        SELECT
            test_fingerprint,
            MAX(test_name)   AS test_name,
            MAX(suite_name)  AS suite_name,
            MAX(class_name)  AS class_name,
            MAX(project_name) AS project_name,
            COUNT(*)         AS total_runs,
            COUNT(*) FILTER (WHERE is_failed = 1) AS fail_count,
            COUNT(*) FILTER (WHERE is_passed = 1) AS pass_count,
            ROUND(COUNT(*) FILTER (WHERE is_failed = 1) * 100.0 / COUNT(*), 1) AS failure_rate_pct,
            MAX(created_at)  AS last_seen
        FROM (
            SELECT
                tch.test_fingerprint AS test_fingerprint,
                tc.test_name         AS test_name,
                tc.suite_name        AS suite_name,
                tc.class_name        AS class_name,
                p.name               AS project_name,
                tch.created_at       AS created_at,
                CASE WHEN tch.status IN ('FAILED', 'BROKEN') THEN 1 ELSE 0 END AS is_failed,
                CASE WHEN tch.status = 'PASSED' THEN 1 ELSE 0 END AS is_passed,
                LAG(CASE WHEN tch.status IN ('FAILED', 'BROKEN') THEN 1 ELSE 0 END) OVER (
                    PARTITION BY tch.test_fingerprint
                    ORDER BY tch.created_at, tch.id
                ) AS prev_failed
            FROM test_case_history tch
            JOIN test_cases tc ON tc.id = tch.test_case_id
            JOIN test_runs tr   ON tr.id = tch.test_run_id
            LEFT JOIN projects p ON p.id = tr.project_id
            WHERE tch.created_at >= :period_start
              {project_filter}
              {suite_filter}
              {release_filter}
        ) seq
        GROUP BY test_fingerprint
        HAVING COUNT(*) >= 3
           AND COUNT(*) FILTER (WHERE is_failed = 1) * 1.0 / COUNT(*) BETWEEN 0.05 AND 0.95
           AND COUNT(*) FILTER (
                   WHERE prev_failed IS NOT NULL AND prev_failed <> is_failed
               ) >= {_FLAKY_MIN_FLIPS}
        -- Ties are broken by name, then fingerprint: without that, equal
        -- counts came back in plan order (hash vs sort aggregate, table
        -- statistics), so the list reshuffled between reloads and the golden
        -- characterisation flaked.
        ORDER BY failure_rate_pct DESC, suite_name, class_name, test_name, test_fingerprint
        """,
        params,
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
    #
    # Always asked, even when the auto list alone fills ``limit``: a
    # human-flagged flake the detector missed still counts toward ``total``.
    manual_params: dict = {
        "period_start": _period_start(days),
        "flaky_status": TriageStatus.FLAKY_TEST.value,
    }
    m_project_filter = _tenant_filter(
        manual_params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    m_suite_filter = _add_suite_param(manual_params, suite_name)
    # Scope the merge the same way as the auto-detected half above.
    #
    # This branch builds a FRESH params dict, so the `release_filter` from
    # the top of the function is bound to the wrong one and the query had
    # no filter at all. Under a release, /failures therefore listed
    # release-scoped intermittents merged with human-triaged flakes from
    # EVERY release, in one list, with `source` the only hint and nothing
    # saying the two halves were scoped differently.
    m_release_filter = _add_release_param(manual_params, release_id)
    manual_query = scoped_text(
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
          {m_release_filter}
        GROUP BY tc.test_fingerprint
        ORDER BY last_seen DESC
        """,
        manual_params,
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

    total = len(items)
    items = items[:limit]

    # FLK-P4: attribute a likely cause to each listed flake from its
    # intermittency signals so /failures explains *why*, matching /flaky-coach.
    # Best-effort + tenant-scoped — never breaks the list on failure.
    await _attach_flaky_likely_cause(
        db, items, days=days, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )

    return {"items": items, "period_days": days, "total": total}


async def _attach_flaky_likely_cause(
    db: AsyncSession,
    items: list[dict],
    *,
    days: int,
    project_id: str | None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
) -> None:
    """Mutate ``items`` in place, adding ``likely_cause`` / ``likely_cause_code``
    from FLK-P1 intermittency signals over each fingerprint's recent window.

    One batched, tenant-scoped windowed query (top-30 runs per fingerprint).
    Pure-read; swallows any error so the /failures list always renders.
    """
    fps = [it["test_fingerprint"] for it in items if it.get("test_fingerprint")]
    if not fps:
        return
    try:
        params: dict = {"period_start": _period_start(days), "fps": fps}
        project_filter = _tenant_filter(
            params, project_id=project_id, allowed_project_ids=allowed_project_ids,
        )
        query = text(
            f"""
            SELECT fp, status, created_at, error_message, stack_trace,
                   retry_count, is_flaky_run
            FROM (
                SELECT
                    tch.test_fingerprint AS fp,
                    tch.status AS status,
                    tch.created_at AS created_at,
                    tc.error_message AS error_message,
                    tc.stack_trace AS stack_trace,
                    tc.retry_count AS retry_count,
                    tc.is_flaky_run AS is_flaky_run,
                    ROW_NUMBER() OVER (
                        PARTITION BY tch.test_fingerprint
                        ORDER BY tch.created_at DESC
                    ) AS rn
                FROM test_case_history tch
                JOIN test_cases tc ON tc.id = tch.test_case_id
                JOIN test_runs tr  ON tr.id = tch.test_run_id
                WHERE tch.created_at >= :period_start
                  AND tch.test_fingerprint = ANY(:fps)
                  {project_filter}
            ) ranked
            WHERE rn <= 30
            ORDER BY fp, created_at DESC
            """
        )
        rows_by_fp: dict[str, list[dict]] = {}
        for r in (await db.execute(query, params)).fetchall():
            m = r._mapping
            rows_by_fp.setdefault(m["fp"], []).append({
                "status": m["status"],
                "created_at": m["created_at"],
                "error_message": m["error_message"],
                "stack_trace": m["stack_trace"],
                "retry_count": m["retry_count"],
                "is_flaky_run": m["is_flaky_run"],
            })
        for it in items:
            recs = rows_by_fp.get(it.get("test_fingerprint"))
            if not recs:
                continue
            signals = compute_intermittency_signals(recs)
            cause, code = determine_likely_cause(signals)
            it["likely_cause"] = cause
            it["likely_cause_code"] = code
    except Exception:  # pragma: no cover — best-effort enrichment
        return


async def failure_categories(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    suite_name: SuiteArg = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    # S4a. None = no release filter, and the SQL is then byte-identical
    # to before this parameter existed (NFR1). VIZ-201: a sequence is OR.
    release_id: ReleaseArg = None,
) -> dict:
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
    release_filter = _add_release_param(params, release_id)
    # Grouped by (category, status) so the derived failure-kind triad
    # (US-9.1) can apply its BROKEN nudge; ``items`` keeps its historical
    # per-category shape by re-aggregating in Python (rows are few).
    query = scoped_text(
        f"""
        SELECT
            COALESCE(tc.failure_category, 'UNKNOWN') AS category,
            tc.status AS status,
            COUNT(*) AS count
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.status IN ('FAILED', 'BROKEN')
          AND tc.created_at >= :period_start
          {project_filter}
          {suite_filter}
          {release_filter}
        GROUP BY category, tc.status
        ORDER BY count DESC
        """,
        params,
    )
    result = await db.execute(query, params)
    raw_rows = [dict(row._mapping) for row in result.fetchall()]

    from app.services.failure_kind import failure_kind, kind_counts

    # Items are grouped by (category, KIND), not by category alone.
    #
    # They used to be per-category with ``failure_kind(category, None)`` — a
    # category-only derivation that cannot apply the BROKEN nudge, because the
    # nudge needs a per-row status the aggregate had thrown away. The result
    # contradicted ``by_kind`` inside the SAME response: one payload reported
    # ``items: [{category: "UNKNOWN", count: 11, kind: "unknown"}]`` while
    # ``by_kind`` correctly showed 2 of those 11 as ``infrastructure``.
    #
    # That was not cosmetic. ``FailureAnalysisPage`` FILTERS the category
    # distribution card on ``item.kind``, so selecting "infrastructure" silently
    # missed rows that genuinely were infrastructure failures — and infra
    # failures are exactly what must not be triaged as product bugs.
    #
    # Splitting keeps every count exact and makes the filter correct. A category
    # may now appear once per kind, which is faithful: a category legitimately
    # contains failures of more than one kind. ``by_kind`` is unchanged, and the
    # SPA's client-side fallback (summing ``item.count`` per ``item.kind``) now
    # produces the right totals too, where before it inherited the same error.
    by_cat_kind: dict[tuple[str, str], int] = {}
    for row in raw_rows:
        key = (row["category"], failure_kind(row["category"], row["status"]))
        by_cat_kind[key] = by_cat_kind.get(key, 0) + row["count"]
    items = [
        {"category": category, "count": count, "kind": kind}
        for (category, kind), count in sorted(
            by_cat_kind.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
        )
    ]

    # Parallel by-kind aggregation (product / test_code / infrastructure /
    # unknown) — AI-derived, zero counts included for stable UI chips.
    by_kind = kind_counts(
        (row["category"], row["status"], row["count"]) for row in raw_rows
    )
    return {"items": items, "by_kind": by_kind, "period_days": days}


async def top_failing_tests(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    limit: int,
    suite_name: SuiteArg = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    # S4a. None = no release filter, and the SQL is then byte-identical
    # to before this parameter existed (NFR1). VIZ-201: a sequence is OR.
    release_id: ReleaseArg = None,
) -> dict:
    params: dict = {"period_start": _period_start(days), "limit": limit}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
    release_filter = _add_release_param(params, release_id)
    query = scoped_text(
        f"""
        SELECT
            tc.test_fingerprint,
            MAX(tc.test_name)   AS test_name,
            MAX(tc.suite_name)  AS suite_name,
            MAX(tc.class_name)  AS class_name,
            MAX(tc.failure_category) AS failure_category,
            COUNT(*) AS fail_count,
            MAX(tc.created_at)  AS last_failed,
            -- The run holding that last failure (HON-05): the Suspects panel
            -- asked the newest FAILED run of the whole project instead, which
            -- for any other test named commits from the wrong range. Same
            -- order as MAX(created_at) above, so it is the same row; the run's
            -- own time breaks a tie. Scoped by the tenant filter below. Its
            -- build number names the run in the panel (same ORDER BY).
            (ARRAY_AGG(tc.test_run_id ORDER BY tc.created_at DESC, tr.created_at DESC, tc.test_run_id DESC))[1]
                AS last_failed_run_id,
            (ARRAY_AGG(tr.build_number ORDER BY tc.created_at DESC, tr.created_at DESC, tc.test_run_id DESC))[1]
                AS last_failed_build,
            -- Over every failing test, before LIMIT: /failures read the
            -- top-N list's length as "Repeat failures" (15, the default
            -- limit, for a project with more; browser E2E pass, 2026-10-08).
            COUNT(*) OVER () AS failing_total,
            SUM(CASE WHEN COUNT(*) >= 2 THEN 1 ELSE 0 END) OVER () AS repeat_total
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.status IN ('FAILED', 'BROKEN')
          AND tc.created_at >= :period_start
          {project_filter}
          {suite_filter}
          {release_filter}
        GROUP BY tc.test_fingerprint
        -- Ties are broken by name, then fingerprint: without that, equal
        -- counts came back in plan order (hash vs sort aggregate, table
        -- statistics), so the list reshuffled between reloads and the golden
        -- characterisation flaked.
        ORDER BY fail_count DESC, suite_name, class_name, test_name, tc.test_fingerprint
        LIMIT :limit
        """,
        params,
    )
    result = await db.execute(query, params)
    items = [dict(row._mapping) for row in result.fetchall()]
    # The window totals ride on every row; a row without them (a stand-in
    # result) counts what it has.
    failing_total = len(items)
    repeat_total = sum(1 for i in items if (i.get("fail_count") or 0) >= 2)
    if items and items[0].get("failing_total") is not None:
        failing_total = int(items[0]["failing_total"])
        repeat_total = int(items[0].get("repeat_total") or 0)
    for i in items:
        i.pop("failing_total", None)
        i.pop("repeat_total", None)
        # A UUID from asyncpg; the payload is cached and embedded in reports
        # as JSON, so it travels as a string. ``.get``: a hand-built stand-in
        # row may not carry the column.
        run_id = i.get("last_failed_run_id")
        i["last_failed_run_id"] = str(run_id) if run_id is not None else None
        i.setdefault("last_failed_build", None)

    # Derived failure kind (US-9.1). Category-only fidelity: this query
    # aggregates per fingerprint (no per-row status survives MAX()), so the
    # BROKEN nudge cannot apply here — uncategorised rows stay "unknown".
    from app.services.failure_kind import failure_kind

    for i in items:
        i["failure_kind"] = failure_kind(i.get("failure_category"), None)

    # Granular enrichment (Phase 5): FAILURE LOCATION — the first FAILED/BROKEN
    # step name for each failing test, read from the LATEST-RUN-ONLY snapshot
    # anchored to the test's canonical id. Batched once for all rows → no N+1.
    # Only resolvable when a single project is in scope (the snapshot anchor is
    # project-scoped and ``test_fingerprint`` is not salted); unscoped / multi-
    # tenant views leave ``failure_step`` as None to avoid cross-tenant reads.
    #
    # FAIL-01, same single-project rule: the test's NEWEST failing row inside
    # this scope (window, suite, release) -- its first error line, the head of
    # its trace (truncated and redacted server-side), its owner and the run /
    # test case "Open in run" opens. One statement (three when some row needs
    # the ownership rules); in All Projects every key is None and
    # ``detail_available`` False, so the page says to pick a project.
    from app.services.failure_detail_service import empty_detail as _empty_failure_detail

    if project_id and items:
        from app.services.failure_detail_service import latest_failure_detail_by_fingerprint
        from app.services.runs_service import first_failed_step_by_fingerprint

        fingerprints = [i["test_fingerprint"] for i in items if i.get("test_fingerprint")]
        step_by_fp = await first_failed_step_by_fingerprint(db, project_id, fingerprints)
        detail_by_fp = await latest_failure_detail_by_fingerprint(
            db, project_id, fingerprints, days=days, suite_name=suite_name, release_id=release_id,
            period_start=params["period_start"],
        )
        for i in items:
            i["failure_step"] = step_by_fp.get(i.get("test_fingerprint"))
            i["detail_available"] = True
            i.update(detail_by_fp.get(str(i.get("test_fingerprint") or "")) or _empty_failure_detail())
    else:
        for i in items:
            i["failure_step"] = None
            i["detail_available"] = False
            i.update(_empty_failure_detail())

    return {
        "items": items,
        "period_days": days,
        # Every failing test in the window, and those failing twice or more.
        "total": failing_total,
        "repeat_total": repeat_total,
    }


# ── FAIL-02: median time to fix (owner decision D02) ────────────────────────

#: Fewer fixed episodes than this in the window: "Not measured", never a number.
TIME_TO_FIX_MIN_FIXES = 5
#: A test that failed and recovered this many times in the window oscillates:
#: its "fixes" are re-runs, not repairs, and it is left out (and counted).
TIME_TO_FIX_OSCILLATION_MIN = 3


def _time_to_fix_reason(fixed: int, days: int) -> str:
    return f"Fewer than {TIME_TO_FIX_MIN_FIXES} tests were fixed in the last {days} days ({fixed} fixed)."


def _hours(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


async def time_to_fix(db: AsyncSession, scope: AnalyticsScope) -> dict:
    """The MEDIAN time from a test's first failure to its next pass, over the
    window (D02): test recovery, not defect resolution.

    An episode starts at a FAILED/BROKEN execution that follows a pass (or is
    the test's first execution in the window) and closes at the next PASSED
    one; SKIPPED / UNKNOWN neither open nor close an episode. An episode
    already open when the window starts begins at its first failure INSIDE
    the window. Still-open episodes are counted (``open``) and never enter
    the median.

    Left out, and counted: tests FLAGGED flaky -- a live quarantine request
    (``flaky_quarantine_service._LIVE_STATES``) or a human ``FLAKY_TEST``
    triage in the window (not the /failures oscillation rule: pass, fail,
    fail, pass is 2 flips and every genuine fix meets it); and tests with
    ``TIME_TO_FIX_OSCILLATION_MIN`` or more episodes in the window.

    A test is ``(project, fingerprint)``: the fingerprint is not salted per
    project, so the same test in two projects is two tests. Below
    ``TIME_TO_FIX_MIN_FIXES`` closed episodes the answer is not measured:
    ``median_hours`` / ``mean_hours`` are None and ``reason`` says why.
    ONE statement; the scope's suite and release fragments apply to the
    executions, the tenant filter to every half.
    """
    from app.models.postgres import FlakyQuarantineRequest
    from app.services.flaky_quarantine_service import _LIVE_STATES

    days = scope.window_days
    params: dict = {
        "period_start": _period_start(days),
        "live_states": list(_LIVE_STATES),
        "flaky_status": TriageStatus.FLAKY_TEST.value,
        "oscillation_min": TIME_TO_FIX_OSCILLATION_MIN,
    }
    pid, allowed = scope.project, scope.allowed_project_ids
    project_filter = _tenant_filter(params, project_id=pid, allowed_project_ids=allowed)
    q_project_filter = _tenant_filter(params, project_id=pid, allowed_project_ids=allowed, table_alias="q")
    tr2_project_filter = _tenant_filter(params, project_id=pid, allowed_project_ids=allowed, table_alias="tr2")
    suite_filter = _add_suite_param(params, scope.suite_arg)
    release_filter = _add_release_param(params, scope.release_arg)
    quarantine_table = FlakyQuarantineRequest.__tablename__
    statement = scoped_text(
        f"""
        WITH h AS (
            SELECT tr.project_id AS pid, tch.test_fingerprint AS fp, tch.created_at AS at, tch.id AS hid,
                   (tch.status IN ('FAILED', 'BROKEN')) AS failing
            FROM test_case_history tch
            JOIN test_cases tc ON tc.id = tch.test_case_id
            JOIN test_runs tr  ON tr.id = tch.test_run_id
            WHERE tch.created_at >= :period_start
              AND tch.status IN ('PASSED', 'FAILED', 'BROKEN')
              {project_filter}
              {suite_filter}
              {release_filter}
        ), m AS (
            SELECT pid, fp, at, hid, failing,
                   LAG(failing) OVER (PARTITION BY pid, fp ORDER BY at, hid) AS prev
            FROM h
        ), n AS (
            SELECT pid, fp, at, failing, prev,
                   SUM(CASE WHEN failing AND prev IS DISTINCT FROM TRUE THEN 1 ELSE 0 END)
                       OVER (PARTITION BY pid, fp ORDER BY at, hid ROWS UNBOUNDED PRECEDING) AS ep
            FROM m
        ), e AS (
            SELECT pid, fp, ep,
                   MIN(at) FILTER (WHERE failing) AS started_at,
                   MIN(at) FILTER (WHERE NOT failing AND prev IS TRUE) AS closed_at
            FROM n WHERE ep > 0 GROUP BY pid, fp, ep
        ), per_test AS (
            SELECT pid, fp, COUNT(*) AS episodes FROM e GROUP BY pid, fp
        ), flagged AS (
            SELECT q.project_id AS pid, q.test_fingerprint AS fp
            FROM {quarantine_table} q
            WHERE q.status IN :live_states
              {q_project_filter}
            UNION
            SELECT tr2.project_id, tc2.test_fingerprint
            FROM test_cases tc2 JOIN test_runs tr2 ON tr2.id = tc2.test_run_id
            WHERE tc2.triage_status = :flaky_status
              AND tc2.triage_updated_at >= :period_start
              {tr2_project_filter}
        ), kept AS (
            SELECT e.* FROM e JOIN per_test p USING (pid, fp)
            WHERE p.episodes < :oscillation_min
              AND NOT EXISTS (SELECT 1 FROM flagged f WHERE f.pid = e.pid AND f.fp = e.fp)
        )
        SELECT
            (SELECT COUNT(*) FROM kept WHERE closed_at IS NOT NULL) AS fixed,
            (SELECT COUNT(*) FROM kept WHERE closed_at IS NULL) AS open,
            (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM closed_at - started_at))
               FROM kept WHERE closed_at IS NOT NULL) / 3600.0 AS median_hours,
            (SELECT AVG(EXTRACT(EPOCH FROM closed_at - started_at))
               FROM kept WHERE closed_at IS NOT NULL) / 3600.0 AS mean_hours,
            (SELECT COUNT(DISTINCT (e.pid, e.fp)) FROM e JOIN flagged f ON f.pid = e.pid AND f.fp = e.fp)
                AS excluded_flaky_tests,
            (SELECT COUNT(*) FROM per_test p WHERE p.episodes >= :oscillation_min
               AND NOT EXISTS (SELECT 1 FROM flagged f WHERE f.pid = p.pid AND f.fp = p.fp))
                AS excluded_oscillating_tests
        """,
        params,
    ).bindparams(bindparam("live_states", expanding=True))
    row = dict((await db.execute(statement, params)).one()._mapping)
    fixed = int(row.get("fixed") or 0)
    measured = fixed >= TIME_TO_FIX_MIN_FIXES
    return {
        "measured": measured,
        "reason": None if measured else _time_to_fix_reason(fixed, days),
        "median_hours": _hours(row.get("median_hours")) if measured else None,
        "mean_hours": _hours(row.get("mean_hours")) if measured else None,
        "fixed": fixed,
        "open": int(row.get("open") or 0),
        "excluded_flaky_tests": int(row.get("excluded_flaky_tests") or 0),
        "excluded_oscillating_tests": int(row.get("excluded_oscillating_tests") or 0),
        "min_fixes": TIME_TO_FIX_MIN_FIXES,
        "window_days": days,
    }


async def coverage_stats(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    suite_name: SuiteArg = None,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    # S4a. None = no release filter, and the SQL is then byte-identical
    # to before this parameter existed (NFR1). VIZ-201: a sequence is OR.
    release_id: ReleaseArg = None,
) -> dict:
    period_start = _period_start(days)
    params: dict = {"period_start": period_start}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    suite_filter = _add_suite_param(params, suite_name)
    release_filter = _add_release_param(params, release_id)
    # Effective suite — preferred over raw ``tc.suite_name`` for
    # GROUP BY so live_stream runs whose SDK stamped the test class
    # name on every event still bucket under their session-level
    # ``primary_suite_name`` (e.g. "API Regression Multi-Class")
    # instead of splitting into per-class buckets that the user
    # never asked for. Shares ``_effective_suite_sql`` with the filter
    # helper so grouping and filtering can never drift; the extra
    # 'Unknown Suite' fallback keeps NULL-suite rows in one labelled
    # bucket rather than dropping them.
    effective_suite = f"COALESCE({effective_suite_sql()}, 'Unknown Suite')"
    suite_query = scoped_text(
        f"""
        SELECT
            {effective_suite} AS suite_name,
            -- Rows group by suite name alone, so in All-Projects scope one row
            -- can span several projects — `api`, `smoke` and `regression` are
            -- the most collidable names there are. The old `MAX(p.name)` then
            -- stated a single project as fact: measured live, the `api` row
            -- summed Checkout Service (5 tests) and a probe project (10) and
            -- labelled all 15 with whichever name sorted highest.
            --
            -- Answer honestly instead: name the project only when the row has
            -- exactly one, and publish the count so a consumer can tell a
            -- roll-up from a single-project row. The aggregation itself is
            -- unchanged — merging a suite across projects is a legitimate
            -- org-wide view, and no consumer reads this field today (the UI
            -- type omits it; the MCP tool and the report service both scope by
            -- project). This keeps the payload from lying to the next one.
            CASE WHEN COUNT(DISTINCT tr.project_id) = 1 THEN MAX(p.name) END
                                                     AS project_name,
            COUNT(DISTINCT tr.project_id)            AS project_count,
            COUNT(DISTINCT tc.test_fingerprint)      AS unique_tests,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED') AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN')) AS failed,
            COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped,
            -- Denominator is EVALUATED = passed + failed + broken. A SKIPPED
            -- test never ran, so it is neither a pass nor a failure and must
            -- not dilute the rate (product decision 2026-08-08).
            --
            -- The summary block below already did this; these per-suite rows
            -- were missed, so one response carried a correct summary rate and
            -- an inconsistent per-suite one — measured as suites[api]
            -- 20/(20+3+2)=80.0 where evaluated-only gives 20/23=87.0.
            -- The ``skipped`` column above still reports them; only the RATE
            -- excludes them.
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')), 0), 1
            ) AS pass_rate
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        LEFT JOIN projects p ON p.id = tr.project_id
        WHERE tc.created_at >= :period_start
          {project_filter}
          {suite_filter}
          {release_filter}
        GROUP BY {effective_suite}
        -- Ties by suite name: equal counts otherwise came back in plan order.
        ORDER BY unique_tests DESC, suite_name
        LIMIT 50
        """,
        params,
    )
    total_query = scoped_text(
        f"""
        SELECT
            COUNT(DISTINCT tc.test_fingerprint)  AS unique_tests,
            COUNT(DISTINCT {effective_suite})    AS suite_count,
            COUNT(*)                             AS total_executions,
            -- Denominator is EVALUATED = passed + failed + broken. SKIPPED is
            -- excluded: a skipped test was never executed, so it is neither a
            -- pass nor a fail and must not dilute the rate.
            --
            -- This was the last surface still dividing by COUNT(*). It made
            -- Coverage read 78.3% where the dashboard read 81.0% for the same
            -- project and window (F-014) -- 47/60 vs 47/58.
            --
            -- The rest of the codebase already states this rule outright:
            -- analysis_report_service ("evaluated = passed + failed + broken;
            -- skips don't count"), ingestion._update_run_aggregates ("EXCLUDES
            -- skipped from the denominator") and metrics_service._evaluated.
            -- ``total_executions`` above deliberately stays COUNT(*): that is a
            -- count of executions, where a skip genuinely happened.
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(
                    COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')), 0
                  ), 1
            ) AS avg_pass_rate,
            COUNT(DISTINCT DATE_TRUNC('day', tr.created_at)) AS days_with_runs
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.created_at >= :period_start
          {project_filter}
          {suite_filter}
          {release_filter}
        """,
        params,
    )
    # Both halves of ONE response must share a scope. `suite_query` above was
    # release-filtered and this one was not, so the page rendered a
    # release-scoped table under project-wide headline tiles — and
    # `computeHealthModel` derived the coverage health score from the unscoped
    # numbers sitting directly above the scoped rows. No error, just two
    # different questions answered side by side under one heading.
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
    suite_name: SuiteArg,
    days: int,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    # S4a. None = no release filter, and the SQL is then byte-identical
    # to before this parameter existed (NFR1). VIZ-201: a sequence is OR.
    release_id: ReleaseArg = None,
) -> dict:
    """One suite's drill-down -- or, since VIZ-201, the union of several
    (``suite_name`` a sequence; the echoed ``suite_name`` is then the list)."""
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    # Same fragment for every subquery below: all five sites alias
    # test_runs as `tr`, so a release filter applied to only some of them
    # would make one response internally inconsistent — a suite total scoped
    # to a release beside a per-test breakdown that was not.
    release_filter = _add_release_param(params, release_id)
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
    # No suite matches nothing (``= ''``), never everything: this query is
    # ABOUT the suite. One suite binds ``:suite_key`` exactly as before.
    suite_match = suite_match_sql(params, suite_name)
    summary_query = scoped_text(
        f"""
        SELECT
            COUNT(DISTINCT tc.test_fingerprint)                              AS unique_tests,
            COUNT(*)                                                         AS total_executions,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED')                     AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('FAILED', 'BROKEN'))        AS failed,
            COUNT(*) FILTER (WHERE tc.status = 'SKIPPED')                    AS skipped,
            -- Denominator is EVALUATED = passed + failed + broken. A SKIPPED
            -- test never ran, so it must not dilute the rate (product decision
            -- 2026-08-08). ``coverage_stats`` already excludes skips; this
            -- drill-down for the SAME suite view was missed, so /coverage read
            -- 20/23=87.0 for a suite while its detail read 20/(20+3+2)=80.0.
            -- The ``skipped`` column and ``total_executions`` still count them.
            ROUND(
                COUNT(*) FILTER (WHERE tc.status = 'PASSED') * 100.0
                / NULLIF(COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')), 0), 1
            ) AS pass_rate,
            ROUND(AVG(tc.duration_ms)::numeric, 0)                           AS avg_duration_ms,
            MAX(tc.created_at)                                               AS last_run_at
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE {suite_match}
          AND tc.created_at >= :period_start
          {project_filter}
          {release_filter}
        """,
        params,
    )
    cases_query = scoped_text(
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
                / NULLIF(COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')), 0), 1
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
          {release_filter}
        GROUP BY tc.test_fingerprint
        ORDER BY failed DESC, total_executions DESC
        LIMIT 200
        """,
        params,
    )
    runs_query = scoped_text(
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
                / NULLIF(COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')), 0), 1
            ) AS pass_rate
        FROM test_runs tr
        JOIN test_cases tc ON tc.test_run_id = tr.id
        WHERE {suite_match}
          AND tr.created_at >= :period_start
          {project_filter}
          {release_filter}
        GROUP BY tr.id, tr.build_number, tr.created_at
        ORDER BY tr.created_at DESC
        LIMIT 15
        """,
        params,
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
        # Carry the release bind into the FALLBACK params too.
        #
        # `release_filter` was built against `params` far above, but this branch
        # builds a FRESH dict — and then interpolates that same fragment into
        # both fallback queries. So `AND tr.primary_release_id = :release_id`
        # was executed with no value bound: a StatementError, surfacing as a 500
        # on the one path that exists to render an empty state.
        #
        # The trigger is ordinary: pick a release, open a suite that only ran in
        # a different one. All three primary queries come back empty, which is
        # exactly what puts us in this branch.
        _add_release_param(run_fallback_params, release_id)
        run_fallback_project_filter = _tenant_filter(
            run_fallback_params,
            project_id=project_id,
            allowed_project_ids=allowed_project_ids,
        )
        # The run's own label is the only suite evidence a run without rows has.
        run_label_match = run_label_match_sql(run_fallback_params, suite_name)
        run_fallback_query = scoped_text(
            f"""
            SELECT
                COALESCE(SUM(tr.total_tests),   0)  AS unique_tests,
                COALESCE(SUM(tr.total_tests),   0)  AS total_executions,
                COALESCE(SUM(tr.passed_tests),  0)  AS passed,
                COALESCE(SUM(tr.failed_tests),  0)
                  + COALESCE(SUM(tr.broken_tests), 0) AS failed,
                COALESCE(SUM(tr.skipped_tests), 0)  AS skipped,
                -- Denominator is EVALUATED = passed + failed + broken, matching
                -- the test_cases path above and the canonical rule
                -- (ingestion._update_run_aggregates, metrics_service). Dividing
                -- by total_tests would put SKIPPED back in the denominator and
                -- make this fallback disagree with the primary summary.
                CASE
                    WHEN COALESCE(SUM(tr.passed_tests), 0)
                       + COALESCE(SUM(tr.failed_tests), 0)
                       + COALESCE(SUM(tr.broken_tests), 0) = 0 THEN 0.0
                    ELSE ROUND(
                        SUM(tr.passed_tests) * 100.0
                        / NULLIF(
                            SUM(tr.passed_tests) + SUM(tr.failed_tests)
                              + SUM(tr.broken_tests), 0
                          ), 1
                    )
                END                                  AS pass_rate,
                COALESCE(ROUND(AVG(tr.duration_ms)::numeric, 0), 0) AS avg_duration_ms,
                MAX(tr.created_at)                   AS last_run_at
            FROM test_runs tr
            WHERE {run_label_match}
              AND tr.created_at >= :period_start
              AND NOT EXISTS (
                  SELECT 1 FROM test_cases tc2
                  WHERE tc2.test_run_id = tr.id
              )
              {run_fallback_project_filter}
              {release_filter}
            """,
            run_fallback_params,
        )
        recent_runs_fallback_query = scoped_text(
            f"""
            SELECT
                tr.id::text                                  AS test_run_id,
                tr.build_number,
                tr.created_at                                AS run_date,
                COALESCE(tr.passed_tests, 0)                 AS passed,
                COALESCE(tr.failed_tests, 0)
                  + COALESCE(tr.broken_tests, 0)             AS failed,
                COALESCE(tr.skipped_tests, 0)                AS skipped,
                -- EVALUATED denominator (passed + failed + broken); see the
                -- run_fallback summary above. total_tests would re-include skips.
                CASE
                    WHEN COALESCE(tr.passed_tests, 0)
                       + COALESCE(tr.failed_tests, 0)
                       + COALESCE(tr.broken_tests, 0) = 0 THEN 0.0
                    ELSE ROUND(
                        COALESCE(tr.passed_tests, 0) * 100.0
                        / NULLIF(
                            COALESCE(tr.passed_tests, 0) + COALESCE(tr.failed_tests, 0)
                              + COALESCE(tr.broken_tests, 0), 0
                          ), 1
                    )
                END                                          AS pass_rate
            FROM test_runs tr
            WHERE {run_label_match}
              AND tr.created_at >= :period_start
              AND NOT EXISTS (
                  SELECT 1 FROM test_cases tc2
                  WHERE tc2.test_run_id = tr.id
              )
              {run_fallback_project_filter}
              {release_filter}
            ORDER BY tr.created_at DESC
            LIMIT 15
            """,
            run_fallback_params,
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
        "suite_name": list(suite_name) if isinstance(suite_name, (list, tuple)) else suite_name,
        "summary": summary,
        "test_cases": [_row_dict(row) for row in cases_rows],
        "recent_runs": [_row_dict(row) for row in runs_rows],
        "period_days": days,
    }


#: How ``list_defects`` places a defect in a release (W06 DEF-05):
#: ``found_in`` -- its originating execution's primary release (VIZ-202);
#: ``affects`` -- the release gate's rule (asserted impact, else where found).
RELEASE_MATCHES = ("found_in", "affects")


def _release_list(release_id: ReleaseArg) -> list[str]:
    if release_id is None:
        return []
    if isinstance(release_id, (str, uuid.UUID)):
        return [str(release_id)]
    return [str(item) for item in release_id]


def _defect_scope_sql(
    params: dict,
    *,
    release_id: ReleaseArg,
    suite_name: SuiteArg,
    release_match: str = "found_in",
) -> tuple[str, bool]:
    """The release + suite WHERE fragment of a defects query, and whether it needs the execution joins.

    ``found_in`` keeps the VIZ-202 text exactly: release and suite place a
    defect through its originating execution (``tc`` / ``tr``), and
    ``tr.id IS NOT NULL`` keeps a defect with no execution out of a filtered
    list (without it the Unattributed bucket, ``tr.primary_release_id IS
    NULL``, would match every unlinked defect through the LEFT JOIN's NULLs).

    ``affects`` (W06 DEF-05) asks the release gate's own question instead
    (``release_defect_service.affects_release_sql``): asserted impact, else
    where it was found. It needs NO execution, so a hand-filed defect that
    affects the release is listed. The suite filter still places the defect
    through its execution in both modes.
    """
    if release_match not in RELEASE_MATCHES:
        raise ValueError("release_match must be found_in or affects")
    suite_part = _add_suite_param(params, suite_name)
    if release_match == "found_in":
        scope = " ".join(part for part in (_add_release_param(params, release_id), suite_part) if part)
        return (f"AND tr.id IS NOT NULL {scope}", True) if scope else ("", False)
    from app.services.release_defect_service import affects_release_sql

    affects = affects_release_sql(params, _release_list(release_id), alias="d")
    parts = [affects] if affects else []
    if suite_part:
        parts.append(f"AND tr.id IS NOT NULL {suite_part}")
    return " ".join(parts), bool(suite_part)


def _defect_status_sql(params: dict, resolution_status: str | None) -> str:
    """``AND`` the status filter: one stored word, or ``ACTIVE`` = every live status; ``""`` for none."""
    if not resolution_status:
        return ""
    wanted = resolution_status.strip().upper()
    if wanted == ACTIVE_QUERY_VALUE:
        # Constants of ``defect_status``, never request text: safe to inline.
        return "AND d.resolution_status IN (" + ", ".join(f"'{s}'" for s in ACTIVE_STATUSES) + ")"
    params["resolution_status"] = wanted
    return "AND d.resolution_status = :resolution_status"


#: The page's priority (P0-P3), derived once in SQL (``defect_status.priority_sql``).
_PRIORITY_SQL = priority_sql("d")
#: The assignee's name when that user may own work (the W05 release-owner rule:
#: active, not a service account, not synthetic); NULL otherwise.
_ASSIGNEE_NAME_SQL = (
    "CASE WHEN au.is_active IS NOT FALSE AND au.is_service_account IS NOT TRUE "
    "AND au.is_synthetic IS NOT TRUE THEN COALESCE(au.full_name, au.username) END"
)
#: Test attributes the project's ownership rules match on (the attrs of
#: ``ownership_resolver_service.resolve_cluster_ownership``). Popped from every
#: row before it is returned: they are not part of the wire contract.
_OWNER_HELPERS = ("own_test_case", "own_component", "own_package", "own_path", "own_label", "own_owner")
_OWNER_HELPER_SQL = (
    "tc.id AS own_test_case, tc.class_name AS own_component, tc.package_name AS own_package, "
    "tc.full_name AS own_path, COALESCE(tc.feature, tc.epic) AS own_label, tc.owner AS own_owner"
)
#: ``OwnershipResult.match_source`` -> the row's ``owner_source`` family.
_OWNER_SOURCE_FAMILY = {"component_owner_map": "component_map", "test_owner_label": "test_label"}


def _own_team(value: Any) -> str | None:
    """A team name that names a team: trimmed, not blank, not ``Unknown`` (what promotion stores for nobody)."""
    team = str(value or "").strip()
    if not team or team.lower() == "unknown":
        return None
    return team


async def _resolve_owner_teams(db: AsyncSession, items: list[dict]) -> None:
    """Give every row an ``owner_team`` and ``owner_source`` (W06 DEF-02), in place.

    The defect's own team wins (``defect``). A row without one gets the team
    ITS project's ownership rules resolve for its test case (``rule``,
    ``component_map``, ``test_label``: ``ownership_resolver_service`` decides),
    else both stay ``None``. At most two statements per page (the active rules
    of the page's projects, their component maps) and none when every row has
    its own team. A rule of project A is never applied to a row of project B.
    """
    needs: list[dict] = []
    for item in items:
        own = _own_team(item.get("owner_team"))
        item["owner_team"] = own
        item["owner_source"] = "defect" if own else None
        if own is None and item.get("own_test_case") is not None and item.get("project_id") is not None:
            needs.append(item)
    if needs:
        project_uuids = sorted({uuid.UUID(str(item["project_id"])) for item in needs}, key=str)
        rules = (
            await db.execute(
                select(ServiceOwnershipRule)
                .where(
                    ServiceOwnershipRule.project_id.in_(project_uuids),
                    ServiceOwnershipRule.is_active.is_(True),
                )
                .order_by(
                    ServiceOwnershipRule.project_id,
                    ServiceOwnershipRule.priority.desc(),
                    ServiceOwnershipRule.created_at,
                )
            )
        ).scalars().all()
        maps = (
            await db.execute(select(Project.id, Project.component_owner_map).where(Project.id.in_(project_uuids)))
        ).all()
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules_of: dict[str, list] = defaultdict(list)
        for rule in rules:
            rules_of[str(rule.project_id)].append(rule)
        map_of = {str(pid): cmap for pid, cmap in maps}
        for item in needs:
            pid = str(item["project_id"])
            attrs = {
                "suite_name": item.get("suite_name") or "",
                "component": item.get("own_component") or "",
                "package": item.get("own_package") or "",
                "path": item.get("own_path") or "",
                "label": item.get("own_label") or "",
                "owner": item.get("own_owner") or "",
            }
            result = resolve_test_ownership(rules_of.get(pid, []), attrs, map_of.get(pid))
            team = str(result.team_name or "").strip() or None
            if team and result.match_source:
                item["owner_team"] = team
                item["owner_source"] = _OWNER_SOURCE_FAMILY.get(result.match_source, "rule")
    for item in items:
        for key in _OWNER_HELPERS:
            item.pop(key, None)


async def list_defects(
    db: AsyncSession,
    project_id: str | None,
    resolution_status: str | None,
    page: int,
    size: int,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    release_id: ReleaseArg = None,
    suite_name: SuiteArg = None,
    *,
    release_match: str = "found_in",
    defect_id: str | None = None,
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
    # ``ACTIVE`` lists every live status (OPEN and IN_PROGRESS, W06 DEF-01).
    status_filter = _defect_status_sql(params, resolution_status)
    # VIZ-202 / W06 DEF-05: the release and suite fragments, and whether the
    # count must join the execution too.
    scope_filter, needs_execution = _defect_scope_sql(
        params, release_id=release_id, suite_name=suite_name, release_match=release_match,
    )
    # One defect (``defect_row``): the same row shape, by id.
    if defect_id is not None:
        try:
            params["defect_id"] = str(uuid.UUID(str(defect_id)))
        except (ValueError, TypeError):
            return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}
        scope_filter = f"{scope_filter} AND d.id = :defect_id"
    query = scoped_text(
        f"""
        SELECT
            d.id,
            d.project_id,
            -- The defect's own title, severity and component. Without them
            -- the Defects page titled a row with its test's name (or its
            -- category: "product bug") and derived a severity from the
            -- category, so the title and P0-P3 a person entered never showed
            -- (the UX redesign's browser E2E pass, 2026-10-08).
            d.title,
            d.severity,
            d.component,
            d.jira_ticket_id,
            d.jira_ticket_url,
            d.jira_status,
            d.external_status_at,
            d.external_status_conflict,
            d.failure_category,
            d.resolution_status,
            d.ai_confidence_score,
            d.created_at,
            d.resolved_at,
            tc.test_name,
            tc.suite_name,
            r.name AS release_name,
            {_PRIORITY_SQL} AS priority,
            d.owner_team,
            d.assignee_user_id,
            {_ASSIGNEE_NAME_SQL} AS assignee_name,
            d.approval_status,
            d.policy_evaluation -> 'jira_filing' ->> 'status' AS jira_filing_status,
            d.policy_evaluation -> 'jira_filing' ->> 'reason' AS jira_filing_reason,
            {_OWNER_HELPER_SQL}
        FROM defects d
        -- LEFT, not INNER: ``test_case_id`` is nullable by design. The intake
        -- path stores NULL when a failure signature matches no current test
        -- case (see _find_recent_test_case_id), and the FK is
        -- ``ondelete="SET NULL"`` so a retention purge orphans defects too.
        -- An inner join dropped those rows from ``items`` while the COUNT
        -- below — which joins nothing — still counted them, so the endpoint
        -- reported a total it refused to list (measured: items=[], total=5).
        LEFT JOIN test_cases tc ON tc.id = d.test_case_id
        LEFT JOIN test_runs tr ON tr.id = tc.test_run_id
        LEFT JOIN release_test_run_links rtrl ON rtrl.test_run_id = tr.id
        LEFT JOIN releases r ON r.id = rtrl.release_id
        -- W06 DEF-01: the assignee's name (LEFT: most defects have none).
        LEFT JOIN users au ON au.id = d.assignee_user_id
        WHERE 1=1
        {project_filter}
        {status_filter}
        {scope_filter}
        ORDER BY d.created_at DESC
        LIMIT :limit OFFSET :offset
        """,
        params,
    )

    rows = (await db.execute(query, params)).fetchall()
    # The count joins the execution only when a filter needs it -- and never
    # the release link table, which would multiply a run linked twice.
    count_joins = (
        "LEFT JOIN test_cases tc ON tc.id = d.test_case_id "
        "LEFT JOIN test_runs tr ON tr.id = tc.test_run_id"
    ) if needs_execution else ""
    count_params = {key: value for key, value in params.items() if key not in ("limit", "offset")}
    count_query = scoped_text(
        f"""
        SELECT COUNT(*) FROM defects d
        {count_joins}
        WHERE 1=1
        {project_filter}
        {status_filter}
        {scope_filter}
        """,
        count_params,
    )
    total = (await db.execute(count_query, count_params)).scalar() or 0
    items = [dict(row._mapping) for row in rows]
    # W06 DEF-02: the owner column (the defect's own team, else the project's
    # ownership rules for its test); the rule-matching helper columns are popped.
    await _resolve_owner_teams(db, items)
    return {"items": items, "total": total, "page": page, "size": size, "pages": -(-total // size)}


async def defect_row(db: AsyncSession, project_id: uuid.UUID | str, defect_id: uuid.UUID | str) -> dict | None:
    """Exactly one ``list_defects`` row (the ``GET /analytics/defects`` shape), or ``None``.

    Scoped to ``project_id`` (and to a live project, as every list read is):
    another project's defect is ``None``. The triage PATCH returns it so the
    page replaces its row with the server's.
    """
    listed = await list_defects(db, str(project_id), None, 1, 1, defect_id=str(defect_id))
    items = listed.get("items") or []
    return items[0] if items else None


# ── Defect trend (W06 DEF-04) ────────────────────────────────────────────────

#: Live = OPEN or IN_PROGRESS, exactly the release gate's set. Every other word
#: (RESOLVED, INVALID, the legacy CLOSED) is closed for the trend, so the last
#: point's backlog equals the ``open`` total. Constants, safe to inline.
_LIVE_SQL = "COALESCE(d.resolution_status, '') IN (" + ", ".join(f"'{s}'" for s in ACTIVE_STATUSES) + ")"
_EXECUTION_JOINS = (
    "LEFT JOIN test_cases tc ON tc.id = d.test_case_id "
    "LEFT JOIN test_runs tr ON tr.id = tc.test_run_id"
)
#: Rolling window of the per-point mean time to resolve, in points (days).
TREND_MTTR_POINTS = 7


def trend_window(days: int, today: date | None = None) -> tuple[date, date]:
    """``(d0, d1)``: the window's first and last UTC day, ``days`` days inclusive, ending today."""
    d1 = today or datetime.now(timezone.utc).date()
    return d1 - timedelta(days=days - 1), d1


def _ratio(total: float, count: int) -> float | None:
    return round(total / count, 2) if count else None


def build_trend_payload(rows: list[dict], totals: dict, window_days: int) -> dict:
    """The trend response from the two statements' rows (pure: no database).

    ``rows`` are the per-day rows in date order (``day``, ``opened``,
    ``closed``, ``open_backlog``, ``p0p1_open``, ``resolved_n``,
    ``resolve_days_sum``); ``totals`` the totals row (``open_total``,
    ``p0_open``, ``p1_open``, ``closed_without_time``). ``mttr_days`` is a
    mean over RESOLVED closes only and ``None`` -- never 0 -- when nothing was
    resolved (a point: in its trailing 7 days; the totals: in the window).
    """
    points: list[dict] = []
    resolved = [int(row.get("resolved_n") or 0) for row in rows]
    resolve_days = [float(row.get("resolve_days_sum") or 0) for row in rows]
    for i, row in enumerate(rows):
        lo = max(0, i - (TREND_MTTR_POINTS - 1))
        day = row["day"]
        points.append({
            "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
            "opened": int(row.get("opened") or 0),
            "closed": int(row.get("closed") or 0),
            "open_backlog": int(row.get("open_backlog") or 0),
            "p0p1_open": int(row.get("p0p1_open") or 0),
            "mttr_days": _ratio(sum(resolve_days[lo:i + 1]), sum(resolved[lo:i + 1])),
        })
    last = points[-min(7, window_days):] if points else []
    opened_last = sum(p["opened"] for p in last)
    closed_last = sum(p["closed"] for p in last)
    return {
        "points": points,
        "totals": {
            "open": int(totals.get("open_total") or 0),
            "p0_open": int(totals.get("p0_open") or 0),
            "p1_open": int(totals.get("p1_open") or 0),
            "mttr_days": _ratio(sum(resolve_days), sum(resolved)),
            "resolved_in_window": sum(resolved),
            "opened_in_window": sum(p["opened"] for p in points),
            "closed_in_window": sum(p["closed"] for p in points),
            "opened_last_7d": opened_last,
            "closed_last_7d": closed_last,
            "net_last_7d": opened_last - closed_last,
            "closed_without_time": int(totals.get("closed_without_time") or 0),
        },
        "window_days": window_days,
    }


async def defect_trend(
    db: AsyncSession,
    scope: AnalyticsScope,
    *,
    release_match: str = "found_in",
    today: date | None = None,
) -> dict:
    """Per UTC day of the window: opened, closed, the open backlog at the end of
    the day and its P0/P1 part, with a rolling MTTR; and totals over EVERY
    defect in scope (no row cap). Two statements, whatever the window.

    "Open at the end of day ``d``" = created on or before ``d`` and not closed on
    or before ``d``: the backlog open at the window's start plus the running
    opened - closed. A close is placed on ``GREATEST(resolved_at, created_at)``
    (a ``resolved_at`` before the creation clamps). A closed row WITHOUT a
    ``resolved_at`` (legacy INVALID closes, before ``apply_status``) cannot be
    placed: it is in no point and counted in ``closed_without_time``.
    """
    d0, d1 = trend_window(scope.window_days, today)
    params: dict = {"d0": d0, "d1": d1}
    project_filter = _tenant_filter(
        params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids, table_alias="d",
    )
    scope_filter, needs_execution = _defect_scope_sql(
        params, release_id=scope.release_arg, suite_name=scope.suite_arg, release_match=release_match,
    )
    joins = _EXECUTION_JOINS if needs_execution else ""
    points_sql = scoped_text(
        f"""
        WITH bounds AS (SELECT CAST(:d0 AS date) AS d0, CAST(:d1 AS date) AS d1),
        s AS (
            SELECT d.id,
                   CAST(d.created_at AT TIME ZONE 'UTC' AS date) AS created_day,
                   CASE WHEN NOT ({_LIVE_SQL})
                        THEN CAST(GREATEST(d.resolved_at, d.created_at) AT TIME ZONE 'UTC' AS date) END AS closed_day,
                   d.resolution_status,
                   EXTRACT(EPOCH FROM (GREATEST(d.resolved_at, d.created_at) - d.created_at)) / 86400.0 AS resolve_days,
                   ({_PRIORITY_SQL}) IN ('P0', 'P1') AS hi
            FROM defects d
            {joins}
            WHERE 1=1
            {project_filter}
            {scope_filter}
              AND NOT (NOT ({_LIVE_SQL}) AND d.resolved_at IS NULL)
        ),
        cal AS (
            SELECT CAST(g AS date) AS day
            FROM bounds, generate_series(bounds.d0, bounds.d1, interval '1 day') AS g
        ),
        opened AS (
            SELECT created_day AS day, COUNT(*) AS n, COUNT(*) FILTER (WHERE hi) AS n_hi
            FROM s, bounds WHERE created_day BETWEEN bounds.d0 AND bounds.d1 GROUP BY created_day
        ),
        closed AS (
            SELECT closed_day AS day, COUNT(*) AS n, COUNT(*) FILTER (WHERE hi) AS n_hi,
                   COUNT(*) FILTER (WHERE resolution_status = 'RESOLVED') AS resolved_n,
                   COALESCE(SUM(resolve_days) FILTER (WHERE resolution_status = 'RESOLVED'), 0) AS resolve_days_sum
            FROM s, bounds WHERE closed_day BETWEEN bounds.d0 AND bounds.d1 GROUP BY closed_day
        ),
        base AS (
            SELECT COUNT(*) FILTER (
                       WHERE created_day < bounds.d0 AND (closed_day IS NULL OR closed_day >= bounds.d0)) AS n,
                   COUNT(*) FILTER (
                       WHERE hi AND created_day < bounds.d0 AND (closed_day IS NULL OR closed_day >= bounds.d0)) AS n_hi
            FROM s, bounds
        )
        SELECT cal.day,
               COALESCE(o.n, 0) AS opened,
               COALESCE(c.n, 0) AS closed,
               base.n + SUM(COALESCE(o.n, 0) - COALESCE(c.n, 0)) OVER (ORDER BY cal.day) AS open_backlog,
               base.n_hi + SUM(COALESCE(o.n_hi, 0) - COALESCE(c.n_hi, 0)) OVER (ORDER BY cal.day) AS p0p1_open,
               COALESCE(c.resolved_n, 0) AS resolved_n,
               COALESCE(c.resolve_days_sum, 0) AS resolve_days_sum
        FROM cal CROSS JOIN base
        LEFT JOIN opened o ON o.day = cal.day
        LEFT JOIN closed c ON c.day = cal.day
        ORDER BY cal.day
        """,
        params,
    )
    rows = [dict(row._mapping) for row in (await db.execute(points_sql, params)).fetchall()]
    totals_params = {key: value for key, value in params.items() if key not in ("d0", "d1")}
    totals_sql = scoped_text(
        f"""
        SELECT COUNT(*) FILTER (WHERE {_LIVE_SQL}) AS open_total,
               COUNT(*) FILTER (WHERE {_LIVE_SQL} AND {_PRIORITY_SQL} = 'P0') AS p0_open,
               COUNT(*) FILTER (WHERE {_LIVE_SQL} AND {_PRIORITY_SQL} = 'P1') AS p1_open,
               COUNT(*) FILTER (WHERE NOT ({_LIVE_SQL}) AND d.resolved_at IS NULL) AS closed_without_time
        FROM defects d
        {joins}
        WHERE 1=1
        {project_filter}
        {scope_filter}
        """,
        totals_params,
    )
    totals = dict((await db.execute(totals_sql, totals_params)).one()._mapping)
    return build_trend_payload(rows, totals, scope.window_days)


# ── Escape rate (W06 DEF-03) ─────────────────────────────────────────────────

ESCAPE_UNATTRIBUTED_REASON = 'Escapes are counted against shipped releases; "Unattributed" has none.'


def escape_rate_reason(*, in_window: int, attributed: int, window_days: int) -> str | None:
    """Why the escape rate is not measured, or ``None`` when it is (``attributed`` at the floor)."""
    if attributed >= ESCAPE_RATE_MIN_SAMPLE:
        return None
    if in_window == 0:
        return f"No defects were reported in the last {window_days} days."
    return (
        f"{attributed} defect(s) reported in the last {window_days} days affect a release that has shipped; "
        f"{ESCAPE_RATE_MIN_SAMPLE} are needed."
    )


def build_escape_payload(
    *, in_window: int, attributed: int, escaped: int, window_days: int, outcomes: dict, reason: str | None = None,
) -> dict:
    """The escape-rate response (pure). ``rate`` is a percentage with one
    decimal when measured, ``None`` (never 0) below the floor."""
    measured = attributed >= ESCAPE_RATE_MIN_SAMPLE and reason is None
    return {
        "rate": round(escaped / attributed * 100, 1) if measured else None,
        "escaped": escaped,
        "attributed": attributed,
        "in_window": in_window,
        "measured": measured,
        "reason": None if measured else (
            reason or escape_rate_reason(in_window=in_window, attributed=attributed, window_days=window_days)
        ),
        "sample_floor": ESCAPE_RATE_MIN_SAMPLE,
        "window_days": window_days,
        "outcomes": {
            "incidents": int(outcomes.get("incidents") or 0),
            "rollbacks": int(outcomes.get("rollbacks") or 0),
            "releases": int(outcomes.get("releases") or 0),
        },
    }


async def defect_escape_rate(db: AsyncSession, scope: AnalyticsScope) -> dict:
    """The share of the window's defects filed AFTER a release they affect had shipped (owner decision D01).

    "Affects" is the release gate's rule (asserted impact, else where it was
    found); only releases of the defect's OWN project count, and only shipped
    ones (``released_at`` set). A defect affecting several shipped releases is
    counted once, and is an escape when it was filed after ANY of them shipped.
    The production outcomes marked on releases in the window (incidents,
    rollbacks) are a caption beside the rate. The suite filter does not apply.
    """
    from app.core.release_filter import is_unattributed
    from app.services.release_defect_service import asserted_impact_sql

    days = scope.window_days
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids, table_alias="d",
    )
    outcome_params: dict = {"period_start": params["period_start"]}
    outcome_filter = _tenant_filter(
        outcome_params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids, table_alias="o",
    )
    real_ids = [rid.lower() for rid in scope.release_ids if not is_unattributed(rid)]
    sentinel_only = bool(scope.release_ids) and not real_ids
    release_filter = ""
    outcome_release_filter = ""
    if real_ids:
        names = []
        for i, rid in enumerate(real_ids):
            params[f"escape_rid_{i}"] = rid
            outcome_params[f"escape_rid_{i}"] = rid
            names.append(f":escape_rid_{i}")
        in_list = "(" + ", ".join(names) + ")"
        release_filter = f"AND lower(CAST(r.id AS text)) IN {in_list}"
        outcome_release_filter = f"AND lower(CAST(o.release_id AS text)) IN {in_list}"
    elif sentinel_only:
        release_filter = "AND FALSE"
        outcome_release_filter = "AND FALSE"
    asserted = asserted_impact_sql("d")
    rate_sql = text(
        f"""
        WITH s AS (
            SELECT d.id, d.project_id, d.created_at, d.release_id,
                   CASE WHEN {asserted} THEN d.affects_releases END AS asserted
            FROM defects d
            WHERE d.created_at >= :period_start
            {project_filter}
        ),
        targets AS (
            SELECT s.id, s.project_id, s.created_at, lower(a.rid) AS rid
            FROM s CROSS JOIN LATERAL json_array_elements_text(COALESCE(s.asserted, CAST('[]' AS json))) AS a(rid)
            UNION ALL
            SELECT s.id, s.project_id, s.created_at, lower(CAST(s.release_id AS text)) AS rid
            FROM s WHERE s.asserted IS NULL AND s.release_id IS NOT NULL
        ),
        shipped AS (
            SELECT t.id, t.created_at, r.released_at
            FROM targets t
            JOIN releases r ON lower(CAST(r.id AS text)) = t.rid AND r.project_id = t.project_id
            WHERE r.released_at IS NOT NULL
            {release_filter}
        )
        SELECT (SELECT COUNT(*) FROM s) AS in_window,
               COUNT(DISTINCT shipped.id) AS attributed,
               COUNT(DISTINCT shipped.id) FILTER (WHERE shipped.created_at > shipped.released_at) AS escaped
        FROM shipped
        """
    )
    counts = dict((await db.execute(rate_sql, params)).one()._mapping)
    outcomes_sql = text(
        f"""
        SELECT COUNT(*) FILTER (WHERE o.outcome_kind = 'incident') AS incidents,
               COUNT(*) FILTER (WHERE o.outcome_kind = 'rollback') AS rollbacks,
               COUNT(DISTINCT o.release_id) AS releases
        FROM release_outcomes o
        WHERE o.marked_at >= :period_start
        {outcome_filter}
        {outcome_release_filter}
        """
    )
    outcomes = dict((await db.execute(outcomes_sql, outcome_params)).one()._mapping)
    return build_escape_payload(
        in_window=int(counts.get("in_window") or 0),
        attributed=int(counts.get("attributed") or 0),
        escaped=int(counts.get("escaped") or 0),
        window_days=days,
        outcomes=outcomes,
        reason=ESCAPE_UNATTRIBUTED_REASON if sentinel_only else None,
    )


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
    # Deduplicated and sorted by the service that owns the rule, so the stored
    # value does not churn on rewrite — a column that reorders itself makes
    # every audit diff look like a change.
    from app.services.release_defect_service import assert_affects

    affects = await _affects_releases_of_project(db, project_id, payload.get("affects_releases") or [])
    defect.affects_releases = assert_affects(defect, affects)
    db.add(defect)
    await db.flush()
    return defect


#: One refusal text whatever is wrong with the ids (malformed, unknown, another
#: project's): the answer must not tell a caller which release ids exist.
AFFECTS_RELEASES_NOT_IN_PROJECT = "One or more of these releases are not in this project."


async def _affects_releases_of_project(db: AsyncSession, project_id: uuid.UUID, values: list) -> list[str]:
    """``values`` as canonical lower-case release ids, each a release of ``project_id``; 422 otherwise (W06 DEF-03).

    The intake stored whatever ids the body named, so a defect could claim to
    affect another project's release (a body-id IDOR) and a typo was kept
    forever. ONE statement checks them all.
    """
    if not values:
        return []
    ids: set[uuid.UUID] = set()
    for value in values:
        try:
            ids.add(uuid.UUID(str(value).strip()))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=422, detail=AFFECTS_RELEASES_NOT_IN_PROJECT)
    from app.models.postgres import Release

    found = set(
        (
            await db.execute(select(Release.id).where(Release.project_id == project_id, Release.id.in_(ids)))
        ).scalars().all()
    )
    if found != ids:
        raise HTTPException(status_code=422, detail=AFFECTS_RELEASES_NOT_IN_PROJECT)
    return sorted(str(rid).lower() for rid in ids)


async def ai_analysis_summary(
    db: AsyncSession,
    project_id: str | None,
    days: int,
    allowed_project_ids: Optional[Iterable[uuid.UUID | str]] = None,
    release_id: ReleaseArg = None,
    suite_name: SuiteArg = None,
) -> dict:
    params: dict = {"period_start": _period_start(days)}
    project_filter = _tenant_filter(
        params, project_id=project_id, allowed_project_ids=allowed_project_ids,
    )
    # VIZ-202: the analysed execution's release and effective suite.
    # Conditional fragments: nothing is appended when nothing was asked.
    scope_filter = " ".join(
        part for part in (_add_release_param(params, release_id), _add_suite_param(params, suite_name))
        if part
    )
    query = scoped_text(
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
          {scope_filter}
        """,
        params,
    )
    result = await db.execute(query, params)
    return dict(result.one()._mapping)
