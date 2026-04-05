"""Tools: get_flaky_tests, get_failure_categories, get_top_failing_tests,
get_coverage_report, get_defects, get_ai_analysis_summary."""

from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]

_CATEGORY_ICON = {
    "PRODUCT_BUG": "🐛",
    "INFRASTRUCTURE": "🔧",
    "TEST_DATA": "📦",
    "AUTOMATION_DEFECT": "🤖",
    "FLAKY": "🎲",
    "UNKNOWN": "❓",
}

_RESOLUTION_ICON = {
    "OPEN": "🔴",
    "RESOLVED": "✅",
    "DUPLICATE": "🔁",
    "WONTFIX": "🚫",
}


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_flaky_tests(
        project_id: str,
        days: int = 30,
        limit: int = 20,
    ) -> str:
        """
        Return the flakiness leaderboard — tests that alternate between passing and
        failing (5–95% failure rate) sorted by failure rate descending.
        High values indicate tests that need stabilisation, not necessarily real bugs.

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-365, default 30).
            limit: Maximum number of tests to return (1-100, default 20).
        """
        data = await api.get(
            "/api/v1/analytics/flaky-tests",
            params={"project_id": project_id, "days": days, "limit": limit},
        )

        items = data.get("items", [])
        if not items:
            return f"No flaky tests detected in the last {days} days. 🎉"

        lines = [
            f"## 🎲 Flaky Tests — Last {days} Days ({len(items)} found)\n",
            f"| # | Test Name | Suite | Failure Rate | Runs | Fails | Last Seen |",
            f"|---|-----------|-------|-------------|------|-------|-----------|",
        ]
        for i, t in enumerate(items, 1):
            rate = t.get("failure_rate_pct", 0)
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            lines.append(
                f"| {i} | {t.get('test_name', 'N/A')[:45]} "
                f"| {(t.get('suite_name') or 'N/A')[:25]} "
                f"| `{bar}` {rate:.0f}% "
                f"| {t.get('total_runs', 0)} "
                f"| {t.get('fail_count', 0)} "
                f"| {(t.get('last_seen') or 'N/A')[:10]} |"
            )

        return "\n".join(lines)

    @mcp.tool()
    async def get_failure_categories(project_id: str, days: int = 30) -> str:
        """
        Get the distribution of test failures by root cause category
        (PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY, UNKNOWN).
        Helps distinguish real bugs from automation debt and infra noise.

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-365, default 30).
        """
        data = await api.get(
            "/api/v1/analytics/failure-categories",
            params={"project_id": project_id, "days": days},
        )

        items = data.get("items", [])
        if not items:
            return "No failure data available for this period."

        total = sum(i.get("count", 0) for i in items)
        lines = [f"## Failure Category Distribution — Last {days} Days\n"]

        for item in sorted(items, key=lambda x: x.get("count", 0), reverse=True):
            cat = item.get("category", "UNKNOWN")
            count = item.get("count", 0)
            pct = (count / total * 100) if total else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            icon = _CATEGORY_ICON.get(cat, "❓")
            lines.append(f"{icon} **{cat}**: {count} ({pct:.1f}%)  `{bar}`")

        lines += [
            "",
            f"**Total failures categorised:** {total}",
            "",
            "**Legend:**",
            "🐛 PRODUCT_BUG — Defects in application code",
            "🔧 INFRASTRUCTURE — Pod crashes, OOM, network timeouts",
            "📦 TEST_DATA — Missing fixtures, stale test data",
            "🤖 AUTOMATION_DEFECT — Test code issues (fragile selectors, bad waits)",
            "🎲 FLAKY — Non-deterministic failures",
            "❓ UNKNOWN — Not yet triaged by AI",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def get_top_failing_tests(
        project_id: str,
        days: int = 30,
        limit: int = 15,
    ) -> str:
        """
        Return tests with the highest raw failure count in a period.
        Unlike flaky tests, these fail consistently — likely real product bugs.

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-365, default 30).
            limit: Maximum tests to return (1-100, default 15).
        """
        data = await api.get(
            "/api/v1/analytics/top-failing",
            params={"project_id": project_id, "days": days, "limit": limit},
        )

        items = data.get("items", [])
        if not items:
            return f"No consistently failing tests in the last {days} days."

        lines = [
            f"## Top Failing Tests — Last {days} Days\n",
            f"| # | Test Name | Suite | Category | Failures | Last Failed |",
            f"|---|-----------|-------|----------|----------|-------------|",
        ]
        for i, t in enumerate(items, 1):
            cat = t.get("failure_category") or "UNKNOWN"
            icon = _CATEGORY_ICON.get(cat, "❓")
            lines.append(
                f"| {i} | {t.get('test_name', 'N/A')[:45]} "
                f"| {(t.get('suite_name') or 'N/A')[:25]} "
                f"| {icon} {cat} "
                f"| {t.get('fail_count', 0)} "
                f"| {(t.get('last_failed') or 'N/A')[:10]} |"
            )

        return "\n".join(lines)

    @mcp.tool()
    async def get_coverage_report(project_id: str, days: int = 30) -> str:
        """
        Get test suite coverage: how many unique tests ran in each suite,
        per-suite pass rates, and overall execution statistics.

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-365, default 30).
        """
        data = await api.get(
            "/api/v1/analytics/coverage",
            params={"project_id": project_id, "days": days},
        )

        summary = data.get("summary", {})
        suites = data.get("suites", [])

        lines = [
            f"## Coverage Report — Last {days} Days",
            "",
            "### Summary",
            f"- **Unique Tests:** {summary.get('unique_tests', 0)}",
            f"- **Suite Count:** {summary.get('suite_count', 0)}",
            f"- **Total Executions:** {summary.get('total_executions', 0)}",
            f"- **Avg Pass Rate:** {summary.get('avg_pass_rate', 0):.1f}%",
            f"- **Days with Runs:** {summary.get('days_with_runs', 0)} / {days}",
            "",
            "### Per-Suite Breakdown",
            f"| Suite | Tests | ✅ Pass | ❌ Fail | ⏭ Skip | Pass Rate |",
            f"|-------|-------|--------|--------|--------|-----------|",
        ]

        for s in sorted(suites, key=lambda x: x.get("pass_rate", 0)):
            rate = s.get("pass_rate", 0)
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            lines.append(
                f"| {(s.get('suite_name') or 'N/A')[:40]} "
                f"| {s.get('unique_tests', 0)} "
                f"| {s.get('passed', 0)} "
                f"| {s.get('failed', 0)} "
                f"| {s.get('skipped', 0)} "
                f"| `{bar}` {rate:.1f}% |"
            )

        return "\n".join(lines)

    @mcp.tool()
    async def get_defects(
        project_id: str,
        resolution_status: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ) -> str:
        """
        List defects linked to test failures, optionally filtered by resolution status.
        Each defect is linked to a Jira ticket and a specific test case.

        Args:
            project_id: Project UUID.
            resolution_status: Filter by OPEN, RESOLVED, DUPLICATE, or WONTFIX.
            page: Page number.
            size: Results per page.
        """
        data = await api.get(
            "/api/v1/analytics/defects",
            params={
                "project_id": project_id,
                "resolution_status": resolution_status,
                "page": page,
                "size": size,
            },
        )

        items = data.get("items", [])
        total = data.get("total", 0)
        pages = data.get("pages", 1)

        if not items:
            return "No defects found with the given filters."

        lines = [
            f"## Defects — Page {page}/{pages} ({total} total)\n",
            f"| Jira | Test | Category | Resolution | Confidence | Created |",
            f"|------|------|----------|------------|------------|---------|",
        ]
        for d in items:
            cat = d.get("failure_category") or "UNKNOWN"
            res = d.get("resolution_status") or "OPEN"
            res_icon = _RESOLUTION_ICON.get(res, "❓")
            jira_id = d.get("jira_ticket_id") or "N/A"
            jira_url = d.get("jira_ticket_url")
            jira_cell = f"[{jira_id}]({jira_url})" if jira_url else jira_id
            conf = d.get("ai_confidence_score")
            conf_str = f"{conf}%" if conf is not None else "N/A"
            lines.append(
                f"| {jira_cell} "
                f"| {(d.get('test_name') or 'N/A')[:35]} "
                f"| {_CATEGORY_ICON.get(cat, '❓')} {cat} "
                f"| {res_icon} {res} "
                f"| {conf_str} "
                f"| {(d.get('created_at') or 'N/A')[:10]} |"
            )

        return "\n".join(lines)

    @mcp.tool()
    async def get_ai_analysis_summary(project_id: str, days: int = 30) -> str:
        """
        Get statistics on AI triage coverage: how many failures were auto-analysed,
        confidence distribution, and how many require human review.

        Args:
            project_id: Project UUID.
            days: Analysis window in days (1-365, default 30).
        """
        data = await api.get(
            "/api/v1/analytics/ai-summary",
            params={"project_id": project_id, "days": days},
        )

        total = data.get("total_analysed", 0)
        high = data.get("high_confidence", 0)
        needs_review = data.get("needs_review", 0)
        avg_conf = data.get("avg_confidence", 0)

        auto_rate = (high / total * 100) if total else 0
        review_rate = (needs_review / total * 100) if total else 0

        lines = [
            f"## AI Triage Summary — Last {days} Days",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Failures Analysed | **{total}** |",
            f"| High-Confidence Results (≥threshold) | {high} ({auto_rate:.0f}%) |",
            f"| Requires Human Review | {needs_review} ({review_rate:.0f}%) |",
            f"| Avg Confidence Score | **{avg_conf:.0f}%** |",
            f"| 🎲 Flaky Tests Detected | {data.get('flaky_detected', 0)} |",
            f"| 🔥 Backend Errors Found | {data.get('backend_errors', 0)} |",
            f"| 🔧 Pod Issues Found | {data.get('pod_issues', 0)} |",
        ]

        if total == 0:
            lines.append("\n*No AI analyses recorded yet. Use `trigger_ai_analysis` on failing tests.*")

        return "\n".join(lines)
