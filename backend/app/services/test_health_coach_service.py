"""
Test Health Coach Service.

Productizes test health signals into persisted recommendations with
quarantine scoring.  Provides:
  - Per-run test health retrieval (from DB or pipeline state)
  - Project-level flaky coach leaderboard with quarantine ranking
  - Stabilization action generation based on anti-pattern type

All DB writes are idempotent and safe for concurrent pipeline executions.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    FlakyCoachResult,
    TestCase,
    TestCaseHistory,
    TestHealthRecommendation,
    TestRun,
    TestStatus,
    TriageStatus,
)
from app.models.schemas import (
    FlakyCoachEntry,
    FlakyCoachResponse,
    TestHealthFinding,
    TestHealthResponse,
    TestHealthViolation,
)

logger = logging.getLogger("services.test_health_coach")

# ── Stabilization action templates per anti-pattern ──────────────────────────

_STABILIZATION_ACTIONS: dict[str, list[str]] = {
    "retry_smell": [
        "Remove retry logic and fix the root flakiness cause",
        "If retries are needed, add them at the framework level with exponential backoff",
    ],
    "timing_dependency": [
        "Replace Thread.sleep / time.sleep with explicit waits or polling",
        "Use WebDriverWait or equivalent framework-specific wait utilities",
    ],
    "shared_state": [
        "Isolate test data per test using setUp/tearDown or fixtures",
        "Remove static mutable fields from test classes",
    ],
    "data_dependency": [
        "Use test data factories or fixtures instead of shared database rows",
        "Add teardown to clean up test-created data",
    ],
    "environment_dependency": [
        "Mock external service calls or use contract stubs",
        "Add environment health checks in test preconditions",
    ],
    "empty_catch": [
        "Remove empty catch blocks — let failures propagate",
        "If exception handling is needed, log and re-throw with context",
    ],
    "missing_assertions": [
        "Add explicit assertions for expected outcomes",
        "Verify both positive and negative conditions",
    ],
    "brittle_selector": [
        "Replace XPath with data-testid or semantic selectors",
        "Use page object pattern for selector management",
    ],
}

# ── Quarantine thresholds ────────────────────────────────────────────────────

_QUARANTINE_THRESHOLD = 0.50       # failure_rate > 50% → QUARANTINE
_INVESTIGATE_THRESHOLD = 0.25      # 25-50% → INVESTIGATE
_MONITOR_THRESHOLD = 0.10          # 10-25% → MONITOR
# Below 10% → HEALTHY


def _compute_quarantine_recommendation(failure_rate: float) -> str:
    if failure_rate >= _QUARANTINE_THRESHOLD:
        return "QUARANTINE"
    if failure_rate >= _INVESTIGATE_THRESHOLD:
        return "INVESTIGATE"
    if failure_rate >= _MONITOR_THRESHOLD:
        return "MONITOR"
    return "HEALTHY"


def _compute_impact_score(failure_rate: float, total_runs: int) -> float:
    """Impact = failure_rate × log2(total_runs + 1), capped at 100."""
    import math
    raw = failure_rate * math.log2(total_runs + 1) * 20
    return round(min(100.0, max(0.0, raw)), 1)


def _classify_anti_patterns(violations: list[dict]) -> list[str]:
    """Map violation patterns to stabilization action categories."""
    patterns = set()
    for v in violations:
        raw = v.get("pattern", "") or ""
        desc = raw.lower()
        if "sleep" in desc or "wait" in desc:
            patterns.add("timing_dependency")
        elif "catch" in desc or "swallow" in desc:
            patterns.add("empty_catch")
        elif "assertion" in desc or "vacuous" in desc:
            patterns.add("missing_assertions")
        elif "xpath" in desc or "brittle" in desc or "selector" in desc:
            patterns.add("brittle_selector")
        elif "static" in desc or "shared" in desc or "mutable" in desc:
            patterns.add("shared_state")
        elif "suppress" in desc or "ignore" in desc or "skip" in desc:
            patterns.add("retry_smell")
    return list(patterns)


def _get_stabilization_actions(anti_patterns: list[str]) -> list[str]:
    """Compile concrete stabilization actions from detected anti-patterns."""
    actions: list[str] = []
    for pattern in anti_patterns:
        actions.extend(_STABILIZATION_ACTIONS.get(pattern, []))
    if not actions:
        actions.append("Review test for environment sensitivity or data dependencies")
    return actions


# ── Per-run test health retrieval ────────────────────────────────────────────

async def get_run_test_health(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> TestHealthResponse:
    """
    Retrieve persisted test health findings for a specific run.
    Returns empty response if no findings exist (pipeline hasn't run yet).
    """
    result = await db.execute(
        select(TestHealthRecommendation)
        .where(TestHealthRecommendation.test_run_id == run_id)
        .order_by(TestHealthRecommendation.health_score.asc())
    )
    rows = result.scalars().all()

    findings = []
    for row in rows:
        violations = [
            TestHealthViolation(**v) if isinstance(v, dict) else TestHealthViolation(pattern=str(v), severity="info")
            for v in (row.violations or [])
        ]
        findings.append(TestHealthFinding(
            test_case_id=str(row.test_case_id),
            test_name=row.test_name,
            health_score=row.health_score,
            violations=violations,
            critical_count=row.critical_count,
            warning_count=row.warning_count,
            recommendation=row.recommendation or "",
            anti_patterns=row.anti_patterns or [],
        ))

    scores = [f.health_score for f in findings]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    return TestHealthResponse(
        run_id=str(run_id),
        total_analyzed=len(findings),
        with_violations=sum(1 for f in findings if f.violations),
        avg_health_score=avg_score,
        findings=findings,
    )


async def persist_test_health_findings(
    run_id: uuid.UUID,
    findings: list[dict],
    db: AsyncSession,
) -> int:
    """
    Stage test health findings from the pipeline. Caller owns ``db.commit()``.

    Idempotent: deletes existing findings for the run before inserting so
    the latest pipeline run's findings are authoritative.

    Currently unused — intended to be called from the deep-investigation
    pipeline once findings are wired in. Staged-only so it integrates
    cleanly with whichever handler eventually owns the transaction.
    """
    if not findings:
        return 0

    await db.execute(
        delete(TestHealthRecommendation)
        .where(TestHealthRecommendation.test_run_id == run_id)
    )

    count = 0
    for f in findings:
        tc_id = f.get("test_case_id")
        if not tc_id:
            continue

        anti_patterns = _classify_anti_patterns(f.get("violations", []))

        rec = TestHealthRecommendation(
            test_run_id=run_id,
            test_case_id=tc_id,
            test_name=f.get("test_name", ""),
            health_score=f.get("health_score", 50),
            violations=f.get("violations", []),
            critical_count=f.get("critical_count", 0),
            warning_count=f.get("warning_count", 0),
            recommendation=f.get("recommendation", ""),
            anti_patterns=anti_patterns,
        )
        db.add(rec)
        count += 1

    return count


# ── Project-level flaky coach ────────────────────────────────────────────────

async def _load_flaky_cache(
    db: AsyncSession,
    project_id: uuid.UUID,
    limit: int,
):
    """Pure read: return cached flaky-coach rows ordered by impact."""
    result = await db.execute(
        select(FlakyCoachResult)
        .where(FlakyCoachResult.project_id == project_id)
        .order_by(FlakyCoachResult.impact_score.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def _populate_flaky_cache_in_new_session(
    project_id: uuid.UUID,
    days: int,
) -> None:
    """Item #4 (command/query separation): the GET endpoint must not
    mutate persistent state on its own transaction, but we still want
    first-page-load to show data. Compromise: open a **dedicated write
    session**, stage the refresh there, commit it, and return. The GET
    handler's own transaction stays read-only; the next read of
    ``FlakyCoachResult`` sees the committed rows.
    """
    from app.db.postgres import AsyncSessionLocal
    async with AsyncSessionLocal() as write_db:
        try:
            await refresh_flaky_coach(project_id, write_db, days=days)
            await write_db.commit()
        except Exception as exc:
            logger.warning("Flaky cache populate failed: %s", exc)
            await write_db.rollback()


async def get_flaky_coach(
    project_id: uuid.UUID,
    db: AsyncSession,
    days: int = 30,
    limit: int = 50,
) -> FlakyCoachResponse:
    """
    Project-level flaky test leaderboard ranked by impact.

    Pure read from the caller's perspective — if the cache is empty, we
    fire a one-shot populate on a dedicated write session and re-read.
    The caller's ``db`` session is never mutated here, so this function
    could be served from a read replica once pooling supports it.
    """
    rows = await _load_flaky_cache(db, project_id, limit)

    if not rows:
        # Cache miss: populate via a dedicated write session so this
        # endpoint stays pure-read on the caller's transaction.
        await _populate_flaky_cache_in_new_session(project_id, days)
        rows = await _load_flaky_cache(db, project_id, limit)

    entries = []
    for row in rows:
        entries.append(FlakyCoachEntry(
            test_fingerprint=row.test_fingerprint,
            test_name=row.test_name,
            suite_name=row.suite_name,
            failure_rate=round(row.failure_rate, 3),
            total_runs=row.total_runs,
            failed_runs=row.failed_runs,
            flaky_since=row.flaky_since.isoformat() if row.flaky_since else None,
            last_failure_at=row.last_failure_at.isoformat() if row.last_failure_at else None,
            quarantine_recommendation=row.quarantine_recommendation,
            stabilization_actions=row.stabilization_actions or [],
            impact_score=round(row.impact_score, 1),
            status_history=row.status_history or [],
        ))

    # Augment with tests humans have manually triaged as ``FLAKY_TEST``
    # via /my-failures. The auto-detector only fires when a fingerprint
    # has BOTH passes and failures in the window (>=3 runs) — manual
    # triage covers the long tail where the engineer recognises a flake
    # before the auto-detector has enough signal. Deduped by fingerprint
    # so a test that's BOTH auto-detected AND manually triaged appears
    # exactly once.
    seen_fingerprints = {e.test_fingerprint for e in entries}
    manual_rows = await _load_manual_flaky_triage(db, project_id, days=days)
    for row in manual_rows:
        if not row.test_fingerprint or row.test_fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(row.test_fingerprint)
        entries.append(FlakyCoachEntry(
            test_fingerprint=row.test_fingerprint,
            test_name=row.test_name or row.test_fingerprint[:12],
            suite_name=row.suite_name,
            # The auto-detector computes a ratio; manual triage doesn't
            # have one. Use 1.0 to signal "human-flagged" — the UI can
            # render an icon or different copy when this is the marker.
            failure_rate=1.0,
            total_runs=int(row.failed_count or 0),
            failed_runs=int(row.failed_count or 0),
            flaky_since=row.first_marked_at.isoformat() if row.first_marked_at else None,
            last_failure_at=row.last_marked_at.isoformat() if row.last_marked_at else None,
            # A human triaged this specifically — it lands in INVESTIGATE
            # so it shows up under the same KPI as auto-detected
            # 25-50%-failure-rate flakes. Auto-detected matches with
            # higher failure_rate still take precedence via the
            # dedup-by-fingerprint loop above.
            quarantine_recommendation="INVESTIGATE",
            stabilization_actions=["Manually triaged as flaky on /my-failures"],
            impact_score=0.0,
            status_history=["FLAKY (manual triage)"],
        ))

    quarantine_count = sum(1 for e in entries if e.quarantine_recommendation == "QUARANTINE")

    return FlakyCoachResponse(
        project_id=str(project_id),
        total_flaky=len(entries),
        quarantine_candidates=quarantine_count,
        entries=entries,
    )


async def _load_manual_flaky_triage(
    db: AsyncSession, project_id: uuid.UUID, days: int,
) -> list:
    """Return aggregate rows for test_cases manually triaged as
    ``FLAKY_TEST`` within the window.

    Aggregates per fingerprint (one entry per distinct test) so a test
    flagged across multiple runs collapses to a single leaderboard
    row. ``last_marked_at`` is the most recent triage event;
    ``first_marked_at`` is the earliest.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            TestCase.test_fingerprint,
            sa_func.max(TestCase.test_name).label("test_name"),
            sa_func.max(TestCase.suite_name).label("suite_name"),
            sa_func.count(TestCase.id).label("failed_count"),
            sa_func.min(TestCase.triage_updated_at).label("first_marked_at"),
            sa_func.max(TestCase.triage_updated_at).label("last_marked_at"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestRun.project_id == project_id)
        .where(TestCase.triage_status == TriageStatus.FLAKY_TEST.value)
        .where(TestCase.triage_updated_at >= cutoff)
        .where(TestCase.test_fingerprint.isnot(None))
        .group_by(TestCase.test_fingerprint)
        .order_by(sa_func.max(TestCase.triage_updated_at).desc())
    )
    return list((await db.execute(stmt)).all())


