"""Tools: Global Search — search across all system entities."""

from __future__ import annotations

import client as api  # type: ignore[import]


def _step_match_note(result: dict, query: str) -> str:
    """Best-effort note when a result matched on something other than its
    visible name/suite.

    The keyword search (Phase 3) also matches a test's failing step name /
    assertion message and its error message, but the search payload exposes
    only the test name and suite — not the error message or step text — so we
    cannot tell *which* hidden field matched. Heuristic: if the query text
    isn't visible in the test name or suite, the hit came from the error
    message or step text; note it (without over-asserting which) so the user
    understands why the result surfaced. Empty string when the query is
    plainly visible or absent.
    """
    q = (query or "").strip().lower()
    if not q:
        return ""
    haystack = " ".join(
        str(result.get(k) or "")
        for k in ("test_name", "suite_name")
    ).lower()
    if q not in haystack:
        return " · may match error/step text"
    return ""


def _render_global_search(data: dict, query: str) -> str:
    """Render partial metadata before interpreting an empty item list."""
    items = data.get("items", [])
    counts = data.get("entity_counts", {})
    exact = data.get("counts_are_exact", True)
    partial = data.get("result_status") == "partial"
    failed = ", ".join(data.get("failed_entity_types", []))
    detail = f" Unavailable sources: {failed}." if failed else ""

    if not items:
        if partial:
            return (
                f"No returned results for '{query}', but search was incomplete. "
                f"Totals are lower bounds.{detail}"
            )
        return f"No results for '{query}'."

    suffix = "" if exact else "+"
    lines = [f"## Global Search: '{query}' ({data.get('total', 0)}{suffix} results)"]
    if partial:
        lines.append(f"> Search totals are lower bounds.{detail}")
    if counts:
        lines.append("**Entity counts:** " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    for result in items[:10]:
        lines.append(
            f"- [{result.get('entity_type', '?')}] **{result.get('title', '?')}** "
            f"— {result.get('subtitle', '')}"
        )
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def search_test_cases(query: str, project_id: str | None = None) -> str:
        """
        Search test cases by name, error message, or suite (legacy
        test-case-only search endpoint).

        For the richer status/date-filtered search, use ``search_tests``;
        for cross-entity search use ``global_search``.
        """
        params = {"q": query, "search_type": "keyword", "size": 15}
        if project_id:
            params["project_id"] = project_id
        data = await api.get("/api/v1/search", params=params)
        items = data.get("items", [])
        if not items:
            return f"No test case results for '{query}'."

        lines = [f"## Search Results ({data.get('total', 0)} matches)"]
        for r in items[:10]:
            note = _step_match_note(r, query)
            lines.append(
                f"- **{r.get('test_name', '?')}** — {r.get('suite_name', 'no suite')} "
                f"[{r.get('status', '?')}]{note}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def global_search(query: str, entity_types: str | None = None, project_id: str | None = None) -> str:
        """
        Search across all TestLookup entities: tests, runs, suites, defects, flaky tests, releases.
        entity_types: comma-separated filter (e.g., "test_case,test_run,defect").
        """
        params: dict = {"q": query, "size": 15}
        if entity_types:
            params["entity_types"] = entity_types
        if project_id:
            params["project_id"] = project_id

        data = await api.get("/api/v1/search/global", params=params)
        return _render_global_search(data, query)
