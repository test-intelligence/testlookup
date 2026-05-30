"""Confluence knowledge connector — fetches page content via REST API v2."""
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

# Match Confluence page ID from URL patterns like /pages/123456 or /wiki/spaces/SPACE/pages/123456
_PAGE_ID_RE = re.compile(r"/pages/(\d+)")
# Match /wiki/spaces/SPACE/pages/PAGEID/TITLE or similar
_SPACE_PAGE_RE = re.compile(r"/wiki/spaces/([^/]+)/pages/(\d+)")


class ConfluenceKnowledgeConnector(KnowledgeConnectorBase):
    """Handles confluence_page source type via Confluence REST API."""

    connector_type = "confluence_page"

    def _base_url(self) -> str:
        domain = settings.CONFLUENCE_DOMAIN or settings.JIRA_DOMAIN
        if not domain:
            raise ConnectorFetchError(
                "Confluence domain not configured (CONFLUENCE_DOMAIN or JIRA_DOMAIN)"
            )
        return f"https://{domain}"

    def _auth_header(self) -> str:
        email = settings.CONFLUENCE_EMAIL or settings.JIRA_EMAIL
        token = settings.CONFLUENCE_API_TOKEN or settings.JIRA_API_TOKEN
        if not email or not token:
            raise ConnectorFetchError(
                "Confluence credentials not configured "
                "(CONFLUENCE_EMAIL/CONFLUENCE_API_TOKEN or JIRA_EMAIL/JIRA_API_TOKEN)"
            )
        raw = f"{email}:{token}"
        return f"Basic {base64.b64encode(raw.encode()).decode()}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": self._auth_header(),
        }

    def _extract_page_id(self, canonical_url: str, external_id: Optional[str]) -> str:
        """Extract Confluence page ID from URL or external_id."""
        if external_id and external_id.isdigit():
            return external_id

        match = _PAGE_ID_RE.search(canonical_url)
        if match:
            return match.group(1)

        # external_id might be the page ID directly
        if external_id:
            return external_id

        raise ConnectorFetchError(
            f"Cannot extract Confluence page ID from URL: {canonical_url}"
        )

    async def test_connection(self) -> dict:
        t_start = time.monotonic()
        try:
            domain = settings.CONFLUENCE_DOMAIN or settings.JIRA_DOMAIN
            email = settings.CONFLUENCE_EMAIL or settings.JIRA_EMAIL
            token = settings.CONFLUENCE_API_TOKEN or settings.JIRA_API_TOKEN
            if not domain or not email or not token:
                return {
                    "success": False,
                    "latency_ms": 0,
                    "error": "Confluence credentials not configured",
                    "detail": "Set CONFLUENCE_DOMAIN, CONFLUENCE_EMAIL, and CONFLUENCE_API_TOKEN",
                }

            # Use v2 API to check connectivity
            url = f"{self._base_url()}/wiki/api/v2/spaces?limit=1"
            client = get_http_client()
            resp = await client.get(url, headers=self._headers(), timeout=10.0)
            latency = int((time.monotonic() - t_start) * 1000)
            if resp.status_code == 200:
                return {
                    "success": True,
                    "latency_ms": latency,
                    "error": None,
                    "detail": "Confluence API accessible",
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
        page_id = self._extract_page_id(canonical_url, external_id)

        # Fetch page content using REST API v1 (storage format)
        url = f"{self._base_url()}/wiki/rest/api/content/{page_id}"
        params = {"expand": "body.storage,version,space,ancestors"}

        try:
            client = get_http_client()
            resp = await client.get(url, headers=self._headers(), params=params, timeout=15.0)
            if resp.status_code == 401:
                raise ConnectorFetchError("Confluence authentication failed — check credentials")
            if resp.status_code == 403:
                raise ConnectorFetchError(f"Access denied to Confluence page {page_id}")
            if resp.status_code == 404:
                raise ConnectorFetchError(f"Confluence page {page_id} not found")
            resp.raise_for_status()
            data = resp.json()
        except ConnectorFetchError:
            raise
        except httpx.TimeoutException:
            raise ConnectorFetchError(f"Timeout fetching Confluence page {page_id}", retryable=True)
        except httpx.HTTPStatusError as exc:
            raise ConnectorFetchError(f"Confluence API error: HTTP {exc.response.status_code}")
        except Exception as exc:
            raise ConnectorFetchError(
                f"Failed to fetch Confluence page {page_id}: {exc}", retryable=True
            )

        title = data.get("title", "Untitled")
        space_key = (data.get("space") or {}).get("key", "")
        version = (data.get("version") or {}).get("number", 0)

        # Extract storage-format HTML body and convert to text
        storage_html = (data.get("body", {}).get("storage") or {}).get("value", "")
        raw_text = self._storage_to_text(title, space_key, version, storage_html)

        page_url = f"{self._base_url()}/wiki/spaces/{space_key}/pages/{page_id}" if space_key else canonical_url

        return FetchedContent(
            raw_text=raw_text,
            content_hash=FetchedContent.compute_hash(raw_text),
            title=title,
            source_url=page_url,
            metadata={
                "connector": "confluence",
                "external_id": page_id,
                "space_key": space_key,
                "version": version,
            },
        )

    def _storage_to_text(self, title: str, space_key: str, version: int, html: str) -> str:
        """Convert Confluence storage-format HTML to structured plain text."""
        parts: list[str] = [f"# {title}\n"]
        if space_key:
            parts.append(f"**Space:** {space_key} | **Version:** {version}\n")

        if not html.strip():
            parts.append("(Empty page)")
            return "\n".join(parts)

        text = self._html_to_text(html)
        parts.append(text)
        return "\n".join(parts)

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text, preserving structure."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            return self._soup_to_text(soup)
        except ImportError:
            # Fallback: strip HTML tags with regex
            return self._regex_strip_html(html)

    def _soup_to_text(self, element: object) -> str:
        """Walk BeautifulSoup elements and produce structured text."""
        from bs4 import NavigableString, Tag

        if isinstance(element, NavigableString):
            return str(element)

        if not isinstance(element, Tag):
            return ""

        parts: list[str] = []
        tag = element.name

        # Collect children text
        children_text = ""
        for child in element.children:
            children_text += self._soup_to_text(child)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            parts.append(f"\n{'#' * level} {children_text.strip()}\n")
        elif tag == "p":
            parts.append(f"{children_text.strip()}\n\n")
        elif tag == "li":
            parts.append(f"- {children_text.strip()}\n")
        elif tag in ("ul", "ol"):
            parts.append(f"\n{children_text}")
        elif tag == "table":
            parts.append(f"\n{children_text}\n")
        elif tag == "tr":
            parts.append(f"| {children_text}\n")
        elif tag in ("td", "th"):
            parts.append(f"{children_text.strip()} | ")
        elif tag == "br":
            parts.append("\n")
        elif tag in ("strong", "b"):
            parts.append(f"**{children_text}**")
        elif tag in ("em", "i"):
            parts.append(f"*{children_text}*")
        elif tag == "code":
            parts.append(f"`{children_text}`")
        elif tag == "pre":
            parts.append(f"```\n{children_text}\n```\n")
        elif tag == "a":
            href = element.get("href", "")
            parts.append(f"[{children_text}]({href})")
        elif tag == "ac:structured-macro":
            # Confluence macros — extract body content
            parts.append(children_text)
        else:
            parts.append(children_text)

        return "".join(parts)

    @staticmethod
    def _regex_strip_html(html: str) -> str:
        """Fallback HTML tag stripper when BeautifulSoup is not available."""
        text = re.sub(r"<br\s*/?>", "\n", html)
        text = re.sub(r"</(p|h[1-6]|li|tr|div)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
