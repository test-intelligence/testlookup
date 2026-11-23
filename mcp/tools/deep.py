"""Tools: Deep Investigation — multi-agent deep analysis pipeline."""

from __future__ import annotations

import json

import client as api  # type: ignore[import]
import untrusted  # type: ignore[import]

#: ``get_deep_findings``: characters of a finding's root cause shown (the web UI has the rest).
DEEP_ROOT_CAUSE_LIMIT = 300


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def trigger_deep_analysis(run_id: str) -> str:
        """
        Trigger the deep investigation pipeline for a test run.
        Returns the queued task, or the pipeline that is already running for
        the run (nothing new is queued then). Poll get_pipeline_status for progress.
        """
        data = await api.post(api.path("/api/v1/deep-investigate/{run_id}", run_id=api.uuid_id(run_id)), json_body={"mode": "deep"})
        data = data if isinstance(data, dict) else {}
        if data.get("already_in_progress"):
            # TL-QA-05: the API answered with the run's active pipeline and
            # dispatched nothing. There is no task id, so none is shown.
            return "\n".join(untrusted.with_notice([
                f"Deep analysis is already in progress for run {run_id}; no new pipeline was started.",
                f"Pipeline ID: {untrusted.short(data.get('pipeline_run_id'), limit=64, missing='not reported')}",
                f"Message: {untrusted.inline(data.get('message'), missing='OK')}",
                "Poll `get_pipeline_status` for its progress.",
            ]))
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
            # TL-QA-04: only what ``DeepFindingResponse`` carries. It has no
            # severity, and its explanation is ``root_cause``.
            facts = [f"**Category:** {untrusted.short(f.get('failure_category'), missing='not categorised')}"]
            confidence = f.get("confidence_score")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                basis = untrusted.short(f.get("confidence_basis"), missing="")
                facts.append(f"**Confidence:** {confidence:g}%" + (f" ({basis})" if basis else ""))
            origin = untrusted.short(f.get("origin"), missing="")
            if origin and origin != "unknown":
                facts.append(f"**Origin:** {origin}")
            lines.append(" | ".join(facts))
            if str(f.get("root_cause") or "").strip():
                # AI text written from test output: it can repeat what a report said.
                lines.append(f"**Root Cause:** {untrusted.inline(f['root_cause'], limit=DEEP_ROOT_CAUSE_LIMIT)}")
            else:
                lines.append("**Root Cause:** none recorded for this cluster.")
        return "\n".join(untrusted.with_notice(lines))