async def refresh_flaky_coach(
    project_id: uuid.UUID,
    db: AsyncSession,
    days: int = 30,
) -> int:
    """
    Recompute flaky coach results for a project from test case history.
    This is called as part of the pipeline or via a scheduled task.

    Returns the number of flaky tests found.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Find all test fingerprints with history in this project
    # A test is flaky if it has both PASSED and FAILED/BROKEN in the window
    history_q = (
        select(
            TestCaseHistory.test_fingerprint,
            sa_func.count(TestCaseHistory.id).label("total_runs"),
            sa_func.count(
                sa_func.nullif(
                    TestCaseHistory.status.in_([TestStatus.FAILED, TestStatus.BROKEN]),
                    False,
                )
            ).label("failed_runs_raw"),
            sa_func.max(TestCaseHistory.created_at).label("last_run_at"),
        )
        .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
        .where(TestRun.project_id == project_id)
        .where(TestCaseHistory.created_at >= cutoff)
        .group_by(TestCaseHistory.test_fingerprint)
        .having(sa_func.count(TestCaseHistory.id) >= 3)
    )

    result = await db.execute(history_q)
    all_fingerprints = result.all()

    # Delete existing results for this project
    await db.execute(
        delete(FlakyCoachResult).where(FlakyCoachResult.project_id == project_id)
    )

    count = 0
    for row in all_fingerprints:
        fp = row.test_fingerprint
        if not fp:
            continue

        # Get detailed status history for this fingerprint
        status_q = (
            select(TestCaseHistory.status, TestCaseHistory.created_at)
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .where(TestRun.project_id == project_id)
            .where(TestCaseHistory.test_fingerprint == fp)
            .where(TestCaseHistory.created_at >= cutoff)
            .order_by(TestCaseHistory.created_at.desc())
            .limit(30)
        )
        status_result = await db.execute(status_q)
        status_rows = status_result.all()

        statuses = [str(s.status) for s in status_rows]
        total = len(statuses)
        if total < 3:
            continue

        failed = sum(1 for s in statuses if s in ("FAILED", "BROKEN", str(TestStatus.FAILED), str(TestStatus.BROKEN)))
        passed = sum(1 for s in statuses if s in ("PASSED", str(TestStatus.PASSED)))

        # Must have both passed and failed to be flaky
        if failed == 0 or passed == 0:
            continue

        failure_rate = failed / total
        quarantine_rec = _compute_quarantine_recommendation(failure_rate)
        impact = _compute_impact_score(failure_rate, total)

        # Get test name from latest test case with this fingerprint
        tc_q = (
            select(TestCase.test_name, TestCase.suite_name)
            .where(TestCase.test_fingerprint == fp)
            .order_by(TestCase.created_at.desc())
            .limit(1)
        )
        tc_result = await db.execute(tc_q)
        tc_row = tc_result.first()
        test_name = tc_row.test_name if tc_row else fp
        suite_name = tc_row.suite_name if tc_row else None

        # Find flaky_since (earliest alternation)
        flaky_since = None
        first_failure_dates = [s.created_at for s in status_rows if str(s.status) in ("FAILED", "BROKEN", str(TestStatus.FAILED), str(TestStatus.BROKEN))]
        if first_failure_dates:
            flaky_since = min(first_failure_dates)

        last_failure_at = max(first_failure_dates) if first_failure_dates else None

        # Generate stabilization actions based on quarantine level
        actions = []
        if quarantine_rec == "QUARANTINE":
            actions = [
                "Quarantine this test immediately to stabilize the CI pipeline",
                "Create a dedicated investigation ticket with full history",
                "Review test isolation — check for shared state or ordering dependencies",
            ]
        elif quarantine_rec == "INVESTIGATE":
            actions = [
                "Prioritize flakiness investigation in next sprint",
                "Add retry annotations as temporary mitigation",
                "Check for timing dependencies or environment sensitivity",
            ]
        elif quarantine_rec == "MONITOR":
            actions = [
                "Continue monitoring — flag if failure rate increases",
                "Review test for potential data dependency issues",
            ]

        db.add(FlakyCoachResult(
            project_id=project_id,
            test_fingerprint=fp,
            test_name=test_name,
            suite_name=suite_name,
            failure_rate=failure_rate,
            total_runs=total,
            failed_runs=failed,
            flaky_since=flaky_since,
            last_failure_at=last_failure_at,
            quarantine_recommendation=quarantine_rec,
            stabilization_actions=actions,
            impact_score=impact,
            status_history=statuses[:10],
        ))
        count += 1

    # Stage-only: the caller owns the commit. Called from:
    #   (a) POST /flaky-coach/refresh — the router handler commits.
    #   (b) ``_populate_flaky_cache_in_new_session`` — its own dedicated
    #       write session commits.
    return count
