"""Duration regressions of a run (EXEC-05, absorbs GATE-07).

One evaluation per run answers "did anything get slower?" twice over and stores
the answer in ``test_runs.duration_findings`` (migration 0204), which the run
page (``GET /api/v1/runs/{run_id}/duration-regressions``), the release gate's
opt-in ``perf_regression`` policy rule and the "became slower" notifications
all read:

* **Per-test spikes** -- a PASSED test's duration against its Welford baseline
  (``perf_regression_service``): a spike is ``observed - mean >= 3 sigma`` on a
  baseline of at least 10 samples. Needs the ``perf_regression_detection``
  flag for the run's project.
* **Suite regressions** -- a suite's median over the last
  ``SUITE_RECENT_RUNS`` (5) runs, this one included, against the median of the
  ``SUITE_BASELINE_RUNS`` (20) before them, same branch, from EXEC-02's
  ``suite_executions``: a ratio of ``SUITE_RATIO`` (1.5) or more. Wall clock
  when every row in the window has one, summed test time otherwise (never a
  mix: a format that starts reporting timestamps would read as a speed-up).
  Needs no flag and no LLM.

The stored document (``FINDINGS_SCHEMA_VERSION``)::

    {measured, reason, spikes[], suites[], spike_count, suite_regression_count,
     test_check{measured, reason, ...}, suite_check{measured, reason, ...},
     notified{tests[], suites[]}, evaluated_at, schema_version,
     baseline_fed_through?}

``measured`` is false only when NEITHER check could be made, with the reason
("not enough history (4 of 10)"); an unmeasured check is never shown as "no
regressions" (absence is not health). ``spikes`` and ``suites`` are bounded
(``MAX_STORED``), the counts are not.

Order inside :func:`evaluate_duration_regressions`, which is the finalize step
(the pipeline wires it into its own isolated session and commits):

1. lock the run row, so a re-finalize waits and then finds the stored findings;
2. judge the run's tests against their baselines BEFORE feeding it (a baseline
   holding the outlier itself is diluted by it);
3. feed the run's rows into the baselines once (``feed_run_observations``),
   recording ``baseline_fed_through`` so the nightly sweep skips them;
4. suite regressions;
5. on the FIRST evaluation of the run only: stage the ``test.newly_slow`` /
   ``suite.newly_slow`` notification for findings that are new -- the test or
   suite was not already slow on its previous run -- and that nobody was told
   about in the last ``NOTIFY_COOLDOWN`` (per test / suite);
6. write the document. Stage-only: nothing here commits.
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    NotificationEventType,
    NotificationLog,
    Project,
    SuiteExecution,
    TestRun,
)
from app.services import perf_regression_service as perf

logger = structlog.get_logger("services.duration_regression")

FINDINGS_SCHEMA_VERSION = 1

#: Spikes and suites kept in the stored document (the counts are not capped).
MAX_STORED = 50

#: Suite regression rule (documented constants; the policy rule can tighten
#: the ratio, never loosen it below what was detected).
SUITE_RECENT_RUNS = 5
SUITE_BASELINE_RUNS = 20
SUITE_MIN_BASELINE_RUNS = 10
SUITE_RATIO = 1.5

#: A test or suite is announced at most once per this window.
NOTIFY_COOLDOWN = timedelta(hours=24)

#: Lines in one notification before "…and K more".
MAX_NOTIFICATION_LINES = 10

SLOW_EVENTS = (
    NotificationEventType.TEST_NEWLY_SLOW,
    NotificationEventType.SUITE_NEWLY_SLOW,
)

#: Keys of the stored document that are bookkeeping, not findings.
_INTERNAL_KEYS = ("baseline_fed_through",)

_FLAG_OFF_REASON = (
    "duration regression detection is off for this project "
    "(feature flag perf_regression_detection)"
)
_NOT_EVALUATED_REASON = "not evaluated for this run yet"


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _history_reason(have: int, need: int) -> str:
    return f"not enough history ({have} of {need})"


# ── Suite regressions (pure) ──────────────────────────────────────────────────


@dataclass
class SuiteVerdict:
    """One suite judged over its history (newest first, this run first)."""
    measured: bool
    history: int
    metric: Optional[str] = None
    ratio: Optional[float] = None
    recent_median_ms: Optional[float] = None
    baseline_median_ms: Optional[float] = None
    baseline_runs: int = 0
    observed_ms: Optional[int] = None
    regression: bool = False
    was_regression_before: bool = False


def _window_ratio(values: list[int], start: int) -> Optional[tuple[float, float, float, int]]:
    """``(ratio, recent_median, baseline_median, baseline_runs)`` for the
    window whose newest run is ``values[start]``, or None without enough
    history."""
    recent = values[start:start + SUITE_RECENT_RUNS]
    baseline = values[start + SUITE_RECENT_RUNS:start + SUITE_RECENT_RUNS + SUITE_BASELINE_RUNS]
    if len(recent) < SUITE_RECENT_RUNS or len(baseline) < SUITE_MIN_BASELINE_RUNS:
        return None
    recent_median = float(statistics.median(recent))
    baseline_median = float(statistics.median(baseline))
    if baseline_median <= 0:
        return None
    return recent_median / baseline_median, recent_median, baseline_median, len(baseline)


def judge_suite_history(rows: list[dict[str, Any]], *, ratio: float = SUITE_RATIO) -> SuiteVerdict:
    """Judge one suite. ``rows`` are its executions, newest first, the run
    being judged first, each ``{"wall_ms", "summed_ms"}``.

    The metric is the wall clock when every row of the window carries one,
    else the summed test time; rows without the chosen metric are left out.
    ``was_regression_before`` applies the same rule one run earlier, so a
    suite that stays slow is announced once, on the run it became slow.
    """
    window = rows[: 1 + SUITE_RECENT_RUNS + SUITE_BASELINE_RUNS]
    metric = "wall" if window and all((r.get("wall_ms") or 0) > 0 for r in window) else "summed"
    field_name = "wall_ms" if metric == "wall" else "summed_ms"
    if not rows or not (rows[0].get(field_name) or 0) > 0:
        return SuiteVerdict(measured=False, history=0, metric=metric)
    values = [int(r[field_name]) for r in rows if (r.get(field_name) or 0) > 0]
    needed = SUITE_RECENT_RUNS + SUITE_MIN_BASELINE_RUNS
    now = _window_ratio(values, 0)
    if now is None:
        return SuiteVerdict(measured=False, history=min(len(values), needed), metric=metric)
    current_ratio, recent_median, baseline_median, baseline_runs = now
    before = _window_ratio(values, 1)
    return SuiteVerdict(
        measured=True,
        history=len(values),
        metric=metric,
        ratio=round(current_ratio, 2),
        recent_median_ms=round(recent_median, 1),
        baseline_median_ms=round(baseline_median, 1),
        baseline_runs=baseline_runs,
        observed_ms=values[0],
        regression=current_ratio >= ratio,
        was_regression_before=bool(before and before[0] >= ratio),
    )


async def _suite_findings(
    db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID, branch: Optional[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``(regressions, suite_check)`` for the run's suites."""
    needed = SUITE_RECENT_RUNS + SUITE_MIN_BASELINE_RUNS
    check: dict[str, Any] = {
        "measured": False,
        "reason": None,
        "evaluated": 0,
        "history": 0,
        "required_history": needed,
        "recent_runs": SUITE_RECENT_RUNS,
        "baseline_runs": SUITE_BASELINE_RUNS,
        "ratio": SUITE_RATIO,
    }
    own = (
        await db.execute(
            select(
                SuiteExecution.suite_key,
                SuiteExecution.suite_name,
                SuiteExecution.wall_ms,
                SuiteExecution.summed_ms,
                SuiteExecution.run_created_at,
            ).where(SuiteExecution.test_run_id == run_id)
        )
    ).all()
    if not own:
        check["reason"] = "no suite timing was recorded for this run"
        return [], check

    keys = [row.suite_key for row in own]
    cutoff = max(row.run_created_at for row in own)
    depth = SUITE_RECENT_RUNS + SUITE_BASELINE_RUNS  # rows before this run: one window + one step back
    position = (
        func.row_number()
        .over(
            partition_by=SuiteExecution.suite_key,
            order_by=(SuiteExecution.run_created_at.desc(), SuiteExecution.test_run_id.desc()),
        )
        .label("position")
    )
    history = (
        select(
            SuiteExecution.suite_key,
            SuiteExecution.wall_ms,
            SuiteExecution.summed_ms,
            position,
        )
        .where(
            SuiteExecution.project_id == project_id,
            SuiteExecution.suite_key.in_(keys),
            SuiteExecution.test_run_id != run_id,
            SuiteExecution.run_created_at <= cutoff,
            SuiteExecution.branch.is_not_distinct_from(branch),
        )
        .subquery()
    )
    earlier = (
        await db.execute(
            select(history.c.suite_key, history.c.wall_ms, history.c.summed_ms)
            .where(history.c.position <= depth)
            .order_by(history.c.suite_key, history.c.position)
        )
    ).all()
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in earlier:
        by_key.setdefault(row.suite_key, []).append(
            {"wall_ms": row.wall_ms, "summed_ms": row.summed_ms}
        )

    regressions: list[dict[str, Any]] = []
    deepest = 0
    for row in own:
        rows = [{"wall_ms": row.wall_ms, "summed_ms": row.summed_ms}, *by_key.get(row.suite_key, [])]
        verdict = judge_suite_history(rows)
        deepest = max(deepest, verdict.history)
        if not verdict.measured:
            continue
        check["evaluated"] += 1
        if not verdict.regression:
            continue
        regressions.append({
            "suite_name": row.suite_name,
            "suite_key": row.suite_key,
            "metric": verdict.metric,
            "ratio": verdict.ratio,
            "recent_median_ms": verdict.recent_median_ms,
            "baseline_median_ms": verdict.baseline_median_ms,
            "recent_runs": SUITE_RECENT_RUNS,
            "baseline_runs": verdict.baseline_runs,
            "observed_ms": verdict.observed_ms,
            "was_slow_before": verdict.was_regression_before,
        })
    check["history"] = deepest
    if check["evaluated"]:
        check["measured"] = True
    else:
        check["reason"] = _history_reason(min(deepest, needed), needed)
    regressions.sort(key=lambda s: -(s["ratio"] or 0))
    return regressions, check


