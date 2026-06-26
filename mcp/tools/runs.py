"""Tools: list_test_runs, get_run_details, list_test_cases, get_test_case."""

from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]

_STATUS_EMOJI = {
    "PASSED": "✅",
    "FAILED": "❌",
    "IN_PROGRESS": "⏳",
    "STOPPED": "⏹",
    "SKIPPED": "⏭",
    "BROKEN": "💥",
    "UNKNOWN": "❓",
}


def _status(s: Optional[str]) -> str:
    if not s:
        return "UNKNOWN"
    return f"{_STATUS_EMOJI.get(s, '')} {s}"


def _render_step_rows(nodes: list, out: list, depth: int = 0) -> None:
    """Recursively flatten the nested step tree into markdown table rows.

    Mirrors the LATEST-RUN-ONLY snapshot shape from
    ``GET /runs/{run_id}/tests/{test_id}/steps`` (``runs_service.get_test_steps_tree``):
    each node has ``name``/``status``/``duration_ms``/``assertion_message`` and a
    nested ``steps`` list. Indentation encodes depth; ``ordinal`` (1-based here)
    numbers the steps in document order.
    """
    for node in nodes or []:
        indent = "··" * depth
        name = (node.get("name") or "(unnamed)").replace("\n", " ")[:60]
        dur_ms = node.get("duration_ms") or 0
        msg = (node.get("assertion_message") or "").replace("\n", " ").strip()
        if len(msg) > 60:
            msg = msg[:60] + "…"
        out.append(
            f"| {len(out) + 1} "
            f"| {indent}{name} "
            f"| {_status(node.get('status'))} "
            f"| {dur_ms / 1000:.2f}s "
            f"| {msg or '-'} |"
        )
        _render_step_rows(node.get("steps") or [], out, depth + 1)


def _render_step_tree(tree: dict) -> list:
    """Render the step tree dict as readable markdown lines (best-effort)."""
    roots = tree.get("steps") or []
    if not roots:
        return ["", "### Steps", "_No granular step data captured for this test._"]

    rows: list = []
    _render_step_rows(roots, rows)
    lines = [
        "",
        f"### Steps ({tree.get('step_count') or len(rows)})",
        "| # | Step | Status | Duration | Assertion |",
        "|---|------|--------|----------|-----------|",
    ]
    lines += rows
    return lines


