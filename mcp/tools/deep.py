"""Tools: Deep Investigation — multi-agent deep analysis pipeline."""

from __future__ import annotations

import json

import client as api  # type: ignore[import]
import untrusted  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def trigger_deep_analysis(run_id: str) -> str:
        """
        Trigger the deep investigation pipeline for a test run.
        Returns the queued task info. Poll pipeline_status for progress.
        """
        data = await api.post(api.path("/api/v1/deep-investigate/{run_id}", run_id=api.uuid_id(run_id)), json_body={"mode": "deep"})
        return "\n".join(untrusted.with_notice([
            f"Deep analysis queued for run {run_id}.",
            f"Task ID: {untrusted.short(data.get('task_id'), limit=64, missing='?')}",
            f"Message: {untrusted.inline(data.get('message'), missing='OK')}",
        ]))

    @mcp.tool()
    async def get_pipeline_status(run_id: str, workflow_type: str = "deep") -> str:
        """
        Check the execution status of the latest pipeline for a run.
        Returns: status, timestamps, stage summary counts.
        """
        data = await api.get(
            api.path("/api/v1/agents/runs/{run_id}/pipeline-status", run_id=run_id),
            params={"workflow_type": workflow_type},
        )
        status = untrusted.short(data.get("status"), missing="unknown")
        lines = [
            f"**Pipeline Status:** {status}",
            f"**Workflow:** {untrusted.short(data.get('workflow_type'), missing='?')}",
        ]
        if data.get("started_at"):
            lines.append(f"**Started:** {data['started_at']}")
        if data.get("completed_at"):
            lines.append(f"**Completed:** {data['completed_at']}")
        if data.get("error"):
            # A pipeline error can quote the test output it failed on.
            lines.append(f"**Error:** {untrusted.inline(data['error'])}")

        summary = data.get("stage_summary", {})
        if summary:
            lines.append(f"**Stages:** {summary.get('completed', 0)} completed, {summary.get('failed', 0)} failed, {summary.get('skipped', 0)} skipped")

        return "\n".join(untrusted.with_notice(lines))

    @mcp.tool()
    async def get_failure_clusters(run_id: str) -> str:
        """Get failure clusters from deep investigation."""
        data = await api.get(api.path("/api/v1/deep-investigate/{run_id}/clusters", run_id=api.uuid_id(run_id)))
        clusters = data if isinstance(data, list) else []
        if not clusters:
            return "No failure clusters found for this run."

        lines = [f"## Failure Clusters ({len(clusters)})"]
        for c in clusters:
            lines.append(f"\n### {untrusted.inline(c.get('label'), missing='?')} ({c.get('size', 0)} tests)")
            if c.get('representative_error'):
                lines.append(f"*Error:* {untrusted.inline(c['representative_error'], limit=200)}")
        return "\n".join(untrusted.with_notice(lines))

    @mcp.tool()
    async def get_deep_findings(run_id: str) -> str:
        """Get root-cause findings from deep investigation."""
        data = await api.get(api.path("/api/v1/deep-investigate/{run_id}/findings", run_id=api.uuid_id(run_id)))
        findings = data if isinstance(data, list) else []
        if not findings:
            return "No deep findings available for this run."

        lines = [f"## Deep Findings ({len(findings)})"]
        for f in findings:
            lines.append(f"\n### Cluster: {untrusted.short(f.get('cluster_id'), limit=64, missing='?')}")
            lines.append(
                f"**Category:** {untrusted.short(f.get('failure_category'), missing='?')} "
                f"| **Severity:** {untrusted.short(f.get('severity'), missing='?')}"
            )
            if f.get('root_cause_summary'):
                # AI text written from test output: it can repeat what a report said.
                lines.append(f"**Root Cause:** {untrusted.inline(f['root_cause_summary'], limit=300)}")
        return "\n".join(untrusted.with_notice(lines))
