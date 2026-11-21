"""Jira knowledge connector — fetches issue/epic content via REST API v3."""
from __future__ import annotations

import base64
import re
import time
from typing import Any, Optional

import httpx
import structlog

from app.core.config import settings
from app.core.http_client import get_http_client
from app.services.connectors.base import (
    ConnectorFetchError,
    ConnectorScopeRefused,
    FetchedContent,
    KnowledgeConnectorBase,
)

logger = structlog.get_logger(__name__)

# A Jira issue key. The key goes into a REST path and, for an epic, into a JQL
# string, on requests that carry the deployment's Jira credential, so nothing
# but this shape is ever used. ASCII digits only (``\d`` also matches other
# scripts' digits). ``schemas.KnowledgeSourceCreate`` applies the same pattern
# on create; a regression test keeps the two equal.
_ISSUE_KEY_PATTERN = r"[A-Z][A-Z0-9_]+-[0-9]+"
_ISSUE_KEY_RE = re.compile(f"({_ISSUE_KEY_PATTERN})")
_ISSUE_KEY_FULL_RE = re.compile(_ISSUE_KEY_PATTERN)
# A Jira project key, as it goes into the child search's JQL.
_PROJECT_KEY_FULL_RE = re.compile(r"[A-Z][A-Z0-9_]+")


