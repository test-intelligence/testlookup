"""Tools: the agent catalog and single-agent invocations (architecture E1.5).

One registry, two transports: the catalog the REST API serves (E1.1), as MCP tools.

  * ``list_agents``          -- GET  /api/v1/agents/catalog
  * ``get_agent``            -- GET  /api/v1/agents/catalog/{agent_id}
  * ``invoke_agent``         -- POST /api/v1/agents/{agent_id}/invoke   (SIDE EFFECT: runs the agent)
  * ``get_agent_invocation`` -- GET  /api/v1/agents/invocations/{invocation_id}

``INVOKABLE_AGENT_IDS`` mirrors the backend's invocable set
(``agent_planner.invocation_workflow_type``);
``backend/tests/test_mcp_agent_catalog_parity.py`` fails when they drift. Every
other catalog agent is still reachable through ``list_agents`` / ``get_agent``.

Accepting or rejecting the review of an invoked report is deliberately NOT
here: the MCP server forwards the caller's token, so an agent could approve
its own output (section 8.3).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import client as api  # type: ignore[import]
import review_notice  # type: ignore[import]

#: Agents that can be invoked on their own. Mirrors the backend registry; see the module docstring.
INVOKABLE_AGENT_IDS: frozenset[str] = frozenset({
    "agent.anomaly_detection.v1",
    "agent.change_ownership.v1",
    "agent.contract_validation.v1",
    "agent.decision_report.v1",
    "agent.decision_report_critic.v1",
    "agent.defect_commander.v1",
    "agent.failure_clustering.v1",
    "agent.flaky_sentinel.v1",
    "agent.gap_detection.v1",
    "agent.ingestion.v1",
    "agent.log_intelligence.v1",
    "agent.regression_watchman.v1",
    "agent.release_risk.v1",
    "agent.report_refinement.v1",
    "agent.root_cause_analysis.v1",
    "agent.summary.v1",
    "agent.test_health.v1",
    "agent.triage.v1",
})

AGENT_ID_PATTERN = re.compile(r"^agent\.[a-z_]+\.v\d+$")
_MODES = ("async", "sync")
_MAX_OUTPUT_CHARS = 1500


def _yes(value: Any) -> str:
    return "yes" if value else "no"


def render_catalog(entries: Any) -> str:
    """Markdown table of the catalog, marking what can be invoked directly."""
    if not isinstance(entries, list) or not entries:
        return "No agents are registered."
    lines = [
        f"## Agent catalog ({len(entries)})",
        "",
        "| Agent | Permission | Invocable | Sync | Report (needs review) |",
        "|-------|------------|-----------|------|-----------------------|",
    ]
    for entry in entries:
        agent_id = entry.get("agent_id", "?")
        lines.append(
            f"| `{agent_id}` | {entry.get('permission', '?')} | {_yes(agent_id in INVOKABLE_AGENT_IDS)} "
            f"| {_yes(entry.get('sync_eligible'))} | {_yes(entry.get('produces_report'))} |"
        )
    lines += [
        "",
        "Run an invocable agent with `invoke_agent`. The others run only inside a workflow.",
    ]
    return "\n".join(lines)


def render_agent(detail: Any) -> str:
    """One catalog entry, including how to invoke it."""
    if not isinstance(detail, dict):
        return "Agent not found."
    agent_id = detail.get("agent_id", "?")
    input_note = "" if detail.get("input_schema_resolved") else " (label only: invoke with a test_run_id)"
    output_note = "" if detail.get("output_schema_resolved") else " (label only)"
    lines = [
        f"## `{agent_id}`",
        f"**Stage:** {detail.get('stage_name', '?')}  |  **Permission:** {detail.get('permission', '?')}"
        f"  |  **Execution:** {detail.get('execution', '?')}",
        f"**Input:** {detail.get('input_schema', '?')}{input_note}",
        f"**Output:** {detail.get('output_schema', '?')}{output_note}",
        f"**Depends on:** {', '.join(detail.get('dependencies') or []) or 'nothing'}",
        f"**Sync-eligible:** {_yes(detail.get('sync_eligible'))}  |  **Produces a report:** "
        f"{_yes(detail.get('produces_report'))}",
    ]
    if agent_id in INVOKABLE_AGENT_IDS:
        lines.append(f'\nInvoke with `invoke_agent(agent_id="{agent_id}", project_id=..., test_run_id=...)`.')
    else:
        lines.append("\nThis agent runs only inside a workflow; it cannot be invoked on its own.")
    if detail.get("produces_report"):
        lines.append("Its output is a draft until a person accepts the review in TestLookup.")
    return "\n".join(lines)


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise ValueError(f"{name} must be a UUID") from None


def invoke_body(
    agent_id: str,
    project_id: str,
    test_run_id: str,
    mode: str = "async",
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The invoke request body, or ValueError naming what is wrong."""
    if not AGENT_ID_PATTERN.match(agent_id or ""):
        raise ValueError("agent_id must look like agent.<name>.v<version>, for example agent.summary.v1")
    if agent_id not in INVOKABLE_AGENT_IDS:
        raise ValueError(f"{agent_id} cannot be invoked on its own; see list_agents")
    if mode not in _MODES:
        raise ValueError("mode must be 'async' or 'sync'")
    body = {
        "project_id": _uuid(project_id, "project_id"),
        "input": {"agent_id": agent_id, "payload": {"test_run_id": _uuid(test_run_id, "test_run_id")}},
        "mode": mode,
    }
    if config_overrides is not None:
        body["config_overrides"] = config_overrides
    return body


