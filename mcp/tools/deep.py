"""Tools: Deep Investigation — multi-agent deep analysis pipeline."""

from __future__ import annotations

import json

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def trigger_deep_analysis(run_id: str) -> str:
        """
        Trigger the deep investigation pipeline for a test run.
        Returns the queued task info. Poll pipeline_status for progress.
        """
        data = await api.post(f"/api/v1/deep-investigate/{run_id}", json_body={"mode": "deep"})
        return (
            f"Deep analysis queued for run {run_id}.\n"
            f"Task ID: {data.get('task_id', '?')}\n"
            f"Message: {data.get('message', 'OK')}"
        )

    @mcp.tool()
    async def get_pipeline_status(run_id: str, workflow_type: str = "deep") -> str:
        """
        Check the execution status of the latest pipeline for a run.
        Returns: status, timestamps, stage summary counts.
        """
        data = await api.get(
            f"/api/v1/agents/runs/{run_id}/pipeline-status",
            params={"workflow_type": workflow_type},
        )
        status = data.get("status", "unknown")
        lines = [
            f"**Pipeline Status:** {status}",
            f"**Workflow:** {data.get('workflow_type', '?')}",
        ]
        if data.get("started_at"):
            lines.append(f"**Started:** {data['started_at']}")
        if data.get("completed_at"):
            lines.append(f"**Completed:** {data['completed_at']}")
        if data.get("error"):
            lines.append(f"**Error:** {data['error']}")

        summary = data.get("stage_summary", {})
        if summary:
            lines.append(f"**Stages:** {summary.get('completed', 0)} completed, {summary.get('failed', 0)} failed, {summary.get('skipped', 0)} skipped")

        return "\n".join(lines)

    @mcp.tool()
    async def get_failure_clusters(run_id: str) -> str:
        """Get failure clusters from deep investigation."""
        data = await api.get(f"/api/v1/deep-investigate/{run_id}/clusters")
        clusters = data if isinstance(data, list) else []
        if not clusters:
            return "No failure clusters found for this run."

        lines = [f"## Failure Clusters ({len(clusters)})"]
        for c in clusters:
            lines.append(f"\n### {c.get('label', '?')} ({c.get('size', 0)} tests)")
            if c.get('representative_error'):
                lines.append(f"*Error:* {c['representative_error'][:200]}")
        return "\n".join(lines)

    @mcp.tool()
    async def get_deep_findings(run_id: str) -> str:
        """Get root-cause findings from deep investigation."""
        data = await api.get(f"/api/v1/deep-investigate/{run_id}/findings")
        findings = data if isinstance(data, list) else []
        if not findings:
            return "No deep findings available for this run."

        lines = [f"## Deep Findings ({len(findings)})"]
        for f in findings:
            lines.append(f"\n### Cluster: {f.get('cluster_id', '?')}")
            lines.append(f"**Category:** {f.get('failure_category', '?')} | **Severity:** {f.get('severity', '?')}")
            if f.get('root_cause_summary'):
                lines.append(f"**Root Cause:** {f['root_cause_summary'][:300]}")
        return "\n".join(lines)
