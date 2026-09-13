"""Tools: Run Intelligence — AI-powered test run analysis."""

from __future__ import annotations

import json

import client as api  # type: ignore[import]
import review_notice  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_run_intelligence(run_id: str) -> str:
        """
        Retrieve the unified Run Intelligence snapshot for a test run.

        Returns: executive summary, failure clusters, release decision,
        dimension scores, baseline diff, defect candidates, and provenance.
        """
        data = await api.get(f"/api/v1/runs/{run_id}/intelligence")
        run = data.get("run", {})
        decision = data.get("release_decision")
        summary = data.get("structured_summary", {})

        lines = [
            f"## Run Intelligence — Build {run.get('build_number', '?')}",
            f"**Status:** {run.get('status', '?')}  |  **Pass Rate:** {run.get('pass_rate', 0):.1f}%  |  **Failed:** {run.get('failed_tests', 0)}",
        ]

        if decision:
            lines.append(f"**Release:** {decision.get('recommendation', '?')} (risk: {decision.get('risk_score', '?')}/100)")

        if summary and summary.get("executive_summary"):
            lines.append(f"\n### Executive Summary\n{summary['executive_summary'][:500]}")

        deep = data.get("deep_pipeline_status", {})
        if deep and deep.get("status") != "never_run":
            lines.append(f"\n**Deep Analysis:** {deep.get('status', '?')} ({deep.get('completed_at', 'in progress')})")

        clusters = data.get("failure_clusters", [])
        if clusters:
            lines.append(f"\n### Failure Clusters ({len(clusters)})")
            for c in clusters[:5]:
                lines.append(f"- **{c.get('label', '?')}** — {c.get('size', 0)} tests, {c.get('criticality_level', '?')}")

        # E8.4: the agent must see whether a person has accepted this AI content.
        lines.extend(review_notice.review_lines(data))
        return "\n".join(lines)

    @mcp.tool()
    async def refresh_intelligence(run_id: str) -> str:
        """Force-refresh the cached intelligence snapshot for a run."""
        await api.post(f"/api/v1/runs/{run_id}/intelligence/refresh")
        return f"Intelligence snapshot refreshed for run {run_id}."

    @mcp.tool()
    async def get_run_summary(run_id: str, mode: str = "executive") -> str:
        """
        Get a mode-specific narrative summary for a run.
        Modes: executive, developer, manager.
        """
        data = await api.get(f"/api/v1/runs/{run_id}/summary", params={"mode": mode})
        summary = data.get("executive_summary") or data.get("markdown_report", "No summary available.")
        return "\n".join([f"## {mode.title()} Summary", "", str(summary), *review_notice.review_lines(data)])
