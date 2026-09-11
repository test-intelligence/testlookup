"""Microsoft Teams notifications via incoming webhooks (Adaptive Cards)."""

import logging


logger = logging.getLogger(__name__)

_EVENT_COLOUR = {
    "run_failed": "attention",
    "run_passed": "good",
    "high_failure_rate": "warning",
    "ai_analysis_complete": "accent",
    "quality_gate_failed": "attention",
    "flaky_test_detected": "emphasis",
    # Transition events (PMF US-7.1)
    "test.newly_failing": "attention",
    "test.recovered": "good",
    "test.newly_flaky": "warning",
    "test.quarantined": "warning",
    "test.unquarantined": "good",
    # Quarantine lifecycle events (PMF US-5.4 / US-5.5)
    "test.quarantine_stale": "warning",
    "test.ready_to_unquarantine": "good",
}

_EVENT_EMOJI = {
    "run_failed": "🚨",
    "run_passed": "✅",
    "high_failure_rate": "⚠️",
    "ai_analysis_complete": "🤖",
    "quality_gate_failed": "🔴",
    "flaky_test_detected": "🌊",
    # Transition events (PMF US-7.1)
    "test.newly_failing": "🔴",
    "test.recovered": "🟢",
    "test.newly_flaky": "🟡",
    "test.quarantined": "🔒",
    "test.unquarantined": "🔓",
    # Quarantine lifecycle events (PMF US-5.4 / US-5.5)
    "test.quarantine_stale": "⏰",
    "test.ready_to_unquarantine": "✅",
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
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "View in Dashboard",
                "url": dashboard_url,
            }
        )

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
    delivery_id: str | None = None,
    *,
    deployment_wide: bool = False,
) -> None:
    """
    POST an Adaptive Card to a Microsoft Teams incoming webhook URL.
    Raises httpx.HTTPStatusError on non-2xx response.

    ``deployment_wide`` is True only for the deployment's own webhook, the
    global Teams setting an admin configures. Only that one may use
    ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS``: every tenant's webhook lives under
    webhook.office.com, so a user's or a team's webhook is judged by residency
    alone (code review of H10).
    """
    from app.services.notification.egress import assert_delivery_allowed_async

    # Re-audit H10: offline mode is documented as an egress ceiling, and
    # this path posted notification content to a caller-configured URL
    # without checking it. Residency, not channel name: a self-hosted
    # webhook on the LAN is still a legitimate offline destination.
    await assert_delivery_allowed_async("Teams", webhook_url, deployment_wide=deployment_wide)

    meta = metadata or {}
    payload = _build_adaptive_card(title, body, event_type, meta)

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

    logger.info("Teams notification sent — event=%s", event_type)
