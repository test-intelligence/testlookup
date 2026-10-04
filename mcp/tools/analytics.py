"""Tools: get_flaky_tests, get_failure_categories, get_top_failing_tests,
get_coverage_report, get_defects, get_ai_analysis_summary, get_chart_data.

The leaderboard tools answer project-wide. ``get_chart_data`` (VIZ-211) is the
one that takes a release and a suite: it reads ``/api/v1/analytics/chart-data``
with the REST parameter names, so an agent, the CLI and the UI's charts get the
same series for the same scope.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

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

#: The ``metric`` values chart-data accepts (``chart_data_service.METRICS``).
#: This job cannot import the backend, so the backend's wire-contract test
#: holds the two lists together.
CHART_METRICS: tuple[str, ...] = (
    "broken",
    "duration_p50",
    "duration_p95",
    "duration_total",
    "executions",
    "failed",
    "failure_rate",
    "failures",
    "flaky_tests",
    "pass_rate",
    "passed",
    "retried_tests",
    "run_count",
    "skipped",
    "unique_tests",
    "unknown",
)

#: The ``group_by`` values chart-data accepts (``chart_data_service.DIMENSIONS``).
CHART_DIMENSIONS: tuple[str, ...] = (
    "branch",
    "day",
    "environment",
    "failure_category",
    "ingestion_source",
    "project",
    "release",
    "status",
    "suite",
    "test",
    "week",
)


def _analytics_error(exc: Exception) -> dict[str, Any]:
    """``api.error_payload`` plus the fields of the analytics error contract.

    An analytics route refuses with ``code`` (the C1 rule id), ``param`` and
    ``allowed`` beside ``detail`` (VIZ-210), and those are what let an agent
    correct its own call: ``window_days_range`` with ``{"min": 1, "max": 365}``
    says what to send instead. The shared helper keeps only ``detail``, and
    every write tool's result shape rests on it, so the extra keys are added
    here rather than there. A 429 also carries ``retry_after`` in seconds.
    """
    payload = api.error_payload(exc)
    if not isinstance(exc, httpx.HTTPStatusError):
        return payload
    try:
        body = exc.response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("code", "param", "allowed"):
            if key in body:
                payload[key] = body[key]
    retry_after = exc.response.headers.get("Retry-After")
    if retry_after:
        payload["retry_after"] = retry_after
    return payload


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
        
        Scope: this answers for the WHOLE PROJECT over the time window. The web
        UI can narrow the same data to a single release; this tool cannot, so
        a number here and a number on a release-filtered dashboard are answering
        different questions and will legitimately differ. For a release- or
        suite-scoped number, use `get_chart_data`, which takes `release_id` and
        `suite_name` and reads the endpoint the UI's charts read.
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
        
        Scope: this answers for the WHOLE PROJECT over the time window. The web
        UI can narrow the same data to a single release; this tool cannot, so
        a number here and a number on a release-filtered dashboard are answering
        different questions and will legitimately differ. For a release- or
        suite-scoped number, use `get_chart_data`, which takes `release_id` and
        `suite_name` and reads the endpoint the UI's charts read.
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
        
        Scope: this answers for the WHOLE PROJECT over the time window. The web
        UI can narrow the same data to a single release; this tool cannot, so
        a number here and a number on a release-filtered dashboard are answering
        different questions and will legitimately differ. For a release- or
        suite-scoped number, use `get_chart_data`, which takes `release_id` and
        `suite_name` and reads the endpoint the UI's charts read.
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
        
        Scope: this answers for the WHOLE PROJECT over the time window. The web
        UI can narrow the same data to a single release; this tool cannot, so
        a number here and a number on a release-filtered dashboard are answering
        different questions and will legitimately differ. For a release- or
        suite-scoped number, use `get_chart_data`, which takes `release_id` and
        `suite_name` and reads the endpoint the UI's charts read.
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

    @mcp.tool()
    async def get_chart_data(
        group_by: list[str],
        metric: str = "executions",
        project_id: Optional[str] = None,
        release_id: Optional[list[str]] = None,
        suite_name: Optional[list[str]] = None,
        days: int = 30,
        top_n: Optional[int] = None,
    ) -> dict:
        """
        Return one metric grouped by one or two dimensions: the same series the
        REST API (GET /api/v1/analytics/chart-data) and the web UI's charts
        draw, returned verbatim with its `meta` envelope.

        This tool is release- and suite-scoped: pass `release_id` and/or
        `suite_name` to narrow it exactly as a release-filtered dashboard
        does. Without `project_id` it is project-wide over every project you
        can read. Any release or project you cannot read refuses the whole
        request with 403; there are never partial results.

        Args:
            group_by: 1-2 dimensions, the time dimension first, e.g. ["day"]
                or ["day", "suite"] (the second keys the series). One of:
                branch, day, environment, failure_category, ingestion_source,
                project, release, status, suite, test, week.
            metric: What to measure (default executions). One of: broken,
                duration_p50, duration_p95, duration_total, executions, failed,
                failure_rate, failures, flaky_tests, pass_rate, passed,
                retried_tests, run_count, skipped, unique_tests, unknown.
            project_id: Project UUID. Omit for every project you can read.
            release_id: Release UUIDs, or "unattributed" for runs no release
                claims (OR, at most 20).
            suite_name: Suite names, matched case-insensitively (OR, at most 50).
            days: Window in days ending today, UTC (1-365, default 30).
            top_n: Keep the N largest keys and merge the rest into "other".
                Required for group_by test outside a single suite.

        Reading it: every point has `y` and `n` (the sample behind it). A
        `y` of null with `measured: false` means no data, never 0. Rates are
        percentages (0-100) and durations are milliseconds. `meta.scope` is
        what the server applied, `meta.totals` how much the filters kept and
        `meta.definitions` what the numbers mean.

        Size: 365 days by eight series is about 200 KB. For a long window,
        group by `week` or pass `top_n`.

        Errors come back as {ok: false, status_code, detail} plus `code`,
        `param` and `allowed` from the server (a 422 names the parameter and
        what it accepts), and `retry_after` on a 429 (120 requests a minute).
        """
        params = {
            "metric": metric,
            "group_by": group_by,
            "project_id": project_id,
            "release_id": release_id,
            "suite_name": suite_name,
            "days": days,
            "top_n": top_n,
        }
        try:
            return await api.get("/api/v1/analytics/chart-data", params=params)
        except Exception as exc:
            return _analytics_error(exc)