def _render_step_flips(payload: dict) -> str:
    """Render the cross-run step-flip report (FLK-P6) as readable markdown.

    Mirrors the per-test ``StepFlipPanel`` UI semantics over the
    ``GET /runs/{run_id}/tests/{test_id}/step-flips`` payload: it distinguishes
    "not enough history" from "stable" from "flickering", then lists each step
    that oscillated PASSED<->FAILED with its flip count, how many runs observed
    it, and its current status. Pure / best-effort — malformed payloads degrade
    to the insufficient-history message rather than raising.
    """
    report = (payload or {}).get("report") or {}
    test_id = str((payload or {}).get("test_id") or "")
    runs_analyzed = report.get("runs_analyzed") or 0
    title = f"## Step-Level Flakiness — Test `{test_id[:8]}`"

    if runs_analyzed < 2:
        return (
            f"{title}\n\n"
            "_Not enough cross-run step history to detect step-level flakiness yet "
            f"(analysed {runs_analyzed} run(s); need at least 2)._"
        )

    if not report.get("has_step_flip"):
        return (
            f"{title}\n\n"
            f"_No cross-run step-flip across {runs_analyzed} runs — steps are stable._"
        )

    lines = [
        f"{title} — {report.get('total_flips', 0)} total flips across {runs_analyzed} runs",
        "",
        report.get("summary") or "",
        "",
        "| Step | Flips | Runs Observed | Current |",
        "|------|-------|---------------|---------|",
    ]
    for s in report.get("flipping_steps") or []:
        label = s.get("step_name") or f"step #{s.get('ordinal', '?')}"
        lines.append(
            f"| {str(label)[:60]} "
            f"| {s.get('flip_count', 0)} "
            f"| {s.get('runs_observed', 0)} "
            f"| {_status(s.get('last_status') or 'UNKNOWN')} |"
        )
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_test_runs(
        project_id: str,
        status: Optional[str] = None,
        days: int = 7,
        page: int = 1,
        size: int = 20,
    ) -> str:
        """
        List recent test runs for a project, newest first.

        Args:
            project_id: Project UUID (from list_projects).
            status: Filter by run status: PASSED, FAILED, IN_PROGRESS, or STOPPED.
            days: How far back to look (1-90, default 7).
            page: Page number (default 1).
            size: Results per page (1-100, default 20).
        """
        data = await api.get(
            "/api/v1/runs",
            params={
                "project_id": project_id,
                "status": status,
                "page": page,
                "size": size,
            },
        )

        items = data.get("items", [])
        total = data.get("total", 0)
        pages = data.get("pages", 1)

        if not items:
            return f"No test runs found for project `{project_id}` with the given filters."

        lines = [
            f"## Test Runs — Page {page}/{pages} ({total} total)\n",
            f"| Build | Branch | Status | Pass Rate | Tests | Duration | Trigger | Started |",
            f"|-------|--------|--------|-----------|-------|----------|---------|---------|",
        ]
        for r in items:
            duration_s = (r.get("duration_ms") or 0) / 1000
            lines.append(
                f"| #{r.get('build_number', '?')} `{r['id'][:8]}` "
                f"| {r.get('branch', 'N/A')} "
                f"| {_status(r.get('status'))} "
                f"| {r.get('pass_rate', 0):.1f}% "
                f"| {r.get('total_tests', 0)} "
                f"| {duration_s:.0f}s "
                f"| {r.get('trigger_source', 'N/A')} "
                f"| {(r.get('start_time') or '')[:16]} |"
            )

        lines.append(f"\n*Use `get_run_details` with a run ID for full details.*")
        return "\n".join(lines)

    @mcp.tool()
    async def get_run_details(run_id: str) -> str:
        """
        Get full details and aggregated statistics for a specific test run.

        Args:
            run_id: Test run UUID (from list_test_runs).
        """
        r = await api.get(f"/api/v1/runs/{run_id}")

        duration_s = (r.get("duration_ms") or 0) / 1000
        total = r.get("total_tests", 0)
        passed = r.get("passed_tests", 0)
        failed = r.get("failed_tests", 0)
        skipped = r.get("skipped_tests", 0)
        broken = r.get("broken_tests", 0)

        lines = [
            f"## Run #{r.get('build_number', '?')} — {_status(r.get('status'))}",
            f"- **Run ID:** `{r['id']}`",
            f"- **Project:** `{r.get('project_id', 'N/A')}`",
            f"- **Branch:** {r.get('branch', 'N/A')}",
            f"- **Commit:** `{(r.get('commit_hash') or 'N/A')[:12]}`",
            f"- **Trigger:** {r.get('trigger_source', 'N/A')}",
            f"- **Jenkins Job:** {r.get('jenkins_job', 'N/A')}",
            "",
            "### Test Results",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Tests | {total} |",
            f"| ✅ Passed | {passed} |",
            f"| ❌ Failed | {failed} |",
            f"| 💥 Broken | {broken} |",
            f"| ⏭ Skipped | {skipped} |",
            f"| Pass Rate | **{r.get('pass_rate', 0):.2f}%** |",
            f"| Duration | {duration_s:.1f}s |",
            "",
            "### Timeline",
            f"- **Started:** {r.get('start_time', 'N/A')}",
            f"- **Ended:** {r.get('end_time', 'N/A')}",
        ]

        if r.get("ocp_pod_name"):
            lines += [
                "",
                "### OpenShift Context",
                f"- **Pod:** {r['ocp_pod_name']}",
                f"- **Node:** {r.get('ocp_node', 'N/A')}",
                f"- **Namespace:** {r.get('ocp_namespace', 'N/A')}",
            ]

        return "\n".join(lines)

    @mcp.tool()
    async def list_test_cases(
        run_id: str,
        status: Optional[str] = None,
        suite: Optional[str] = None,
        page: int = 1,
        size: int = 50,
    ) -> str:
        """
        List test cases within a run, filterable by status or suite name.

        Args:
            run_id: Test run UUID (from list_test_runs).
            status: Filter: PASSED, FAILED, SKIPPED, BROKEN, UNKNOWN.
            suite: Filter by suite name (partial match supported).
            page: Page number.
            size: Results per page (1-200, default 50).
        """
        data = await api.get(
            f"/api/v1/runs/{run_id}/tests",
            params={"status": status, "suite": suite, "page": page, "size": size},
        )

        items = data.get("items", [])
        total = data.get("total", 0)
        pages = data.get("pages", 1)

        if not items:
            return "No test cases found with the given filters."

        lines = [
            f"## Test Cases — Run `{run_id[:8]}` — Page {page}/{pages} ({total} total)\n",
            f"| Test Name | Suite | Status | Duration | Category | Error |",
            f"|-----------|-------|--------|----------|----------|-------|",
        ]
        for t in items:
            error_snippet = (t.get("error_message") or "")[:60].replace("\n", " ")
            if error_snippet:
                error_snippet = f"`{error_snippet}...`"
            lines.append(
                f"| {t.get('test_name', 'N/A')[:50]} "
                f"| {(t.get('suite_name') or 'N/A')[:30]} "
                f"| {_status(t.get('status'))} "
                f"| {(t.get('duration_ms') or 0) / 1000:.1f}s "
                f"| {t.get('failure_category') or '-'} "
                f"| {error_snippet or '-'} |"
            )
            # Append test_id as a subtle row annotation for follow-up tool calls
            lines.append(f"> ID: `{t['id']}`")

        return "\n".join(lines)

    @mcp.tool()
    async def get_test_case(
        run_id: str,
        test_id: str,
        include_steps: bool = False,
    ) -> str:
        """
        Get full details of a single test case including error message, labels, and metadata.

        Args:
            run_id: Test run UUID.
            test_id: Test case UUID (from list_test_cases).
            include_steps: When True, also fetch and render the granular step tree
                (step #/indent, name, status, duration, assertion message) from the
                LATEST-RUN-ONLY snapshot. Best-effort — older runs have no steps and
                the default output is unchanged when omitted.
        """
        t = await api.get(f"/api/v1/runs/{run_id}/tests/{test_id}")

        lines = [
            f"## Test: {t.get('test_name', 'N/A')}",
            f"- **ID:** `{t['id']}`",
            f"- **Status:** {_status(t.get('status'))}",
            f"- **Suite:** {t.get('suite_name', 'N/A')}",
            f"- **Full Name:** `{t.get('full_name', 'N/A')}`",
            f"- **Class:** `{t.get('class_name', 'N/A')}`",
            f"- **Package:** `{t.get('package_name', 'N/A')}`",
            f"- **Duration:** {(t.get('duration_ms') or 0) / 1000:.2f}s",
            f"- **Failure Category:** {t.get('failure_category') or 'Not categorised'}",
            "",
            "### Allure Labels",
            f"- **Severity:** {t.get('severity') or 'N/A'}",
            f"- **Feature:** {t.get('feature') or 'N/A'}",
            f"- **Story:** {t.get('story') or 'N/A'}",
            f"- **Epic:** {t.get('epic') or 'N/A'}",
            f"- **Owner:** {t.get('owner') or 'N/A'}",
            f"- **Tags:** {', '.join(t.get('tags') or []) or 'None'}",
            f"- **Has Attachments:** {t.get('has_attachments', False)}",
        ]

        if t.get("error_message"):
            lines += [
                "",
                "### Error Message",
                f"```",
                t["error_message"][:3000],
                f"```",
            ]

        if include_steps:
            try:
                tree = await api.get(f"/api/v1/runs/{run_id}/tests/{test_id}/steps")
                if isinstance(tree, dict):
                    lines += _render_step_tree(tree)
            except Exception:
                # Best-effort: degrade gracefully if steps are unavailable
                # (older runs have none / endpoint 404) — keep core detail intact.
                lines += ["", "### Steps", "_Granular step data unavailable._"]

        lines += [
            "",
            f"*Use `trigger_ai_analysis` with test_case_id `{t['id']}` for root-cause analysis.*",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def get_test_step_flips(run_id: str, test_id: str) -> str:
        """
        Show which step oscillated PASSED<->FAILED across runs for one test (FLK-P6).

        Unlike `get_test_case(include_steps=True)` — a LATEST-RUN-ONLY snapshot —
        this reads the retained per-run step outcomes and reports step-level
        flakiness: which step flickered, how often, how many runs observed it, and
        its current status. Use it to pin a single flaky step instead of
        quarantining a whole test. Read-only.

        Args:
            run_id: Test run UUID (from list_test_runs).
            test_id: Test case UUID (from list_test_cases).
        """
        payload = await api.get(f"/api/v1/runs/{run_id}/tests/{test_id}/step-flips")
        if not isinstance(payload, dict):
            return "No step-flip report available for this test."
        return _render_step_flips(payload)
