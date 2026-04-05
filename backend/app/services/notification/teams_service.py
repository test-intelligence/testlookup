"""Microsoft Teams notifications via incoming webhooks (Adaptive Cards)."""
import logging

import httpx

logger = logging.getLogger(__name__)

_EVENT_COLOUR = {
    "run_failed": "attention",
    "run_passed": "good",
    "high_failure_rate": "warning",
    "ai_analysis_complete": "accent",
    "quality_gate_failed": "attention",
    "flaky_test_detected": "emphasis",
}

_EVENT_EMOJI = {
    "run_failed": "🚨",
    "run_passed": "✅",
    "high_failure_rate": "⚠️",
    "ai_analysis_complete": "🤖",
    "quality_gate_failed": "🔴",
    "flaky_test_detected": "🌊",
}


def _build_adaptive_card(
    title: str,
    body: str,
    event_type: str,
    metadata: dict,
) -> dict:
    """Build an Adaptive Card payload for Teams."""
    emoji = _EVENT_EMOJI.get(event_type, "🔔")
    colour = _EVENT_COLOUR.get(event_type, "accent")

    body_items: list = [
        {
            "type": "TextBlock",
            "text": f"{emoji} {title}",
            "weight": "Bolder",
            "size": "Large",
            "color": colour,
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": body,
            "wrap": True,
            "spacing": "Medium",
            "color": "Default",
        },
    ]

    # Fact set for metadata
    facts = []
    if metadata.get("project_name"):
        facts.append({"title": "Project", "value": metadata["project_name"]})
    if metadata.get("build_number"):
        facts.append({"title": "Build", "value": f"#{metadata['build_number']}"})
    if metadata.get("pass_rate") is not None:
        facts.append({"title": "Pass rate", "value": f"{metadata['pass_rate']:.1f}%"})
    if metadata.get("total_tests"):
        facts.append({"title": "Total tests", "value": str(metadata["total_tests"])})
    if metadata.get("failed_tests"):
        facts.append({"title": "Failed", "value": str(metadata["failed_tests"])})

    if facts:
        body_items.append({"type": "FactSet", "facts": facts, "spacing": "Medium"})

    actions = []
    dashboard_url = metadata.get("dashboard_url")
    if dashboard_url and dashboard_url != "#":
        actions.append({
            "type": "Action.OpenUrl",
            "title": "View in Dashboard",
            "url": dashboard_url,
        })

    card: dict = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body_items,
    }
    if actions:
        card["actions"] = actions

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


async def send_notification(
    webhook_url: str,
    title: str,
    body: str,
    event_type: str,
    metadata: dict | None = None,
) -> None:
    """
    POST an Adaptive Card to a Microsoft Teams incoming webhook URL.
    Raises httpx.HTTPStatusError on non-2xx response.
    """
    meta = metadata or {}
    payload = _build_adaptive_card(title, body, event_type, meta)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()

    logger.info("Teams notification sent — event=%s", event_type)
