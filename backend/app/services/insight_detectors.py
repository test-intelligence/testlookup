"""Cross-run insight detectors (W23a FAIL-11): READ-ONLY rules over stored results.

Four detectors, one :class:`DetectorResult` each per project per evaluation
(the hourly beat ``tasks.detect_cross_run_insights`` gives each its own
session; ``insights_service.apply_result`` stores the outcome). Rules only: no
LLM, no new statistic, no write -- every number here is read from what another
feature already stored:

* ``perf_drift`` AGGREGATES EXEC-05's stored ``test_runs.duration_findings``
  of the last 7 days (``duration_regression_service`` judged each run at
  finalize). It never calls ``assess_run_durations`` / ``feed_run_observations``
  / ``detect_spikes_for_run``: no second baseline, no second feed.
* ``flake_rising`` applies the project's flaky-test rule,
  ``analytics_service.flaky_tests`` (oscillation over ``test_case_history``:
  >= 3 observations, a failure share in 0.05..0.95, >= 2 status flips), to two
  adjacent 7-day windows per effective suite in ONE statement. That function
  is not called: its window always ends now and it cannot count the previous
  week. The constants are imported, the SQL shape is copied (cited below).
* ``coverage_drop`` compares two stored TRND-07 ``coverage_snapshots`` rows; it
  never recomputes coverage.
* ``cost_budget`` reads ``llm_cost_budget.get_current_usage`` (two reads). It
  never calls ``check_and_apply_cap``, which writes ``cap_hits`` and may emit
  the ``quota.exceeded`` webhook.

Absence is not health: a detector that cannot judge returns ``measured=False``
with an ``insight_vocab.NOT_MEASURED`` key, and the store then records only
that state (it opens nothing and clears nothing).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import CoverageSnapshot, SuiteExecution, TestRun
from app.services import feature_flags
from app.services import llm_cost_budget
from app.services import perf_regression_service as perf
from app.services.analytics_scope import effective_suite_sql, scoped_text
from app.services.coverage_snapshot_service import exercised_pct, utc_today
from app.services.flaky_signals import MIN_FLIPS_FOR_INTERMITTENCY
from app.services.insight_vocab import (
    COST_FIRING_STATUSES,
    COVERAGE_COMPARE_DAYS,
    COVERAGE_HIGH_DROP_SHARE,
    COVERAGE_MAX_SNAPSHOT_AGE_DAYS,
    COVERAGE_MIN_DROP_SHARE,
    COVERAGE_MIN_DROP_TESTS,
    COVERAGE_MIN_EXERCISED,
    COVERAGE_PREVIOUS_TOLERANCE_DAYS,
    FLAKE_EXAMPLES,
    FLAKE_FAIL_SHARE,
    FLAKE_HIGH_MIN_CURRENT,
    FLAKE_HIGH_RATIO,
    FLAKE_MIN_CURRENT,
    FLAKE_MIN_DELTA,
    FLAKE_MIN_OBSERVATIONS,
    FLAKE_MIN_RATIO,
    FLAKE_MIN_RUNS_PER_WINDOW,
    FLAKE_WINDOW_DAYS,
    INSIGHT_SEVERITIES,
    MAX_SUBJECTS_PER_DETECTOR,
    PERF_MAX_RUNS_READ,
    PERF_SUITE_HIGH_RATIO,
    PERF_TEST_HIGH_RUNS,
    PERF_TEST_HIGH_SIGMA,
    PERF_TEST_MIN_SPIKE_RUNS,
    PERF_TEST_RECENT_HOURS,
    PERF_WINDOW_DAYS,
    WINDOW_KEY,
)

_slog = structlog.get_logger(__name__)

_SEVERITY_RANK = {name: rank for rank, name in enumerate(INSIGHT_SEVERITIES)}
#: The display names of the two subjects that are not a suite or a test.
COVERAGE_SUBJECT_LABEL = "Tests exercised"
COST_SUBJECT_LABEL = "AI budget"


@dataclass(frozen=True)
class Finding:
    """One subject firing in one evaluation."""
    subject_key: str
    subject_label: str
    severity: str
    evidence: dict


@dataclass(frozen=True)
class DetectorResult:
    """One detector's evaluation of one project (contract §1.4)."""
    kind: str
    window_key: str
    measured: bool
    reason_code: Optional[str]
    #: <= MAX_SUBJECTS_PER_DETECTOR, most severe first.
    findings: tuple[Finding, ...] = ()
    #: Subjects this evaluation could judge: only these may be "cleared".
    measured_subjects: frozenset[str] = frozenset()
    #: Subjects firing, before the cap.
    firing: int = 0
    #: cost_budget: resolve open rows of an earlier billing period.
    clear_earlier_windows: bool = False


