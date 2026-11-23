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
import untrusted  # type: ignore[import]


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
                RESOLVED, INVALID, or ACTIVE (OPEN and IN_PROGRESS together).
                Default returns all statuses.
            limit: Max rows to return (default 20, cap 100).
        """
        params = {
            "project_id": project_id,
            "resolution_status": resolution_status,
            "page": 1,
            "size": min(max(1, limit), 100),
        }
        data = await api.get(api.path("/api/v1/analytics/defects"), params=params)
        items = (data or {}).get("items", [])
        if not items:
            return f"No defects found for project `{project_id}` with the given filter."

        lines = [
            f"## Defects — project `{project_id}`"
            + (f" ({resolution_status})" if resolution_status else ""),
            f"_Showing {len(items)} of {data.get('total', len(items))}_\n",
        ]
        # Render only fields ``/api/v1/analytics/defects`` actually returns:
        # among them the title, severity and priority a person entered (O-12).
        # A defect filed by hand has no test, so its title is its name.
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
            name = untrusted.inline(d.get("title") or d.get("test_name"), limit=120, missing="Untitled defect")
            lines.append(f"- **{name}** · {category} · status `{status}`{jira_note}")
            detail = []
            if d.get("title") and d.get("test_name"):
                detail.append(f"test {untrusted.inline(d['test_name'], limit=80)}")
            if d.get("severity"):
                detail.append(f"severity {untrusted.short(d['severity'])}")
            if d.get("priority"):
                detail.append(f"priority {untrusted.short(d['priority'])}")
            if d.get("suite_name"):
                detail.append(f"suite {untrusted.inline(d['suite_name'])}")
            confidence = d.get("ai_confidence_score")
            if confidence is not None:
                detail.append(f"AI confidence {confidence}%")
            if d.get("created_at"):
                detail.append(f"opened {str(d['created_at'])[:10]}")
            if d.get("resolved_at"):
                detail.append(f"resolved {str(d['resolved_at'])[:10]}")
            if d.get("release_name"):
                detail.append(f"release {untrusted.inline(d['release_name'])}")
            if d.get("jira_status"):
                detail.append(f"Jira status `{untrusted.short(d['jira_status'])}`")
            if d.get("external_status_conflict"):
                detail.append("Jira status conflicts with TestLookup")
            if detail:
                lines.append("  - " + " · ".join(detail))
        return "\n".join(untrusted.with_notice(lines))

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
        confirm_not_filed: bool = False,
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
            jira_project_key: Jira project to file under. It must be the project's own Jira key
                when it has one; without one only a QA lead or administrator may name a key
                other than the default (a refused key answers 422 with the allowed one).
            assignee: Jira accountId to assign the issue to.
            extra_comment: Extra context appended to the issue description.
            dry_run: True = return the server's preview payload only.
            confirm_not_filed: Set True only after a 409 ``jira_outcome_unknown``
                and only once the user has checked Jira and the issue is not
                there: it tells the server to file it. Ignored otherwise.
        """
        try:
            sig = _signature_params(fingerprint, cluster_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if dry_run:
            try:
                preview = await api.get(
                    api.path("/api/v1/projects/{project_id}/defects/jira/preview", project_id=api.uuid_id(project_id)),
                    params=sig,
                )
            except Exception as exc:
                return api.error_payload(exc)
            return {
                "ok": True,
                "dry_run": True,
                # The preview's summary and description are built from the failing test's output.
                "content_notice": untrusted.DATA_NOTICE,
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
            "confirm_not_filed": True if confirm_not_filed else None,
        }
        try:
            result = await api.post(
                api.path("/api/v1/projects/{project_id}/defects/jira", project_id=api.uuid_id(project_id)),
                json_body={k: v for k, v in body.items() if v is not None},
            )
        except Exception as exc:
            return api.error_payload(exc)
        result = result or {}
        if target == "webhook" and result.get("subscriptions_notified") == 0:
            # O-12: a 200 with nobody notified is not a delivery.
            return {**result, "ok": False, "dry_run": False, "delivered": False,
                    "detail": result.get("message") or "No enabled webhook subscription received the defect."}
        return {"ok": True, "dry_run": False, **result}
