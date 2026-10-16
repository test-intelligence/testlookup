"""W08 TRND-01: the ONE payload the Trends PDF and the Trends email render.

Before W08 the emailed report had its own query (``report_service.fetch_trend_data``:
per-row grain over ``tc.created_at``, a rolling cutoff, a pass rate that
divided by ``COUNT(*)`` so skips diluted it) while the page drew
``metrics_service.get_trend_data`` (run aggregates, UTC-day buckets, the
evaluated basis): the report and the page disagreed. This builder reads the
page's OWN service functions for the same :class:`AnalyticsScope`, so the PDF,
the email and the screen say the same numbers:

* ``trend`` -- ``get_trend_data`` (``/metrics/trends``);
* ``suites`` -- ``coverage_stats``' suite rows (``/analytics/coverage``, at most 50);
* ``summary`` -- ``trend_summary_service.summarize`` over those SAME two
  answers (no third read): the verdict, the confidence score, the gap;
* ``pass_rate_delta_pp`` -- ``get_dashboard_summary``'s
  ``avg_pass_rate_7d.pass_rate_delta_pp`` (the KPI strip's "vs previous N
  days"; Redis-cached 60 s), ``None`` when it is absent;
* ``meta`` -- ``build_meta`` with the trend chart's UTC-midnight window, as
  ``/metrics/trends`` builds it (the context block and the footer).

The caller resolved and authorised ``scope`` (one project). Nothing here
writes; nothing waits on anything but the database.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Final, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics_scope import AnalyticsScope

#: The page's verdict pills (``TrendsPage.tsx`` ``VERDICT_THEME``), word for word.
VERDICT_LABELS: Final = {
    "HEALTHY": "Trend healthy",
    "MIXED": "Trend mixed",
    "INSUFFICIENT": "Insufficient data",
    "DECLINING": "Trend declining",
    "PENDING": "Pending",
}
#: Not measured: printed, never ``0 %``.
NO_VALUE: Final = "—"
PASS_RATE_BASIS_TEXT: Final = "pass rate per test execution"


# ── The texts both renderers print (pure; the renderers escape them) ─────────


def pct_text(value: Any) -> str:
    """``85.7%``, or ``—`` when not measured."""
    if value is None:
        return NO_VALUE
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return NO_VALUE


def day_pass_rate(passed: int, failed: int, broken: int) -> Optional[float]:
    """A day's rate on the evaluated basis (skips are not evaluated); ``None`` when nothing was."""
    evaluated = passed + failed + broken
    return round(passed / evaluated * 100, 1) if evaluated > 0 else None


