"""Project Summary Report — consolidated pass/fail/skip/flaky view.

The report has two aggregation modes (driven by the ``mode`` query param):

  * ``window`` (default): aggregate every TestRun in the selected window
    (24h / 7d / 30d). Headline percentages weight by test count, not by
    run, so a 1000-test run isn't outweighed by a 5-test smoke.
  * ``latest``: take only the most recent TestRun per suite. Best for a
    "current state" snapshot when the user doesn't care about volume.

Both modes return the same envelope so the frontend can swap without a
schema branch.

Per-suite breakdown comes from the ``test_cases`` table grouped by
``suite_name`` for the runs that survive the mode filter. Flaky count
reuses ``services/metrics_service._count_flaky_tests`` so the
methodology stays consistent with the dashboard headline.

All counts read aggregates from ``test_runs.*`` where possible (live-
stream runs persist aggregates first, per-test rows later — same
robustness logic as ``metrics_service._period_stats``). The per-suite
breakdown unavoidably touches ``test_cases``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import structlog
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, TestCase, TestRun, TestStatus
from app.services.metrics_service import _count_flaky_tests

logger = structlog.get_logger(__name__)


SummaryMode = Literal["window", "latest"]
ALLOWED_MODES: tuple[SummaryMode, ...] = ("window", "latest")


@dataclass(frozen=True)
class _Totals:
    total: int
    passed: int
    failed: int
    skipped: int
    broken: int

    @property
    def evaluated(self) -> int:
        # Skipped tests don't count toward pass rate — pass / (pass + fail + broken).
        return self.passed + self.failed + self.broken

    def pct(self, n: int) -> float:
        return round((n / self.total) * 100.0, 1) if self.total else 0.0


async def build_summary_report(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    days: int,
    mode: SummaryMode = "window",
) -> dict:
    """Compose the summary report for a project.

    Parameters
    ----------
    project_id:
        Project to scope to. ``None`` returns an empty envelope so
        callers that have no project resolved render the empty state
        instead of leaking cross-tenant aggregates.
    days:
        Window size in days (clamped 1..365 at the router). Used
        directly in ``window`` mode; in ``latest`` mode it still bounds
        which suites are considered "active".
    mode:
        ``"window"`` or ``"latest"`` — see module docstring.

    The envelope always carries the same top-level keys so the UI can
    render without branching on mode.
    """
    if project_id is None:
        return _empty_envelope(days=days, mode=mode)
    if mode not in ALLOWED_MODES:
        raise ValueError(f"invalid mode: {mode!r}")

    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=max(1, days))

    project_name = await _resolve_project_name(db, project_id)

    if mode == "window":
        totals, run_count, avg_duration_ms, latest_run_at = await _window_totals(
            db, project_id, period_start, now
        )
        suites = await _per_suite_breakdown_window(db, project_id, period_start, now)
    else:  # latest
        totals, run_count, avg_duration_ms, latest_run_at = await _latest_totals(
            db, project_id, period_start, now
        )
        suites = await _per_suite_breakdown_latest(db, project_id, period_start, now)

    flaky_count = await _count_flaky_tests(db, str(project_id), None)
    top_failing = await _top_failing_tests(db, project_id, period_start, now, limit=10)
    runs_per_day = round(run_count / max(1, days), 2) if mode == "window" else None

    return {
        "project_id": str(project_id),
        "project_name": project_name,
        "mode": mode,
        "window_days": days,
        "generated_at": now.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": now.isoformat(),
        "totals": {
            "total_test_cases": totals.total,
            "passed": totals.passed,
            "failed": totals.failed,
            "skipped": totals.skipped,
            "broken": totals.broken,
            "evaluated": totals.evaluated,
            "pass_rate_pct": totals.pct(totals.passed),
            "fail_rate_pct": totals.pct(totals.failed),
            "skip_rate_pct": totals.pct(totals.skipped),
            "broken_rate_pct": totals.pct(totals.broken),
            # Weighted pass rate that ignores skipped tests — matches the
            # /overview headline so two surfaces agree.
            "weighted_pass_rate_pct": (
                round(totals.passed / totals.evaluated * 100.0, 1)
                if totals.evaluated
                else 0.0
            ),
        },
        "run_count": run_count,
        "runs_per_day": runs_per_day,
        "avg_duration_ms": avg_duration_ms,
        "latest_run_at": latest_run_at.isoformat() if latest_run_at else None,
        "flaky_test_count": flaky_count,
        "flaky_rate_pct": (
            round(flaky_count / totals.total * 100.0, 1) if totals.total else 0.0
        ),
        "suites": suites,
        "top_failing_tests": top_failing,
    }


# ── helpers ────────────────────────────────────────────────────────────────


def _empty_envelope(*, days: int, mode: SummaryMode) -> dict:
    return {
        "project_id": None,
        "project_name": None,
        "mode": mode,
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_start": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
        "period_end": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "total_test_cases": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "broken": 0,
            "evaluated": 0,
            "pass_rate_pct": 0.0,
            "fail_rate_pct": 0.0,
            "skip_rate_pct": 0.0,
            "broken_rate_pct": 0.0,
            "weighted_pass_rate_pct": 0.0,
        },
        "run_count": 0,
        "runs_per_day": 0.0 if mode == "window" else None,
        "avg_duration_ms": 0,
        "latest_run_at": None,
        "flaky_test_count": 0,
        "flaky_rate_pct": 0.0,
        "suites": [],
        "top_failing_tests": [],
    }


async def _resolve_project_name(
    db: AsyncSession, project_id: uuid.UUID
) -> Optional[str]:
    row = (
        await db.execute(select(Project.name).where(Project.id == project_id))
    ).scalar_one_or_none()
    return row


async def _window_totals(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> tuple[_Totals, int, int, Optional[datetime]]:
    """Project-wide unique-test totals across the window.

    Counts are DISTINCT-fingerprint counts, not execution counts. A
    project that ran 4 tests 25 times reports total=4. Status buckets
    come from each fingerprint's most recent execution in the window.

    Falls back to the run-aggregate SUM only when no fingerprinted
    test_cases are present in the window (e.g., live-stream runs that
    never landed test_cases — their TestRun.total_tests still reflect
    HINCRBY counters and would otherwise be lost from the headline).
    ``run_count`` / ``avg_duration_ms`` / ``latest`` are always read
    from test_runs because they're inherently run-scoped, not
    test-scoped.
    """
    run_stmt = select(
        func.count(TestRun.id).label("runs"),
        func.coalesce(func.avg(TestRun.duration_ms), 0).label("avg_duration_ms"),
        func.max(TestRun.created_at).label("latest"),
        func.coalesce(func.sum(TestRun.total_tests), 0).label("agg_total"),
        func.coalesce(func.sum(TestRun.passed_tests), 0).label("agg_passed"),
        func.coalesce(func.sum(TestRun.failed_tests), 0).label("agg_failed"),
        func.coalesce(func.sum(TestRun.skipped_tests), 0).label("agg_skipped"),
        func.coalesce(func.sum(TestRun.broken_tests), 0).label("agg_broken"),
    ).where(
        TestRun.project_id == project_id,
        TestRun.created_at >= start,
        TestRun.created_at < end,
    )
    run_row = (await db.execute(run_stmt)).one()

    uniq_stmt = text(
        """
        WITH latest_per_fp AS (
            SELECT DISTINCT ON (tc.test_fingerprint)
                tc.test_fingerprint,
                tc.status
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              AND tc.test_fingerprint IS NOT NULL
            ORDER BY tc.test_fingerprint, tr.created_at DESC
        )
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'PASSED')  AS passed,
            COUNT(*) FILTER (WHERE status = 'FAILED')  AS failed,
            COUNT(*) FILTER (WHERE status = 'SKIPPED') AS skipped,
            COUNT(*) FILTER (WHERE status = 'BROKEN')  AS broken
        FROM latest_per_fp
        """
    )
    uniq_row = (
        await db.execute(
            uniq_stmt,
            {"project_id": str(project_id), "start": start, "end": end},
        )
    ).one()

    unique_total = int(uniq_row.total or 0)
    if unique_total > 0:
        totals = _Totals(
            total=unique_total,
            passed=int(uniq_row.passed or 0),
            failed=int(uniq_row.failed or 0),
            skipped=int(uniq_row.skipped or 0),
            broken=int(uniq_row.broken or 0),
        )
    else:
        # No fingerprinted test_cases — surface the run-aggregate sums
        # so live-stream-only projects don't see a hollow zero.
        totals = _Totals(
            total=int(run_row.agg_total or 0),
            passed=int(run_row.agg_passed or 0),
            failed=int(run_row.agg_failed or 0),
            skipped=int(run_row.agg_skipped or 0),
            broken=int(run_row.agg_broken or 0),
        )
    return (
        totals,
        int(run_row.runs or 0),
        int(run_row.avg_duration_ms or 0),
        run_row.latest,
    )


async def _latest_totals(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> tuple[_Totals, int, int, Optional[datetime]]:
    """Sum aggregates across the latest TestRun per primary_suite_name.

    A run with no ``primary_suite_name`` (legacy or imperfect ingest)
    still counts so the user sees their data — falls back to grouping
    by run id itself.
    """
    query = text(
        """
        WITH latest_per_suite AS (
            SELECT DISTINCT ON (COALESCE(primary_suite_name, id::text))
                id, total_tests, passed_tests, failed_tests, skipped_tests,
                broken_tests, duration_ms, created_at
            FROM test_runs
            WHERE project_id = :project_id
              AND created_at >= :start
              AND created_at < :end
            ORDER BY COALESCE(primary_suite_name, id::text), created_at DESC
        )
        SELECT
            COUNT(*) AS runs,
            COALESCE(SUM(total_tests), 0) AS total,
            COALESCE(SUM(passed_tests), 0) AS passed,
            COALESCE(SUM(failed_tests), 0) AS failed,
            COALESCE(SUM(skipped_tests), 0) AS skipped,
            COALESCE(SUM(broken_tests), 0) AS broken,
            COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
            MAX(created_at) AS latest
        FROM latest_per_suite
        """
    )
    row = (
        await db.execute(
            query,
            {"project_id": str(project_id), "start": start, "end": end},
        )
    ).one()
    return (
        _Totals(
            total=int(row.total or 0),
            passed=int(row.passed or 0),
            failed=int(row.failed or 0),
            skipped=int(row.skipped or 0),
            broken=int(row.broken or 0),
        ),
        int(row.runs or 0),
        int(row.avg_duration_ms or 0),
        row.latest,
    )


async def _per_suite_breakdown_window(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Per-suite stats across every test_case row in the window.

    Counts are unique-test counts (DISTINCT test_fingerprint), not
    execution counts. A suite of 4 unique tests run 25 times in the
    window reports ``total=4``, not ``total=100`` — the page label
    "Total tests" implies the test catalog, not per-execution volume.
    The most recent execution per fingerprint determines its
    passed/failed/skipped/broken status for the window.

    Two data sources are merged so suites visible on /runs and /live but
    missing their per-test rows still show up (the live-stream Redis
    buffer eviction issue means a run's ``test_runs`` aggregates can
    land before its ``test_cases`` rows do):

      1. ``test_cases`` grouped by ``suite_name`` — the per-test ground
         truth when it exists. Drops rows with NULL/empty suite_name so
         the table doesn't show a meaningless "—" bucket.
      2. ``test_runs.primary_suite_name`` aggregates for runs that
         currently have zero ``test_cases`` rows. Run-level totals are
         attributed wholesale to the dominant suite — this overcounts
         for the rare multi-suite-per-run case but matches how /runs and
         /live label those runs.

    The two sources are unioned and re-aggregated so a suite that
    appears in both is summed correctly (rather than appearing twice).
    """
    query = text(
        """
        WITH latest_per_fp AS (
            -- One row per (suite, fingerprint): the most recent
            -- execution in the window. Pass/fail buckets come from
            -- this row so the snapshot reads as "of the N unique
            -- tests in this suite, how many last ran green/red".
            --
            -- Suite attribution rule: for live_stream runs we trust
            -- ``tr.primary_suite_name`` — that's the authoritative
            -- session-supplied label the SDK stamped at create_session
            -- time (from ``testlookup.suite`` / ``testlookup.launch``
            -- / the testng.xml ``<suite name="…">``). Per-event
            -- ``tc.suite_name`` for live_stream runs can be the test
            -- class name when the SDK didn't resolve a session suite,
            -- which would split one logical run into N per-class
            -- buckets here. File uploads keep their per-event
            -- grouping — multi-``<testsuite>`` XML inputs need the
            -- breakout, and the parser sets tc.suite_name from each
            -- <testsuite name="…"> directly. Coalesce to tc.suite_name
            -- so legacy / cross-source rows still bucket correctly.
            SELECT DISTINCT ON (effective_suite, tc.test_fingerprint)
                effective_suite AS suite_name,
                tc.test_fingerprint,
                tc.status,
                tr.created_at AS run_created_at
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            CROSS JOIN LATERAL (
                SELECT COALESCE(
                    CASE
                        WHEN tr.trigger_source = 'live_stream'
                            THEN NULLIF(TRIM(tr.primary_suite_name), '')
                        ELSE NULL
                    END,
                    NULLIF(TRIM(tc.suite_name), '')
                ) AS effective_suite
            ) eff
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              AND effective_suite IS NOT NULL
              AND tc.test_fingerprint IS NOT NULL
            ORDER BY effective_suite, tc.test_fingerprint, tr.created_at DESC
        ),
        cases_agg AS (
            SELECT
                suite_name,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'PASSED')  AS passed,
                COUNT(*) FILTER (WHERE status = 'FAILED')  AS failed,
                COUNT(*) FILTER (WHERE status = 'SKIPPED') AS skipped,
                COUNT(*) FILTER (WHERE status = 'BROKEN')  AS broken,
                MAX(run_created_at) AS last_run_at
            FROM latest_per_fp
            GROUP BY suite_name
        ),
        runs_missing_cases AS (
            -- Runs in the window whose per-test rows didn't land. The
            -- correlated ``NOT EXISTS`` is cheap because test_cases has
            -- ix_test_cases_run_status — index-only probe.
            SELECT
                tr.id,
                NULLIF(TRIM(tr.primary_suite_name), '') AS suite_name,
                tr.total_tests, tr.passed_tests, tr.failed_tests,
                tr.skipped_tests, tr.broken_tests, tr.created_at
            FROM test_runs tr
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              AND tr.primary_suite_name IS NOT NULL
              AND TRIM(tr.primary_suite_name) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM test_cases tc2
                  WHERE tc2.test_run_id = tr.id
              )
        ),
        runs_agg AS (
            SELECT
                suite_name,
                COALESCE(SUM(total_tests),   0) AS total,
                COALESCE(SUM(passed_tests),  0) AS passed,
                COALESCE(SUM(failed_tests),  0) AS failed,
                COALESCE(SUM(skipped_tests), 0) AS skipped,
                COALESCE(SUM(broken_tests),  0) AS broken,
                MAX(created_at) AS last_run_at
            FROM runs_missing_cases
            WHERE suite_name IS NOT NULL
            GROUP BY suite_name
        ),
        merged AS (
            -- ``cases_agg`` is the DISTINCT-fingerprint (unique-test) count
            -- per suite — the SAME definition /coverage and /coverage/suite
            -- use, so the two pages agree. ``runs_agg`` (run-level execution
            -- aggregates for live-stream runs whose per-test rows never
            -- landed) is a FALLBACK only for suites that have no test_cases
            -- at all in the window. Previously it was UNION-ed unconditionally
            -- and SUM-ed on top of cases_agg, which double-counted suites that
            -- had both: e.g. RealisticTestNGSuite read 7570 (2423 unique +
            -- ~5147 missing-run executions) instead of the 2423 unique tests
            -- /coverage reports. The NOT IN guard makes a suite counted by
            -- EITHER distinct fingerprints OR run aggregates, never both.
            -- (Bug 2026-05-20.)
            SELECT * FROM cases_agg
            UNION ALL
            SELECT * FROM runs_agg
            WHERE suite_name NOT IN (SELECT suite_name FROM cases_agg)
        )
        SELECT
            suite_name,
            SUM(total)   AS total,
            SUM(passed)  AS passed,
            SUM(failed)  AS failed,
            SUM(skipped) AS skipped,
            SUM(broken)  AS broken,
            MAX(last_run_at) AS last_run_at
        FROM merged
        GROUP BY suite_name
        ORDER BY total DESC
        """
    )
    rows = (
        await db.execute(
            query,
            {"project_id": str(project_id), "start": start, "end": end},
        )
    ).all()
    return [_suite_row_to_dict(r) for r in rows]


