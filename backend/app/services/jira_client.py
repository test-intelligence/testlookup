"""Jira REST API v3 client with ADF payload builder and self-healing retry.

W10 INT-01 (D-7): the legacy ticket path (``POST /api/v1/integrations/jira``
and the triage stage) takes the Jira config saved at ``/settings/integrations``
(``defect_jira_service.resolve_jira_config``) as ``cfg``; without one it uses
THIS module's ``settings``. Before anything is sent it refuses, with a fixed
sentence (``JiraUnavailable``): offline mode FIRST, then a disabled or
unconfigured integration, then a host the egress guard refuses. A create is
retried only on errors that provably never reached Jira (a connect failure or
a pool timeout): a read timeout, a dropped connection or a 5xx may follow a
created issue and is raised, never re-POSTed.
"""
import base64
import logging
from typing import Any, Optional, cast

import httpx

from app.core.config import settings
from app.core.http_client import get_http_client
from app.services.resilience import async_retry
from app.services.url_safety import egress_block_reason

logger = logging.getLogger(__name__)

LEGACY_JIRA_REASON_TEXT = {
    "offline_mode": "Jira is unavailable: AI_OFFLINE_MODE is enabled, so outbound calls are blocked.",
    "disabled": "Jira is unavailable: the Jira integration is disabled in Settings › Integrations.",
    "not_configured": "Jira is unavailable: set the domain, email and API token in Settings › Integrations.",
    "target_not_allowed": "Jira is unavailable: Target host is not allowed.",
}

#: Errors raised before the request could reach Jira: safe to retry a create.
_UNSENT_ERRORS: tuple[type[Exception], ...] = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class JiraUnavailable(RuntimeError):
    """The legacy Jira path refused before sending anything (``reason`` is a
    key of ``LEGACY_JIRA_REASON_TEXT``; the message is its sentence)."""

    def __init__(self, reason: str):
        super().__init__(LEGACY_JIRA_REASON_TEXT.get(reason, reason))
        self.reason = reason


def env_jira_config() -> dict:
    """The Jira config of THIS module's settings, in ``resolve_jira_config``'s shape."""
    return {
        "enabled": settings.JIRA_ENABLED,
        "domain": settings.JIRA_DOMAIN,
        "email": settings.JIRA_EMAIL,
        "api_token": settings.JIRA_API_TOKEN,
        "default_project_key": settings.JIRA_DEFAULT_PROJECT_KEY,
    }


async def jira_unavailable_reason(cfg: dict) -> Optional[str]:
    """Why the legacy path must not reach Jira with ``cfg`` (``None`` = it may).

    ``offline_mode`` (checked FIRST, read live) | ``disabled`` |
    ``not_configured`` (``defect_jira_service.availability_reason``) |
    ``target_not_allowed`` (the egress guard on ``https://{domain}``). Keys of
    ``LEGACY_JIRA_REASON_TEXT``. No credential is sent by this check.
    """
    from app.services.defect_jira_service import availability_reason

    reason = availability_reason(cfg)
    if reason is not None:
        return reason
    block = await egress_block_reason(f"https://{str(cfg['domain']).strip()}")
    if block is not None:
        logger.info("Legacy Jira refused before sending credentials: %s", block)
        return "target_not_allowed"
    return None


def _auth_header(cfg: Optional[dict] = None) -> str:
    cfg = cfg if cfg is not None else env_jira_config()
    raw = f"{cfg.get('email')}:{cfg.get('api_token')}"
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
    *,
    cfg: Optional[dict] = None,
) -> dict:
    """Create a Jira Bug issue from AI analysis output.

    ``cfg`` is ``resolve_jira_config``'s dict (the settings saved in the UI);
    ``None`` uses this module's settings. Raises ``JiraUnavailable`` before
    any request when offline mode is on (checked first), the integration is
    disabled or unconfigured, or the host is not allowed.
    """
    cfg = cfg if cfg is not None else env_jira_config()
    reason = await jira_unavailable_reason(cfg)
    if reason is not None:
        raise JiraUnavailable(reason)
    domain = str(cfg["domain"]).strip()

    url = f"https://{domain}/rest/api/3/issue"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _auth_header(cfg),
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
            "ticket_url": f"https://{domain}/browse/{ticket_key}",
        }

    # "A timeout is not a failure": only errors that never reached Jira are
    # retried; anything after the request was sent (a read timeout, a dropped
    # connection, any HTTP status) may follow a created issue and is raised.
    return cast(dict[Any, Any], await async_retry(
        _do_create,
        max_retries=settings.AI_MAX_RETRIES,
        base_delay=2.0,
        max_delay=30.0,
        retryable_exceptions=_UNSENT_ERRORS,
        retryable_status_codes=frozenset(),
        operation_name=f"jira_create_issue({project_key}/{test_name[:40]})",
    ))