@dataclass(frozen=True)
class SnapshotRow:
    """The four columns of a ``coverage_snapshots`` row the coverage detector reads."""
    snapshot_day: date
    total_tests: int
    automated_count: int
    total_suites: int


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _not_measured(kind: str, window_key: str, reason_code: str) -> DetectorResult:
    return DetectorResult(kind=kind, window_key=window_key, measured=False, reason_code=reason_code)


def _capped(findings: Iterable[Finding]) -> tuple[tuple[Finding, ...], int, frozenset[str]]:
    """Most severe first (a stable sort: each detector pre-orders by magnitude),
    at most ``MAX_SUBJECTS_PER_DETECTOR``; the count before the cap; and the
    subjects the cap left out. Those still FIRE, so the caller removes them
    from ``measured_subjects``: a subject cut by the cap is never "cleared"."""
    ordered = sorted(findings, key=lambda f: -_SEVERITY_RANK.get(f.severity, 0))
    kept = tuple(ordered[:MAX_SUBJECTS_PER_DETECTOR])
    dropped = frozenset(f.subject_key for f in ordered[MAX_SUBJECTS_PER_DETECTOR:])
    return kept, len(ordered), dropped


# ── The judges (pure) ─────────────────────────────────────────────────────────


def judge_flake(prev: int, cur: int, runs_prev: int, runs_cur: int) -> tuple[bool, bool, Optional[str]]:
    """``(measured, firing, severity)`` for one suite's flaky-test counts.

    Judged only with ``FLAKE_MIN_RUNS_PER_WINDOW`` runs in BOTH weeks. Fires
    when this week holds >= 3 flaky tests, at least twice last week's, and at
    least 2 more (0 -> 3, 1 -> 3, 3 -> 6 fire; 2 -> 3 and 3 -> 5 do not).
    """
    if runs_prev < FLAKE_MIN_RUNS_PER_WINDOW or runs_cur < FLAKE_MIN_RUNS_PER_WINDOW:
        return False, False, None
    firing = cur >= FLAKE_MIN_CURRENT and cur >= FLAKE_MIN_RATIO * prev and cur - prev >= FLAKE_MIN_DELTA
    if not firing:
        return True, False, None
    high = cur >= FLAKE_HIGH_MIN_CURRENT and cur >= FLAKE_HIGH_RATIO * max(prev, 1)
    return True, True, "high" if high else "medium"


def _percent(share: float) -> int:
    """A share as whole percent (0.10 -> 10), so the floors compare in integers
    (``0.1 * 100`` is not exactly 10 in floating point)."""
    return int(round(share * 100))


def pick_snapshots(rows: Sequence[SnapshotRow]) -> tuple[Optional[SnapshotRow], Optional[SnapshotRow]]:
    """``(newest, previous)``: the newest row, and the NEWEST row 7..9 days
    before it (``COVERAGE_COMPARE_DAYS`` .. + ``COVERAGE_PREVIOUS_TOLERANCE_DAYS``)."""
    if not rows:
        return None, None
    ordered = sorted(rows, key=lambda r: r.snapshot_day, reverse=True)
    newest = ordered[0]
    latest = newest.snapshot_day - timedelta(days=COVERAGE_COMPARE_DAYS)
    earliest = latest - timedelta(days=COVERAGE_PREVIOUS_TOLERANCE_DAYS)
    previous = next((r for r in ordered if earliest <= r.snapshot_day <= latest), None)
    return newest, previous


