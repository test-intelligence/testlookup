"""URL knowledge connector — fetches content from internal/external URLs via HTTP."""
from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog

from app.core.http_client import http_verify as _http_verify
from app.services.connectors.base import (
    ConnectorFetchError,
    FetchedContent,
    KnowledgeConnectorBase,
)
from app.services.url_safety import is_safe_public_url
from app.core.config import settings

logger = structlog.get_logger(__name__)

# Max response size to prevent abuse (5 MB)
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

# Untrusted user-supplied URLs: cap redirect hops (matches the previous
# follow_redirects max_redirects=5).
_MAX_REDIRECTS = 5

# Content types we can extract text from
_TEXT_CONTENT_TYPES = {"text/html", "text/plain", "text/markdown", "application/json"}


async def _assert_fetchable(url: str) -> None:
    """SSRF + scheme guard for a single URL hop.

    Raises ``ConnectorFetchError`` (not retryable) when the URL uses a blocked
    scheme or resolves to a non-public address. Applied to the initial URL
    *and* to every redirect ``Location`` before that hop is requested, so a
    public host can't 30x us into cloud metadata / a private service.
    """
    from app.services.rag_redaction_service import validate_url_scheme
    try:
        validate_url_scheme(url)
    except ValueError as exc:
        raise ConnectorFetchError(str(exc))
    safe, reason = await asyncio.to_thread(is_safe_public_url, url)
    if not safe:
        raise ConnectorFetchError(f"Refusing to fetch unsafe URL ({reason}): {url}")


async def _read_response_bounded(response: httpx.Response) -> bytes:
    """Read a response without allowing decompressed bytes to exceed the cap."""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_RESPONSE_BYTES:
        raise ConnectorFetchError(
            f"Response too large ({int(declared) / 1024 / 1024:.1f} MB) "
            f"— max {_MAX_RESPONSE_BYTES / 1024 / 1024:.0f} MB"
        )

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise ConnectorFetchError(
                f"Response too large ({total / 1024 / 1024:.1f} MB) "
                f"— max {_MAX_RESPONSE_BYTES / 1024 / 1024:.0f} MB"
            )
        chunks.append(chunk)
    return b"".join(chunks)


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

        # RAG-13 + SSRF guard: validate scheme AND reject non-public targets
        # before fetching.
        await _assert_fetchable(url)

        try:
            # NB: we deliberately disable httpx auto-redirect following and walk
            # the redirect chain by hand. A public host can return a 30x whose
            # Location points at cloud metadata / a private service, so every
            # hop must pass the SSRF guard *before* we request it — automatic
            # following would issue those requests for us, defeating the guard.
            async with asyncio.timeout(float(settings.KNOWLEDGE_SYNC_TIMEOUT_SECONDS)):
                async with httpx.AsyncClient(
                    # Per-hop budget for the hand-walked redirect chain.
                    timeout=float(settings.KNOWLEDGE_SYNC_TIMEOUT_SECONDS),
                    follow_redirects=False,
                    verify=_http_verify(),
                ) as client:
                    current = url
                    for _hop in range(_MAX_REDIRECTS + 1):
                        async with client.stream(
                            "GET",
                            current,
                            headers={
                                "User-Agent": "TestLookup-KnowledgeSync/1.0",
                                "Accept": "text/html, text/plain, application/json, */*",
                            },
                        ) as resp:
                            location = resp.headers.get("location")
                            if resp.is_redirect and location:
                                nxt = urljoin(current, location)
                                await _assert_fetchable(nxt)  # re-resolve every hop
                                current = nxt
                                continue

                            if resp.status_code == 401 or resp.status_code == 403:
                                raise ConnectorFetchError(f"Access denied to URL ({resp.status_code}): {url}")
                            if resp.status_code == 404:
                                raise ConnectorFetchError(f"URL not found (404): {url}")
                            resp.raise_for_status()

                            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                            raw_body = await _read_response_bounded(resp)
                            encoding = resp.encoding or "utf-8"
                            body = raw_body.decode(encoding, errors="replace")
                            content_length = len(raw_body)
                        break
                    else:
                        raise ConnectorFetchError(
                            f"Too many redirects (>{_MAX_REDIRECTS}) fetching URL: {url}"
                        )

        except ConnectorFetchError:
            raise
        except (httpx.TimeoutException, TimeoutError):
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
