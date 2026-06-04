"""Regression: the URL knowledge connector was an SSRF sink (S1, 2026-06-03).

``URLConnector.fetch_content`` validated only the URL *scheme*
(``validate_url_scheme`` blocks file/data/ftp/javascript) and an optional,
opt-in knowledge-source domain allowlist. Neither blocks a URL whose host
resolves to a private / loopback / link-local / metadata address. A
QA_ENGINEER+ creating an ``external_url`` knowledge source could point sync at
``http://169.254.169.254/...`` (cloud metadata), ``http://127.0.0.1:6379``
(Redis), or any RFC1918 host — server-side fetch + content exfiltration.

Fix: a shared ``url_safety.is_safe_public_url`` guard (extracted from
``webhook_service``) now runs on the initial URL AND on every redirect hop
(auto-redirect following is disabled so a public host can't 30x us into a
private target). These tests pin:

1. the initial-URL guard blocks private/metadata/loopback targets with no GET;
2. a public→private redirect is blocked on the *second* hop (the first GET
   happens, the redirect target is rejected before it's requested);
3. the existing scheme block still fires first;
4. a fully-public single-hop fetch still works.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")

from app.services.connectors.base import ConnectorFetchError  # noqa: E402
from app.services.connectors.url_connector import URLConnector  # noqa: E402

pytestmark = pytest.mark.regression


class _FakeResp:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, *, status_code=200, headers=None, text="<html><body>ok</body></html>"):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.content = text.encode("utf-8")
        self.url = "https://example.com/final"

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        return None


def _client_returning(responses):
    """Build a fake httpx.AsyncClient context manager whose .get() yields the
    queued responses in order, recording each requested URL."""
    requested: list[str] = []
    queue = list(responses)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            requested.append(url)
            return queue.pop(0)

    return _Client(), requested


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:6379/",                    # loopback (Redis)
        "http://localhost:8000/admin",               # loopback by name
        "http://10.0.0.5/internal",                  # private
        "http://192.168.1.10/internal",              # private
    ],
)
@pytest.mark.asyncio
async def test_initial_url_to_private_target_is_blocked_without_fetching(url):
    spy = AsyncMock()
    with patch("httpx.AsyncClient", spy):
        with pytest.raises(ConnectorFetchError, match="unsafe URL"):
            await URLConnector().fetch_content(url)
    # The guard runs before any client is constructed → no network at all.
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_redirect_to_private_target_is_blocked_on_the_hop():
    """A public URL that 302s to cloud metadata is rejected at the redirect
    hop — the metadata host is never requested."""
    redirect = _FakeResp(
        status_code=302,
        headers={"location": "http://169.254.169.254/latest/meta-data/"},
    )
    client, requested = _client_returning([redirect])

    # Host-aware guard: the public start URL is allowed, the metadata target is
    # not — deterministic + offline (no real DNS for the .example start host).
    def _guard(u):
        return (False, "private") if "169.254" in u else (True, "")

    with patch("httpx.AsyncClient", lambda *a, **k: client), \
         patch("app.services.connectors.url_connector.is_safe_public_url", _guard):
        with pytest.raises(ConnectorFetchError, match="unsafe URL"):
            await URLConnector().fetch_content("https://public.example/start")

    # Only the public start URL was fetched; the private redirect target wasn't.
    assert requested == ["https://public.example/start"]


@pytest.mark.asyncio
async def test_scheme_block_still_fires_first():
    spy = AsyncMock()
    with patch("httpx.AsyncClient", spy):
        with pytest.raises(ConnectorFetchError, match="not allowed"):
            await URLConnector().fetch_content("file:///etc/passwd")
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_public_single_hop_fetch_succeeds():
    """A fully-public URL still fetches + extracts text (guard allows it)."""
    ok = _FakeResp(
        status_code=200,
        headers={"content-type": "text/plain"},
        text="hello world from a public site",
    )
    client, requested = _client_returning([ok])

    # is_safe_public_url is patched to allow (avoid a real DNS lookup in CI).
    with patch("httpx.AsyncClient", lambda *a, **k: client), \
         patch(
             "app.services.connectors.url_connector.is_safe_public_url",
             lambda _u: (True, ""),
         ):
        result = await URLConnector().fetch_content("https://public.example/page")

    assert "hello world" in result.raw_text
    assert requested == ["https://public.example/page"]
