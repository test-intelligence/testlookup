"""Tools: Global Search — search across all system entities."""

from __future__ import annotations

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def search_tests(query: str, project_id: str | None = None) -> str:
        """
        Search test cases by name, error message, or suite.
        Uses the legacy test-case-only search endpoint.
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
            lines.append(f"- **{r.get('test_name', '?')}** — {r.get('suite_name', 'no suite')} [{r.get('status', '?')}]")
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
        items = data.get("items", [])
        counts = data.get("entity_counts", {})

        if not items:
            return f"No results for '{query}'."

        lines = [f"## Global Search: '{query}' ({data.get('total', 0)} results)"]
        if counts:
            lines.append("**Entity counts:** " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
        for r in items[:10]:
            lines.append(f"- [{r.get('entity_type', '?')}] **{r.get('title', '?')}** — {r.get('subtitle', '')}")
        return "\n".join(lines)
