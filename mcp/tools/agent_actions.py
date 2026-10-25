"""Tools: the agent-action queue (W15 AGT-03, contract section 2.3).

Read-only by design. Approving, rejecting or undoing an agent action is a
person's decision made in TestLookup (Inbox > Approvals > Agent actions). The
MCP server forwards the caller's bearer token, so a decide tool would let an
agent acting for a QA lead approve the effects its own investigation proposed.
There is deliberately no approve / reject / execute / rollback / undo tool, and
``tests/test_mcp_agent_actions.py`` fails if one appears or if any tool reaches
a single action's path.
"""

from __future__ import annotations

from typing import Any

import client as api  # type: ignore[import]

MAX_LIMIT = 200
VIEWS = ("pending", "decided")
NOT_AVAILABLE = "Approving, rejecting or undoing an agent action is not available through MCP."


def _cell(value: Any) -> str:
    """One table cell: the server's text, kept on one line and out of the table syntax."""
    if value is None or value == "":
        return "-"
    text = " ".join(str(value).split())
    return text.replace("|", "/")[:200]


def _target(row: dict) -> Any:
    target = row.get("target")
    if isinstance(target, dict) and target.get("label"):
        return target.get("label")
    return row.get("target_id")


def render_agent_actions(project_id: str, view: str, payload: Any) -> str:
    """Markdown for one page of a project's agent-action queue."""
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        if view == "decided":
            return f"No agent actions have been decided in project {project_id}. {NOT_AVAILABLE}"
        return f"No agent actions are waiting for approval in project {project_id}. {NOT_AVAILABLE}"
    total = payload.get("total", payload.get("count", len(rows)))
    lines = [
        f"## Agent actions - {view} ({len(rows)} of {total})",
        "",
        "| Action | Type | Target | Status | Outcome |",
        "|--------|------|--------|--------|---------|",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {_cell(row.get('title') or row.get('id'))} "
            f"| {_cell(row.get('canonical_type') or row.get('action_type'))} "
            f"| {_cell(_target(row))} | {_cell(row.get('status'))} "
            f"| {_cell(row.get('outcome_text'))} |"
        )
    lines += [
        "",
        "Agent actions are AI proposals; a QA lead decides each one in TestLookup "
        "(Inbox > Approvals > Agent actions). " + NOT_AVAILABLE,
    ]
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_agent_actions(project_id: str, view: str = "pending", limit: int = 50) -> str:
        """
        List a project's agent actions: the typed proposals AI agents filed for a person to decide.

        Read-only. ``view="pending"`` (default) lists what waits for a QA lead's approval, oldest
        first; ``view="decided"`` lists the recent decisions and their outcomes. Approving,
        rejecting or undoing an agent action is a human decision made in TestLookup; no MCP
        tool can do it. Needs a QA lead's access to the project.

        Args:
            project_id: Project UUID.
            view: "pending" or "decided" (anything else reads as "pending").
            limit: Maximum actions to return (1-200, default 50).
        """
        view = view if view in VIEWS else "pending"
        limit = max(1, min(int(limit), MAX_LIMIT))
        payload = await api.get(
            f"/api/v1/projects/{project_id}/agent-actions",
            params={"view": view, "limit": limit},
        )
        return render_agent_actions(project_id, view, payload)
