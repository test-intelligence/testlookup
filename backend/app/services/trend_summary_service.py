"""W08 TRND-06: the Trends page's verdict, confidence score, variance and run gap, server-side.

Until W08 the page derived these in the browser (``computeConfidenceModel`` +
``pickVerdict``, ``TrendsPage.tsx``), so nothing else -- the PDF, the email, a
digest, an MCP tool -- could state the verdict the reader saw. This module is
that derivation, ported LITERALLY over the page's own two reads
(:func:`trend_summary`): ``metrics_service.get_trend_data`` (``/metrics/trends``)
and ``analytics_service.coverage_stats`` (``/analytics/coverage``) for the same
scope. No third read: the trends report reuses :func:`summarize` over the same
two answers.

Port rules that matter (each pinned by ``tests/test_trend_summary.py::CASES``):

* rounding is :func:`half_up` (JS ``Math.round``: ``.5`` goes up), never
  Python's banker's ``round()``, for the composite score;
* the variance is the POPULATION standard deviation over the ACTIVE days, and
  a day with only skipped tests counts as 0 %;
* the gap is the FIRST longest run of empty days (a strictly longer run
  replaces it); ``expected_runs_missed`` = its length, i.e. the silent days
  -- NOT an expectation (kept on the wire for the CLI / MCP / the SPA
  parser; the Trends callout states expected-run misses only from a
  configured TRND-03 cadence, ``/run-cadences/status``); ``ongoing`` = it
  ends today;
* the arithmetic is done in the page's order so the floats are the page's.

``null`` is not measured, never 0: ``pass_rate`` with nothing evaluated,
``variance.stddev_pp`` below two active days, ``gap`` without one.
:func:`summarize` is pure (no database, no clock of its own).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Final, Mapping, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics_scope import AnalyticsScope

TREND_VERDICTS: Final = ("HEALTHY", "MIXED", "INSUFFICIENT", "DECLINING", "PENDING")
#: ``(id, label, weight)``, the page's order.
TREND_DIMENSIONS: Final = (
    ("data_coverage", "Data coverage", 0.40),
    ("sample_size", "Sample size", 0.25),
    ("variance_stability", "Variance stability", 0.20),
    ("tag_quality", "Tag quality", 0.15),
)
#: Suite names that mean "no suite label", compared after ``strip().lower()``
#: (``TrendsPage.tsx``'s untagged share; ``Unknown Suite`` is what
#: ``coverage_stats`` names the unlabelled group).
UNTAGGED_SUITE_NAMES: Final = ("", "unknown suite", "unknown", "untagged")
#: Executions at which the sample-size dimension is full.
SAMPLE_FULL_EXECUTIONS: Final = 100
#: A standard deviation of this many percentage points scores 0 stability.
VARIANCE_ZERO_AT_PP: Final = 30.0
#: Stability of a single active day (a warning-level proxy).
ONE_ACTIVE_DAY_VARIANCE: Final = 45
#: A recent half this many points below the prior half is DECLINING.
DECLINE_PP: Final = 10.0
#: Active days needed for a direction.
MIN_TREND_DAYS: Final = 3
INSUFFICIENT_BELOW: Final = 33
HEALTHY_FROM: Final = 66

NO_EXECUTIONS_REASON: Final = "No executions in this window."


def half_up(x: float) -> int:
    """``floor(x + 0.5)``: JS ``Math.round`` (``.5`` rounds up)."""
    return int(math.floor(x + 0.5))


def _int(row: Optional[Mapping[str, Any]], key: str) -> int:
    if row is None:
        return 0
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _is_untagged(name: Any) -> bool:
    return str(name or "").strip().lower() in UNTAGGED_SUITE_NAMES


def untagged_share(suites: Sequence[Mapping[str, Any]]) -> float:
    """Share of the suites' executions (passed + failed + skipped) under no
    suite label; 0 when the suites hold none."""
    total = 0
    untagged = 0
    for suite in suites:
        n = _int(suite, "passed") + _int(suite, "failed") + _int(suite, "skipped")
        total += n
        if _is_untagged(suite.get("suite_name")):
            untagged += n
    return untagged / total if total else 0.0


@dataclass(frozen=True)
class _Cell:
    """One UTC day of the window (the page's ``CadenceCell``)."""

    iso: str
    executions: int
    passed: int
    failed: int
    broken: int


def _band(score: int) -> str:
    if score >= HEALTHY_FROM:
        return "High"
    if score >= INSUFFICIENT_BELOW:
        return "Moderate"
    return "Low"


def summarize(
    trend_rows: Sequence[Mapping[str, Any]],
    suites: Sequence[Mapping[str, Any]],
    *,
    days: int,
    today: date,
) -> dict:
    """The trend summary of ``days`` UTC days ending ``today`` (the §1.3 body without ``meta``)."""
    by_date: dict[str, Mapping[str, Any]] = {}
    for trend_row in trend_rows:
        by_date[str(trend_row.get("date") or "")[:10]] = trend_row

    cells: list[_Cell] = []
    for i in range(days):
        iso = (today - timedelta(days=days - 1 - i)).isoformat()
        row = by_date.get(iso)
        day_passed, day_failed = _int(row, "passed"), _int(row, "failed")
        day_skipped, day_broken = _int(row, "skipped"), _int(row, "broken")
        cells.append(_Cell(
            iso=iso,
            executions=day_passed + day_failed + day_skipped + day_broken,
            passed=day_passed,
            failed=day_failed,
            broken=day_broken,
        ))
    active = [cell for cell in cells if cell.executions > 0]
    days_with_runs = len(active)
    empty_days = len(cells) - days_with_runs

    passed = sum(_int(row, "passed") for row in trend_rows)
    failed = sum(_int(row, "failed") for row in trend_rows)
    skipped = sum(_int(row, "skipped") for row in trend_rows)
    broken = sum(_int(row, "broken") for row in trend_rows)
    total = passed + failed + skipped + broken
    evaluated = passed + failed + broken
    pass_rate = round(passed / evaluated * 100, 1) if evaluated > 0 else None

    per_day: list[float] = []
    for cell in active:
        day_evaluated = cell.passed + cell.failed + cell.broken
        per_day.append(cell.passed / day_evaluated * 100 if day_evaluated > 0 else 0)

    stddev: Optional[float] = None
    if not active:
        stability: float = 0
    elif len(active) == 1:
        stability = ONE_ACTIVE_DAY_VARIANCE
    else:
        mean = 0.0
        for x in per_day:
            mean += x
        mean = mean / len(per_day)
        acc = 0.0
        for x in per_day:
            acc += (x - mean) ** 2
        sd = math.sqrt(acc / len(per_day))
        stddev = sd
        stability = max(0, min(100, 100 - (sd / VARIANCE_ZERO_AT_PP) * 100))

    share = untagged_share(suites)
    scores = {
        "data_coverage": min(100, (days_with_runs / days) * 100) if days > 0 else 0,
        "sample_size": min(100, (total / SAMPLE_FULL_EXECUTIONS) * 100),
        "variance_stability": stability,
        "tag_quality": max(0, 100 - share * 100),
    }
    weighted = 0.0
    for dimension_id, _label, weight in TREND_DIMENSIONS:
        weighted = weighted + scores[dimension_id] * weight
    score = half_up(weighted)

    isos = sorted(cell.iso for cell in active)
    last_run_day = isos[-1] if isos else None
    previous_run_day = isos[-2] if len(isos) > 1 else None

    gap = None
    if 0 < days_with_runs < days:
        longest = 0
        gap_start: Optional[str] = None
        gap_end: Optional[str] = None
        run_start = -1
        run_len = 0
        for i, cell in enumerate(cells):
            if cell.executions == 0:
                if run_start == -1:
                    run_start = i
                run_len += 1
            else:
                if run_len > longest:
                    longest = run_len
                    gap_start = cells[run_start].iso if run_start >= 0 else None
                    gap_end = cells[i - 1].iso if i > 0 else None
                run_start = -1
                run_len = 0
        if run_len > longest:
            longest = run_len
            gap_start = cells[run_start].iso if run_start >= 0 else None
            gap_end = cells[-1].iso
        gap = {
            "silent_days": longest,
            "start": gap_start,
            "end": gap_end,
            "ongoing": gap_end == today.isoformat(),
            "expected_runs_missed": longest,
        }

    if total == 0:
        verdict = "PENDING"
    elif score < INSUFFICIENT_BELOW:
        verdict = "INSUFFICIENT"
    elif days_with_runs < MIN_TREND_DAYS:
        verdict = "INSUFFICIENT"
    else:
        half = max(1, len(per_day) // 2)
        recent = sum(per_day[-half:]) / half
        prior = sum(per_day[:half]) / half
        if recent < prior - DECLINE_PP:
            verdict = "DECLINING"
        elif score >= HEALTHY_FROM:
            verdict = "HEALTHY"
        else:
            verdict = "MIXED"

    measured = total > 0
    return {
        "window_days": days,
        "today": today.isoformat(),
        "measured": measured,
        "reason": None if measured else NO_EXECUTIONS_REASON,
        "days_with_runs": days_with_runs,
        "empty_days": empty_days,
        "active_day_share": round(days_with_runs / days, 4) if days > 0 else 0.0,
        "executions": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "broken": broken,
            "skipped": skipped,
            "evaluated": evaluated,
        },
        "pass_rate": pass_rate,
        "variance": {
            "stddev_pp": round(stddev, 2) if stddev is not None else None,
            "stability": round(float(stability), 2),
        },
        "gap": gap,
        "last_run_day": last_run_day,
        "previous_run_day": previous_run_day,
        "untagged_share": round(share, 4),
        "confidence": {
            "score": score,
            "dimensions": [
                {"id": dimension_id, "label": label, "score": round(float(scores[dimension_id]), 2), "weight": weight}
                for dimension_id, label, weight in TREND_DIMENSIONS
            ],
            "verdict": verdict,
            "band": _band(score),
        },
    }


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


async def trend_summary(db: AsyncSession, scope: AnalyticsScope, *, today: Optional[date] = None) -> dict:
    """:func:`summarize` over the page's two reads for ``scope``, verbatim.

    A denied scope (the route's ``on_denied="empty"``) reads nothing and is the
    empty, ``measured: false`` summary.
    """
    day = today or utc_today()
    if scope.denied:
        return summarize([], [], days=scope.window_days, today=day)
    from app.services import analytics_service, metrics_service  # noqa: PLC0415

    trend = await metrics_service.get_trend_data(
        db, scope.project, scope.window_days,
        suite_name=scope.suite_arg, release_id=scope.release_arg,
    )
    coverage = await analytics_service.coverage_stats(
        db, scope.project, scope.window_days, scope.suite_arg,
        scope.allowed_project_ids, scope.release_arg,
    )
    return summarize(trend, coverage.get("suites") or [], days=scope.window_days, today=day)
