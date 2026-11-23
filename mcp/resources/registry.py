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
import untrusted  # type: ignore[import]
from tools.runs import FAILURE_STATUSES, RUNS_NEWEST_FIRST  # type: ignore[import]

#: ``GET /projects/{id}/investigations``: the route's page cap.
INVESTIGATION_PAGE_SIZE = 100
#: Pages read looking for one run's investigation before the answer is "not known".
INVESTIGATION_MAX_PAGES = 20


async def _latest_investigation_for_run(project_id: str, run_id: str) -> tuple[dict | None, bool]:
    """The newest investigation summary of ``run_id``, and whether the search was complete (TL-QA-08).

    The list is asked for this run only (``run_id``), newest first, so the
    answer is normally its first row. Every row is still checked against the
    run and the pages are followed to ``total``: a backend that predates the
    filter returns the whole project's list, and reading only its first page
    reported "no investigation" for any run with 100 newer ones elsewhere.
    """
    wanted = run_id.strip().lower()
    offset = 0
    for _ in range(INVESTIGATION_MAX_PAGES):
        listing = await api.get(
            api.path("/api/v1/projects/{project_id}/investigations", project_id=api.uuid_id(project_id)),
            params={"run_id": run_id, "limit": INVESTIGATION_PAGE_SIZE, "offset": offset},
        )
        listing = listing if isinstance(listing, dict) else {}
        items = listing.get("items") or []
        for item in items:
            if isinstance(item, dict) and str(item.get("run_id")).strip().lower() == wanted:
                return item, True
        offset += len(items)
        total = listing.get("total")
        if not items:
            return None, True
        if isinstance(total, int):
            if offset >= total:
                return None, True
        elif len(items) < INVESTIGATION_PAGE_SIZE:
            # No total to go by: a short page is the last one.
            return None, True
    return None, False


def without_meta(data):  # noqa: ANN001, ANN201
    """The analytics body without its VIZ-204 ``meta`` envelope.

    A resource is read passively into an assistant's context, whole: the
    envelope (scope echo, totals, timestamps -- ~1 KB) is paid for in tokens
    on every read and says nothing a resource's fixed scope does not. Tools
    keep it; they are invoked for an answer and may need to cite the scope.
    """
    if isinstance(data, dict) and "meta" in data:
        return {key: value for key, value in data.items() if key != "meta"}
    return data


