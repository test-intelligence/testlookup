"""MCP tools for flaky-test quarantine — Tier 1 item 3 surface.

Exposes the review queue, stats, approve, and reject so an AI assistant
can drive the quarantine workflow on behalf of a QA Lead.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_quarantine_requests(
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        live_only: bool = False,
        limit: int = 50,
    ) -> str:
        """
        List flaky-test quarantine requests visible to the caller.

        Proposed requests are the review queue — a QA Lead reviews each
        and decides to approve (quarantine the test for N days), reject
        (leave it in rotation), or release an active quarantine early.

        Args:
            project_id: Optional — restrict to a single project.
            status: Optional — filter by status. One of: DETECTED |
                PROPOSED | APPROVED | QUARANTINED | RECHECK_SCHEDULED |
                RE_QUARANTINED | RELEASED | REJECTED | EXPIRED.
            live_only: When True, exclude terminal states (RELEASED,
                REJECTED, EXPIRED).
            limit: Max rows to return (default 50, cap 500).
        """
        params: dict = {"limit": limit, "live_only": live_only}
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status_filter"] = status

        data = await api.get("/api/v1/quarantine", params=params)
        if not data:
            return "No quarantine requests found for this filter."

        lines = [f"## Quarantine requests ({len(data)})\n"]
        for row in data:
            flip = row.get("flip_rate")
            flip_str = f"{flip * 100:.0f}%" if flip is not None else "—"
            window = row.get("flip_window_size") or "—"
            lines.append(
                f"- **{row.get('test_name') or row.get('test_fingerprint', '?')[:16]}** "
                f"· `{row['status']}` · flip {flip_str} over {window} runs"
            )
            if row.get("suite_name"):
                lines.append(f"  - Suite: {row['suite_name']}")
            lines.append(f"  - Request id: `{row['id']}`")
            if row.get("quarantine_expires_at"):
                lines.append(
                    f"  - Window ends: {(row['quarantine_expires_at'] or '')[:10]}"
                )
        return "\n".join(lines)

    @mcp.tool()
    async def get_quarantine_stats(project_id: Optional[str] = None) -> str:
        """
        Return counts per status for the quarantine workflow. Use this
        to answer "how many flaky proposals are waiting for review?"
        and similar aggregate questions.

        Args:
            project_id: Optional — scope to a single project.
        """
        params: dict = {}
        if project_id:
            params["project_id"] = project_id
        data = await api.get("/api/v1/quarantine/stats", params=params)

        lines = ["## Quarantine stats\n"]
        lines.append(f"- **Awaiting review:** {data.get('proposed', 0) + data.get('detected', 0)}")
        lines.append(
            f"- **Active quarantines:** "
            f"{data.get('quarantined', 0) + data.get('approved', 0) + data.get('recheck_scheduled', 0) + data.get('re_quarantined', 0)}"
        )
        lines.append(f"- **Released (stable):** {data.get('released', 0)}")
        lines.append(f"- **Rejected:** {data.get('rejected', 0)}")
        lines.append(f"- **Expired:** {data.get('expired', 0)}")
        lines.append(f"- **Total live:** {data.get('total_live', 0)}")
        return "\n".join(lines)

    @mcp.tool()
    async def approve_quarantine(
        request_id: str,
        notes: Optional[str] = None,
        quarantine_duration_days: Optional[int] = None,
    ) -> str:
        """
        Approve a flaky-test quarantine proposal. QA_LEAD+ required.

        The test is immediately marked QUARANTINED — subsequent test
        runs tag it with the ``quarantined`` tag and exclude it from
        release gate scoring. An auto-recheck cycle is scheduled one
        day before the window closes (default 14 days).

        Args:
            request_id: The quarantine request UUID to approve.
            notes: Human justification recorded on the audit trail.
            quarantine_duration_days: Override the default 14-day window.
        """
        body: dict = {}
        if notes is not None:
            body["notes"] = notes
        if quarantine_duration_days is not None:
            body["quarantine_duration_days"] = quarantine_duration_days
        try:
            row = await api.post(
                f"/api/v1/quarantine/{request_id}/approve",
                json_body=body,
            )
        except Exception as exc:
            return f"❌ Failed to approve: {exc}"
        return (
            f"✅ Quarantine approved for `{row.get('test_name') or request_id}`\n"
            f"- Status: **{row['status']}**\n"
            f"- Window ends: {(row.get('quarantine_expires_at') or '')[:10]}\n"
            f"- Recheck scheduled: {(row.get('recheck_at') or '')[:10]}"
        )

    @mcp.tool()
    async def reject_quarantine(
        request_id: str,
        notes: Optional[str] = None,
    ) -> str:
        """
        Reject a flaky-test quarantine proposal. QA_LEAD+ required.

        The test stays in normal rotation and the request row is
        preserved as REJECTED for the audit trail. Useful when the QA
        Lead looks at the flip-rate history and decides it's a legitimate
        failure, not flakiness.

        Args:
            request_id: The quarantine request UUID to reject.
            notes: Reason for the rejection — lands in the audit trail.
        """
        body: dict = {}
        if notes is not None:
            body["notes"] = notes
        try:
            row = await api.post(
                f"/api/v1/quarantine/{request_id}/reject",
                json_body=body,
            )
        except Exception as exc:
            return f"❌ Failed to reject: {exc}"
        return f"Quarantine rejected for `{row.get('test_name') or request_id}`."