# ── Per-test spikes ───────────────────────────────────────────────────────────


def _test_check(
    assessment: Optional[perf.RunDurationAssessment], *, flag_on: bool,
) -> dict[str, Any]:
    needed = perf._MIN_SAMPLES_FOR_SPIKE
    check: dict[str, Any] = {
        "measured": False,
        "reason": None,
        "considered": 0,
        "judged": 0,
        "history": 0,
        "required_history": needed,
        "sigma": perf._SPIKE_SIGMA,
    }
    if not flag_on:
        check["reason"] = _FLAG_OFF_REASON
        return check
    if assessment is None:
        check["reason"] = _NOT_EVALUATED_REASON
        return check
    check["considered"] = assessment.tests_considered
    check["judged"] = assessment.tests_judged
    check["history"] = assessment.max_samples
    if not assessment.tests_considered:
        check["reason"] = "no passed test in this run reported a duration"
    elif not assessment.tests_judged:
        check["reason"] = _history_reason(min(assessment.max_samples, needed), needed)
    else:
        check["measured"] = True
    return check


def _assemble(
    *,
    spikes: list[dict[str, Any]],
    spike_count: int,
    test_check: dict[str, Any],
    suites: list[dict[str, Any]],
    suite_check: dict[str, Any],
    evaluated_at: Optional[datetime],
) -> dict[str, Any]:
    measured = bool(test_check["measured"] or suite_check["measured"])
    reason = None
    if not measured:
        reason = test_check.get("reason") or suite_check.get("reason")
    return {
        "schema_version": FINDINGS_SCHEMA_VERSION,
        "measured": measured,
        "reason": reason,
        "spikes": spikes[:MAX_STORED],
        "spike_count": spike_count,
        "suites": suites[:MAX_STORED],
        "suite_regression_count": len(suites),
        "test_check": test_check,
        "suite_check": suite_check,
        "notified": {"tests": [], "suites": []},
        "evaluated_at": _iso(evaluated_at),
    }


