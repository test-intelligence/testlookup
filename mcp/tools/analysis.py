"""Tools: trigger_ai_analysis, search_tests."""

from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]
import untrusted  # type: ignore[import]
from tools.metrics import DEFAULT_WINDOW_DAYS  # type: ignore[import]

_CATEGORY_ICON = {
    "PRODUCT_BUG": "🐛",
    "INFRASTRUCTURE": "🔧",
    "TEST_DATA": "📦",
    "AUTOMATION_DEFECT": "🤖",
    "FLAKY": "🎲",
    "UNKNOWN": "❓",
}

#: The server's confidence gate (``AnalysisResponse.confidence_gate_status``), in words.
_GATE_TEXT = {
    "above_threshold": "✅ meets the configured confidence threshold",
    "below_threshold": "⚠️ below the configured confidence threshold — needs human review",
    "not_evaluated": "⚪ the confidence gate was not evaluated",
}


def _confidence_label(data: dict) -> str:
    """The server's verdict on the confidence score, never one worked out here.

    The threshold is an operator setting. Fixed bands in this tool called a
    score of 86 "High — fully automated" under a threshold of 90.
    """
    if data.get("low_confidence"):
        return _GATE_TEXT["below_threshold"]
    return _GATE_TEXT.get(str(data.get("confidence_gate_status") or "not_evaluated"), _GATE_TEXT["not_evaluated"])


def _flatten_steps(nodes: list) -> list:
    """Depth-first flatten of the nested step tree, preserving document order."""
    flat: list = []
    for node in nodes or []:
        flat.append(node)
        flat.extend(_flatten_steps(node.get("steps") or []))
    return flat


def _first_failed_step_evidence(tree: dict) -> Optional[str]:
    """Return ``"step N failed: <assertion>"`` for the first FAILED/BROKEN step.

    "First" = first in document (depth-first / ordinal) order. Returns ``None``
    when the snapshot has no captured steps or no failing step (best-effort;
    older runs have no granular data).
    """
    flat = _flatten_steps(tree.get("steps") or [])
    for i, node in enumerate(flat, 1):
        if (node.get("status") or "").upper() in ("FAILED", "BROKEN"):
            name = (node.get("name") or "(unnamed)").replace("\n", " ").strip()
            msg = (
                node.get("assertion_message")
                or node.get("expected_value")
                or ""
            ).replace("\n", " ").strip()
            detail = f"{name}: {msg}" if msg else name
            return f"step {i} failed: {detail[:200]}"
    return None


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def trigger_ai_analysis(
        test_case_id: str,
        service_name: Optional[str] = None,
        ocp_pod_name: Optional[str] = None,
        ocp_namespace: Optional[str] = None,
        run_id: Optional[str] = None,
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
            run_id: Run UUID of the failing test (optional). When supplied, the granular
                step snapshot is fetched and the first FAILED/BROKEN step is added to the
                Evidence References as "step N failed: <assertion>" (best-effort).
        """
        body: dict = {"test_case_id": test_case_id}
        if service_name:
            body["service_name"] = service_name
        if ocp_pod_name:
            body["ocp_pod_name"] = ocp_pod_name
        if ocp_namespace:
            body["ocp_namespace"] = ocp_namespace

        data = await api.post(api.path("/api/v1/analyze"), json_body=body)

        cat = data.get("failure_category", "UNKNOWN")
        icon = _CATEGORY_ICON.get(cat, "❓")
        score = data.get("confidence_score", 0)

        lines = [
            f"## AI Root-Cause Analysis",
            f"- **Test Case ID:** `{data.get('test_case_id', test_case_id)}`",
            f"- **LLM:** {untrusted.short(data.get('llm_provider'))} / `{untrusted.short(data.get('llm_model'), limit=80)}`",
            "",
            f"### {icon} Failure Category: **{untrusted.short(cat)}**",
            f"### Confidence Score: **{score}%** — {_confidence_label(data)}",
            f"### Requires Human Review: {'⚠️ YES' if data.get('requires_human_review') else '✅ No'}",
            "",
            "### Root Cause Summary",
            # AI text written from test output: it can repeat what a report said.
            *(untrusted.block(data.get("root_cause_summary"), limit=3000) or ["No summary available."]),
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
                lines.append(f"{i}. {untrusted.inline(action, limit=400, missing='(empty)')}")

        # Best-effort: surface the first failing granular step as an extra
        # evidence reference. Requires run_id (the analyze response carries only
        # test_case_id); degrades silently if steps are absent/unavailable.
        step_evidence: Optional[str] = None
        if run_id:
            try:
                tree = await api.get(
                    api.path("/api/v1/runs/{run_id}/tests/{test_id}/steps", run_id=api.uuid_id(run_id), test_id=api.uuid_id(test_case_id))
                )
                if isinstance(tree, dict):
                    step_evidence = _first_failed_step_evidence(tree)
            except Exception:
                step_evidence = None

        refs = data.get("evidence_references", [])
        if refs or step_evidence:
            lines += ["", "### Evidence References"]
            if step_evidence:
                lines.append(f"- **steps** `{untrusted.short(test_case_id, limit=64)}`: {untrusted.inline(step_evidence)}")
            for ref in refs:
                lines.append(
                    f"- **{untrusted.short(ref.get('source'), missing='unknown')}** "
                    f"`{untrusted.short(ref.get('reference_id'), limit=80, missing='')}`: "
                    f"{untrusted.inline(ref.get('excerpt'), limit=200, missing='')}"
                )

        return "\n".join(untrusted.with_notice(lines))

    @mcp.tool()
    async def search_tests(
        query: str,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        days: int = DEFAULT_WINDOW_DAYS,
        page: int = 1,
        size: int = 20,
    ) -> str:
        """
        Full-text search across all test cases by name, suite, error message or step text
        (step names and assertion messages). Class names are not searched.
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
            api.path("/api/v1/search"),
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
            return f'No tests found matching "{query}" in the last {days} days.'

        lines = [
            f'## Search Results for "{query}" ({search_type}) — {total} matches in the last {days} days\n',
            # The count is not windowed: every FAILED result the test ever had (BROKEN not counted).
            f"| Test Name | Suite | Status | Failures (all time, FAILED only) | Last Run |",
            f"|-----------|-------|--------|----------------------------------|----------|",
        ]
        for t in items:
            status_str = t.get("status", "UNKNOWN")
            lines.append(
                f"| {untrusted.inline(t.get('test_name'), limit=80)} "
                f"| {untrusted.inline(t.get('suite_name'), limit=40)} "
                f"| {untrusted.short(status_str)} "
                f"| {t.get('failure_count', 0)} "
                f"| {(t.get('last_run_date') or 'N/A')[:10]} |"
            )
            lines.append(
                f"> test_id: `{untrusted.short(t.get('test_case_id'), limit=64)}` "
                f"| run_id: `{untrusted.short(t.get('test_run_id'), limit=64)}`"
            )

        if pages > 1:
            lines.append(f"\n*Page {page}/{pages} — use page parameter for more results.*")

        return "\n".join(untrusted.with_notice(lines))
