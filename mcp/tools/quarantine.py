"""MCP tools for flaky-test quarantine — Tier 1 item 3 surface.

Exposes the review queue, stats, approve, and reject so an AI assistant
can drive the quarantine workflow on behalf of a QA Lead.

PMF US-14.1 adds the write path: propose, release, and bulk-promote.
All writes are thin wrappers over ``/api/v1/quarantine`` — RBAC
(QA_LEAD+) and the ``SettingsAuditLog`` audit trail are enforced
server-side against the JWT identity the MCP server logged in with.
"""
from __future__ import annotations

from typing import Any, Optional

import client as api  # type: ignore[import]

# Bulk-release safety cap for promote_ready_quarantines (US-14.1).
PROMOTE_BATCH_CAP = 10


def _select_ready_to_promote(rows: list[dict], cap: int = PROMOTE_BATCH_CAP) -> list[dict]:
    """Pure filter: the first ``cap`` rows flagged ``ready_to_promote``.

    ``ready_to_promote`` is derived server-side (US-5.5): the test has
    passed ``promote_after_passes`` consecutive times while quarantined
    but the project's ``auto_promote`` policy is off, so a human (or an
    agent acting for one) must release it explicitly.
    """
    ready = [r for r in rows if r.get("ready_to_promote")]
    return ready[: max(0, cap)]


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

    # ── Write path (PMF US-14.1) ───────────────────────────────────────────

    @mcp.tool()
    async def propose_quarantine(
        project_id: str,
        fingerprint: str,
        reason: str,
        test_name: Optional[str] = None,
        suite_name: Optional[str] = None,
        quarantine_duration_days: int = 14,
    ) -> dict:
        """
        SIDE EFFECT: creates (or refreshes) a PROPOSED quarantine request.

        This is a *proposal*, not a quarantine — the test stays in normal
        rotation until a QA Lead (human or agent-assisted) approves it via
        `approve_quarantine`. Human approval stays in the loop by design.
        Idempotent: an existing live request for the same (project,
        fingerprint) is merged/refreshed instead of duplicated.

        Requires QA_LEAD+ (enforced server-side against the MCP login
        identity) and the `flaky_auto_quarantine` feature flag (503 when
        off). The proposal and every later transition land in the
        settings audit log with the acting user's id.

        Args:
            project_id: Project UUID the test belongs to.
            fingerprint: Test fingerprint (sha256(class::test)[:16] — the
                same id shown by failure/flaky tools).
            reason: Why this test should be quarantined — recorded in the
                proposal's rationale for the reviewing QA Lead.
            test_name: Optional human-readable test name for the queue.
            suite_name: Optional suite name for the queue.
            quarantine_duration_days: Proposed window (1-90, default 14).
        """
        body: dict[str, Any] = {
            "project_id": project_id,
            "test_fingerprint": fingerprint,
            "detection_method": "manual",
            "rationale": {"reason": reason, "source": "mcp"},
            "quarantine_duration_days": quarantine_duration_days,
        }
        if test_name:
            body["test_name"] = test_name
        if suite_name:
            body["suite_name"] = suite_name
        try:
            row = await api.post("/api/v1/quarantine", json_body=body)
        except Exception as exc:
            return api.error_payload(exc)
        return {
            "ok": True,
            "action": "proposed",
            "request_id": row.get("id"),
            "status": row.get("status"),
            "test_name": row.get("test_name"),
            "fingerprint": row.get("test_fingerprint"),
            "next_step": (
                "Awaiting QA Lead review — approve with approve_quarantine("
                f"request_id='{row.get('id')}') or reject with reject_quarantine."
            ),
        }

    @mcp.tool()
    async def release_quarantine(request_id: str, reason: str) -> dict:
        """
        SIDE EFFECT: ends an active quarantine early — the test returns to
        normal rotation and counts against release gates again immediately.

        Only valid from an active state (QUARANTINED / RECHECK_SCHEDULED /
        RE_QUARANTINED); other states return a 409 conflict. Requires
        QA_LEAD+ (server-side RBAC) and is written to the settings audit
        log with the acting user and the reason given here.

        Args:
            request_id: The quarantine request UUID (from
                list_quarantine_requests or promote_ready_quarantines).
            reason: Why the quarantine is being released (e.g. "fix for
                flaky wait merged in #123") — recorded as reviewer notes.
        """
        try:
            row = await api.post(
                f"/api/v1/quarantine/{request_id}/release",
                json_body={"notes": reason},
            )
        except Exception as exc:
            return api.error_payload(exc)
        return {
            "ok": True,
            "action": "released",
            "request_id": row.get("id"),
            "status": row.get("status"),
            "test_name": row.get("test_name"),
            "fingerprint": row.get("test_fingerprint"),
        }

    @mcp.tool()
    async def promote_ready_quarantines(
        project_id: str,
        dry_run: bool = True,
    ) -> dict:
        """
        Release quarantined tests that are ready to return to rotation.

        "Ready to promote" (US-5.5) = the test passed the project policy's
        `promote_after_passes` consecutive-pass threshold while quarantined,
        but `auto_promote` is off so a human decision is required.

        With dry_run=True (the default) this only LISTS the candidates —
        no side effects. Review the list with the user before re-running
        with dry_run=False, which releases at most 10 per call (each
        release is reported individually; failures don't stop the batch).
        Requires QA_LEAD+; every release is audit-logged server-side.

        Args:
            project_id: Project UUID to scan.
            dry_run: True = report candidates only; False = release them
                (capped at 10 per call).
        """
        try:
            rows = await api.get(
                "/api/v1/quarantine",
                params={"project_id": project_id, "live_only": True, "limit": 500},
            )
        except Exception as exc:
            return api.error_payload(exc)

        candidates = _select_ready_to_promote(rows or [])
        listing = [
            {
                "request_id": r.get("id"),
                "test_name": r.get("test_name"),
                "fingerprint": r.get("test_fingerprint"),
                "consecutive_passes": r.get("consecutive_passes"),
                "status": r.get("status"),
            }
            for r in candidates
        ]
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "ready_to_promote": listing,
                "count": len(listing),
                "note": (
                    "No changes made. Re-run with dry_run=False to release "
                    f"these (max {PROMOTE_BATCH_CAP} per call) after the user confirms."
                ),
            }

        results = []
        for entry in listing:
            try:
                row = await api.post(
                    f"/api/v1/quarantine/{entry['request_id']}/release",
                    json_body={
                        "notes": (
                            "Promoted out of quarantine: reached the "
                            "consecutive-pass threshold (via MCP "
                            "promote_ready_quarantines)."
                        )
                    },
                )
                results.append(
                    {**entry, "released": True, "status": row.get("status")}
                )
            except Exception as exc:
                results.append({**entry, "released": False, **api.error_payload(exc)})
        return {
            "ok": all(r.get("released") for r in results) if results else True,
            "dry_run": False,
            "released_count": sum(1 for r in results if r.get("released")),
            "results": results,
        }
