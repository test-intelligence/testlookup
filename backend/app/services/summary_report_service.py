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

from sqlalchemy import case, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    Project,
    TestCase,
    TestRun,
    TestStatus,
    TestStep,
)
from app.services.analytics_service import _effective_suite_sql
from app.services.metrics_service import (  # F-067: one vocabulary, two surfaces
    PASS_RATE_BASIS_LABELS,
    PASS_RATE_BASIS_UNIQUE_TESTS,
    _count_flaky_tests,
)


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


def _release_predicate(release_id) -> list:
    """The one Core predicate, imported rather than restated.

    It lived here for exactly one commit before `my_failures` needed the same
    rule — which is the moment a local copy becomes two surfaces that can
    disagree about what a release contains.
    """
    from app.core.release_filter import release_predicate

    return release_predicate(release_id)


def _release_filter(params: dict, release_id, alias: str = "tr") -> str:
    """Release scoping for this module's raw-SQL queries.

    Imported from ``analytics_service`` rather than restated. The rule has two
    halves — a conditional fragment (never ``(:p IS NULL OR col = :p)``, which
    loses ``ix_test_runs_project_release_created`` for every caller who passes
    no release) and the Unattributed sentinel, which arrives as a literal
    string rather than a UUID. A second copy would drift, and this module and
    the analytics pages would then disagree about what a release contains.
    """
    from app.services.analytics_service import _add_release_param

    return _add_release_param(params, release_id, table_alias=alias)