async def _flag_on(db: AsyncSession, project_id: uuid.UUID) -> bool:
    return await perf._feature_enabled(db, project_id=project_id)


# ── Notifications ─────────────────────────────────────────────────────────────


def _test_key(spike: dict[str, Any]) -> str:
    return f"test:{spike['test_fingerprint']}"


def _suite_key(suite: dict[str, Any]) -> str:
    return f"suite:{suite['suite_key']}"


def _ms(value: Optional[float]) -> str:
    if value is None:
        return "?"
    if abs(value) >= 1000:
        return f"{value / 1000:.1f} s"
    return f"{int(round(value))} ms"


def render_slow_message(
    *,
    project_name: str,
    build_number: str,
    tests: list[dict[str, Any]],
    suites: list[dict[str, Any]],
    include_tests: bool = True,
) -> tuple[str, str]:
    """``(title, body)`` of one "became slower" message for a run."""
    lines: list[str] = []
    shown_tests = tests if include_tests else []
    for spike in shown_tests:
        lines.append(
            f"🐢 Slower than baseline: {spike.get('test_name') or spike['test_fingerprint']} "
            f"+{_ms(spike.get('delta_ms'))} ({spike.get('sigma')}σ; "
            f"{_ms(spike.get('observed_ms'))} vs a mean of {_ms(spike.get('baseline_mean_ms'))})"
        )
    for suite in suites:
        lines.append(
            f"🐢 Suite {suite.get('suite_name') or suite['suite_key']}: {suite.get('ratio')}× slower "
            f"than its previous {suite.get('baseline_runs')} runs "
            f"(median of the last {suite.get('recent_runs')}: {_ms(suite.get('recent_median_ms'))})"
        )
    total = len(lines)
    if total > MAX_NOTIFICATION_LINES:
        lines = lines[:MAX_NOTIFICATION_LINES] + [f"…and {total - MAX_NOTIFICATION_LINES} more"]
    parts = []
    if shown_tests:
        parts.append(f"{len(shown_tests)} test{'s' if len(shown_tests) != 1 else ''}")
    if suites:
        parts.append(f"{len(suites)} suite{'s' if len(suites) != 1 else ''}")
    subject = " and ".join(parts) or "nothing"
    title = f"🐢 {subject} became slower — {project_name} build #{build_number}"
    return title, "\n".join(lines)


