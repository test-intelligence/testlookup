"""Tools: get_dashboard_metrics, get_test_trends.

``GET /api/v1/metrics/summary`` answers every KPI as a card
``{"value", "trend", "trend_direction"}`` (``metrics_service.get_dashboard_summary``),
where ``trend`` is already the relative % change against the previous window of
the same length, and the verdict as ``release_readiness`` (GREEN / AMBER / RED,
``null`` with no executions in the window) beside ``release_readiness_band`` and
``release_readiness_downgrades`` from the project's release-gate policy.

The helpers below are the one reader of that shape for every MCP tool
(``release.check_release_readiness`` imports them). They read ``value``; a
``current`` key does not exist, and reading it rendered every KPI as 0.
"""

from __future__ import annotations

import re
from typing import Any

import client as api  # type: ignore[import]

_READINESS_ICON = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}

#: The KPI cards this module reads from the summary payload.
SUMMARY_KPIS = (
    "total_executions_7d",
    "avg_pass_rate_7d",
    "active_defects",
    "flaky_test_count",
    "new_failures_24h",
    "avg_duration_ms",
)

PENDING_TEXT = "Pending — no executions in window"

#: ``classify_with_policy`` records each fired hard cap as ``<cap>:<actual>><limit>``.
_DOWNGRADE = re.compile(r"^(?P<cap>[a-z0-9_]+):(?P<actual>-?\d+)>(?P<limit>-?\d+)$")
_DOWNGRADE_TEXT = {
    "p0_defects": "{actual} open critical (P0) defect(s); the policy allows {limit}",
    "flaky": "{actual} flaky tests; the policy allows {limit}",
    "new_failures_24h": "{actual} new failures in the last 24 h; the policy allows {limit}",
}


def kpi_value(data: dict, key: str) -> float:
    """The card's ``value`` (a bare number is accepted too); 0 when absent or null."""
    card = data.get(key)
    if isinstance(card, dict):
        card = card.get("value")
    if isinstance(card, bool) or not isinstance(card, (int, float)):
        return 0
    return card


def kpi_trend(data: dict, key: str, days: int) -> str:
    """The server's own trend, rendered: ``▲4.2% vs previous 7 days``, or ``—``."""
    card = data.get(key)
    if not isinstance(card, dict):
        return "—"
    change = card.get("trend")
    if isinstance(change, bool) or not isinstance(change, (int, float)):
        return "—"
    direction = card.get("trend_direction")
    arrow = "▲" if direction == "up" else "▼" if direction == "down" else ("▲" if change > 0 else "▼" if change < 0 else "→")
    window = "24 hours" if days == 1 else f"{days} days"
    return f"{arrow}{abs(change):.1f}% vs previous {window}"


def describe_downgrade(code: str) -> str:
    """A fired hard cap in words, keeping the server's code for traceability."""
    match = _DOWNGRADE.match(code or "")
    template = _DOWNGRADE_TEXT.get(match.group("cap")) if match else None
    if not match or template is None:
        return f"`{code}`"
    return f"{template.format(actual=match.group('actual'), limit=match.group('limit'))} (`{code}`)"


def readiness_of(data: dict) -> str | None:
    """The server's verdict, or ``None`` (pending) — never a local re-derivation."""
    readiness = data.get("release_readiness")
    return readiness if isinstance(readiness, str) and readiness else None


