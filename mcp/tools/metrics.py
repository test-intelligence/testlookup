"""Tools: get_dashboard_metrics, get_test_trends."""

from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]

_READINESS_ICON = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_dashboard_metrics(project_id: str, days: int = 7) -> str:
        """
        Get executive-level quality KPIs for a project: pass rate, defect count,
        flakiness, new failures, and release readiness signal (GREEN / AMBER / RED).

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-90, default 7).
        """
        data = await api.get(
            "/api/v1/metrics/summary",
            params={"project_id": project_id, "days": days},
        )

        readiness = data.get("release_readiness", "UNKNOWN")
        icon = _READINESS_ICON.get(readiness, "⚪")

        def trend(val: Optional[dict]) -> str:
            if not val:
                return ""
            current = val.get("current", 0)
            previous = val.get("previous", 0)
            if previous and previous != 0:
                change = ((current - previous) / previous) * 100
                arrow = "▲" if change >= 0 else "▼"
                return f" ({arrow}{abs(change):.1f}% vs prev period)"
            return ""

        lines = [
            f"## Dashboard Metrics — Last {days} Days",
            "",
            f"### {icon} Release Readiness: **{readiness}**",
            "",
            "| KPI | Value | Trend |",
            "|-----|-------|-------|",
        ]

        # Handle both flat values and trend-wrapped dicts
        def val(key: str) -> float:
            v = data.get(key, 0)
            if isinstance(v, dict):
                return v.get("current", 0)
            return v or 0

        lines += [
            f"| Total Executions ({days}d) | {int(val('total_executions_7d'))} | {trend(data.get('total_executions_7d'))} |",
            f"| Avg Pass Rate ({days}d) | {val('avg_pass_rate_7d'):.1f}% | {trend(data.get('avg_pass_rate_7d'))} |",
            f"| Active Defects | {int(val('active_defects'))} | — |",
            f"| Flaky Test Count | {int(val('flaky_test_count'))} | — |",
            f"| New Failures (24h) | {int(val('new_failures_24h'))} | — |",
            f"| Avg Run Duration | {val('avg_duration_ms') / 1000:.0f}s | {trend(data.get('avg_duration_ms'))} |",
        ]

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
            f"| Date | Passed | Failed | Broken | Skipped | Total | Pass Rate |",
            f"|------|--------|--------|--------|---------|-------|-----------|",
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