def judge_coverage(
    new: Optional[SnapshotRow], prev: Optional[SnapshotRow], *, today: date,
) -> tuple[bool, Optional[str], bool, Optional[str]]:
    """``(measured, reason_code, firing, severity)`` over two snapshots.

    Fires when the tests exercised fell by >= 10 % AND by >= 5 tests in a
    week; ``high`` from a 25 % drop.
    """
    if new is None:
        return False, "coverage_no_snapshot", False, None
    if new.snapshot_day < today - timedelta(days=COVERAGE_MAX_SNAPSHOT_AGE_DAYS):
        return False, "coverage_stale_snapshot", False, None
    if prev is None:
        return False, "coverage_no_previous", False, None
    if prev.automated_count < COVERAGE_MIN_EXERCISED:
        return False, "coverage_too_small", False, None
    drop = prev.automated_count - new.automated_count
    firing = (
        drop >= COVERAGE_MIN_DROP_TESTS
        and drop * 100 >= _percent(COVERAGE_MIN_DROP_SHARE) * prev.automated_count
    )
    if not firing:
        return True, None, False, None
    high = drop * 100 >= _percent(COVERAGE_HIGH_DROP_SHARE) * prev.automated_count
    return True, None, True, "high" if high else "medium"


def judge_perf_suite(
    newest_run_flags_it: bool, newest_measured: bool, ratio: Optional[float],
) -> tuple[bool, bool, Optional[str]]:
    """``(judged, firing, severity)`` for one suite, from its NEWEST run's stored
    findings. EXEC-05 already applied its rule (median of the last 5 runs >=
    1.5 x the median of the 20 before, same branch); this adds no floor."""
    if not newest_measured:
        return False, False, None
    if not newest_run_flags_it:
        return True, False, None
    high = ratio is not None and ratio >= PERF_SUITE_HIGH_RATIO
    return True, True, "high" if high else "medium"


def judge_perf_test(
    spike_runs: int, newest_spike_at: datetime, max_sigma: float, *, now: datetime,
) -> tuple[bool, Optional[str]]:
    """``(firing, severity)`` for one test: spiking in >= 3 distinct runs of the
    window, the newest within 48 h; ``high`` at sigma >= 6 or >= 5 runs."""
    recent = newest_spike_at >= now - timedelta(hours=PERF_TEST_RECENT_HOURS)
    if spike_runs < PERF_TEST_MIN_SPIKE_RUNS or not recent:
        return False, None
    high = max_sigma >= PERF_TEST_HIGH_SIGMA or spike_runs >= PERF_TEST_HIGH_RUNS
    return True, "high" if high else "medium"


def judge_cost(status: str) -> tuple[bool, bool, Optional[str]]:
    """``(measured, firing, severity)`` for the budget status of
    ``get_current_usage``: ``UNLIMITED`` is not measured, ``OK`` is judged and
    quiet, ``SOFT_WARN`` / ``CAPPED`` fire."""
    if status in COST_FIRING_STATUSES:
        return True, True, COST_FIRING_STATUSES[status]
    if status == "OK":
        return True, False, None
    return False, False, None


# ── perf_drift: EXEC-05's stored findings, aggregated ─────────────────────────


def _suite_entries(findings: Mapping[str, Any]) -> dict[str, dict]:
    """The stored ``suites[]`` of one run, by ``suite_key``."""
    out: dict[str, dict] = {}
    for entry in findings.get("suites") or []:
        if isinstance(entry, dict) and entry.get("suite_key"):
            out.setdefault(str(entry["suite_key"]), entry)
    return out


def _check_measured(findings: Mapping[str, Any], check: str) -> bool:
    part = findings.get(check)
    return bool(isinstance(part, dict) and part.get("measured"))


