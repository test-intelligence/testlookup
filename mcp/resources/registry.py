"""
MCP Resources — readable data objects that AI Assistants can inspect as context.

URI scheme:  testlookup://<entity>/<id>/<sub-resource>

Resources differ from tools: they are read passively as background context
rather than being explicitly invoked. AI Assistants read them automatically when
the URI appears in conversation.
"""

from __future__ import annotations

import json

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    # ── Project Resources ─────────────────────────────────────────────────────

    @mcp.resource("testlookup://projects")
    async def all_projects() -> str:
        """Index of all active QA projects."""
        data = await api.get("/api/v1/projects")
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}")
    async def single_project(project_id: str) -> str:
        """Full project record including integration configuration."""
        data = await api.get(f"/api/v1/projects/{project_id}")
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/metrics")
    async def project_metrics(project_id: str) -> str:
        """Live dashboard KPIs for a project (last 7 days)."""
        data = await api.get(
            "/api/v1/metrics/summary",
            params={"project_id": project_id, "days": 7},
        )
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/runs/latest")
    async def latest_runs(project_id: str) -> str:
        """The 10 most recent test runs for a project."""
        data = await api.get(
            "/api/v1/runs",
            params={"project_id": project_id, "page": 1, "size": 10},
        )
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/quarantine-manifest")
    async def quarantine_manifest(project_id: str) -> str:
        """CI quarantine manifest — currently-effective quarantined tests only
        (US-5.1): the identity tuples (fingerprint, test/suite/class name) that
        `testlookup ci-verdict` matches a run's failures against."""
        data = await api.get(f"/api/v1/projects/{project_id}/quarantine/manifest")
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/flaky-tests")
    async def flaky_tests(project_id: str) -> str:
        """Current flakiness leaderboard (last 30 days, top 20)."""
        data = await api.get(
            "/api/v1/analytics/flaky-tests",
            params={"project_id": project_id, "days": 30, "limit": 20},
        )
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/defects/open")
    async def open_defects(project_id: str) -> str:
        """All currently open defects linked to test failures."""
        data = await api.get(
            "/api/v1/analytics/defects",
            params={"project_id": project_id, "resolution_status": "OPEN", "size": 100},
        )
        return json.dumps(data, indent=2, default=str)

    # ── Run Resources ─────────────────────────────────────────────────────────

    @mcp.resource("testlookup://runs/{run_id}")
    async def single_run(run_id: str) -> str:
        """Full test run record with aggregated statistics."""
        data = await api.get(f"/api/v1/runs/{run_id}")
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://runs/{run_id}/failures")
    async def run_failures(run_id: str) -> str:
        """All failed and broken test cases in a run (up to 200)."""
        data = await api.get(
            f"/api/v1/runs/{run_id}/tests",
            params={"status": "FAILED", "page": 1, "size": 200},
        )
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://runs/{run_id}/investigation")
    async def run_investigation(run_id: str) -> str:
        """Latest Investigator-agent investigation for a run (AI-5), if any —
        status, hypothesis boards, and verdict. Null ``investigation`` with a
        note when the run has never been investigated."""
        run = await api.get(f"/api/v1/runs/{run_id}")
        project_id = (run or {}).get("project_id")
        latest = None
        if project_id:
            listing = await api.get(
                f"/api/v1/projects/{project_id}/investigations",
                params={"limit": 100, "offset": 0},
            )
            # Newest-first list; the first entry for this run is the latest.
            latest = next(
                (
                    item
                    for item in (listing or {}).get("items", [])
                    if str(item.get("run_id")) == str(run_id)
                ),
                None,
            )
        if latest is None:
            return json.dumps(
                {
                    "run_id": run_id,
                    "investigation": None,
                    "note": (
                        "No investigation recorded for this run — use the "
                        "start_investigation tool to launch one."
                    ),
                },
                indent=2,
                default=str,
            )
        detail = await api.get(f"/api/v1/investigations/{latest['id']}")
        return json.dumps(detail, indent=2, default=str)

    # ── Test Case Resources ───────────────────────────────────────────────────

    @mcp.resource("testlookup://tests/{run_id}/{test_id}")
    async def single_test(run_id: str, test_id: str) -> str:
        """Full test case record including error message and Allure labels."""
        data = await api.get(f"/api/v1/runs/{run_id}/tests/{test_id}")
        return json.dumps(data, indent=2, default=str)
