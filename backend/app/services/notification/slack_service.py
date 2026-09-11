"""Slack notifications via incoming webhooks (Block Kit)."""

import logging


logger = logging.getLogger(__name__)

_EVENT_EMOJI = {
    "run_failed": ":rotating_light:",
    "run_passed": ":white_check_mark:",
    "high_failure_rate": ":warning:",
    "ai_analysis_complete": ":robot_face:",
    "quality_gate_failed": ":red_circle:",
    "flaky_test_detected": ":ocean:",
    # Transition events (PMF US-7.1)
    "test.newly_failing": ":small_red_triangle:",
    "test.recovered": ":large_green_circle:",
    "test.newly_flaky": ":yellow_circle:",
    "test.quarantined": ":lock:",
    "test.unquarantined": ":unlock:",
    # Quarantine lifecycle events (PMF US-5.4 / US-5.5)
    "test.quarantine_stale": ":alarm_clock:",
    "test.ready_to_unquarantine": ":white_check_mark:",
}

_EVENT_COLOUR = {
    "run_failed": "#ef4444",
    "run_passed": "#22c55e",
    "high_failure_rate": "#f97316",
    "ai_analysis_complete": "#3b82f6",
    "quality_gate_failed": "#dc2626",
    "flaky_test_detected": "#a855f7",
    # Transition events (PMF US-7.1)
    "test.newly_failing": "#ef4444",
    "test.recovered": "#22c55e",
    "test.newly_flaky": "#eab308",
    "test.quarantined": "#f97316",
    "test.unquarantined": "#22c55e",
    # Quarantine lifecycle events (PMF US-5.4 / US-5.5)
    "test.quarantine_stale": "#f59e0b",
    "test.ready_to_unquarantine": "#22c55e",
}


def _build_blocks(
    title: str,
    body: str,
    event_type: str,
    metadata: dict,
) -> list:
    emoji = _EVENT_EMOJI.get(event_type, ":bell:")
    blocks: list = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji}  {title}", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body},
        },
    ]

    # Stats fields
    fields = []
    if metadata.get("project_name"):
        fields.append(
            {"type": "mrkdwn", "text": f"*Project*\n{metadata['project_name']}"}
        )
    if metadata.get("build_number"):
        fields.append(
            {"type": "mrkdwn", "text": f"*Build*\n#{metadata['build_number']}"}
        )
    if metadata.get("pass_rate") is not None:
        fields.append(
            {"type": "mrkdwn", "text": f"*Pass rate*\n{metadata['pass_rate']:.1f}%"}
        )
    if metadata.get("total_tests"):
        fields.append(
            {"type": "mrkdwn", "text": f"*Total tests*\n{metadata['total_tests']}"}
        )
    if metadata.get("failed_tests"):
        fields.append(
            {"type": "mrkdwn", "text": f"*Failed*\n{metadata['failed_tests']}"}
        )

    if fields:
        # Slack supports max 10 fields per section; chunk if needed
        for i in range(0, len(fields), 10):
            blocks.append({"type": "section", "fields": fields[i : i + 10]})

    dashboard_url = metadata.get("dashboard_url")
    if dashboard_url and dashboard_url != "#":
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "View in Dashboard",
                            "emoji": True,
                        },
                        "url": dashboard_url,
                        "style": "primary",
                    }
                ],
            }
        )

    return blocks


async def send_notification(
    webhook_url: str,
    title: str,
    body: str,
    event_type: str,
    metadata: dict | None = None,
    delivery_id: str | None = None,
    *,
    deployment_wide: bool = False,
) -> None:
    """
    POST a Block Kit message to a Slack incoming webhook URL.
    Raises httpx.HTTPStatusError on non-2xx response.

    ``deployment_wide`` is True only for the deployment's own webhook, the
    global Slack setting an admin configures. Only that one may use
    ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS``: every Slack workspace shares
    hooks.slack.com, so a user's or a team's webhook is judged by residency
    alone (code review of H10).
    """
    from app.services.notification.egress import assert_delivery_allowed_async

    # Re-audit H10: offline mode is documented as an egress ceiling, and
    # this path posted notification content to a caller-configured URL
    # without checking it. Residency, not channel name: a self-hosted
    # webhook on the LAN is still a legitimate offline destination.
    await assert_delivery_allowed_async("Slack", webhook_url, deployment_wide=deployment_wide)

    meta = metadata or {}
    colour = _EVENT_COLOUR.get(event_type, "#3b82f6")
    blocks = _build_blocks(title, body, event_type, meta)

    payload = {
        "text": title,  # fallback for notifications-only clients
        "attachments": [
            {
                "color": colour,
                "blocks": blocks,
                "fallback": title,
            }
        ],
    }

    from app.core.http_client import get_public_http_client

    client = get_public_http_client()
    headers = {"X-TestLookup-Delivery": delivery_id} if delivery_id else None
    response = await client.post(
        webhook_url,
        json=payload,
        headers=headers,
        timeout=10.0,
        follow_redirects=False,
    )
    response.raise_for_status()

    logger.info("Slack notification sent — event=%s", event_type)