def _suite_findings(
    runs: Sequence[Any], newest_by_suite: Sequence[Any], window: dict,
) -> tuple[list[Finding], set[str]]:
    """Each suite judged on its NEWEST run of the window (statement 2), from
    that run's stored ``suites[]`` (statement 1)."""
    by_run = {row.id: row for row in runs}
    flagged_runs: dict[str, int] = {}
    for row in runs:
        for key in _suite_entries(row.duration_findings):
            flagged_runs[key] = flagged_runs.get(key, 0) + 1
    findings: list[Finding] = []
    judged: set[str] = set()
    for suite in newest_by_suite:
        run = by_run.get(suite.test_run_id)
        if run is None:
            continue
        stored = run.duration_findings
        entries = _suite_entries(stored)
        # ``suites[]`` is capped at EXEC-05's MAX_STORED: a suite missing from a
        # truncated list cannot be called quiet.
        complete = int(stored.get("suite_regression_count") or 0) <= len(stored.get("suites") or [])
        entry = entries.get(suite.suite_key)
        if entry is None and not complete:
            continue
        ok, firing, severity = judge_perf_suite(
            entry is not None, _check_measured(stored, "suite_check"), (entry or {}).get("ratio"),
        )
        if not ok:
            continue
        subject = f"suite:{suite.suite_key}"
        judged.add(subject)
        if not firing or entry is None or severity is None:
            continue
        label = str(entry.get("suite_name") or suite.suite_name)
        findings.append(Finding(subject, label, severity, {
            "suite_name": label,
            "suite_key": suite.suite_key,
            "ratio": entry.get("ratio"),
            "metric": entry.get("metric"),
            "recent_median_ms": entry.get("recent_median_ms"),
            "baseline_median_ms": entry.get("baseline_median_ms"),
            "recent_runs": entry.get("recent_runs"),
            "baseline_runs": entry.get("baseline_runs"),
            "flagged_runs": flagged_runs.get(suite.suite_key, 0),
            "newest_run_id": str(run.id),
            "newest_build": run.build_number,
            "branch": run.branch,
            "newest_at": _iso(run.created_at),
            **window,
        }))
    findings.sort(key=lambda f: -(f.evidence.get("ratio") or 0))
    return findings, judged


def _test_findings(runs: Sequence[Any], window: dict, *, now: datetime) -> list[Finding]:
    """Per fingerprint over the stored ``spikes[]`` of the window's runs
    (newest first, so the first sighting is the newest spike)."""
    seen: dict[str, dict] = {}
    for row in runs:
        for spike in row.duration_findings.get("spikes") or []:
            if not isinstance(spike, dict) or not spike.get("test_fingerprint"):
                continue
            fp = str(spike["test_fingerprint"])
            sigma = float(spike.get("sigma") or 0.0)
            agg = seen.get(fp)
            if agg is None:
                seen[fp] = {"runs": {row.id}, "max_sigma": sigma, "spike": spike, "run": row}
                continue
            agg["runs"].add(row.id)
            agg["max_sigma"] = max(agg["max_sigma"], sigma)
    findings: list[Finding] = []
    for fp, agg in seen.items():
        run, spike = agg["run"], agg["spike"]
        newest_at = run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=timezone.utc)
        firing, severity = judge_perf_test(len(agg["runs"]), newest_at, agg["max_sigma"], now=now)
        if not firing or severity is None:
            continue
        observed, mean = spike.get("observed_ms"), spike.get("baseline_mean_ms")
        pct = round((float(observed) / float(mean) - 1) * 100) if observed and mean else None
        label = str(spike.get("test_name") or fp)
        findings.append(Finding(f"test:{fp}", label, severity, {
            "test_name": label,
            "suite_name": spike.get("suite_name"),
            "test_fingerprint": fp,
            "spike_runs": len(agg["runs"]),
            "max_sigma": round(agg["max_sigma"], 2),
            "observed_ms": observed,
            "baseline_mean_ms": mean,
            "pct": pct,
            "newest_run_id": str(run.id),
            "newest_build": run.build_number,
            "newest_at": _iso(run.created_at),
            **window,
        }))
    findings.sort(key=lambda f: (-(f.evidence["max_sigma"] or 0), -f.evidence["spike_runs"]))
    return findings


