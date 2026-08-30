"""Tools: Reports — PDF export and share links."""

from __future__ import annotations

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def check_run_release_readiness(run_id: str) -> str:
        """
        Get the release readiness assessment for a single test run.
        Returns: recommendation (GO/CONDITIONAL_GO/NO_GO), risk score,
        blocking issues, and conditions for GO.

        For a project-level rollup over a time window, use
        ``check_release_readiness`` instead.
        """
        data = await api.get(f"/api/v1/release-readiness/{run_id}")

        lines = [
            f"## Release Readiness — Run {run_id}",
            f"**Recommendation:** {data.get('recommendation', '?')}",
            f"**Risk Score:** {data.get('risk_score', '?')}/100",
        ]

        blocking = data.get("blocking_issues", [])
        if blocking:
            lines.append("\n**Blocking Issues:**")
            for issue in blocking:
                lines.append(f"- {issue}")

        conditions = data.get("conditions_for_go", [])
        if conditions:
            lines.append("\n**Conditions for GO:**")
            for cond in conditions:
                lines.append(f"- {cond}")

        if data.get("reasoning"):
            lines.append(f"\n**Reasoning:** {data['reasoning'][:300]}")

        return "\n".join(lines)

    @mcp.tool()
    async def create_share_link(run_id: str, expires_days: int = 7, layout: str = "executive") -> str:
        """
        Create a time-limited share link for a run report.
        Returns the share URL and expiry details.
        """
        data = await api.post(
            f"/api/v1/reports/runs/{run_id}/share",
            json_body={"expiry_days": expires_days, "layout": layout},
        )
        return (
            f"Share link created for run {run_id}.\n"
            f"**Token:** {data.get('token', '?')}\n"
            f"**Expires:** {data.get('expires_at', '?')}\n"
            f"**Layout:** {layout}"
        )