async def _recently_notified(
    db: AsyncSession, project_id: uuid.UUID, now: datetime,
) -> set[str]:
    """Test / suite keys a "became slower" message named in the cooldown."""
    rows = (
        await db.execute(
            select(NotificationLog.delivery_metadata).where(
                NotificationLog.project_id == project_id,
                NotificationLog.event_type.in_([e.value for e in SLOW_EVENTS]),
                NotificationLog.created_at >= now - NOTIFY_COOLDOWN,
            ).limit(1000)
        )
    ).all()
    keys: set[str] = set()
    for row in rows:
        metadata = row[0]
        if isinstance(metadata, dict):
            keys.update(str(k) for k in metadata.get("duration_keys") or [])
    return keys


def pick_new_findings(
    spikes: list[dict[str, Any]],
    suites: list[dict[str, Any]],
    recently_notified: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The findings worth a message: new on this run (not already slow on the
    previous one) and not announced within the cooldown."""
    new_tests = [
        s for s in spikes
        if not s.get("was_slow_before") and _test_key(s) not in recently_notified
    ]
    new_suites = [
        s for s in suites
        if not s.get("was_slow_before") and _suite_key(s) not in recently_notified
    ]
    return new_tests, new_suites


async def _stage_slow_notifications(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    build_number: str,
    new_tests: list[dict[str, Any]],
    new_suites: list[dict[str, Any]],
) -> None:
    """Stage the run's one "became slower" message per subscribed channel on
    the caller's transaction (the durable relay delivers it).

    A preference subscribed to ``test.newly_slow`` gets the tests and the
    suites (the settings page offers one "A test or suite became slower"
    toggle for both events); one subscribed to ``suite.newly_slow`` alone
    gets the suites.
    """
    if not new_tests and not new_suites:
        return
    from app.core.config import settings
    from app.services.notification.manager import stage_scoped_preference_deliveries

    project_name = (
        await db.execute(select(Project.name).where(Project.id == project_id))
    ).scalar_one_or_none() or str(project_id)
    events: list[NotificationEventType] = []
    if new_tests:
        events.append(NotificationEventType.TEST_NEWLY_SLOW)
    if new_suites:
        events.append(NotificationEventType.SUITE_NEWLY_SLOW)

    def build(event: NotificationEventType) -> tuple[str, str]:
        return render_slow_message(
            project_name=project_name,
            build_number=build_number,
            tests=new_tests,
            suites=new_suites,
            include_tests=event == NotificationEventType.TEST_NEWLY_SLOW,
        )

    metadata = {
        "project_name": project_name,
        "build_number": build_number,
        "dashboard_url": f"{settings.public_base_url}/runs/{run_id}?tab=changes",
        "duration_keys": [_test_key(s) for s in new_tests] + [_suite_key(s) for s in new_suites],
    }
    await stage_scoped_preference_deliveries(
        db,
        project_id=project_id,
        run_id=run_id,
        events=events,
        build_title_fn=build,
        metadata=metadata,
        delivery_scope=f"duration-run:{run_id}",
    )


# ── Entry points ──────────────────────────────────────────────────────────────


async def evaluate_duration_regressions(
    db: AsyncSession, run_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """The finalize step: evaluate, feed the baselines once, notify, store.

    Returns the public findings (``public_findings``), or None for an unknown
    run. Stage-only (transaction ratchet): the caller commits, in its own
    session, right after (the run and cursor row locks are held until then).
    Idempotent: a second evaluation of the same run neither feeds the
    baselines again nor notifies again.
    """
    run = (
        await db.execute(
            select(
                TestRun.id,
                TestRun.project_id,
                TestRun.branch,
                TestRun.build_number,
                TestRun.duration_findings,
            )
            .where(TestRun.id == run_id)
            .with_for_update(key_share=True)
        )
    ).one_or_none()
    if run is None:
        return None
    project_id = run.project_id
    previous: dict[str, Any] = dict(run.duration_findings or {})
    first_evaluation = not previous.get("evaluated_at")
    fed_through = previous.get("baseline_fed_through")
    now = datetime.now(timezone.utc)

    flag_on = await _flag_on(db, project_id)
    assessment: Optional[perf.RunDurationAssessment] = None
    reused_tests = False
    if flag_on:
        if fed_through and isinstance(previous.get("test_check"), dict):
            # Already judged and fed: the baseline now holds this run, so a
            # re-judgement would be diluted by the run itself. Keep the first.
            reused_tests = True
        else:
            assessment = await perf.assess_run_durations(db, project_id, run_id)
            feed = await perf.feed_run_observations(db, project_id, run_id)
            if feed.fed_through is not None:
                fed_through = _iso(feed.fed_through)

    suites, suite_check = await _suite_findings(db, run_id, project_id, run.branch)

    if reused_tests:
        spikes = list(previous.get("spikes") or [])
        spike_count = int(previous.get("spike_count") or len(spikes))
        test_check = dict(previous.get("test_check") or {})
    else:
        spikes = list(assessment.spikes) if assessment else []
        spike_count = len(spikes)
        test_check = _test_check(assessment, flag_on=flag_on)

    findings = _assemble(
        spikes=spikes,
        spike_count=spike_count,
        test_check=test_check,
        suites=suites,
        suite_check=suite_check,
        evaluated_at=now if first_evaluation else datetime.fromisoformat(previous["evaluated_at"]),
    )
    if fed_through:
        findings["baseline_fed_through"] = fed_through

    if first_evaluation:
        recently = await _recently_notified(db, project_id, now)
        new_tests, new_suites = pick_new_findings(findings["spikes"], findings["suites"], recently)
        await _stage_slow_notifications(
            db,
            run_id=run_id,
            project_id=project_id,
            build_number=run.build_number or "",
            new_tests=new_tests,
            new_suites=new_suites,
        )
        findings["notified"] = {
            "tests": [s["test_fingerprint"] for s in new_tests],
            "suites": [s["suite_key"] for s in new_suites],
        }
    else:
        findings["notified"] = previous.get("notified") or {"tests": [], "suites": []}
    _mark_new(findings)

    await db.execute(
        update(TestRun).where(TestRun.id == run_id).values(duration_findings=findings)
    )
    logger.info(
        "duration_regressions_evaluated",
        run_id=str(run_id),
        project_id=str(project_id),
        measured=findings["measured"],
        spikes=findings["spike_count"],
        suites=findings["suite_regression_count"],
        # Announced, not delivered: a message is staged only per subscribed channel, and a
        # project with none still announces (the stored ``notified`` lists the same keys).
        announced_tests=len(findings["notified"]["tests"]),
        announced_suites=len(findings["notified"]["suites"]),
        first_evaluation=first_evaluation,
    )
    return public_findings(findings, stored=True)


def _mark_new(findings: dict[str, Any]) -> None:
    """Tag each finding with whether it is new on this run."""
    for spike in findings.get("spikes") or []:
        spike["new"] = not spike.get("was_slow_before")
    for suite in findings.get("suites") or []:
        suite["new"] = not suite.get("was_slow_before")


def public_findings(findings: dict[str, Any], *, stored: bool) -> dict[str, Any]:
    """The document as the API serves it: bookkeeping keys removed."""
    out = {k: v for k, v in findings.items() if k not in _INTERNAL_KEYS}
    out["stored"] = stored
    return out


async def get_duration_regressions(
    db: AsyncSession, run_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """The run page's read. The stored findings when the run was evaluated;
    otherwise the same checks computed now, read-only (no baseline feed, no
    notification, nothing written: a GET must not mutate), and marked
    ``stored: false``. None for an unknown run (the route answers 404)."""
    run = (
        await db.execute(
            select(
                TestRun.id, TestRun.project_id, TestRun.branch, TestRun.duration_findings,
            ).where(TestRun.id == run_id)
        )
    ).one_or_none()
    if run is None:
        return None
    if run.duration_findings:
        stored = dict(run.duration_findings)
        _mark_new(stored)
        return public_findings(stored, stored=True)

    flag_on = await _flag_on(db, run.project_id)
    assessment = (
        await perf.assess_run_durations(db, run.project_id, run_id) if flag_on else None
    )
    suites, suite_check = await _suite_findings(db, run_id, run.project_id, run.branch)
    spikes = list(assessment.spikes) if assessment else []
    findings = _assemble(
        spikes=spikes,
        spike_count=len(spikes),
        test_check=_test_check(assessment, flag_on=flag_on),
        suites=suites,
        suite_check=suite_check,
        evaluated_at=None,
    )
    _mark_new(findings)
    return public_findings(findings, stored=False)


def policy_summary(findings: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The compact view the release gate's ``perf_regression`` rule reads
    (frozen into the decision's replay context, so it stays small)."""
    if not isinstance(findings, dict):
        return None
    return {
        "measured": bool(findings.get("measured")),
        "reason": findings.get("reason"),
        "spike_count": int(findings.get("spike_count") or 0),
        "spikes": [
            {
                "test_name": s.get("test_name"),
                "suite_name": s.get("suite_name"),
                "sigma": s.get("sigma"),
                "observed_ms": s.get("observed_ms"),
                "baseline_mean_ms": s.get("baseline_mean_ms"),
            }
            for s in (findings.get("spikes") or [])[:MAX_STORED]
        ],
        "suite_regression_count": int(findings.get("suite_regression_count") or 0),
        "suites": [
            {"suite_name": s.get("suite_name"), "ratio": s.get("ratio")}
            for s in (findings.get("suites") or [])[:MAX_STORED]
        ],
    }


async def load_policy_summary(
    db: AsyncSession, run_id: Any,
) -> Optional[dict[str, Any]]:
    """``policy_summary`` of a run's STORED findings; None when the run was not
    evaluated (the rule then reports "not measured" and never blocks)."""
    try:
        rid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        return None
    findings = (
        await db.execute(select(TestRun.duration_findings).where(TestRun.id == rid))
    ).scalar_one_or_none()
    return policy_summary(findings)
