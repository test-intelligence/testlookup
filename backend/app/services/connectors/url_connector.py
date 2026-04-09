"""URL knowledge connector — fetches content from internal/external URLs via HTTP."""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx
import structlog

from app.services.connectors.base import (
    ConnectorFetchError,
    FetchedContent,
    KnowledgeConnectorBase,
)

logger = structlog.get_logger(__name__)

# Max response size to prevent abuse (5 MB)
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

# Content types we can extract text from
_TEXT_CONTENT_TYPES = {"text/html", "text/plain", "text/markdown", "application/json"}


class URLConnector(KnowledgeConnectorBase):
    """Handles internal_url and external_url source types via HTTP GET."""

    connector_type = "internal_url"

    async def test_connection(self) -> dict:
        # URL connector has no persistent credentials to test;
        # connectivity is verified per-URL during fetch.
        return {
            "success": True,
            "latency_ms": 0,
            "error": None,
            "detail": "URL connector ready — connectivity verified per URL during sync",
        }

    async def fetch_content(
        self,
        canonical_url: str,
        external_id: Optional[str] = None,
    ) -> FetchedContent:
        url = canonical_url

        # RAG-13: Block dangerous URL schemes before fetching
        from app.services.rag_redaction_service import validate_url_scheme
        try:
            validate_url_scheme(url)
        except ValueError as exc:
            raise ConnectorFetchError(str(exc))

        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "TestLookup-KnowledgeSync/1.0",
                        "Accept": "text/html, text/plain, application/json, */*",
                    },
                )
                if resp.status_code == 401 or resp.status_code == 403:
                    raise ConnectorFetchError(f"Access denied to URL ({resp.status_code}): {url}")
                if resp.status_code == 404:
                    raise ConnectorFetchError(f"URL not found (404): {url}")
                resp.raise_for_status()

                # Check content length
                content_length = len(resp.content)
                if content_length > _MAX_RESPONSE_BYTES:
                    raise ConnectorFetchError(
                        f"Response too large ({content_length / 1024 / 1024:.1f} MB) "
                        f"— max {_MAX_RESPONSE_BYTES / 1024 / 1024:.0f} MB"
                    )

                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                body = resp.text

        except ConnectorFetchError:
            raise
        except httpx.TimeoutException:
            raise ConnectorFetchError(f"Timeout fetching URL: {url}", retryable=True)
        except httpx.HTTPStatusError as exc:
            raise ConnectorFetchError(f"HTTP error {exc.response.status_code} for URL: {url}")
        except Exception as exc:
            raise ConnectorFetchError(f"Failed to fetch URL {url}: {exc}", retryable=True)

        # Extract text based on content type
        if content_type == "text/html" or "<html" in body[:500].lower():
            raw_text = _html_to_text(body, url)
        elif content_type in ("text/plain", "text/markdown"):
            raw_text = body
        elif content_type == "application/json":
            raw_text = f"```json\n{body[:50000]}\n```"
        else:
            # Try HTML extraction as fallback
            raw_text = _html_to_text(body, url) if "<" in body[:100] else body

        if not raw_text.strip():
            raise ConnectorFetchError(f"No text content extracted from URL: {url}")

        # Determine title
        title = _extract_title(body, content_type) or url[:80]

        return FetchedContent(
            raw_text=raw_text,
            content_hash=FetchedContent.compute_hash(raw_text),
            title=title,
            source_url=url,
            metadata={
                "connector": "url",
                "content_type": content_type,
                "content_length": content_length,
                "final_url": str(resp.url),
            },
        )


def _html_to_text(html: str, url: str) -> str:
    """Convert HTML page to clean text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove script, style, nav, footer, header elements
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()

        # Try to find main content area
        main = soup.find("main") or soup.find("article") or soup.find(role="main")
        target = main if main else soup.find("body") or soup

        text = target.get_text(separator="\n", strip=True)
        # Collapse excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    except ImportError:
        # Fallback: regex-based HTML stripping
        return _regex_strip_html(html)


def _regex_strip_html(html: str) -> str:
    """Fallback HTML tag stripper when BeautifulSoup is not available."""
    # Remove script and style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block tags with newlines
    text = re.sub(r"</(p|h[1-6]|li|tr|div|br)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title(body: str, content_type: str) -> Optional[str]:
    """Try to extract a page title from HTML content."""
    if content_type != "text/html" and "<html" not in body[:500].lower():
        return None
    match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        # Clean HTML entities
        title = re.sub(r"&amp;", "&", title)
        title = re.sub(r"&lt;", "<", title)
        title = re.sub(r"&gt;", ">", title)
        title = re.sub(r"&#\d+;", "", title)
        return title[:200] if title else None
    return None
