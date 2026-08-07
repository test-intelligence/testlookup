"""Dashboard metrics aggregation service."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession


from app.models.postgres import Defect, TestCase, TestRun, TestStatus

logger = logging.getLogger(__name__)


# The release-gate "P0" hard cap is expressed in the policy/UI's P0–P3
# vocabulary, but the ``defects.severity`` column only ever holds the values
# the promotion pipeline writes — CRITICAL/HIGH/MEDIUM/LOW (see
# ``defect_promotion_service._composite_to_severity`` and
# ``analytics_service._SEVERITY_FROM_PRIORITY``, where P0 → CRITICAL). No row is
# ever stored with the literal string "P0", so a ``severity == "P0"`` filter
# matches nothing and the cap can never fire. This constant is the single
# source of truth for that mapping; query with it, not the literal "P0".
P0_DEFECT_SEVERITY = "CRITICAL"


async def count_open_critical_defects(db: AsyncSession, project_id) -> int:
    """Count OPEN CRITICAL defects (the canonical severity behind a policy
    "P0" hard cap) for a project. Centralised so every release-gate call site
    counts the same set — the prior inline ``severity == "P0"`` filters
    (metrics readiness + release-council synth/deep) each matched nothing or
    the wrong severity, silently defeating the ``max_p0_defects`` cap.
    """
    conds = [Defect.resolution_status == "OPEN", Defect.severity == P0_DEFECT_SEVERITY]
    if project_id:
        conds.append(Defect.project_id == project_id)
    result = await db.execute(select(func.count(Defect.id)).where(*conds))
    return int(result.scalar() or 0)


def _evaluated(passed: int, failed: int, broken: int) -> int:
    """Tests that actually produced a verdict — the pass-rate denominator.

    ``BROKEN`` (an infra/error result) MUST be in here. It was previously
    omitted, so a run of 10 passed / 0 failed / 2 broken reported a **100% pass
    rate** while a sixth of the suite never passed — a false headline, and one
    that feeds ``_compute_readiness()`` and the release-gate bands.

    Skips are excluded on purpose: a skipped test was never evaluated, so it
    belongs in neither numerator nor denominator.

    This matches the definition the rest of the codebase already uses --
    ``analysis_report_service`` ("evaluated = passed + failed + broken; skips
    don't count"), ``ingestion._executed`` and ``run_status`` -- and the same
    module's own flaky heuristic, which documents FAILED *or* BROKEN as "the
    canonical failed set used everywhere else".
    """
    return passed + failed + broken


def _normalize_suite_name(suite_name: str | None) -> str | None:
    normalized = (suite_name or "").strip().lower()
    return normalized or None


def _suite_match_clause(suite_name: str):
    """Match either per-case suite or run-level suite attribution.

    Live/SDK runs can stamp the suite on ``test_runs.primary_suite_name``
    before every test case has its own suite value, so dashboard filters need
    to honor both sources.
    """
    return or_(
        func.lower(func.trim(TestCase.suite_name)) == suite_name,
        func.lower(func.trim(TestRun.primary_suite_name)) == suite_name,
    )


async def get_dashboard_summary(
    db: AsyncSession,
    project_id: str | None,
    days: int = 7,
    suite_name: str | None = None,
) -> dict:
    """Compute all Executive Dashboard KPIs for a project.

    P3-6: Results are cached in Redis for 60 seconds to avoid re-aggregating
    thousands of test cases on every dashboard load.
    """
    from app.services.cache_service import CACHE_TTL_DASHBOARD, cache_get, cache_set

    suite_key = _normalize_suite_name(suite_name)
    cached = await cache_get("dashboard_summary_v2", project_id, days=days, suite=suite_key or "")
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=days)
    prev_period_start = now - timedelta(days=days * 2)

    # ── Current period stats ──────────────────────────────
    cur = await _period_stats(db, project_id, period_start, now, suite_key)
    prev = await _period_stats(db, project_id, prev_period_start, period_start, suite_key)

    def trend(cur_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((cur_val - prev_val) / prev_val) * 100, 1)
        return None

    def direction(t):
        if t is None:
            return "flat"
        return "up" if t > 0 else "down"

    pass_rate = cur["pass_rate"]
    prev_pass_rate = prev["pass_rate"]
    pass_trend = trend(pass_rate, prev_pass_rate)

    total_exec = cur["total_runs"]
    total_exec_trend = trend(total_exec, prev["total_runs"])

    # Active defects (all open, not time-bounded)
    defect_conditions = [Defect.resolution_status == "OPEN"]
    if project_id:
        defect_conditions.append(Defect.project_id == project_id)
    defect_stmt = select(func.count(Defect.id))
    if suite_key:
        defect_stmt = (
            defect_stmt
            .join(TestCase, Defect.test_case_id == TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
        )
        defect_conditions.append(_suite_match_clause(suite_key))
    defect_result = await db.execute(defect_stmt.where(*defect_conditions))
    active_defects = defect_result.scalar() or 0

    # Flaky tests (>20% failure rate over last 10 runs)
    flaky_count = await _count_flaky_tests(db, project_id, suite_key)

    # New failures in last 24h
    yesterday = now - timedelta(hours=24)
    fail_conditions = [
        TestCase.status == TestStatus.FAILED,
        TestCase.created_at >= yesterday,
    ]
    if project_id:
        fail_conditions.append(TestRun.project_id == project_id)
    if suite_key:
        fail_conditions.append(_suite_match_clause(suite_key))
    new_fail_result = await db.execute(
        select(func.count(TestCase.id))
        .join(TestRun)
        .where(*fail_conditions)
    )
    new_failures_24h = new_fail_result.scalar() or 0

    # Release readiness — None when there's no execution evidence so the UI /
    # CLI / MCP / report consumers can render a neutral "Pending" state instead
    # of falling through to RED on an empty dataset. When a per-project (or
    # system-default) ReleaseGatePolicy is active, its pass_rate_bands +
    # hard_caps drive the 4-colour band and the verdict so the dashboard
    # honours the configured thresholds.
    band: str | None = None
    downgrades: list[str] = []
    if total_exec <= 0:
        readiness = None
    else:
        policy_doc = await _resolve_policy_for_project(db, project_id)
        if policy_doc is not None:
            bands_cfg = policy_doc.get("pass_rate_bands") or {}
            caps_cfg = policy_doc.get("hard_caps") or {}
            # Count open CRITICAL defects for the "P0" hard cap. The prior
            # ``severity == "P0"`` filter matched no rows (defects are stored
            # CRITICAL/HIGH/MEDIUM/LOW), so the cap silently never fired.
            active_p0 = await count_open_critical_defects(db, project_id)
            classified = classify_with_policy(
                pass_rate=pass_rate,
                active_defects_p0=active_p0,
                flaky_count=flaky_count,
                new_failures_24h=new_failures_24h,
                bands=bands_cfg,
                hard_caps=caps_cfg,
            )
            band = classified["band"]
            downgrades = classified["downgrades"]
            verdict_to_legacy = {"GO": "GREEN", "CONDITIONAL": "AMBER", "NO_GO": "RED"}
            readiness = verdict_to_legacy[classified["verdict"]]
        else:
            readiness = _compute_readiness(total_exec, pass_rate, active_defects, flaky_count)

    result_dict = {
        "total_executions_7d": {
            "value": total_exec,
            "trend": total_exec_trend,
            "trend_direction": direction(total_exec_trend),
        },
        "avg_pass_rate_7d": {
            "value": round(pass_rate, 1),
            "trend": pass_trend,
            "trend_direction": direction(pass_trend),
        },
        "active_defects": {
            "value": active_defects,
            "trend": None,
            "trend_direction": "flat",
        },
        "flaky_test_count": {
            "value": flaky_count,
            "trend": None,
            "trend_direction": "flat",
        },
        "new_failures_24h": {
            "value": new_failures_24h,
            "trend": None,
            "trend_direction": "flat",
        },
        "avg_duration_ms": {
            "value": cur["avg_duration_ms"],
            "trend": trend(cur["avg_duration_ms"] or 0, prev["avg_duration_ms"] or 0),
            "trend_direction": "flat",
        },
        "release_readiness": readiness,
        # 4-colour band + downgrade audit so the UI can render the
        # configured pass-rate verdict directly (and show *why* a band
        # was downgraded by hard caps). ``None`` when no policy is
        # resolved or when there's no run evidence.
        "release_readiness_band": band,
        "release_readiness_downgrades": downgrades,
    }

    # P3-6: Cache the result for subsequent requests
    await cache_set("dashboard_summary_v2", result_dict, project_id, ttl=CACHE_TTL_DASHBOARD, days=days, suite=suite_key or "")

    return result_dict


async def get_trend_data(
    db: AsyncSession,
    project_id: str | None,
    days: int = 7,
    suite_name: str | None = None,
) -> list:
    """Return daily pass/fail/skip breakdown for the trend chart."""
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    suite_key = _normalize_suite_name(suite_name)
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    # 2026-05-15 bug fix: matching this filter via INNER JOIN test_cases
    # silently dropped every run whose ``test_cases`` rows weren't persisted
    # (a common state for live-stream ingest, which writes aggregates onto
    # ``test_runs`` first and per-case rows asynchronously). Switch to
    # filter via ``primary_suite_name`` + EXISTS on test_cases as backup
    # so runs with valid aggregates but missing per-case rows still appear.
    # Aggregates are always read from ``tr.*`` since those columns are
    # populated even when ``test_cases`` is empty.
    suite_filter = (
        """
        AND (
          LOWER(TRIM(tr.primary_suite_name)) = :suite_name
          OR EXISTS (
            SELECT 1 FROM test_cases tc
            WHERE tc.test_run_id = tr.id
              AND LOWER(TRIM(tc.suite_name)) = :suite_name
          )
        )
        """
        if suite_key else ""
    )
    # Aggregates always come from ``test_runs`` columns. Even when a suite is
    # filtered, the run-level totals are correct for the runs that survive
    # the filter — and they exist whether or not test_cases rows do.
    select_values = """
        COALESCE(SUM(tr.passed_tests), 0)  AS passed,
        COALESCE(SUM(tr.failed_tests), 0)  AS failed,
        COALESCE(SUM(tr.skipped_tests), 0) AS skipped,
        COALESCE(SUM(tr.broken_tests), 0)  AS broken,
        COALESCE(SUM(tr.total_tests), 0)   AS total,
        -- Denominator is EVALUATED = passed + failed + broken, matching
        -- _evaluated() used by the dashboard headline. BROKEN was previously
        -- omitted here, so the trend line read 83.9% for the same window the
        -- headline reported 81.0% -- and a day whose only failures were BROKEN
        -- charted as a flat 100%. Skips stay out: never evaluated.
        COALESCE(
          SUM(tr.passed_tests) * 100.0
            / NULLIF(
                SUM(tr.passed_tests) + SUM(tr.failed_tests) + SUM(tr.broken_tests), 0
              ),
          0
        ) AS pass_rate
    """
    query = text(f"""
        SELECT
            DATE_TRUNC('day', tr.created_at) AS day,
            {select_values}
        FROM test_runs tr
        WHERE tr.created_at >= :period_start
          {project_filter}
          {suite_filter}
        GROUP BY day
        ORDER BY day ASC
    """)
    params: dict = {"period_start": period_start}
    if project_id:
        params["project_id"] = str(project_id)
    if suite_key:
        params["suite_name"] = suite_key
    result = await db.execute(query, params)
    rows = result.fetchall()

    # ISO yyyy-mm-dd is the wire format every consumer expects:
    # ``frontend/src/pages/CoveragePage.tsx::CadenceHeatmap`` and
    # ``frontend/src/pages/TrendsPage.tsx::buildCadenceCells`` both key
    # their date lookup with ``new Date().toISOString().slice(0, 10)``,
    # and ``OverviewPage`` builds ``${date}T00:00:00Z`` to compute
    # ``timeAgo``. Returning a locale-formatted ``"May 16"`` here made
    # every key miss, leaving the cadence heatmaps and trend timelines
    # empty even when run data existed.
    return [
        {
            "date": row.day.strftime("%Y-%m-%d"),
            "passed": int(row.passed),
            "failed": int(row.failed),
            "skipped": int(row.skipped),
            "broken": int(row.broken),
            "total": int(row.total),
            "pass_rate": round(float(row.pass_rate), 1),
        }
        for row in rows
    ]


async def _period_stats(
    db: AsyncSession,
    project_id: str | None,
    start: datetime,
    end: datetime,
    suite_name: str | None = None,
) -> dict:
    conditions = [TestRun.created_at >= start, TestRun.created_at < end]
    if project_id:
        conditions.append(TestRun.project_id == project_id)
    if suite_name:
        # 2026-05-15 bug fix: live-stream runs persist their aggregates on
        # ``test_runs`` (passed_tests / failed_tests / total_tests) but
        # often DON'T persist per-test ``test_cases`` rows until later.
        # The previous implementation INNER-JOINed ``test_cases``, so a
        # legitimately populated suite returned 0 — dashboard panels +
        # trend chart all went blank when the user picked the suite.
        # Fix: filter by ``primary_suite_name`` directly (with EXISTS on
        # test_cases as a backup for older data where the run-level
        # suite label wasn't set), and read aggregates from ``test_runs``
        # columns which are always populated.
        suite_lower = suite_name  # already lowercased by _normalize_suite_name
        from sqlalchemy import exists, select as _select
        tc_match = exists().where(
            TestCase.test_run_id == TestRun.id,
        ).where(
            func.lower(func.trim(TestCase.suite_name)) == suite_lower,
        )
        conditions.append(
            or_(
                func.lower(func.trim(TestRun.primary_suite_name)) == suite_lower,
                tc_match,
            )
        )
        result = await db.execute(
            _select(
                func.count(TestRun.id).label("total_runs"),
                func.coalesce(func.sum(TestRun.passed_tests), 0).label("sum_passed"),
                func.coalesce(func.sum(TestRun.failed_tests), 0).label("sum_failed"),
                func.coalesce(func.sum(TestRun.broken_tests), 0).label("sum_broken"),
                func.coalesce(func.sum(TestRun.total_tests), 0).label("sum_total"),
                func.avg(TestRun.duration_ms).label("avg_duration_ms"),
            ).where(*conditions)
        )
        row = result.one()
        sum_passed = int(row.sum_passed or 0)
        sum_failed = int(row.sum_failed or 0)
        sum_broken = int(row.sum_broken or 0)
        denom = _evaluated(sum_passed, sum_failed, sum_broken)
        pass_rate = (sum_passed / denom * 100.0) if denom else 0.0
        return {
            "total_runs": row.total_runs or 0,
            "pass_rate": pass_rate,
            "avg_duration_ms": int(row.avg_duration_ms or 0),
        }
    # Weighted pass-rate across the period: sum of passed tests over the sum of
    # EVALUATED tests across every TestRun in the window. Weighted (not
    # ``AVG(TestRun.pass_rate)``) so a single tiny 0%-pass smoke run cannot drag
    # the headline down as if it were a full suite.
    result = await db.execute(
        select(
            func.count(TestRun.id).label("total_runs"),
            func.coalesce(func.sum(TestRun.passed_tests), 0).label("sum_passed"),
            func.coalesce(func.sum(TestRun.failed_tests), 0).label("sum_failed"),
            func.coalesce(func.sum(TestRun.broken_tests), 0).label("sum_broken"),
            func.avg(TestRun.duration_ms).label("avg_duration_ms"),
        ).where(*conditions)
    )
    row = result.one()
    sum_passed = int(row.sum_passed or 0)
    sum_failed = int(row.sum_failed or 0)
    sum_broken = int(row.sum_broken or 0)
    denom = _evaluated(sum_passed, sum_failed, sum_broken)
    pass_rate = (sum_passed / denom * 100.0) if denom else 0.0
    return {
        "total_runs": row.total_runs or 0,
        "pass_rate": pass_rate,
        "avg_duration_ms": int(row.avg_duration_ms or 0),
    }


# A test is "flaky" for the dashboard headline when its recent history mixes
# passes and failures. Two invariants this heuristic must honour (both were
# silently broken before): the window is the most-recent N executions PER
# fingerprint (a test flaky months ago but stable since is NOT flaky now), and
# a "failure" is FAILED *or* BROKEN — the canonical failed set used everywhere
# else (see flaky_signals._FAILED_STATUSES). This is the cheap headline count;
# the Wilson-CI / ML flaky verdict lives in flaky_statistics / flaky_sentinel.
_FLAKY_WINDOW_RUNS = 10
_FLAKY_MIN_RUNS = 5
# Minimum pass<->fail transitions ("flips") required inside the window.
#
# A failure RATIO alone is order-blind: a test that passed twice and has failed
# on every run since sits at 0.8 and used to be counted as flaky, even though it
# is a *persistent regression* — exactly the case flaky_signals._label() calls
# ``persistent_regression`` and explicitly refuses to put on the flake track.
#
# One flip is a state CHANGE (a test broke, or a test got fixed). Only from the
# second flip does the test return to a state it had already left, which is the
# actual signature of intermittency.
#
# Imported, not redefined: three read-path detectors independently re-derived
# flakiness from a ratio and each admitted stable regressions. flaky_signals owns
# the canonical value so they cannot drift apart again.
from app.services.flaky_signals import (  # noqa: E402
    MIN_FLIPS_FOR_INTERMITTENCY as _FLAKY_MIN_FLIPS,
)


async def _count_flaky_tests(
    db: AsyncSession,
    project_id: str | None,
    suite_name: str | None = None,
) -> int:
    """Count tests that show the flaky pattern over their last
    ``_FLAKY_WINDOW_RUNS`` executions.

    Two conditions, both required:

    * failure ratio inside 10%-90% (it neither always passes nor always fails), and
    * at least ``_FLAKY_MIN_FLIPS`` pass<->fail transitions *in run order*.

    The second condition is what separates a flake from a regression. Without it
    the count was order-blind and a permanently-broken test was reported as
    flaky, which points a QA lead away from a real bug (and inflates the
    "known flaky" figure on the summary report)."""
    project_filter = "WHERE tr.project_id = :project_id" if project_id else ""
    suite_join = "JOIN test_cases tc ON tc.id = tch.test_case_id" if suite_name else ""
    suite_match_sql = "(LOWER(TRIM(tc.suite_name)) = :suite_name OR LOWER(TRIM(tr.primary_suite_name)) = :suite_name)"
    suite_filter = (
        f"AND {suite_match_sql}"
        if suite_name and project_filter
        else f"WHERE {suite_match_sql}"
        if suite_name
        else ""
    )
    # Rank each fingerprint's history newest-first, keep only the last N, then
    # apply the flaky ratio over that bounded window. Without the window the
    # HAVING scanned all history, so a fingerprint's flaky flag could only ever
    # accumulate — a test never "recovered" once flaky.
    # `seq` re-reads the bounded window in ASCENDING run order (rn DESC undoes
    # the newest-first ranking) so LAG() compares each execution with the one
    # that actually preceded it, and a flip is a genuine adjacent change.
    query = text(f"""
        SELECT COUNT(DISTINCT fingerprint) FROM (
            SELECT fingerprint
            FROM (
                SELECT
                    fingerprint,
                    is_failed,
                    LAG(is_failed) OVER (
                        PARTITION BY fingerprint ORDER BY rn DESC
                    ) AS prev_failed
                FROM (
                    SELECT
                        tch.test_fingerprint AS fingerprint,
                        CASE WHEN tch.status IN ('FAILED', 'BROKEN') THEN 1 ELSE 0 END AS is_failed,
                        ROW_NUMBER() OVER (
                            PARTITION BY tch.test_fingerprint
                            ORDER BY tch.created_at DESC, tch.id DESC
                        ) AS rn
                    FROM test_case_history tch
                    JOIN test_runs tr ON tr.id = tch.test_run_id
                    {suite_join}
                    {project_filter}
                    {suite_filter}
                ) ranked
                WHERE rn <= {_FLAKY_WINDOW_RUNS}
            ) seq
            GROUP BY fingerprint
            HAVING COUNT(*) >= {_FLAKY_MIN_RUNS}
               AND COUNT(*) FILTER (WHERE is_failed = 1) * 1.0 / COUNT(*) BETWEEN 0.1 AND 0.9
               AND COUNT(*) FILTER (
                       WHERE prev_failed IS NOT NULL AND prev_failed <> is_failed
                   ) >= {_FLAKY_MIN_FLIPS}
        ) flaky
    """)
    params: dict = {}
    if project_id:
        params["project_id"] = str(project_id)
    if suite_name:
        params["suite_name"] = suite_name
    result = await db.execute(query, params)
    return result.scalar() or 0


def _compute_readiness(
    total_runs: int,
    pass_rate: float,
    active_defects: int,
    flaky_count: int,
) -> str | None:
    """Hardcoded fallback verdict — used when no project policy is resolved.

    Returns GREEN / AMBER / RED, or None when there's no evidence to grade
    (no runs in the window). Without any data the previous rule chain fell
    through to RED, which is misleading — "no data" is a neutral state.
    """
    if total_runs <= 0:
        return None
    if pass_rate >= 95 and active_defects == 0 and flaky_count <= 5:
        return "GREEN"
    if pass_rate >= 85 and active_defects <= 5:
        return "AMBER"
    return "RED"


# Ordered worst → best so a downgrade is a leftward step.
_BAND_ORDER: list[str] = ["red", "orange", "yellow", "green"]


def classify_with_policy(
    pass_rate: float,
    active_defects_p0: int,
    flaky_count: int,
    new_failures_24h: int,
    bands: dict,
    hard_caps: dict,
) -> dict:
    """Resolve the 4-band colour + GO/CONDITIONAL/NO_GO verdict using a
    project policy. Pure function — no DB access — so it's trivially
    unit-testable and can be reused by ``/release-gate`` and the dashboard.

    Args:
      pass_rate: weighted pass-rate for the period (0-100).
      active_defects_p0: count of OPEN P0 defects in the project.
      flaky_count: live flaky-test count from ``_count_flaky_tests``.
      new_failures_24h: new-failure count from the last 24h.
      bands: ``{"orange_min", "yellow_min", "green_min"}`` from the policy.
      hard_caps: ``{"max_p0_defects", "max_flaky_count", "max_new_failures_24h"}``.

    Returns:
      ``{band, verdict, downgrades}`` — ``band`` is one of red/orange/yellow/green;
      ``verdict`` is GO/CONDITIONAL/NO_GO; ``downgrades`` lists which hard caps
      fired so the UI can show "downgraded from green → orange (P0 defect, flaky)".
    """
    green_min = float(bands.get("green_min", 99.0))
    yellow_min = float(bands.get("yellow_min", 95.0))
    orange_min = float(bands.get("orange_min", 90.0))

    if pass_rate >= green_min:
        idx = 3  # green
    elif pass_rate >= yellow_min:
        idx = 2  # yellow
    elif pass_rate >= orange_min:
        idx = 1  # orange
    else:
        idx = 0  # red

    downgrades: list[str] = []
    max_p0 = int(hard_caps.get("max_p0_defects", 0) or 0)
    max_flaky = int(hard_caps.get("max_flaky_count", 0) or 0)
    max_new_fail = int(hard_caps.get("max_new_failures_24h", 0) or 0)
    if active_defects_p0 > max_p0:
        downgrades.append(f"p0_defects:{active_defects_p0}>{max_p0}")
    if max_flaky > 0 and flaky_count > max_flaky:
        downgrades.append(f"flaky:{flaky_count}>{max_flaky}")
    if max_new_fail > 0 and new_failures_24h > max_new_fail:
        downgrades.append(f"new_failures_24h:{new_failures_24h}>{max_new_fail}")

    # A hard cap is a HARD blocker: any breach forces NO_GO regardless of the
    # pass-rate band. Previously each breach only stepped the band one notch,
    # and yellow still mapped to GO — so an otherwise-green run with open P0
    # defects (or flaky / new-failure breaches) still shipped GO, which made
    # the "hard cap" advisory at best. The band is pinned to red to match the
    # blocking verdict; ``downgrades`` records exactly which caps fired.
    if downgrades:
        return {"band": "red", "verdict": "NO_GO", "downgrades": downgrades}

    band = _BAND_ORDER[idx]
    verdict = (
        "GO" if band == "green"
        else "GO" if band == "yellow"
        else "CONDITIONAL" if band == "orange"
        else "NO_GO"
    )
    return {"band": band, "verdict": verdict, "downgrades": downgrades}


async def _resolve_policy_for_project(db: AsyncSession, project_id: str | None) -> dict | None:
    """Return the active policy document (JSON) for a project, with the
    standard precedence: project-active → system-default (project_id IS NULL,
    is_active=True). Returns None when no policy row matches — callers fall
    back to the hardcoded thresholds. Lean query: no row hydration, just the
    JSON ``rules`` column."""
    from app.models.postgres import ReleaseGatePolicy

    if project_id:
        result = await db.execute(
            select(ReleaseGatePolicy.rules)
            .where(
                ReleaseGatePolicy.project_id == project_id,
                ReleaseGatePolicy.is_active.is_(True),
            )
            .order_by(ReleaseGatePolicy.version.desc())
            .limit(1)
        )
        row = result.first()
        if row and row[0]:
            return row[0]

    result = await db.execute(
        select(ReleaseGatePolicy.rules)
        .where(
            ReleaseGatePolicy.project_id.is_(None),
            ReleaseGatePolicy.is_active.is_(True),
        )
        .order_by(ReleaseGatePolicy.version.desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row and row[0] else None
