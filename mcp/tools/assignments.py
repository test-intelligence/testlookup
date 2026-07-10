"""MCP tools for failure assignment (PMF US-14.1).

Every FAILED/BROKEN test case is auto-assigned to its resolved suite
owner at ingest time and surfaces on that user's ``/my-failures`` inbox.
These tools let an agent re-route a failure to the right engineer —
a thin wrapper over the ``/api/v1/me/assigned-failures`` endpoints.

Authorization is server-side: the reassign endpoint requires the caller
(the MCP login identity) to be QA_LEAD or ADMIN on the failure's
project, and the new assignee must be the resolved suite owner or a
QA_ENGINEER project member (anyone else 422s).
"""
from __future__ import annotations

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_assignment_options(test_case_id: str) -> dict:
        """
        List the valid assignees for a failed test case (read-only).

        Returns the resolved suite owner plus every QA_ENGINEER project
        member — the only user ids `assign_failure` will accept. Call
        this first to translate a person's name into the user id.

        Args:
            test_case_id: The failed TestCase UUID (from run failure
                listings or the my-failures inbox).
        """
        try:
            data = await api.get(
                f"/api/v1/me/assigned-failures/{test_case_id}/reassign-options"
            )
        except Exception as exc:
            return api.error_payload(exc)
        return {"ok": True, "options": data}

    @mcp.tool()
    async def assign_failure(test_case_id: str, new_assignee_user_id: str) -> dict:
        """
        SIDE EFFECT: reassigns a FAILED/BROKEN test case to a new owner —
        it moves off the current assignee's /my-failures inbox and onto
        the new owner's.

        The caller (MCP login identity) must be QA_LEAD or ADMIN on the
        failure's project, and the new assignee must be one of the ids
        returned by `get_assignment_options` (the resolved suite owner or
        a QA_ENGINEER project member) — anything else is rejected
        server-side with a 422.

        Args:
            test_case_id: The failed TestCase UUID to reassign.
            new_assignee_user_id: User UUID of the new owner (validate
                via get_assignment_options first).
        """
        try:
            item = await api.put(
                f"/api/v1/me/assigned-failures/{test_case_id}/reassign",
                json_body={"new_assignee_user_id": new_assignee_user_id},
            )
        except Exception as exc:
            return api.error_payload(exc)
        return {
            "ok": True,
            "action": "reassigned",
            "test_case_id": test_case_id,
            "assigned_to_user_id": new_assignee_user_id,
            "test_name": (item or {}).get("test_name"),
            "navigation_url": (item or {}).get("navigation_url"),
        }
