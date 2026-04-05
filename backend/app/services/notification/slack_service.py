"""Slack notifications via incoming webhooks (Block Kit)."""
import logging

import httpx

logger = logging.getLogger(__name__)

_EVENT_EMOJI = {
    "run_failed": ":rotating_light:",
    "run_passed": ":white_check_mark:",
    "high_failure_rate": ":warning:",
    "ai_analysis_complete": ":robot_face:",
    "quality_gate_failed": ":red_circle:",
    "flaky_test_detected": ":ocean:",
}

_EVENT_COLOUR = {
    "run_failed": "#ef4444",
    "run_passed": "#22c55e",
    "high_failure_rate": "#f97316",
    "ai_analysis_complete": "#3b82f6",
    "quality_gate_failed": "#dc2626",
    "flaky_test_detected": "#a855f7",
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
        fields.append({"type": "mrkdwn", "text": f"*Project*\n{metadata['project_name']}"})
    if metadata.get("build_number"):
        fields.append({"type": "mrkdwn", "text": f"*Build*\n#{metadata['build_number']}"})
    if metadata.get("pass_rate") is not None:
        fields.append({"type": "mrkdwn", "text": f"*Pass rate*\n{metadata['pass_rate']:.1f}%"})
    if metadata.get("total_tests"):
        fields.append({"type": "mrkdwn", "text": f"*Total tests*\n{metadata['total_tests']}"})
    if metadata.get("failed_tests"):
        fields.append({"type": "mrkdwn", "text": f"*Failed*\n{metadata['failed_tests']}"})

    if fields:
        # Slack supports max 10 fields per section; chunk if needed
        for i in range(0, len(fields), 10):
            blocks.append({"type": "section", "fields": fields[i : i + 10]})

    dashboard_url = metadata.get("dashboard_url")
    if dashboard_url and dashboard_url != "#":
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in Dashboard", "emoji": True},
                    "url": dashboard_url,
                    "style": "primary",
                }
            ],
        })

    return blocks


async def send_notification(
    webhook_url: str,
    title: str,
    body: str,
    event_type: str,
    metadata: dict | None = None,
) -> None:
    """
    POST a Block Kit message to a Slack incoming webhook URL.
    Raises httpx.HTTPStatusError on non-2xx response.
    """
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

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()

    logger.info("Slack notification sent — event=%s", event_type)