async def detect_perf_drift(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> DetectorResult:
    """Suites and tests that EXEC-05 found slower, aggregated over 7 days.

    Statement 1 reads the window's runs with stored findings (newest first,
    at most ``PERF_MAX_RUNS_READ``); statement 2 each suite's newest run of the
    window (``ix_suite_executions_project_suite_run``). A suite is judged when
    that run's ``suite_check`` was measured, and fires when its ``suites[]``
    names it. Tests (only while ``perf_regression_detection`` is on, EXEC-05's
    own flag read) fire on 3+ spiking runs with one in the last 48 h; a stored
    finding lists spikes, not the tests judged quiet, so tests are never in
    ``measured_subjects`` and a test drift ends only ``stale``.
    """
    kind, window_key = "perf_drift", WINDOW_KEY["perf_drift"]
    since = now - timedelta(days=PERF_WINDOW_DAYS)
    rows = (
        await db.execute(
            select(TestRun.id, TestRun.build_number, TestRun.branch, TestRun.created_at, TestRun.duration_findings)
            .where(
                TestRun.project_id == project_id,
                TestRun.created_at >= since,
                TestRun.duration_findings.is_not(None),
            )
            .order_by(TestRun.created_at.desc(), TestRun.id.desc())
            .limit(PERF_MAX_RUNS_READ + 1)
        )
    ).all()
    truncated = len(rows) > PERF_MAX_RUNS_READ
    runs = [r for r in rows[:PERF_MAX_RUNS_READ] if isinstance(r.duration_findings, dict)]
    if not runs:
        return _not_measured(kind, window_key, "perf_no_findings")
    window = {"runs_read": len(runs), "truncated": truncated}

    newest_by_suite = (
        await db.execute(
            select(
                SuiteExecution.suite_key, SuiteExecution.suite_name,
                SuiteExecution.test_run_id, SuiteExecution.run_created_at,
            )
            .where(SuiteExecution.project_id == project_id, SuiteExecution.run_created_at >= since)
            .distinct(SuiteExecution.suite_key)
            .order_by(
                SuiteExecution.suite_key, SuiteExecution.run_created_at.desc(), SuiteExecution.test_run_id.desc(),
            )
        )
    ).all()
    suite_findings, judged = _suite_findings(runs, newest_by_suite, window)

    tests_on = await perf._feature_enabled(db, project_id=project_id)
    test_findings: list[Finding] = []
    tests_measured = False
    if tests_on:
        test_findings = _test_findings(runs, window, now=now)
        tests_measured = any(_check_measured(r.duration_findings, "test_check") for r in runs)
    measured = bool(judged) or tests_measured
    if not measured:
        return _not_measured(kind, window_key, "perf_no_findings" if tests_on else "perf_tests_flag_off")
    if truncated:
        _slog.info("insight_perf_runs_truncated", project_id=str(project_id), runs_read=len(runs))
    findings, firing, dropped = _capped([*suite_findings, *test_findings])
    return DetectorResult(
        kind=kind, window_key=window_key, measured=True,
        reason_code=None if tests_on else "perf_tests_flag_off",
        findings=findings, measured_subjects=frozenset(judged) - dropped, firing=firing,
    )


# ── flake_rising: the project's flaky rule over two adjacent weeks ────────────


def flake_statement() -> str:
    """ONE statement: flaky tests per effective suite in each of two adjacent
    windows (``w = 0`` this week, ``w = 1`` the week before) and the runs each
    suite had in each.

    The flaky rule is ``analytics_service.flaky_tests``' (``:178-219``), copied
    here because that function's window always ends now and cannot count the
    previous week: ``HAVING COUNT(*) >= 3``, a failure share ``BETWEEN 0.05 AND
    0.95``, and ``>= MIN_FLIPS_FOR_INTERMITTENCY`` status flips (``LAG`` over
    ``created_at, id``). The flips are counted per window and suite, so a test's
    run of failures last week cannot make this week's first pass a "flip". The
    suite is the effective suite (``analytics_scope.effective_suite_sql``), and
    a row without one is no subject. Human ``FLAKY_TEST`` triage carries no
    time of its own, so it cannot rise and is not counted.
    """
    eff = effective_suite_sql("tr", "tc")
    low, high = FLAKE_FAIL_SHARE
    return f"""
WITH obs AS (
  SELECT tch.test_fingerprint AS fp, tch.test_run_id AS run_id, tch.created_at, tch.id,
         LOWER({eff}) AS suite_key, {eff} AS suite_name,
         tc.test_name, CASE WHEN tch.created_at >= :mid THEN 0 ELSE 1 END AS w,
         CASE WHEN tch.status IN ('FAILED', 'BROKEN') THEN 1 ELSE 0 END AS is_failed
  FROM test_case_history tch
  JOIN test_cases tc ON tc.id = tch.test_case_id
  JOIN test_runs tr ON tr.id = tch.test_run_id
  WHERE tr.project_id = :p AND tch.created_at >= :start AND tch.created_at < :now),
seq AS (SELECT obs.*, LAG(is_failed) OVER (PARTITION BY w, suite_key, fp ORDER BY created_at, id) AS prev_failed
        FROM obs WHERE suite_key IS NOT NULL),
per_test AS (SELECT w, suite_key, fp, MAX(suite_name) AS suite_name, MAX(test_name) AS test_name, COUNT(*) AS n,
                    COUNT(*) FILTER (WHERE is_failed = 1) AS f,
                    COUNT(*) FILTER (WHERE prev_failed IS NOT NULL AND prev_failed <> is_failed) AS flips
             FROM seq GROUP BY w, suite_key, fp),
flaky AS (SELECT * FROM per_test
          WHERE n >= {FLAKE_MIN_OBSERVATIONS} AND f * 1.0 / n BETWEEN {low} AND {high}
            AND flips >= {MIN_FLIPS_FOR_INTERMITTENCY}),
runs AS (SELECT w, suite_key, MAX(suite_name) AS suite_name, COUNT(DISTINCT run_id) AS runs
         FROM obs WHERE suite_key IS NOT NULL GROUP BY w, suite_key)
SELECT r.suite_key, r.w, r.runs, r.suite_name, COUNT(fl.fp) AS flaky,
       (ARRAY_AGG(fl.test_name ORDER BY fl.f DESC, fl.test_name)
            FILTER (WHERE fl.fp IS NOT NULL))[1:{FLAKE_EXAMPLES}] AS examples
FROM runs r LEFT JOIN flaky fl ON fl.w = r.w AND fl.suite_key = r.suite_key
GROUP BY r.suite_key, r.w, r.runs, r.suite_name
"""


async def detect_flake_rising(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> DetectorResult:
    """Suites whose flaky-test count rose this week against the week before."""
    kind, window_key = "flake_rising", WINDOW_KEY["flake_rising"]
    mid = now - timedelta(days=FLAKE_WINDOW_DAYS)
    start = mid - timedelta(days=FLAKE_WINDOW_DAYS)
    params: dict[str, Any] = {"p": project_id, "start": start, "mid": mid, "now": now}
    rows = (await db.execute(scoped_text(flake_statement(), params), params)).all()
    suites: dict[str, dict] = {}
    for row in rows:
        entry = suites.setdefault(str(row.suite_key), {
            "suite_name": row.suite_name, "flaky": [0, 0], "runs": [0, 0], "examples": [],
        })
        w = int(row.w)
        entry["flaky"][w] = int(row.flaky or 0)
        entry["runs"][w] = int(row.runs or 0)
        if w == 0:
            entry["suite_name"] = row.suite_name or entry["suite_name"]
            entry["examples"] = [str(name) for name in (row.examples or [])][:FLAKE_EXAMPLES]
    bounds = {"start": _iso(start), "mid": _iso(mid), "end": _iso(now)}
    judged: set[str] = set()
    found: list[Finding] = []
    for key, entry in suites.items():
        (current, previous), (runs_current, runs_previous) = entry["flaky"], entry["runs"]
        measured, firing, severity = judge_flake(previous, current, runs_previous, runs_current)
        if not measured:
            continue
        subject = f"suite:{key}"
        judged.add(subject)
        if not firing or severity is None:
            continue
        label = str(entry["suite_name"] or key)
        found.append(Finding(subject, label, severity, {
            "suite_name": label, "previous": previous, "current": current,
            "runs_previous": runs_previous, "runs_current": runs_current,
            "examples": entry["examples"], "window": bounds,
        }))
    if not judged:
        return _not_measured(kind, window_key, "flake_few_runs")
    found.sort(key=lambda f: (-f.evidence["current"], f.evidence["previous"]))
    findings, firing_count, dropped = _capped(found)
    return DetectorResult(
        kind=kind, window_key=window_key, measured=True, reason_code=None,
        findings=findings, measured_subjects=frozenset(judged) - dropped, firing=firing_count,
    )


# ── coverage_drop: two stored TRND-07 snapshots ───────────────────────────────


async def detect_coverage_drop(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> DetectorResult:
    """The tests exercised (``automated_count``: distinct tests in the 30 days
    ending the snapshot day) of the newest snapshot against the one a week
    before. ONE statement, at most 12 rows (``uq_coverage_snapshots_project_day``)."""
    kind, window_key = "coverage_drop", WINDOW_KEY["coverage_drop"]
    today = utc_today(now)
    lookback = COVERAGE_MAX_SNAPSHOT_AGE_DAYS + COVERAGE_COMPARE_DAYS + COVERAGE_PREVIOUS_TOLERANCE_DAYS
    rows = (
        await db.execute(
            select(
                CoverageSnapshot.snapshot_day, CoverageSnapshot.total_tests,
                CoverageSnapshot.automated_count, CoverageSnapshot.total_suites,
            )
            .where(
                CoverageSnapshot.project_id == project_id,
                CoverageSnapshot.snapshot_day >= today - timedelta(days=lookback),
            )
            .order_by(CoverageSnapshot.snapshot_day.desc())
        )
    ).all()
    snapshots = [
        SnapshotRow(r.snapshot_day, int(r.total_tests or 0), int(r.automated_count or 0), int(r.total_suites or 0))
        for r in rows
    ]
    new, prev = pick_snapshots(snapshots)
    measured, reason, firing, severity = judge_coverage(new, prev, today=today)
    if not measured or new is None or prev is None:
        return _not_measured(kind, window_key, reason or "coverage_no_snapshot")
    findings: tuple[Finding, ...] = ()
    if firing and severity is not None:
        drop = prev.automated_count - new.automated_count
        findings = (Finding("project", COVERAGE_SUBJECT_LABEL, severity, {
            "previous_day": prev.snapshot_day.isoformat(),
            "current_day": new.snapshot_day.isoformat(),
            "previous": prev.automated_count,
            "current": new.automated_count,
            "pct": round(drop / prev.automated_count * 100),
            "previous_pct": exercised_pct(prev.total_tests, prev.automated_count),
            "current_pct": exercised_pct(new.total_tests, new.automated_count),
            "catalogue_previous": prev.total_tests,
            "catalogue_current": new.total_tests,
            "suites_previous": prev.total_suites,
            "suites_current": new.total_suites,
        }),)
    return DetectorResult(
        kind=kind, window_key=window_key, measured=True, reason_code=None,
        findings=findings, measured_subjects=frozenset({"project"}), firing=len(findings),
    )


# ── cost_budget: the budget state, reused ─────────────────────────────────────


async def detect_cost_budget(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> DetectorResult:
    """An insight while this month's LLM budget is at its soft warning or capped.

    ``get_current_usage`` is the billing page's own read (two selects, no
    write); ``check_and_apply_cap`` is never called. A quota with no hard cap
    reads ``OK`` there, but there is nothing to judge against: it is "no
    budget" here, like no quota at all. The window is the billing month, so
    the first evaluation of a new month clears the last month's open row.
    """
    kind = "cost_budget"
    period_start, _ = llm_cost_budget.current_period_bounds(now)
    window_key = "month:" + period_start.strftime("%Y-%m")
    if not await feature_flags.is_enabled("llm_cost_budget", db=db, project_id=project_id):
        return _not_measured(kind, window_key, "cost_flag_off")
    usage = await llm_cost_budget.get_current_usage(db, project_id, now=now)
    status = str(usage.get("status") or "UNLIMITED")
    if status == "OK" and not float(usage.get("hard_cap_usd") or 0) > 0:
        status = "UNLIMITED"
    measured, firing, severity = judge_cost(status)
    if not measured:
        return _not_measured(kind, window_key, "cost_no_budget")
    findings: tuple[Finding, ...] = ()
    if firing and severity is not None:
        findings = (Finding("budget", COST_SUBJECT_LABEL, severity, {
            "status": status,
            "total_cost_usd": usage.get("total_cost_usd"),
            "hard_cap_usd": usage.get("hard_cap_usd"),
            "utilization_pct": usage.get("utilization_pct"),
            "period_start": _iso(usage.get("period_start")),
            "period_end": _iso(usage.get("period_end")),
            "projected_period_cost_usd": usage.get("projected_period_cost_usd"),
        }),)
    return DetectorResult(
        kind=kind, window_key=window_key, measured=True, reason_code=None,
        findings=findings, measured_subjects=frozenset({"budget"}), firing=len(findings),
        clear_earlier_windows=True,
    )


#: The four detectors in ``INSIGHT_KINDS`` order (the beat's only caller).
DETECTORS: tuple[tuple[str, Callable[..., Awaitable[DetectorResult]]], ...] = (
    ("perf_drift", detect_perf_drift),
    ("flake_rising", detect_flake_rising),
    ("coverage_drop", detect_coverage_drop),
    ("cost_budget", detect_cost_budget),
)
