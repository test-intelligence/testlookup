"""Tools: list_test_runs, get_run_details, list_test_cases, get_test_case,
get_test_step_flips, get_run_timing."""

from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]
import execution_format  # type: ignore[import]
import untrusted  # type: ignore[import]
from tools.metrics import DEFAULT_WINDOW_DAYS  # type: ignore[import]

#: ``get_run_timing``: slowest tests listed by default, and the route's cap.
RUN_TIMING_DEFAULT_SLOWEST = 10
RUN_TIMING_MAX_SLOWEST = 100
#: ``GET /api/v1/runs?order=``: receipt time, newest first (the API's default is build order).
RUNS_NEWEST_FIRST = "newest"
#: ``GET /api/v1/runs/{id}/tests?status=``: the statuses a "failure" list holds (TL-QA-06).
FAILURE_STATUSES = ("FAILED", "BROKEN")

_STATUS_EMOJI = {
    "PASSED": "✅",
    "FAILED": "❌",
    "IN_PROGRESS": "⏳",
    "STOPPED": "⏹",
    "SKIPPED": "⏭",
    "BROKEN": "💥",
    "UNKNOWN": "❓",
}


def _window_text(days: int) -> str:
    """The window a list covers, in words: "in the last 30 days", or "of all time" for 0."""
    if days == 0:
        return "of all time"
    return f"in the last {days} day{'' if days == 1 else 's'}"


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
        # Step names and assertion messages come from the test report: marked as data.
        name = untrusted.inline(node.get("name"), limit=60, missing="(unnamed)")
        dur_ms = node.get("duration_ms") or 0
        raw_msg = " ".join(str(node.get("assertion_message") or "").split())
        msg = untrusted.inline(raw_msg[:60] + ("…" if len(raw_msg) > 60 else ""), missing="")
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
        # The summary sentence quotes step names from the report.
        untrusted.inline(report.get("summary"), missing=""),
        "",
        "| Step | Flips | Runs Observed | Current |",
        "|------|-------|---------------|---------|",
    ]
    for s in report.get("flipping_steps") or []:
        label = untrusted.inline(s.get("step_name"), limit=60, missing=f"step #{s.get('ordinal', '?')}")
        lines.append(
            f"| {label} "
            f"| {s.get('flip_count', 0)} "
            f"| {s.get('runs_observed', 0)} "
            f"| {_status(s.get('last_status') or 'UNKNOWN')} |"
        )
    return "\n".join(untrusted.with_notice(lines))


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_test_runs(
        project_id: str,
        status: Optional[str] = None,
        days: int = DEFAULT_WINDOW_DAYS,
        page: int = 1,
        size: int = 20,
    ) -> str:
        """
        List recent test runs for a project, newest first.

        Args:
            project_id: Project UUID (from list_projects).
            status: Filter by run status: PASSED, FAILED, IN_PROGRESS, or STOPPED.
            days: How far back to look (0-365, 0 = all time; default 30, as the REST API).
            page: Page number (default 1).
            size: Results per page (1-100, default 20).
        """
        data = await api.get(
            api.path("/api/v1/runs"),
            params={
                "project_id": project_id,
                "status": status,
                "days": days,
                # TL-QA-03: "newest first" is receipt time. Without this the
                # API sorts by build label, so `checkout-900` led `billing-12`.
                "order": RUNS_NEWEST_FIRST,
                "page": page,
                "size": size,
            },
        )

        items = data.get("items", [])
        total = data.get("total", 0)
        pages = data.get("pages", 1)

        window = _window_text(days)
        if not items:
            return (
                f"No test runs found for project `{project_id}` {window} with the given filters."
                + ("" if days == 0 else " Pass days=0 for all time.")
            )

        lines = [
            f"## Test Runs {window} — Page {page}/{pages} ({total} total)\n",
            f"| Build | Branch | Status | Pass Rate | Tests | Duration | Trigger | Started |",
            f"|-------|--------|--------|-----------|-------|----------|---------|---------|",
        ]
        for r in items:
            duration_s = (r.get("duration_ms") or 0) / 1000
            lines.append(
                f"| #{untrusted.short(r.get('build_number'), missing='?')} `{r['id'][:8]}` "
                f"| {untrusted.inline(r.get('branch'))} "
                f"| {_status(r.get('status'))} "
                f"| {execution_format.pass_rate(r.get('pass_rate'))} "
                f"| {r.get('total_tests', 0)} "
                f"| {duration_s:.0f}s "
                f"| {untrusted.short(r.get('trigger_source'))} "
                f"| {(r.get('start_time') or '')[:16]} |"
            )

        if days != 0:
            lines.append(f"\n*{total} run(s) {window}; pass days=0 for all time.*")
        lines.append(f"\n*Use `get_run_details` with a run ID for full details.*")
        return "\n".join(untrusted.with_notice(lines))

    @mcp.tool()
    async def get_run_details(run_id: str) -> str:
        """
        Get full details and aggregated statistics for a specific test run.

        Args:
            run_id: Test run UUID (from list_test_runs).
        """
        r = await api.get(api.path("/api/v1/runs/{run_id}", run_id=api.uuid_id(run_id)))

        duration_s = (r.get("duration_ms") or 0) / 1000
        total = r.get("total_tests", 0)
        passed = r.get("passed_tests", 0)
        failed = r.get("failed_tests", 0)
        skipped = r.get("skipped_tests", 0)
        broken = r.get("broken_tests", 0)

        lines = [
            f"## Run #{untrusted.short(r.get('build_number'), missing='?')} — {_status(r.get('status'))}",
            f"- **Run ID:** `{r['id']}`",
            f"- **Project:** `{r.get('project_id', 'N/A')}`",
            f"- **Branch:** {untrusted.inline(r.get('branch'))}",
            f"- **Commit:** `{untrusted.short(r.get('commit_hash'), limit=12)}`",
            f"- **Trigger:** {untrusted.short(r.get('trigger_source'))}",
            f"- **Jenkins Job:** {untrusted.inline(r.get('jenkins_job'))}",
            "",
            "### Test Results",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Tests | {total} |",
            f"| ✅ Passed | {passed} |",
            f"| ❌ Failed | {failed} |",
            f"| 💥 Broken | {broken} |",
            f"| ⏭ Skipped | {skipped} |",
            f"| Pass Rate | **{execution_format.pass_rate(r.get('pass_rate'), digits=2)}** |",
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
                f"- **Pod:** {untrusted.inline(r['ocp_pod_name'])}",
                f"- **Node:** {untrusted.inline(r.get('ocp_node'))}",
                f"- **Namespace:** {untrusted.inline(r.get('ocp_namespace'))}",
            ]

        return "\n".join(untrusted.with_notice(lines))

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
            api.path("/api/v1/runs/{run_id}/tests", run_id=api.uuid_id(run_id)),
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
            error_snippet = (t.get("error_message") or "")[:60]
            if error_snippet.strip():
                error_snippet = untrusted.inline(error_snippet + "...")
            lines.append(
                f"| {untrusted.inline(t.get('test_name'), limit=50)} "
                f"| {untrusted.inline(t.get('suite_name'), limit=30)} "
                f"| {_status(t.get('status'))} "
                f"| {(t.get('duration_ms') or 0) / 1000:.1f}s "
                f"| {t.get('failure_category') or '-'} "
                f"| {error_snippet.strip() or '-'} |"
            )
            # Append test_id as a subtle row annotation for follow-up tool calls
            lines.append(f"> ID: `{t['id']}`")

        return "\n".join(untrusted.with_notice(lines))

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
        t = await api.get(api.path("/api/v1/runs/{run_id}/tests/{test_id}", run_id=api.uuid_id(run_id), test_id=api.uuid_id(test_id)))

        lines = [
            f"## Test: {untrusted.inline(t.get('test_name'))}",
            f"- **ID:** `{t['id']}`",
            f"- **Status:** {_status(t.get('status'))}",
            f"- **Suite:** {untrusted.inline(t.get('suite_name'))}",
            f"- **Full Name:** {untrusted.inline(t.get('full_name'))}",
            f"- **Class:** {untrusted.inline(t.get('class_name'))}",
            f"- **Package:** {untrusted.inline(t.get('package_name'))}",
            f"- **Duration:** {(t.get('duration_ms') or 0) / 1000:.2f}s",
            f"- **Failure Category:** {t.get('failure_category') or 'Not categorised'}",
            "",
            "### Allure Labels",
            f"- **Severity:** {untrusted.inline(t.get('severity'))}",
            f"- **Feature:** {untrusted.inline(t.get('feature'))}",
            f"- **Story:** {untrusted.inline(t.get('story'))}",
            f"- **Epic:** {untrusted.inline(t.get('epic'))}",
            f"- **Owner:** {untrusted.inline(t.get('owner'))}",
            f"- **Tags:** {untrusted.inline(', '.join(str(tag) for tag in t.get('tags') or []), missing='None')}",
            f"- **Has Attachments:** {t.get('has_attachments', False)}",
        ]

        if t.get("error_message"):
            # Not a code fence: a message holding three backticks used to close it.
            lines += ["", "### Error Message", *untrusted.block(t["error_message"], limit=3000)]

        if include_steps:
            try:
                tree = await api.get(api.path("/api/v1/runs/{run_id}/tests/{test_id}/steps", run_id=api.uuid_id(run_id), test_id=api.uuid_id(test_id)))
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
        return "\n".join(untrusted.with_notice(lines))

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
        payload = await api.get(api.path("/api/v1/runs/{run_id}/tests/{test_id}/step-flips", run_id=api.uuid_id(run_id), test_id=api.uuid_id(test_id)))
        if not isinstance(payload, dict):
            return "No step-flip report available for this test."
        return _render_step_flips(payload)

    @mcp.tool()
    async def get_run_timing(run_id: str, slowest: int = RUN_TIMING_DEFAULT_SLOWEST) -> dict:
        """
        How long ONE test run took and where the time went: its wall clock
        (how long it took on the clock), its test time (the sum of the test
        durations) and their ratio, the parallelism; setup / test body /
        teardown totals; each suite's wall clock and test time; the slowest
        tests against their duration baselines; and the run's duration
        regressions (tests slower than their baseline, suites slower than
        their history). Read-only.

        Returns {ok, summary, has_duration_regressions, timing}: `summary` is
        compact markdown, `timing` the REST body of
        GET /api/v1/runs/{run_id}/timing (the waterfall's bars left out).
        Reading it: a null figure means the test report did not carry it --
        "not reported", NEVER 0 (a JUnit run has no timestamps, so no wall
        clock and no parallelism). A block with `measured: false` says why in
        `reason`; do not read it as "nothing got slower". Durations are
        milliseconds. For a project's trend over many runs, per suite, use
        `get_execution_metrics`.

        Errors come back as {ok: false, status_code, detail}: an unknown run,
        or one you cannot read, is a 404.

        Args:
            run_id: Test run UUID (from list_test_runs).
            slowest: How many slowest tests to list (1-100, default 10).
        """
        slowest = max(1, min(int(slowest), RUN_TIMING_MAX_SLOWEST))
        try:
            body = await api.get(
                api.path("/api/v1/runs/{run_id}/timing", run_id=api.uuid_id(run_id)),
                params={"slowest": slowest, "timeline_limit": 1},
            )
        except Exception as exc:
            return api.error_payload(exc)
        if not isinstance(body, dict):
            return {"ok": False, "error": "unexpected response from the timing endpoint"}
        timing = {key: value for key, value in body.items() if key != "timeline"}
        timeline = body.get("timeline") if isinstance(body.get("timeline"), dict) else {}
        timing["timeline"] = {
            key: timeline.get(key) for key in ("measured", "reason", "total", "origin", "end")
        }
        return {
            "ok": True,
            "content_notice": untrusted.DATA_NOTICE,
            "summary": execution_format.run_timing_markdown(body),
            "has_duration_regressions": execution_format.has_duration_findings(
                body.get("duration_findings")
            ),
            "timing": timing,
        }
