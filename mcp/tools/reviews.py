"""Tools: human review queue (architecture E8.4, section 8.3).

Read-only by design. Accepting or rejecting an AI report is a person's
decision. The MCP server forwards the caller's bearer token, so an accept tool
would let an agent acting for a QA lead approve its own output. There is
deliberately no accept or reject tool, and ``tests/test_mcp_reviews.py`` fails
if one appears.
"""

from __future__ import annotations

from typing import Any

import client as api  # type: ignore[import]

MAX_LIMIT = 200


def render_pending_reviews(project_id: str, rows: Any) -> str:
    """Markdown for a project's open review queue."""
    if not isinstance(rows, list) or not rows:
        return f"No AI reports are awaiting human review in project {project_id}."
    lines = [
        f"## Pending reviews ({len(rows)})",
        "",
        "| Review | Kind | Subject | Workflow | Created |",
        "|--------|------|---------|----------|---------|",
    ]
    for row in rows:
        lines.append(
            f"| `{row.get('id', '?')}` | {row.get('kind', '?')} "
            f"| {row.get('subject_type', '?')} `{row.get('subject_id', '?')}` "
            f"| {row.get('workflow_type') or '-'} | {row.get('created_at', '?')} |"
        )
    lines += [
        "",
        "These reports are drafts until a person accepts them in TestLookup. "
        "Accepting or rejecting a review is not available through MCP.",
    ]
    disclaimer = rows[0].get("ai_disclaimer") if isinstance(rows[0], dict) else None
    if disclaimer:
        lines.append(f"> {disclaimer}")
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_pending_reviews(project_id: str, limit: int = 50) -> str:
        """
        List a project's AI-generated reports that still await human review.

        Read-only. Use it to tell the user which AI reports are still drafts.
        Accepting or rejecting a review is a human decision made in TestLookup;
        no MCP tool can do it.

        Args:
            project_id: Project UUID.
            limit: Maximum reviews to return (1-200, default 50).
        """
        limit = max(1, min(int(limit), MAX_LIMIT))
        rows = await api.get(
            f"/api/v1/projects/{project_id}/reviews",
            params={"state": "pending_review", "limit": limit},
        )
        return render_pending_reviews(project_id, rows)
