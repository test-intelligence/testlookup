"""Tools: trigger_ai_analysis, search_tests."""

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

_CONFIDENCE_LABEL = {
    range(0, 40): "⚠️ Low — human review strongly recommended",
    range(40, 70): "🟡 Medium — review recommended",
    range(70, 85): "🟢 Good — reliable",
    range(85, 101): "✅ High — fully automated",
}


def _confidence_label(score: int) -> str:
    for r, label in _CONFIDENCE_LABEL.items():
        if score in r:
            return label
    return "Unknown"


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def trigger_ai_analysis(
        test_case_id: str,
        service_name: Optional[str] = None,
        ocp_pod_name: Optional[str] = None,
        ocp_namespace: Optional[str] = None,
    ) -> str:
        """
        Trigger a LangChain ReAct AI agent to perform root-cause analysis on a
        failing test case. The agent uses up to 5 tools: stack trace retrieval,
        flakiness history, REST payload inspection, Splunk log query, and
        OpenShift pod event analysis.

        ⚠️ This tool takes 10–60 seconds depending on the LLM provider.

        Args:
            test_case_id: UUID of the failing test case (from get_test_case or list_test_cases).
            service_name: Backend service name for Splunk log correlation (optional).
            ocp_pod_name: OpenShift pod name for infrastructure correlation (optional).
            ocp_namespace: OpenShift namespace (optional, uses project default if omitted).
        """
        body: dict = {"test_case_id": test_case_id}
        if service_name:
            body["service_name"] = service_name
        if ocp_pod_name:
            body["ocp_pod_name"] = ocp_pod_name
        if ocp_namespace:
            body["ocp_namespace"] = ocp_namespace

        data = await api.post("/api/v1/analyze", json_body=body)

        cat = data.get("failure_category", "UNKNOWN")
        icon = _CATEGORY_ICON.get(cat, "❓")
        score = data.get("confidence_score", 0)

        lines = [
            f"## AI Root-Cause Analysis",
            f"- **Test Case ID:** `{data.get('test_case_id', test_case_id)}`",
            f"- **LLM:** {data.get('llm_provider', 'N/A')} / `{data.get('llm_model', 'N/A')}`",
            "",
            f"### {icon} Failure Category: **{cat}**",
            f"### Confidence Score: **{score}%** — {_confidence_label(score)}",
            f"### Requires Human Review: {'⚠️ YES' if data.get('requires_human_review') else '✅ No'}",
            "",
            "### Root Cause Summary",
            data.get("root_cause_summary", "No summary available."),
            "",
            "### Findings",
            f"- **Backend Error Found:** {'✅ Yes' if data.get('backend_error_found') else 'No'}",
            f"- **Pod Issue Found:** {'✅ Yes' if data.get('pod_issue_found') else 'No'}",
            f"- **Flaky Test:** {'✅ Yes' if data.get('is_flaky') else 'No'}",
        ]

        actions = data.get("recommended_actions", [])
        if actions:
            lines += ["", "### Recommended Actions"]
            for i, action in enumerate(actions, 1):
                lines.append(f"{i}. {action}")

        refs = data.get("evidence_references", [])
        if refs:
            lines += ["", "### Evidence References"]
            for ref in refs:
                lines.append(
                    f"- **{ref.get('source', 'unknown')}** `{ref.get('reference_id', '')}`: "
                    f"{ref.get('excerpt', '')[:200]}"
                )

        return "\n".join(lines)

    @mcp.tool()
    async def search_tests(
        query: str,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        days: int = 30,
        page: int = 1,
        size: int = 20,
    ) -> str:
        """
        Full-text search across all test cases by name, suite, class, or error message.
        Returns matching tests with their run context.

        Args:
            query: Search string (e.g. "NullPointerException", "payment", "LoginTest").
            project_id: Optionally scope search to a single project.
            status: Filter by status: PASSED, FAILED, SKIPPED, BROKEN, UNKNOWN.
            days: How far back to search (1-365, default 30).
            page: Page number.
            size: Results per page (1-100, default 20).
        """
        data = await api.get(
            "/api/v1/search",
            params={
                "q": query,
                "project_id": project_id,
                "status": status,
                "days": days,
                "page": page,
                "size": size,
            },
        )

        items = data.get("items", [])
        total = data.get("total", 0)
        pages = data.get("pages", 1)
        search_type = data.get("search_type", "keyword")

        if not items:
            return f'No tests found matching "{query}".'

        lines = [
            f'## Search Results for "{query}" ({search_type}) — {total} matches\n',
            f"| Test Name | Suite | Status | Failures | Last Run |",
            f"|-----------|-------|--------|----------|----------|",
        ]
        for t in items:
            status_str = t.get("status", "UNKNOWN")
            lines.append(
                f"| {t.get('test_name', 'N/A')[:50]} "
                f"| {(t.get('suite_name') or 'N/A')[:30]} "
                f"| {status_str} "
                f"| {t.get('failure_count', 0)} "
                f"| {(t.get('last_run_date') or 'N/A')[:10]} |"
            )
            lines.append(f"> test_id: `{t.get('test_case_id', 'N/A')}` | run_id: `{t.get('test_run_id', 'N/A')}`")

        if pages > 1:
            lines.append(f"\n*Page {page}/{pages} — use page parameter for more results.*")

        return "\n".join(lines)