def readiness_heading(data: dict) -> str:
    readiness = readiness_of(data)
    if readiness is None:
        return f"⚪ Release Readiness: **{PENDING_TEXT}**"
    icon = _READINESS_ICON.get(readiness, "⚪")
    band = data.get("release_readiness_band")
    band_text = f" (policy band: {band})" if isinstance(band, str) and band else ""
    return f"{icon} Release Readiness: **{readiness}**{band_text}"


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_dashboard_metrics(project_id: str, days: int = 7) -> str:
        """
        Get executive-level quality KPIs for a project: executions, pass rate,
        open defects, flaky tests, new failures (always the last 24 h) and the
        release-readiness signal (GREEN / AMBER / RED) the /overview page shows.

        The readiness signal and its policy band come from the server, which
        applies the project's release-gate policy; this tool does not apply
        thresholds of its own. "Pending" means no executions in the window.
        Trends are the server's relative % change against the previous window
        of the same length.

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-90, default 7).
        """
        data: dict[str, Any] = await api.get(
            "/api/v1/metrics/summary",
            params={"project_id": project_id, "days": days},
        ) or {}

        if not any(key in data for key in SUMMARY_KPIS):
            return (
                f"No dashboard metrics were returned for project {project_id} "
                f"(no access to it, or no data in the last {days} days)."
            )

        lines = [
            f"## Dashboard Metrics — Last {days} Days",
            "",
            f"### {readiness_heading(data)}",
            "",
            "| KPI | Value | Trend |",
            "|-----|-------|-------|",
        ]

        pass_rate = data.get("avg_pass_rate_7d")
        basis = pass_rate.get("basis_label") if isinstance(pass_rate, dict) else None
        duration = kpi_value(data, "avg_duration_ms")

        lines += [
            f"| Total Executions ({days}d) | {int(kpi_value(data, 'total_executions_7d'))} | {kpi_trend(data, 'total_executions_7d', days)} |",
            f"| Avg Pass Rate ({days}d) | {kpi_value(data, 'avg_pass_rate_7d'):.1f}% | {kpi_trend(data, 'avg_pass_rate_7d', days)} |",
            f"| Active Defects | {int(kpi_value(data, 'active_defects'))} | — |",
            f"| Flaky Test Count | {int(kpi_value(data, 'flaky_test_count'))} | — |",
            f"| New Failures (24h) | {int(kpi_value(data, 'new_failures_24h'))} | — |",
            f"| Avg Run Duration | {duration / 1000:.0f}s | {kpi_trend(data, 'avg_duration_ms', days)} |",
        ]
        if isinstance(basis, str) and basis:
            lines += ["", f"*Pass rate basis: {basis}.*"]

        downgrades = [d for d in data.get("release_readiness_downgrades") or [] if isinstance(d, str)]
        if downgrades:
            lines += ["", "**Policy hard caps that fired:**"]
            lines += [f"- {describe_downgrade(d)}" for d in downgrades]

        return "\n".join(lines)

    @mcp.tool()
    async def get_test_trends(project_id: str, days: int = 7) -> str:
        """
        Get daily pass/fail/skip trend data for a project. Useful for identifying
        when regressions were introduced and whether quality is improving.

        Args:
            project_id: Project UUID.
            days: Number of days of history (7, 14, 30, 60, or 90).
        """
        data = await api.get(
            "/api/v1/metrics/trends",
            params={"project_id": project_id, "days": days},
        )

        points = data.get("data", [])
        if not points:
            return f"No trend data available for the last {days} days."

        lines = [
            f"## Test Trends — Last {days} Days\n",
            "| Date | Passed | Failed | Broken | Skipped | Total | Pass Rate |",
            "|------|--------|--------|--------|---------|-------|-----------|",
        ]
        for p in points:
            lines.append(
                f"| {p.get('date', 'N/A')} "
                f"| {p.get('passed', 0)} "
                f"| {p.get('failed', 0)} "
                f"| {p.get('broken', 0)} "
                f"| {p.get('skipped', 0)} "
                f"| {p.get('total', 0)} "
                f"| **{p.get('pass_rate', 0):.1f}%** |"
            )

        # Simple trend indicator
        if len(points) >= 2:
            first_rate = points[0].get("pass_rate", 0)
            last_rate = points[-1].get("pass_rate", 0)
            diff = last_rate - first_rate
            direction = "▲ improving" if diff > 0 else "▼ declining" if diff < 0 else "→ stable"
            lines.append(f"\n**Overall trend:** {direction} ({diff:+.1f}pp over {days}d)")

        return "\n".join(lines)
