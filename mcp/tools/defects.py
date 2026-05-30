"""MCP tools for defects — reads via ``/api/v1/analytics/defects``.

Defects are the output of the defect_commander pipeline and represent
failure clusters promoted into actionable bugs with optional Jira ticket
links. An AI assistant can use these tools to answer "what's open for
this project" and "which defects have the highest severity" without
driving the UI.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_defects(
        project_id: str,
        resolution_status: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """
        List defects for a project.

        Args:
            project_id: The project UUID to scope the listing.
            resolution_status: Optional filter — one of OPEN, IN_PROGRESS,
                RESOLVED, WONT_FIX, DUPLICATE. Default returns all statuses.
            limit: Max rows to return (default 20, cap 100).
        """
        params = {
            "project_id": project_id,
            "resolution_status": resolution_status,
            "page": 1,
            "size": min(max(1, limit), 100),
        }
        data = await api.get("/api/v1/analytics/defects", params=params)
        items = (data or {}).get("items", [])
        if not items:
            return f"No defects found for project `{project_id}` with the given filter."

        lines = [
            f"## Defects — project `{project_id}`"
            + (f" ({resolution_status})" if resolution_status else ""),
            f"_Showing {len(items)} of {data.get('total', len(items))}_\n",
        ]
        for d in items:
            severity = d.get("severity", "?")
            status = d.get("resolution_status", "?")
            jira = d.get("jira_ticket_id") or d.get("jira_key")
            jira_note = f" · Jira: `{jira}`" if jira else ""
            lines.append(
                f"- **{d.get('title') or d.get('summary') or 'Untitled'}** "
                f"· severity **{severity}** · status `{status}`{jira_note}"
            )
            if d.get("description"):
                desc = d["description"][:200]
                lines.append(f"  - {desc}")
            if d.get("test_case_id"):
                lines.append(f"  - Test case: `{d['test_case_id'][:8]}`")
            if d.get("cluster_id"):
                lines.append(f"  - Cluster: `{d['cluster_id']}`")
        return "\n".join(lines)