def render_invocation(data: Any) -> str:
    """An invocation's status, output and review state."""
    if not isinstance(data, dict):
        return "Invocation not found."
    lines = [
        f"## Invocation `{data.get('id', '?')}`",
        f"**Agent:** `{data.get('agent_id', '?')}`  |  **Status:** {data.get('status', '?')}"
        f"  |  **Attempt:** {data.get('attempt', '?')}/{data.get('max_attempts', '?')}",
    ]
    if data.get("next_retry_at"):
        lines.append(f"**Next retry:** {data['next_retry_at']}")
    if data.get("error"):
        lines.append(f"**Error:** {str(data['error'])[:300]}")
    snapshot = data.get("config_snapshot")
    if isinstance(snapshot, dict):
        rendered_snapshot = json.dumps(snapshot, indent=2, default=str)
        if len(rendered_snapshot) > _MAX_OUTPUT_CHARS:
            rendered_snapshot = rendered_snapshot[: _MAX_OUTPUT_CHARS - 1] + "…"
        lines += ["", "### Frozen configuration", "```json", rendered_snapshot, "```"]
    output = data.get("output")
    if output is not None:
        text = json.dumps(output, indent=2, default=str)
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[: _MAX_OUTPUT_CHARS - 1] + "…"
        lines += ["", "### Output", "```json", text, "```"]
    elif data.get("status") == "in_progress":
        lines.append("\nStill running: poll again with get_agent_invocation.")
    lines.extend(review_notice.review_lines(data))
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_agents() -> str:
        """
        List every agent in TestLookup's catalog: its permission class, whether
        it can be invoked on its own, whether it can run synchronously, and
        whether its output is a report that needs human review.
        """
        return render_catalog(await api.get("/api/v1/agents/catalog"))

    @mcp.tool()
    async def get_agent(agent_id: str) -> str:
        """
        Describe one agent from the catalog: input and output contracts,
        dependencies, flags, and how to invoke it.

        Args:
            agent_id: Catalog id, e.g. agent.summary.v1.
        """
        if not AGENT_ID_PATTERN.match(agent_id or ""):
            return "agent_id must look like agent.<name>.v<version>, for example agent.summary.v1"
        return render_agent(await api.get(f"/api/v1/agents/catalog/{agent_id}"))

    @mcp.tool()
    async def invoke_agent(
        agent_id: str,
        project_id: str,
        test_run_id: str,
        mode: str = "async",
        config_overrides: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """
        SIDE EFFECT: run one agent on a stored test run. It spends the project's
        AI budget, and a mutating agent (see list_agents) can act on what it finds.
        Confirm with the user before invoking.

        The run goes through the same pipeline machinery as any analysis:
        retries, cancellation and human review apply. A report-producing
        agent's output is a DRAFT until a person accepts its review in
        TestLookup; say so when you pass it on.

        Idempotency: every call sends an Idempotency-Key. Reuse the returned
        `idempotency_key` when retrying the SAME invocation, and the backend
        returns the original instead of running the agent twice.

        Returns {ok, invocation_id, status, review_state, idempotency_key}. Poll
        get_agent_invocation until status is no longer in_progress. Failures
        come back as {ok: false, status_code, detail}: 403 (no access), 409
        (a request with this key is still being handled), 422 (invalid input,
        or the key was used for a different request).

        Args:
            agent_id: An invocable catalog id, e.g. agent.summary.v1.
            project_id: Project UUID; must be the test run's project.
            test_run_id: The stored test run to analyse.
            mode: "async" (default) or "sync" (waits briefly, only for sync-eligible agents).
            config_overrides: Optional backend-validated tighten-only override document.
            idempotency_key: Pass the key from an earlier call to retry it safely.
        """
        try:
            body = invoke_body(agent_id, project_id, test_run_id, mode, config_overrides)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        key = idempotency_key or str(uuid.uuid4())
        try:
            data = await api.post(
                f"/api/v1/agents/{agent_id}/invoke",
                json_body=body,
                extra_headers={"Idempotency-Key": key},
            )
        except Exception as exc:
            return {**api.error_payload(exc), "idempotency_key": key}
        data = data or {}
        return {
            "ok": True,
            "invocation_id": data.get("id"),
            "status": data.get("status"),
            "review_state": review_notice.review_state(data),
            "idempotency_key": key,
            "note": "Poll get_agent_invocation for status and output.",
        }

    @mcp.tool()
    async def get_agent_invocation(invocation_id: str) -> str:
        """
        Status, output and review state of one agent invocation.

        Args:
            invocation_id: The invocation_id returned by invoke_agent.
        """
        return render_invocation(await api.get(f"/api/v1/agents/invocations/{invocation_id}"))