def _int(row: Any, key: str) -> int:
    try:
        return int((row or {}).get(key) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def window_days(payload: dict) -> list[dict]:
    """One row per UTC day of the window, oldest first: the counts (0 on a day
    without runs) and the day's pass rate (``None`` when nothing was evaluated:
    a GAP in the chart, ``—`` in the table, never 0 %)."""
    summary = payload.get("summary") or {}
    days = int(payload.get("window_days") or summary.get("window_days") or 0)
    try:
        today = date.fromisoformat(str(summary.get("today")))
    except ValueError:
        today = datetime.now(timezone.utc).date()
    by_date = {str((row or {}).get("date") or "")[:10]: row for row in payload.get("trend") or []}
    rows = []
    for i in range(days):
        iso = (today - timedelta(days=days - 1 - i)).isoformat()
        row = by_date.get(iso)
        passed, failed = _int(row, "passed"), _int(row, "failed")
        broken, skipped = _int(row, "broken"), _int(row, "skipped")
        rows.append({
            "date": iso, "passed": passed, "failed": failed, "broken": broken, "skipped": skipped,
            "executions": passed + failed + broken + skipped,
            "pass_rate": day_pass_rate(passed, failed, broken),
        })
    return rows


def delta_text(payload: dict) -> Optional[str]:
    """``+1.2 pp vs previous 14 days``, or ``None`` without a delta."""
    delta = payload.get("pass_rate_delta_pp")
    if delta is None:
        return None
    return f"{float(delta):+.1f} pp vs previous {payload.get('window_days')} days"


def kpis(payload: dict) -> list[tuple[str, str, str]]:
    """``(label, value, note)`` of the KPI strip, in the page's order."""
    summary = payload.get("summary") or {}
    ex = summary.get("executions") or {}
    note = PASS_RATE_BASIS_TEXT + (f" · {delta_text(payload)}" if delta_text(payload) else "")
    return [
        ("Pass rate", pct_text(summary.get("pass_rate")), note),
        ("Days with runs", f"{summary.get('days_with_runs', 0)} / {payload.get('window_days')}", ""),
        (
            "Executions", f"{int(ex.get('total') or 0):,}",
            f"{int(ex.get('passed') or 0):,} passed · {int(ex.get('failed') or 0):,} failed · "
            f"{int(ex.get('broken') or 0):,} broken · {int(ex.get('skipped') or 0):,} skipped",
        ),
        ("Suites", f"{len(payload.get('suites') or []):,}", ""),
        ("Last run", str(summary.get("last_run_day") or NO_VALUE), ""),
    ]


def verdict_text(payload: dict) -> str:
    """``Trend confidence 76 / 100 · Trend declining`` (the score is ``—`` while PENDING)."""
    confidence = (payload.get("summary") or {}).get("confidence") or {}
    verdict = str(confidence.get("verdict") or "PENDING")
    score = NO_VALUE if verdict == "PENDING" else str(confidence.get("score"))
    return f"Trend confidence {score} / 100 · {VERDICT_LABELS.get(verdict, verdict)}"


def gap_text(payload: dict) -> Optional[str]:
    """``No executions from <start> to <end>`` (``since <start>`` when it is
    ongoing), or ``None`` without a gap."""
    gap = (payload.get("summary") or {}).get("gap")
    if not isinstance(gap, dict) or not gap.get("start"):
        return None
    if gap.get("ongoing"):
        return f"No executions since {gap['start']}"
    return f"No executions from {gap['start']} to {gap['end']}"


def suite_rows(payload: dict) -> list[dict]:
    """The suite table: ``coverage_stats``' rows (``failed`` includes broken there)."""
    return [
        {
            "suite_name": str((row or {}).get("suite_name") or ""),
            "passed": _int(row, "passed"), "failed": _int(row, "failed"), "skipped": _int(row, "skipped"),
            "pass_rate": (row or {}).get("pass_rate"),
        }
        for row in payload.get("suites") or []
    ]


def _project_name(meta: dict) -> str:
    projects = (meta.get("scope") or {}).get("projects") or []
    names = [str(p.get("name") or "").strip() for p in projects if isinstance(p, dict)]
    names = [n for n in names if n]
    return ", ".join(names) if names else "—"


def _delta(dashboard: Any) -> Optional[float]:
    if not isinstance(dashboard, dict):
        return None
    block = dashboard.get("avg_pass_rate_7d")
    value = block.get("pass_rate_delta_pp") if isinstance(block, dict) else None
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


async def build_trends_report(db: AsyncSession, scope: AnalyticsScope) -> dict:
    """The trends report of ``scope``: the page's reads, its summary, its envelope."""
    from app.services import analytics_service, metrics_service  # noqa: PLC0415
    from app.services.analytics_meta import build_meta, utc_day_window_start  # noqa: PLC0415
    from app.services.trend_summary_service import summarize, utc_today  # noqa: PLC0415

    days = scope.window_days
    trend = await metrics_service.get_trend_data(
        db, scope.project, days, suite_name=scope.suite_arg, release_id=scope.release_arg,
    )
    coverage = await analytics_service.coverage_stats(
        db, scope.project, days, scope.suite_arg, scope.allowed_project_ids, scope.release_arg,
    )
    suites = list(coverage.get("suites") or [])
    dashboard = await metrics_service.get_dashboard_summary(
        db, scope.project, days, suite_name=scope.suite_arg, release_id=scope.release_arg,
    )
    meta = await build_meta(
        db, scope, pass_rate_basis=metrics_service.PASS_RATE_BASIS_EXECUTIONS,
        window_start=utc_day_window_start(days),
    )
    return {
        "project_name": _project_name(meta),
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trend": trend,
        "suites": suites,
        "summary": summarize(trend, suites, days=days, today=utc_today()),
        "pass_rate_delta_pp": _delta(dashboard),
        "meta": meta,
    }
