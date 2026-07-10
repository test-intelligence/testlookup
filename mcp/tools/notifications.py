"""MCP tools for the transition-notification policy (PMF US-14.1 / US-7.1).

Per-project policy controlling which state-transition events
(newly-failing, recovered, newly-flaky, quarantine lifecycle, ...) fan
out to the notification channels, plus the legacy per-run event spam
toggle. Thin wrapper over
``/api/v1/notifications/projects/{project_id}/transition-policy``.

RBAC is server-side against the MCP login identity; the PUT requires
project access.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]

# Mirrors backend TRANSITION_EVENT_VALUES (schemas.py); backend validates.
TRANSITION_EVENTS = (
    "test.newly_failing",
    "test.recovered",
    "test.newly_flaky",
    "test.quarantined",
    "test.unquarantined",
    "test.quarantine_stale",
    "test.ready_to_unquarantine",
)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_transition_policy(project_id: str) -> dict:
        """
        Read a project's transition-notification policy (read-only).

        Shows whether transition events are enabled, which event types
        fire, the consecutive-failure threshold for "newly failing", and
        whether legacy per-run event spam is on. `is_default: true` means
        the project has no explicit policy row and the new-project
        defaults apply (transitions ON, per-run spam OFF).

        Args:
            project_id: Project UUID.
        """
        try:
            data = await api.get(
                f"/api/v1/notifications/projects/{project_id}/transition-policy"
            )
        except Exception as exc:
            return api.error_payload(exc)
        return {"ok": True, "policy": data}

    @mcp.tool()
    async def set_transition_policy(
        project_id: str,
        transitions_enabled: bool = True,
        per_run_events_enabled: bool = False,
        enabled_events: Optional[list[str]] = None,
        consecutive_failure_threshold: int = 2,
    ) -> dict:
        """
        SIDE EFFECT: replaces the project's transition-notification policy.
        This changes what notifications every member of the project
        receives — read the current policy with `get_transition_policy`
        first and only change the fields the user asked about.

        Requires project access (enforced server-side against the MCP
        login identity). The PUT is a full replace, not a patch: omitted
        fields fall back to the defaults shown below, not to the current
        values.

        Args:
            project_id: Project UUID.
            transitions_enabled: Master switch for transition events
                (default True).
            per_run_events_enabled: Legacy per-run event fan-out — noisy;
                keep False unless explicitly requested (default False).
            enabled_events: Which transition events fire. Default = all of:
                test.newly_failing, test.recovered, test.newly_flaky,
                test.quarantined, test.unquarantined, test.quarantine_stale,
                test.ready_to_unquarantine.
            consecutive_failure_threshold: Runs a test must fail in a row
                before "newly failing" fires (1-20, default 2).
        """
        body: dict = {
            "transitions_enabled": transitions_enabled,
            "per_run_events_enabled": per_run_events_enabled,
            "consecutive_failure_threshold": consecutive_failure_threshold,
        }
        if enabled_events is not None:
            unknown = [e for e in enabled_events if e not in TRANSITION_EVENTS]
            if unknown:
                return {
                    "ok": False,
                    "error": (
                        f"Unknown transition events: {unknown}. "
                        f"Allowed: {list(TRANSITION_EVENTS)}"
                    ),
                }
            body["enabled_events"] = enabled_events
        try:
            data = await api.put(
                f"/api/v1/notifications/projects/{project_id}/transition-policy",
                json_body=body,
            )
        except Exception as exc:
            return api.error_payload(exc)
        return {"ok": True, "action": "policy_updated", "policy": data}