class JiraKnowledgeConnector(KnowledgeConnectorBase):
    """Handles jira_issue and jira_epic source types via Jira REST API v3."""

    connector_type = "jira_issue"

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        # ``None``: the environment settings (the registry's shared instance).
        # A dict: the deployment's resolved Jira config -- see ``with_config``.
        self._cfg = config

    def with_config(self, config: dict[str, Any]) -> "JiraKnowledgeConnector":
        """This connector bound to the deployment's resolved Jira config.

        A Jira set up under Settings -> Integrations stores its domain and email
        in an AppSetting and its token in the secret service; none of that is
        in ``settings``. Reading ``settings`` alone, knowledge sources reported
        "JIRA_DOMAIN is not configured" on a Jira that filed defects fine (the
        bug ``jira_release_sync`` documents for its own credential).
        """
        return JiraKnowledgeConnector(config)

    def _value(self, key: str, env_value: Optional[str]) -> Optional[str]:
        return self._cfg.get(key) if self._cfg is not None else env_value

    def _base_url(self) -> str:
        domain = self._value("domain", settings.JIRA_DOMAIN)
        if not domain:
            raise ConnectorFetchError("Jira is not configured: no domain (Settings -> Integrations)")
        return f"https://{domain}"

    def _auth_header(self) -> str:
        email = self._value("email", settings.JIRA_EMAIL)
        token = self._value("api_token", settings.JIRA_API_TOKEN)
        if not email or not token:
            raise ConnectorFetchError(
                "Jira credentials not configured (email and API token, Settings -> Integrations)"
            )
        raw = f"{email}:{token}"
        return f"Basic {base64.b64encode(raw.encode()).decode()}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": self._auth_header(),
        }

    def _extract_issue_key(self, canonical_url: str, external_id: Optional[str]) -> str:
        """The Jira issue key for a source: ``external_id`` or, failing that, the URL.

        ``external_id`` is used only when the whole value is an issue key. Any
        other value is ignored, never passed through: it would become part of
        the request path. The result always matches ``_ISSUE_KEY_PATTERN``.
        """
        if external_id and _ISSUE_KEY_FULL_RE.fullmatch(external_id):
            return external_id

        match = _ISSUE_KEY_RE.search(canonical_url)
        if match:
            return match.group(1)
        if external_id:
            raise ConnectorFetchError(
                "Cannot extract a Jira issue key: the source's external id is not "
                "an issue key such as ABC-123 and its URL does not contain one. "
                "Recreate the source with the issue key or the issue's URL."
            )
        raise ConnectorFetchError(
            f"Cannot extract Jira issue key from URL: {canonical_url}"
        )

    async def test_connection(self) -> dict:
        t_start = time.monotonic()
        try:
            if not (
                self._value("domain", settings.JIRA_DOMAIN)
                and self._value("email", settings.JIRA_EMAIL)
                and self._value("api_token", settings.JIRA_API_TOKEN)
            ):
                return {
                    "success": False,
                    "latency_ms": 0,
                    "error": "Jira credentials not configured",
                    "detail": "Set the Jira domain, email and API token under Settings -> Integrations",
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
        *,
        jira_project_key: Optional[str] = None,
    ) -> FetchedContent:
        """``jira_project_key``: the one Jira project the source's TestLookup
        project may read (its own Jira key). When given, an epic's child issues
        are searched in that Jira project only. The caller has already checked
        the issue key itself against it."""
        issue_key = self._extract_issue_key(canonical_url, external_id)
        if jira_project_key is not None and not _PROJECT_KEY_FULL_RE.fullmatch(jira_project_key):
            # Refused before anything is asked of Jira; it would also go into the JQL.
            raise ConnectorFetchError(f"Not a Jira project key: {jira_project_key!r}")

        url = f"{self._base_url()}/rest/api/3/issue/{issue_key}"
        params = {"fields": "summary,description,issuetype,status,priority,labels,subtasks,comment,project"}

        try:
            client = get_http_client()
            resp = await client.get(
                url, headers=self._headers(), params=params,
                timeout=float(settings.KNOWLEDGE_SYNC_TIMEOUT_SECONDS),
            )
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
        # Jira answers a key it no longer uses with the issue it now belongs to:
        # an issue moved to another Jira project is still returned for its old
        # key, under its new one. The key that was authorised is the one asked
        # for, so what came back must be that issue, in that Jira project,
        # before any of it is read.
        allowed_project = jira_project_key or issue_key.rsplit("-", 1)[0]
        self._require_same_issue(issue_key, allowed_project, data, fields)
        raw_text = self._normalize_issue(issue_key, fields)

        # For epics, fetch child issues
        issue_type = (fields.get("issuetype") or {}).get("name", "").lower()
        if issue_type == "epic":
            # Children are read inside the epic's own Jira project (which is
            # ``jira_project_key`` when the caller gave one, already checked
            # equal above): a child can sit in any Jira project the account
            # reads, and nobody authorised those.
            children_text = await self._fetch_epic_children(issue_key, allowed_project)
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

    @staticmethod
    def _require_same_issue(issue_key: str, allowed_project: str, data: dict, fields: dict) -> None:
        """Refuse a response that is not ``issue_key`` inside ``allowed_project``.

        Jira always returns the issue's current ``key``. A response without
        one cannot be tied to what was asked for, so it is refused too. The
        ``project`` field is checked when Jira returned it.
        """
        returned_key = data.get("key")
        if not isinstance(returned_key, str) or returned_key.upper() != issue_key.upper():
            raise ConnectorScopeRefused(
                f"Jira answered the request for {issue_key} with a different issue "
                f"({returned_key or 'no key'}): the issue was moved or renamed. "
                "Nothing was imported. Recreate the source with the issue's current key."
            )
        returned_project = (fields.get("project") or {}).get("key")
        projects = {returned_key.rsplit("-", 1)[0].upper()}
        if isinstance(returned_project, str) and returned_project:
            projects.add(returned_project.upper())
        if projects != {allowed_project.upper()}:
            raise ConnectorScopeRefused(
                f"Jira issue {issue_key} is not in the Jira project {allowed_project} "
                "this source may read. Nothing was imported."
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

    async def _fetch_epic_children(self, epic_key: str, jira_project_key: Optional[str] = None) -> str:
        """Fetch child issues of an epic via JQL search (inside ``jira_project_key`` when given)."""
        if jira_project_key is not None and not _PROJECT_KEY_FULL_RE.fullmatch(jira_project_key):
            # It goes into the JQL: anything that is not key-shaped is refused, not quoted.
            raise ConnectorFetchError(f"Not a Jira project key: {jira_project_key!r}")
        try:
            # /rest/api/3/search answers 410 Gone on Jira Cloud (Atlassian
            # CHANGE-2046); its replacement takes the same jql/fields/maxResults
            # and answers with the same "issues" list.
            url = f"{self._base_url()}/rest/api/3/search/jql"
            # The key is quoted, and refused outright if it is not key-shaped:
            # the caller already pattern-checks it, this keeps the JQL safe on
            # its own.
            if not _ISSUE_KEY_FULL_RE.fullmatch(epic_key):
                raise ConnectorFetchError(f"Not a Jira issue key: {epic_key!r}")
            jql = f'"Epic Link" = "{epic_key}" OR parent = "{epic_key}"'
            if jira_project_key is not None:
                # Children can sit in any Jira project the account reads; keep to this one.
                jql = f'({jql}) AND project = "{jira_project_key}"'
            params = {
                "jql": jql,
                "fields": "summary,description,issuetype,status,priority",
                "maxResults": 50,
            }
            client = get_http_client()
            resp = await client.get(
                url, headers=self._headers(), params=params,
                timeout=float(settings.KNOWLEDGE_SYNC_TIMEOUT_SECONDS),
            )
            if resp.status_code != 200:
                logger.warning(
                    "jira_epic_children_fetch_failed",
                    epic_key=epic_key, status_code=resp.status_code,
                )
                return ""
            data = resp.json()

            children_parts: list[str] = []
            for issue in data.get("issues", []):
                key = issue.get("key", "")
                # The JQL already keeps to the Jira project; a child whose own
                # key says otherwise is left out, whatever the search returned.
                if jira_project_key is not None and (
                    not isinstance(key, str) or key.rsplit("-", 1)[0].upper() != jira_project_key.upper()
                ):
                    logger.warning(
                        "jira_epic_child_outside_project_skipped",
                        epic_key=epic_key, jira_project_key=jira_project_key,
                    )
                    continue
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
            logger.warning(
                "jira_epic_children_error",
                epic_key=epic_key, error=str(exc),
            )
            return ""