async def _per_suite_breakdown_latest(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Per-suite stats from each suite's most recent run only.

    Two source paths, picked per-suite:

      * Suites whose latest run has ``test_cases`` rows → count from
        ``test_cases`` (per-test ground truth).
      * Suites whose latest run only has ``test_runs.primary_suite_name``
        (live-stream Redis gap or pre-finalize state) → use the
        run-level aggregate columns. Wholesale-attributes run-level
        totals to the dominant suite, mirroring how /runs labels those
        runs.

    Without the second path, suites with active runs that haven't
    persisted per-test rows yet are silently invisible on the report.
    """
    query = text(
        """
        WITH all_candidates AS (
            -- Test-cases-driven candidates: every (suite, run) pair where
            -- per-test rows have landed for the run.
            --
            -- Same effective-suite rule as ``_per_suite_breakdown_window``:
            -- for live_stream runs prefer ``tr.primary_suite_name`` over
            -- ``tc.suite_name`` so a session label like "API Regression
            -- Multi-Class" doesn't get split into per-class buckets when
            -- the SDK stamped the test class name on each event. File
            -- uploads still bucket by per-event suite (parsed from
            -- ``<testsuite name="…">``) so multi-suite XML inputs keep
            -- their breakdown.
            SELECT
                COALESCE(
                    CASE
                        WHEN tr.trigger_source = 'live_stream'
                            THEN NULLIF(TRIM(tr.primary_suite_name), '')
                        ELSE NULL
                    END,
                    NULLIF(TRIM(tc.suite_name), '')
                ) AS suite_name,
                tc.test_run_id,
                tr.created_at AS run_created_at,
                FALSE AS uses_run_aggregate
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              AND COALESCE(
                    CASE
                        WHEN tr.trigger_source = 'live_stream'
                            THEN NULLIF(TRIM(tr.primary_suite_name), '')
                        ELSE NULL
                    END,
                    NULLIF(TRIM(tc.suite_name), '')
                  ) IS NOT NULL
            UNION ALL
            -- Run-level fallback: runs whose per-test rows didn't land.
            SELECT
                NULLIF(TRIM(tr.primary_suite_name), '') AS suite_name,
                tr.id AS test_run_id,
                tr.created_at AS run_created_at,
                TRUE AS uses_run_aggregate
            FROM test_runs tr
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              AND tr.primary_suite_name IS NOT NULL
              AND TRIM(tr.primary_suite_name) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM test_cases tc2
                  WHERE tc2.test_run_id = tr.id
              )
        ),
        latest_run_per_suite AS (
            SELECT DISTINCT ON (suite_name)
                suite_name,
                test_run_id,
                run_created_at,
                uses_run_aggregate
            FROM all_candidates
            WHERE suite_name IS NOT NULL
            ORDER BY suite_name, run_created_at DESC
        ),
        cases_path AS (
            -- Match test_cases on the SAME effective-suite expression
            -- ``all_candidates`` uses. Without re-deriving here, a
            -- live_stream run whose ``tc.suite_name`` is a per-class
            -- value but whose ``tr.primary_suite_name`` is the session
            -- label would join no rows (l.suite_name = session label,
            -- tc.suite_name = class name → mismatch) and the suite
            -- would silently report zero tests. Re-derive via the
            -- same CASE so live_stream rows match by run-level label
            -- and file-upload rows continue to match by per-event
            -- ``tc.suite_name``.
            SELECT
                l.suite_name,
                COUNT(tc.id) AS total,
                COUNT(*) FILTER (WHERE tc.status = 'PASSED')  AS passed,
                COUNT(*) FILTER (WHERE tc.status = 'FAILED')  AS failed,
                COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped,
                COUNT(*) FILTER (WHERE tc.status = 'BROKEN')  AS broken,
                MAX(l.run_created_at) AS last_run_at
            FROM latest_run_per_suite l
            JOIN test_runs tr2 ON tr2.id = l.test_run_id
            JOIN test_cases tc
              ON tc.test_run_id = l.test_run_id
             AND COALESCE(
                    CASE
                        WHEN tr2.trigger_source = 'live_stream'
                            THEN NULLIF(TRIM(tr2.primary_suite_name), '')
                        ELSE NULL
                    END,
                    NULLIF(TRIM(tc.suite_name), '')
                 ) = l.suite_name
            WHERE l.uses_run_aggregate = FALSE
            GROUP BY l.suite_name
        ),
        runs_path AS (
            SELECT
                l.suite_name,
                COALESCE(tr.total_tests,   0) AS total,
                COALESCE(tr.passed_tests,  0) AS passed,
                COALESCE(tr.failed_tests,  0) AS failed,
                COALESCE(tr.skipped_tests, 0) AS skipped,
                COALESCE(tr.broken_tests,  0) AS broken,
                tr.created_at AS last_run_at
            FROM latest_run_per_suite l
            JOIN test_runs tr ON tr.id = l.test_run_id
            WHERE l.uses_run_aggregate = TRUE
        )
        SELECT * FROM cases_path
        UNION ALL
        SELECT * FROM runs_path
        ORDER BY total DESC
        """
    )
    rows = (
        await db.execute(
            query,
            {"project_id": str(project_id), "start": start, "end": end},
        )
    ).all()
    return [_suite_row_to_dict(r) for r in rows]


def _suite_row_to_dict(r) -> dict:
    total = int(r.total or 0)
    passed = int(r.passed or 0)
    failed = int(r.failed or 0)
    skipped = int(r.skipped or 0)
    broken = int(r.broken or 0)
    evaluated = passed + failed + broken
    return {
        "suite_name": r.suite_name,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "broken": broken,
        "pass_rate_pct": (
            round(passed / total * 100.0, 1) if total else 0.0
        ),
        "weighted_pass_rate_pct": (
            round(passed / evaluated * 100.0, 1) if evaluated else 0.0
        ),
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
    }


async def _top_failing_tests(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[dict]:
    """Top tests by failure count in the window. Grouped by (suite, class, name)."""
    stmt = (
        select(
            TestCase.suite_name.label("suite_name"),
            TestCase.class_name.label("class_name"),
            TestCase.test_name.label("test_name"),
            func.count(TestCase.id).label("failures"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestRun.created_at >= start,
            TestRun.created_at < end,
            TestCase.status.in_(
                [TestStatus.FAILED.value, TestStatus.BROKEN.value]
            ),
        )
        .group_by(
            TestCase.suite_name, TestCase.class_name, TestCase.test_name
        )
        .order_by(desc("failures"))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "suite_name": r.suite_name,
            "class_name": r.class_name,
            "test_name": r.test_name,
            "failures": int(r.failures or 0),
        }
        for r in rows
    ]