def register(mcp) -> None:  # noqa: ANN001

    # ── Project Resources ─────────────────────────────────────────────────────

    @mcp.resource("testlookup://projects")
    async def all_projects() -> str:
        """Index of all active QA projects."""
        data = await api.get(api.path("/api/v1/projects"))
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}")
    async def single_project(project_id: str) -> str:
        """Full project record including integration configuration."""
        data = await api.get(api.path("/api/v1/projects/{project_id}", project_id=api.uuid_id(project_id)))
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/metrics")
    async def project_metrics(project_id: str) -> str:
        """Live dashboard KPIs for a project (last 7 days)."""
        data = await api.get(
            api.path("/api/v1/metrics/summary"),
            params={"project_id": project_id, "days": 7},
        )
        body = without_meta(data)
        if not body:
            # TL-QA-17: a project the caller cannot read is answered 200 with
            # only the envelope. Without it that was ``{}``: say what it means.
            return json.dumps(
                {
                    "project_id": project_id,
                    "metrics": None,
                    "note": "No dashboard metrics were returned for this project "
                    "(no access to it, or no data in the last 7 days).",
                },
                indent=2,
            )
        return json.dumps(body, indent=2, default=str)

    @mcp.resource("testlookup://projects/{project_id}/runs/latest")
    async def latest_runs(project_id: str) -> str:
        """The 10 most recent test runs for a project, however old."""
        data = await api.get(
            api.path("/api/v1/runs"),
            # TL-QA-03: "most recent" is receipt time; the API's default is build order.
            # TL-QA-18: ``days=0`` is all time. The route's default window is 30
            # days, so a project quiet for a month read as having no runs.
            params={"project_id": project_id, "order": RUNS_NEWEST_FIRST, "days": 0, "page": 1, "size": 10},
        )
        return untrusted.document(data)

    @mcp.resource("testlookup://projects/{project_id}/quarantine-manifest")
    async def quarantine_manifest(project_id: str) -> str:
        """CI quarantine manifest — currently-effective quarantined tests only
        (US-5.1): the identity tuples (fingerprint, test/suite/class name) that
        `testlookup ci-verdict` matches a run's failures against."""
        data = await api.get(api.path("/api/v1/projects/{project_id}/quarantine/manifest", project_id=api.uuid_id(project_id)))
        return untrusted.document(data)

    @mcp.resource("testlookup://projects/{project_id}/flaky-tests")
    async def flaky_tests(project_id: str) -> str:
        """Current flakiness leaderboard (last 30 days, top 20).

        Project-wide. The web UI can narrow the same data to one release; this
        resource cannot, so it and a release-filtered dashboard answer
        different questions. For a release- or suite-scoped series, call the
        `get_chart_data` tool.
        """
        data = await api.get(
            api.path("/api/v1/analytics/flaky-tests"),
            params={"project_id": project_id, "days": 30, "limit": 20},
        )
        return untrusted.document(without_meta(data))

    @mcp.resource("testlookup://projects/{project_id}/defects/open")
    async def open_defects(project_id: str) -> str:
        """Live defects linked to test failures: OPEN and IN_PROGRESS, the
        first 100 (``total`` counts them all)."""
        data = await api.get(
            api.path("/api/v1/analytics/defects"),
            # TL-QA-16: ACTIVE is the API's word for OPEN + IN_PROGRESS. "OPEN"
            # alone left out the defects someone has started on, which still
            # block a release gate.
            params={"project_id": project_id, "resolution_status": "ACTIVE", "size": 100},
        )
        return untrusted.document(without_meta(data))

    # ── Run Resources ─────────────────────────────────────────────────────────

    @mcp.resource("testlookup://runs/{run_id}")
    async def single_run(run_id: str) -> str:
        """Full test run record with aggregated statistics."""
        data = await api.get(api.path("/api/v1/runs/{run_id}", run_id=api.uuid_id(run_id)))
        return untrusted.document(data)

    @mcp.resource("testlookup://runs/{run_id}/failures")
    async def run_failures(run_id: str) -> str:
        """All failed and broken test cases in a run (up to 200; ``total``
        counts every match)."""
        data = await api.get(
            api.path("/api/v1/runs/{run_id}/tests", run_id=api.uuid_id(run_id)),
            # TL-QA-06: one request for both statuses (``status`` repeats), so
            # the API orders, pages and counts their union.
            params={"status": list(FAILURE_STATUSES), "page": 1, "size": 200},
        )
        return untrusted.document(data)

    @mcp.resource("testlookup://runs/{run_id}/investigation")
    async def run_investigation(run_id: str) -> str:
        """Latest Investigator-agent investigation for a run (AI-5), if any —
        status, hypothesis boards, and verdict. Null ``investigation`` with a
        note when the run has never been investigated."""
        run = await api.get(api.path("/api/v1/runs/{run_id}", run_id=api.uuid_id(run_id)))
        project_id = (run or {}).get("project_id")
        latest, complete = None, True
        if project_id:
            latest, complete = await _latest_investigation_for_run(str(project_id), str(run_id))
        if latest is None and not complete:
            # Not "never investigated": the search stopped before the end.
            return untrusted.document(
                {
                    "run_id": run_id,
                    "investigation": None,
                    "search_complete": False,
                    "note": (
                        "The project's investigations could not be searched to the end, so "
                        "whether this run was investigated is not known. Use the "
                        "list_investigations tool, or open the run in the web UI."
                    ),
                }
            )
        if latest is None:
            # Same envelope as the found case, so this resource has one shape.
            return untrusted.document(
                {
                    "run_id": run_id,
                    "investigation": None,
                    "note": (
                        "No investigation recorded for this run — use the "
                        "start_investigation tool to launch one."
                    ),
                }
            )
        detail = await api.get(api.path("/api/v1/investigations/{investigation_id}", investigation_id=api.uuid_id(latest['id'])))
        return untrusted.document(detail)

    # ── Test Case Resources ───────────────────────────────────────────────────

    @mcp.resource("testlookup://tests/{run_id}/{test_id}")
    async def single_test(run_id: str, test_id: str) -> str:
        """Full test case record including error message and Allure labels."""
        data = await api.get(api.path("/api/v1/runs/{run_id}/tests/{test_id}", run_id=api.uuid_id(run_id), test_id=api.uuid_id(test_id)))
        return untrusted.document(data)