async def build_summary_report(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    days: int,
    mode: SummaryMode = "window",
    release_id: Optional[str] = None,
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
    top_failing = await _top_failing_tests(
        db, project_id, period_start, now, limit=10, release_id=release_id
    )
    # Phase 5 enrichment (additive, no migration): attach the LATEST-RUN-ONLY
    # granular step snapshot to each top-failing test so reports/PDF can show
    # where the test failed. Batched — one canonical lookup + one steps fetch.
    await _enrich_failure_steps(db, project_id, top_failing)

    # Phase 5 enrichment (additive): per-suite granular STEP success-rate over
    # the LATEST-RUN-ONLY snapshot. Each suite dict gets OPTIONAL FLAT keys
    # (``step_success_rate`` / ``passed_steps`` / ``total_steps``) — populated
    # only where the suite has captured step data, ``None`` otherwise — so
    # existing consumers are unaffected. The flat shape matches the response
    # model (``SummarySuiteRow``) and the frontend contract; same
    # ``_effective_suite_sql()`` grouping as the breakdown so labels line up.
    step_success_by_suite = await _per_suite_step_success(
        db, project_id, period_start, now
    )
    for s in suites:
        m = step_success_by_suite.get(s.get("suite_name"))
        if m:
            s["step_success_rate"] = m["step_pass_rate_pct"]
            s["passed_steps"] = m["passed_steps"]
            s["total_steps"] = m["total_steps"]
        else:
            s["step_success_rate"] = None
            s["passed_steps"] = None
            s["total_steps"] = None

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
            # F-067: declare the population. This report counts each distinct
            # test once (which is what makes its counts agree with Coverage),
            # while the dashboard counts every execution — 83.3% here vs 81.0%
            # there on the same window. Both are correct; publishing the basis
            # is what stops them reading as a contradiction.
            "pass_rate_basis": PASS_RATE_BASIS_UNIQUE_TESTS,
            "pass_rate_basis_label": PASS_RATE_BASIS_LABELS[PASS_RATE_BASIS_UNIQUE_TESTS],
            "fail_rate_pct": totals.pct(totals.failed),
            "skip_rate_pct": totals.pct(totals.skipped),
            "broken_rate_pct": totals.pct(totals.broken),
            # Pass rate over EVALUATED tests (skipped excluded).
            #
            # This comment used to claim it "matches the /overview headline so
            # two surfaces agree". **It does not, and cannot.** The two numbers
            # are computed over different populations:
            #
            #   /overview (metrics/summary.avg_pass_rate_7d) -> EXECUTIONS
            #   this report                                  -> UNIQUE TESTS
            #
            # Measured live on Checkout Service (30d):
            #
            #   executions   47 passed / 9 failed / 2 broken  -> 47/58 = 81.0%
            #   unique tests 10 passed / 2 failed / 0 broken  -> 10/12 = 83.3%
            #
            # Excluding skips is the only thing this shares with the headline.
            # The broken executions vanish here because a unique test carries
            # one status, so its BROKEN runs are not separately counted — which
            # is exactly why the rates diverge.
            #
            # Both figures are internally correct; which one a user should see
            # for "the" pass rate is a product decision, recorded in the
            # exploratory ledger rather than silently resolved here. Do not
            # "fix" this by switching the basis without deciding that first —
            # the unique-test basis is what makes this report's counts match
            # Coverage's unique_tests.
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
        # ``flaky_count`` is an all-time, project-wide count
        # (metrics_service._count_flaky_tests has no window bound) while
        # ``totals.total`` is windowed, so for a short window the ratio can
        # exceed 100%. Clamp so the report never shows a nonsensical rate.
        "flaky_rate_pct": (
            min(100.0, round(flaky_count / totals.total * 100.0, 1))
            if totals.total
            else 0.0
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
            "pass_rate_basis": PASS_RATE_BASIS_UNIQUE_TESTS,
            "pass_rate_basis_label": PASS_RATE_BASIS_LABELS[PASS_RATE_BASIS_UNIQUE_TESTS],
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
    release_id: Optional[str] = None,
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
    # The same fragment for every statement in this helper: a report whose
    # headline was release-scoped and whose per-suite rows were not would
    # answer two different questions under one heading.
    release_filter = _release_filter({}, release_id)
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
        # Core select, so the predicate is expressed directly rather than
        # through the SQL fragment. `run_count`, `avg_duration_ms` and `latest`
        # are run-scoped by nature, so a release-scoped report must narrow them
        # too — otherwise the headline counts runs from every release while the
        # test totals beside it count one.
        *_release_predicate(release_id),
        TestRun.created_at >= start,
        TestRun.created_at < end,
    )
    run_row = (await db.execute(run_stmt)).one()

    uniq_stmt = text(
        f"""
        WITH latest_per_fp AS (
            SELECT DISTINCT ON (tc.test_fingerprint)
                tc.test_fingerprint,
                tc.status
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              {release_filter}
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
            _p(project_id, start, end, release_id),
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
    release_id: Optional[str] = None,
) -> tuple[_Totals, int, int, Optional[datetime]]:
    """Sum aggregates across the latest TestRun per primary_suite_name.

    A run with no ``primary_suite_name`` (legacy or imperfect ingest)
    still counts so the user sees their data — falls back to grouping
    by run id itself.
    """
    # This query reads test_runs unaliased, so the predicate names the
    # column directly rather than through "tr.".
    release_filter_bare = _release_filter({}, release_id, alias="test_runs")
    release_filter_bare = release_filter_bare.replace("test_runs.", "")
    query = text(
        f"""
        WITH latest_per_suite AS (
            SELECT DISTINCT ON (COALESCE(primary_suite_name, id::text))
                id, total_tests, passed_tests, failed_tests, skipped_tests,
                broken_tests, duration_ms, created_at
            FROM test_runs
            WHERE project_id = :project_id
              AND created_at >= :start
              AND created_at < :end
              {release_filter_bare}
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
            _p(project_id, start, end, release_id),
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
    release_id: Optional[str] = None,
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
    # One fragment per statement in this helper, so a release-scoped
    # headline cannot sit above per-suite rows that ignored it.
    release_filter = _release_filter({}, release_id)
    query = text(
        f"""
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
              {release_filter}
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
              {release_filter}
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
            _p(project_id, start, end, release_id),
        )
    ).all()
    return [_suite_row_to_dict(r) for r in rows]


async def _per_suite_breakdown_latest(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    release_id: Optional[str] = None,
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
    # One fragment per statement in this helper, so a release-scoped
    # headline cannot sit above per-suite rows that ignored it.
    release_filter = _release_filter({}, release_id)
    query = text(
        f"""
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
              {release_filter}
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
              {release_filter}
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
            _p(project_id, start, end, release_id),
        )
    ).all()
    return [_suite_row_to_dict(r) for r in rows]


def _p(project_id, start, end, release_id) -> dict:
    """Params for this module's queries, WITH the release bind.

    One builder, because the defect this fixes elsewhere in the codebase was a
    fresh params dict that did not carry a bind the interpolated SQL referenced
    — a StatementError, surfacing as a 500.
    """
    params: dict = {"project_id": str(project_id), "start": start, "end": end}
    _release_filter(params, release_id)
    return params


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


async def _per_suite_step_success(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    release_id: Optional[str] = None,
) -> dict[str, dict]:
    """Per-suite granular STEP success-rate (Phase 5 enrichment).

    Returns ``{suite_name -> {"passed_steps", "total_steps",
    "step_pass_rate_pct", "tests_with_steps"}}`` over the LATEST-RUN-ONLY step
    snapshot, considering ONLY canonical tests that actually have captured
    steps. Suites without any step data are absent from the map → the caller
    leaves the suite's ``step_success`` field ``None`` (additive/optional).

    Suite grouping uses the SAME ``_effective_suite_sql()`` expression as the
    breakdowns so the labels line up: each canonical test is attributed to its
    effective suite from its window test_cases rows, then its snapshot steps are
    summed. One batched query — no N+1 over tests.
    """
    effective_suite = _effective_suite_sql()
    # One fragment per statement in this helper, so a release-scoped
    # headline cannot sit above per-suite rows that ignored it.
    release_filter = _release_filter({}, release_id)
    query = text(
        f"""
        WITH tests_in_suite AS (
            -- One (suite, canonical) pair per logical test in the window.
            -- DISTINCT collapses the multiple test_cases executions of the
            -- same canonical down to a single attribution; the snapshot is
            -- latest-run-only anchored on the canonical, so summing its steps
            -- once per canonical is correct.
            SELECT DISTINCT
                {effective_suite} AS suite_name,
                tc.canonical_test_case_id AS canonical_id
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.project_id = :project_id
              AND tr.created_at >= :start
              AND tr.created_at < :end
              {release_filter}
              AND tc.canonical_test_case_id IS NOT NULL
              AND {effective_suite} IS NOT NULL
        ),
        step_counts AS (
            -- Per-canonical step tallies, only for tests that HAVE steps.
            SELECT
                ts.canonical_test_case_id AS canonical_id,
                COUNT(*) FILTER (WHERE ts.status = 'PASSED') AS passed_steps,
                COUNT(*) AS total_steps
            FROM test_steps ts
            JOIN tests_in_suite tis
                ON tis.canonical_id = ts.canonical_test_case_id
            GROUP BY ts.canonical_test_case_id
        )
        SELECT
            tis.suite_name,
            SUM(sc.passed_steps) AS passed_steps,
            SUM(sc.total_steps)  AS total_steps,
            COUNT(*)             AS tests_with_steps
        FROM tests_in_suite tis
        JOIN step_counts sc ON sc.canonical_id = tis.canonical_id
        GROUP BY tis.suite_name
        """
    )
    rows = (
        await db.execute(
            query,
            _p(project_id, start, end, release_id),
        )
    ).all()
    out: dict[str, dict] = {}
    for r in rows:
        total_steps = int(r.total_steps or 0)
        if total_steps <= 0:
            continue
        passed_steps = int(r.passed_steps or 0)
        out[r.suite_name] = {
            "passed_steps": passed_steps,
            "total_steps": total_steps,
            "step_pass_rate_pct": round(passed_steps / total_steps * 100.0, 1),
            "tests_with_steps": int(r.tests_with_steps or 0),
        }
    return out


async def _top_failing_tests(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
    release_id: Optional[str] = None,
) -> list[dict]:
    """Top tests by failure count in the window. Grouped by (suite, class, name).

    Suite uses the same effective-suite expression as the per-suite
    breakdowns (``feedback_effective_suite_query_pattern``): for live_stream
    runs prefer ``tr.primary_suite_name`` over the per-event ``tc.suite_name``
    (which can be the test class name), so a failing test's count isn't split
    across divergent suite labels and the label matches the suites table.
    """
    effective_suite = func.coalesce(
        case(
            (
                TestRun.trigger_source == "live_stream",
                func.nullif(func.trim(TestRun.primary_suite_name), ""),
            ),
            else_=None,
        ),
        func.nullif(func.trim(TestCase.suite_name), ""),
    )
    stmt = (
        select(
            effective_suite.label("suite_name"),
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
            # A Core select, so the predicate is built here rather than through
            # the SQL fragment — but it is the same rule, and it has to be
            # applied: a "top failing tests" list that ignored the release
            # would name failures from other releases beside a headline scoped
            # to this one.
            *_release_predicate(release_id),
        )
        .group_by(
            effective_suite, TestCase.class_name, TestCase.test_name
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


# Step statuses that mean "this is where the test broke" — same vocab as
# ``TestStatus`` (ingestion writes step.status from the common step dict).
_FAILED_STEP_STATUSES = (TestStatus.FAILED.value, TestStatus.BROKEN.value)


async def _enrich_failure_steps(
    db: AsyncSession,
    project_id: uuid.UUID,
    top_failing: list[dict],
) -> None:
    """Attach the granular step snapshot to each top-failing test (in place).

    Phase 5 enrichment. The LATEST-RUN-ONLY snapshot (migration 0093) anchors
    to the project-scoped ``CanonicalTestCase``; we resolve each top-failing
    test (keyed by ``test_name`` + ``class_name``) to its canonical anchor and
    read its steps via that anchor — so this stays project-scoped (no
    cross-tenant leak) and reuses the same write-side anchor the step-read
    helper (``runs_service.get_test_steps_tree``) reads.

    Adds two OPTIONAL keys to each entry (existing consumers unaffected):
      * ``failure_step``: name of the FIRST failed/broken step (failure
        location), or ``None`` when no step data / no failed step.
      * ``step_breakdown``: ordered ``[{name, status, assertion_message}]`` for
        the failing test, or ``None`` when no snapshot exists.

    Batched: ONE canonical lookup for all entries, then ONE steps fetch for the
    matched canonical ids — no N+1.
    """
    if not top_failing:
        return

    # Initialise the optional fields up front so every entry carries them even
    # when no snapshot resolves (stable contract for the PDF/UI consumers).
    for t in top_failing:
        t.setdefault("failure_step", None)
        t.setdefault("step_breakdown", None)

    # Build the set of (test_name, class_name) keys we need to resolve. The
    # snapshot is one-per-logical-test, so matching by name+class is exact for
    # the project; if two suites share a (name, class) the first canonical wins
    # (acceptable — the snapshot itself is per-fingerprint, not per-suite).
    wanted = {(t["test_name"], t.get("class_name")) for t in top_failing}

    names = {n for (n, _c) in wanted}
    canon_rows = (
        await db.execute(
            select(
                CanonicalTestCase.id,
                CanonicalTestCase.test_name,
                CanonicalTestCase.class_name,
            ).where(
                CanonicalTestCase.project_id == project_id,
                CanonicalTestCase.test_name.in_(names),
            )
        )
    ).all()

    # Map (name, class) → canonical id. Prefer an exact class match; fall back
    # to a name-only match when the top-failing row has no class.
    canon_by_key: dict[tuple, uuid.UUID] = {}
    for cr in canon_rows:
        canon_by_key.setdefault((cr.test_name, cr.class_name), cr.id)
    canon_ids = [
        canon_by_key.get(key) or canon_by_key.get((key[0], None))
        for key in wanted
    ]
    canon_ids = [cid for cid in {c for c in canon_ids} if cid is not None]
    if not canon_ids:
        return

    step_rows = (
        await db.execute(
            select(
                TestStep.canonical_test_case_id,
                TestStep.ordinal,
                TestStep.name,
                TestStep.status,
                TestStep.assertion_message,
            )
            .where(TestStep.canonical_test_case_id.in_(canon_ids))
            .order_by(TestStep.canonical_test_case_id, TestStep.ordinal)
        )
    ).all()

    steps_by_canon: dict[uuid.UUID, list[dict]] = {}
    for sr in step_rows:
        steps_by_canon.setdefault(sr.canonical_test_case_id, []).append(
            {
                "name": sr.name,
                "status": sr.status,
                "assertion_message": sr.assertion_message,
            }
        )

    for t in top_failing:
        key = (t["test_name"], t.get("class_name"))
        cid = canon_by_key.get(key) or canon_by_key.get((key[0], None))
        steps = steps_by_canon.get(cid) if cid is not None else None
        if not steps:
            t["failure_step"] = None
            t["step_breakdown"] = None
            continue
        t["step_breakdown"] = steps
        # Persisted step.status is upper-cased by ``ingestion._map_step_status``;
        # compare case-insensitively to be robust to any producer drift.
        failing = next(
            (
                s
                for s in steps
                if str(s["status"] or "").upper() in _FAILED_STEP_STATUSES
            ),
            None,
        )
        t["failure_step"] = failing["name"] if failing else None
