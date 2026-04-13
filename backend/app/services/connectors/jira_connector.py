"""Jira knowledge connector — fetches issue/epic content via REST API v3."""
from __future__ import annotations

import base64
import re
import time
from typing import Optional

import httpx
import structlog

from app.core.config import settings
from app.core.http_client import get_http_client
from app.services.connectors.base import (
    ConnectorFetchError,
    FetchedContent,
    KnowledgeConnectorBase,
)

logger = structlog.get_logger(__name__)

_ISSUE_KEY_RE = re.compile(r"([A-Z][A-Z0-9]+-\d+)")


class JiraKnowledgeConnector(KnowledgeConnectorBase):
    """Handles jira_issue and jira_epic source types via Jira REST API v3."""

    connector_type = "jira_issue"

    def _base_url(self) -> str:
        domain = settings.JIRA_DOMAIN
        if not domain:
            raise ConnectorFetchError("JIRA_DOMAIN is not configured")
        return f"https://{domain}"

    def _auth_header(self) -> str:
        email = settings.JIRA_EMAIL
        token = settings.JIRA_API_TOKEN
        if not email or not token:
            raise ConnectorFetchError(
                "Jira credentials not configured (JIRA_EMAIL / JIRA_API_TOKEN)"
            )
        raw = f"{email}:{token}"
        return f"Basic {base64.b64encode(raw.encode()).decode()}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": self._auth_header(),
        }

    def _extract_issue_key(self, canonical_url: str, external_id: Optional[str]) -> str:
        """Extract the Jira issue key from URL or external_id."""
        if external_id:
            match = _ISSUE_KEY_RE.search(external_id)
            if match:
                return match.group(1)
            return external_id

        match = _ISSUE_KEY_RE.search(canonical_url)
        if match:
            return match.group(1)
        raise ConnectorFetchError(
            f"Cannot extract Jira issue key from URL: {canonical_url}"
        )

    async def test_connection(self) -> dict:
        t_start = time.monotonic()
        try:
            if not settings.JIRA_DOMAIN or not settings.JIRA_EMAIL or not settings.JIRA_API_TOKEN:
                return {
                    "success": False,
                    "latency_ms": 0,
                    "error": "Jira credentials not configured",
                    "detail": "Set JIRA_DOMAIN, JIRA_EMAIL, and JIRA_API_TOKEN",
                }
            url = f"{self._base_url()}/rest/api/3/myself"
            client = get_http_client()
            resp = await client.get(url, headers=self._headers(), timeout=10.0)
            latency = int((time.monotonic() - t_start) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "latency_ms": latency,
                    "error": None,
                    "detail": f"Authenticated as {data.get('displayName', data.get('emailAddress', 'unknown'))}",
                }
            return {
                "success": False,
                "latency_ms": latency,
                "error": f"HTTP {resp.status_code}",
                "detail": resp.text[:200],
            }
        except Exception as exc:
            latency = int((time.monotonic() - t_start) * 1000)
            return {
                "success": False,
                "latency_ms": latency,
                "error": str(exc)[:200],
                "detail": None,
            }

    async def fetch_content(
        self,
        canonical_url: str,
        external_id: Optional[str] = None,
    ) -> FetchedContent:
        issue_key = self._extract_issue_key(canonical_url, external_id)

        url = f"{self._base_url()}/rest/api/3/issue/{issue_key}"
        params = {"fields": "summary,description,issuetype,status,priority,labels,subtasks,comment"}

        try:
            client = get_http_client()
            resp = await client.get(url, headers=self._headers(), params=params, timeout=15.0)
            if resp.status_code == 401:
                raise ConnectorFetchError("Jira authentication failed — check credentials")
            if resp.status_code == 403:
                raise ConnectorFetchError(f"Access denied to issue {issue_key}")
            if resp.status_code == 404:
                raise ConnectorFetchError(f"Jira issue {issue_key} not found")
            resp.raise_for_status()
            data = resp.json()
        except ConnectorFetchError:
            raise
        except httpx.TimeoutException:
            raise ConnectorFetchError(f"Timeout fetching Jira issue {issue_key}", retryable=True)
        except httpx.HTTPStatusError as exc:
            raise ConnectorFetchError(f"Jira API error: HTTP {exc.response.status_code}")
        except Exception as exc:
            raise ConnectorFetchError(f"Failed to fetch Jira issue {issue_key}: {exc}", retryable=True)

        fields = data.get("fields", {})
        raw_text = self._normalize_issue(issue_key, fields)

        # For epics, fetch child issues
        issue_type = (fields.get("issuetype") or {}).get("name", "").lower()
        if issue_type == "epic":
            children_text = await self._fetch_epic_children(issue_key)
            if children_text:
                raw_text += f"\n\n## Epic Child Issues\n\n{children_text}"

        return FetchedContent(
            raw_text=raw_text,
            content_hash=FetchedContent.compute_hash(raw_text),
            title=f"{issue_key}: {fields.get('summary', 'Untitled')}",
            source_url=f"{self._base_url()}/browse/{issue_key}",
            metadata={
                "connector": "jira",
                "external_id": issue_key,
                "issue_type": issue_type,
                "status": (fields.get("status") or {}).get("name"),
                "priority": (fields.get("priority") or {}).get("name"),
            },
        )

    def _normalize_issue(self, issue_key: str, fields: dict) -> str:
        """Convert Jira issue fields to normalized text."""
        parts: list[str] = []

        summary = fields.get("summary", "")
        parts.append(f"# {issue_key}: {summary}\n")

        status = (fields.get("status") or {}).get("name", "Unknown")
        priority = (fields.get("priority") or {}).get("name", "Unknown")
        issue_type = (fields.get("issuetype") or {}).get("name", "Unknown")
        parts.append(f"**Type:** {issue_type} | **Status:** {status} | **Priority:** {priority}\n")

        # Description (ADF → plain text)
        description = fields.get("description")
        if description:
            desc_text = self._adf_to_text(description)
            if desc_text.strip():
                parts.append(f"## Description\n\n{desc_text}\n")

        # Labels
        labels = fields.get("labels", [])
        if labels:
            parts.append(f"**Labels:** {', '.join(labels)}\n")

        # Subtasks
        subtasks = fields.get("subtasks", [])
        if subtasks:
            parts.append("## Subtasks\n")
            for st in subtasks:
                st_key = st.get("key", "")
                st_summary = (st.get("fields") or {}).get("summary", "")
                st_status = ((st.get("fields") or {}).get("status") or {}).get("name", "")
                parts.append(f"- [{st_key}] {st_summary} ({st_status})")
            parts.append("")

        return "\n".join(parts)

    def _adf_to_text(self, node: object) -> str:
        """Recursively convert Atlassian Document Format to plain text."""
        if isinstance(node, str):
            return node
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type", "")
        text_val = node.get("text", "")

        if node_type == "text":
            return text_val

        parts: list[str] = []
        for child in node.get("content", []):
            parts.append(self._adf_to_text(child))

        joined = "".join(parts)

        if node_type == "paragraph":
            return f"{joined}\n"
        if node_type == "heading":
            level = node.get("attrs", {}).get("level", 2)
            return f"{'#' * level} {joined}\n"
        if node_type == "bulletList":
            return joined
        if node_type == "orderedList":
            return joined
        if node_type == "listItem":
            return f"- {joined}"
        if node_type == "codeBlock":
            return f"```\n{joined}\n```\n"
        if node_type == "table":
            return f"\n{joined}\n"
        if node_type == "tableRow":
            return f"| {joined}\n"
        if node_type == "tableCell" or node_type == "tableHeader":
            return f"{joined} | "

        return joined

    async def _fetch_epic_children(self, epic_key: str) -> str:
        """Fetch child issues of an epic via JQL search."""
        try:
            url = f"{self._base_url()}/rest/api/3/search"
            jql = f'"Epic Link" = {epic_key} OR parent = {epic_key}'
            params = {
                "jql": jql,
                "fields": "summary,description,issuetype,status,priority",
                "maxResults": 50,
            }
            client = get_http_client()
            resp = await client.get(url, headers=self._headers(), params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.warning("Failed to fetch epic children for %s: HTTP %d", epic_key, resp.status_code)
                return ""
            data = resp.json()

            children_parts: list[str] = []
            for issue in data.get("issues", []):
                key = issue.get("key", "")
                fields = issue.get("fields", {})
                summary = fields.get("summary", "")
                status = (fields.get("status") or {}).get("name", "")
                issue_type = (fields.get("issuetype") or {}).get("name", "")
                children_parts.append(f"### {key}: {summary}")
                children_parts.append(f"**Type:** {issue_type} | **Status:** {status}\n")

                description = fields.get("description")
                if description:
                    desc_text = self._adf_to_text(description)
                    if desc_text.strip():
                        children_parts.append(desc_text[:1000])
                children_parts.append("")

            return "\n".join(children_parts)
        except Exception as exc:
            logger.warning("Error fetching epic children for %s: %s", epic_key, exc)
            return ""
