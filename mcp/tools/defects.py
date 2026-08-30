"""MCP tools for defects — reads via ``/api/v1/analytics/defects``.

Defects are the output of the defect_commander pipeline and represent
failure clusters promoted into actionable bugs with optional Jira ticket
links. An AI assistant can use these tools to answer "what's open for
this project" and "which defects have the highest severity" without
driving the UI.

PMF US-14.1 adds ``create_defect`` — a thin wrapper over the one-click
Jira endpoint (US-6.1/US-6.3). RBAC (QA_ENGINEER+ and project
membership) is enforced server-side against the MCP login identity; the
backend re-assembles the issue payload server-side and applies
dedup-first semantics, so the tool can never file a client-tampered
stack trace or a duplicate ticket.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]


def _signature_params(
    fingerprint: Optional[str], cluster_id: Optional[str]
) -> dict:
    """Validate the failure-signature pair: exactly one must be set."""
    if bool(fingerprint) == bool(cluster_id):
        raise ValueError(
            "Provide exactly one of `fingerprint` or `cluster_id` to "
            "identify the failure signature."
        )
    return {"fingerprint": fingerprint} if fingerprint else {"cluster_id": cluster_id}


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def list_defects(
        project_id: str,
        resolution_status: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """
        List defects for a project.

        Args:
            project_id: The project UUID to scope the listing.
            resolution_status: Optional filter — one of OPEN, IN_PROGRESS,
                RESOLVED, WONT_FIX, DUPLICATE. Default returns all statuses.
            limit: Max rows to return (default 20, cap 100).
        """
        params = {
            "project_id": project_id,
            "resolution_status": resolution_status,
            "page": 1,
            "size": min(max(1, limit), 100),
        }
        data = await api.get("/api/v1/analytics/defects", params=params)
        items = (data or {}).get("items", [])
        if not items:
            return f"No defects found for project `{project_id}` with the given filter."

        lines = [
            f"## Defects — project `{project_id}`"
            + (f" ({resolution_status})" if resolution_status else ""),
            f"_Showing {len(items)} of {data.get('total', len(items))}_\n",
        ]
        # Render only fields ``/api/v1/analytics/defects`` actually returns.
        # This block previously read severity/title/summary/description/
        # test_case_id/cluster_id -- none of which appear in that endpoint's
        # SELECT list -- so every row rendered "Untitled · severity ?" and the
        # model had no signal the field was absent rather than merely unset.
        for d in items:
            status = d.get("resolution_status") or "OPEN"
            category = d.get("failure_category") or "UNKNOWN"
            jira = d.get("jira_ticket_id")
            jira_url = d.get("jira_ticket_url")
            if jira and jira_url:
                jira_note = f" · Jira: [{jira}]({jira_url})"
            elif jira:
                jira_note = f" · Jira: `{jira}`"
            else:
                jira_note = " · Jira: unlinked"
            lines.append(
                f"- **{d.get('test_name') or 'Unlinked defect'}** "
                f"· {category} · status `{status}`{jira_note}"
            )
            detail = []
            if d.get("suite_name"):
                detail.append(f"suite `{d['suite_name']}`")
            confidence = d.get("ai_confidence_score")
            if confidence is not None:
                detail.append(f"AI confidence {confidence}%")
            if d.get("created_at"):
                detail.append(f"opened {str(d['created_at'])[:10]}")
            if d.get("resolved_at"):
                detail.append(f"resolved {str(d['resolved_at'])[:10]}")
            if d.get("release_name"):
                detail.append(f"release `{d['release_name']}`")
            if d.get("jira_status"):
                detail.append(f"Jira status `{d['jira_status']}`")
            if d.get("external_status_conflict"):
                detail.append("Jira status conflicts with TestLookup")
            if detail:
                lines.append("  - " + " · ".join(detail))
        return "\n".join(lines)

    # ── Write path (PMF US-14.1) ───────────────────────────────────────────

    @mcp.tool()
    async def create_defect(
        project_id: str,
        fingerprint: Optional[str] = None,
        cluster_id: Optional[str] = None,
        target: str = "jira",
        issue_type: str = "Bug",
        jira_project_key: Optional[str] = None,
        assignee: Optional[str] = None,
        extra_comment: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """
        SIDE EFFECT (dry_run=False): files a Jira issue (target="jira") or
        emits a `defect.create_requested` outbound webhook (target="webhook")
        for a failure signature, and links the resulting defect in TestLookup.

        ALWAYS call with dry_run=True first and show the human the returned
        preview (summary, description, dedup verdict) before creating
        anything. dry_run=True has no side effects — it returns the exact
        server-prefilled payload the create call would use.

        Dedup-first: if an open defect is already linked to the same
        signature, the backend posts a "recurred in build X" comment on the
        existing ticket instead of filing a duplicate (`deduplicated: true`
        in the result). Requires QA_ENGINEER+ and project membership —
        both enforced server-side against the MCP login identity, and the
        defect row records that user as creator.

        Args:
            project_id: Project UUID.
            fingerprint: Test fingerprint identifying the failure. Provide
                exactly one of `fingerprint` / `cluster_id`.
            cluster_id: Failure-cluster id (from get_failure_clusters).
            target: "jira" files a real Jira issue; "webhook" emits the
                defect.create_requested event to outbound-webhook
                subscribers instead (for shops without Jira).
            issue_type: Jira issue type (default "Bug").
            jira_project_key: Override the project's configured Jira key.
            assignee: Jira accountId to assign the issue to.
            extra_comment: Extra context appended to the issue description.
            dry_run: True = return the server's preview payload only.
        """
        try:
            sig = _signature_params(fingerprint, cluster_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if dry_run:
            try:
                preview = await api.get(
                    f"/api/v1/projects/{project_id}/defects/jira/preview",
                    params=sig,
                )
            except Exception as exc:
                return api.error_payload(exc)
            return {
                "ok": True,
                "dry_run": True,
                "preview": preview,
                "note": (
                    "No issue was created. Show this preview to the user; "
                    "re-run with dry_run=False to file it."
                ),
            }

        body = {
            **sig,
            "target": target,
            "issue_type": issue_type,
            "jira_project_key": jira_project_key,
            "assignee": assignee,
            "extra_comment": extra_comment,
        }
        try:
            result = await api.post(
                f"/api/v1/projects/{project_id}/defects/jira",
                json_body={k: v for k, v in body.items() if v is not None},
            )
        except Exception as exc:
            return api.error_payload(exc)
        return {"ok": True, "dry_run": False, **(result or {})}
