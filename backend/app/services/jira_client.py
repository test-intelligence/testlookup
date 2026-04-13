"""Jira REST API v3 client with ADF payload builder and self-healing retry."""
import base64
import logging
from typing import Any, cast


from app.core.config import settings
from app.core.http_client import get_http_client
from app.services.resilience import async_retry

logger = logging.getLogger(__name__)


def _auth_header() -> str:
    raw = f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}"
    return f"Basic {base64.b64encode(raw.encode()).decode()}"


def _adf_description(ai_summary: str, stack_trace: str, recommended_action: str, dashboard_link: str) -> dict:
    """Build an Atlassian Document Format (ADF) description payload."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "panel",
                "attrs": {"panelType": "warning"},
                "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": "🤖 AI Triage Summary", "marks": [{"type": "strong"}]},
                    ]},
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": ai_summary},
                    ]},
                ],
            },
            {"type": "heading", "attrs": {"level": 3},
             "content": [{"type": "text", "text": "Recommended Action"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": recommended_action}]},
            {"type": "heading", "attrs": {"level": 3},
             "content": [{"type": "text", "text": "Stack Trace"}]},
            {
                "type": "codeBlock",
                "attrs": {"language": "text"},
                "content": [{"type": "text", "text": stack_trace[:3000]}],
            },
            {"type": "paragraph", "content": [
                {"type": "text", "text": "🔗 "},
                {"type": "text", "text": "View in TestLookup",
                 "marks": [{"type": "link", "attrs": {"href": dashboard_link}}]},
            ]},
        ],
    }


async def create_jira_issue(
    project_key: str,
    test_name: str,
    run_id: str,
    ai_summary: str,
    recommended_action: str,
    stack_trace: str = "",
    dashboard_link: str = "",
) -> dict:
    """Create a Jira Bug issue from AI analysis output."""
    if not settings.JIRA_ENABLED:
        raise RuntimeError("Jira integration is not enabled (JIRA_ENABLED=false)")

    url = f"https://{settings.JIRA_DOMAIN}/rest/api/3/issue"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _auth_header(),
    }
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": f"[Auto] Test Failure: {test_name} (Run: {run_id})",
            "issuetype": {"name": "Bug"},
            "description": _adf_description(ai_summary, stack_trace, recommended_action, dashboard_link),
            "labels": ["qa-insight-ai", "automated-triage"],
        }
    }

    async def _do_create() -> dict[str, Any]:
        client = get_http_client()
        resp = await client.post(url, headers=headers, json=payload, timeout=15.0)
        resp.raise_for_status()
        data = cast(dict[str, Any], resp.json())
        ticket_key = data["key"]
        return {
            "ticket_id": data["id"],
            "ticket_key": ticket_key,
            "ticket_url": f"https://{settings.JIRA_DOMAIN}/browse/{ticket_key}",
        }

    return cast(dict[Any, Any], await async_retry(
        _do_create,
        max_retries=settings.AI_MAX_RETRIES,
        base_delay=2.0,
        max_delay=30.0,
        operation_name=f"jira_create_issue({project_key}/{test_name[:40]})",
    ))
