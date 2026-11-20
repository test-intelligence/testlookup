"""A shared-secret header that is not plain ASCII is a wrong secret, not a crash.

The rule: the two routes that accept the deployment's shared webhook secret
(``POST /webhooks/minio`` through ``verify_webhook_secret`` and the legacy path
of ``POST /ws/events/{run_id}``) compare the presented value with the
configured one as bytes. The server decodes a header as latin-1, so a hostile
byte reaches the handler as a non-ASCII ``str``; comparing two such strings
with ``hmac.compare_digest`` raises ``TypeError``, which was a public 500
before any credential check. Now any value that cannot be compared is simply a
mismatch: 403. The correct secret still works, including one that is not
ASCII (sent as UTF-8 bytes, which is how the server sees it on the wire).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.deps import verify_webhook_secret
from tests.regression.test_live_event_tenant_binding import (  # noqa: F401 - fixture used by name
    _call,
    wired,
)

pytestmark = pytest.mark.regression

SECRET = "the-real-secret"  # the value the ``wired`` fixture configures
NOT_ASCII = "caf\u00e9-\u00ff"
#: Outside latin-1: cannot arrive in a real header, but a direct caller can pass it.
NOT_LATIN_1 = "secret-\u2603"


def _as_the_server_decodes(value: str) -> str:
    """UTF-8 bytes on the wire, decoded as latin-1: what a handler receives."""
    return value.encode("utf-8").decode("latin-1")


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", SECRET)


# ── /webhooks/minio ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("header, authorization", [
    (NOT_ASCII, None),
    (None, f"Bearer {NOT_ASCII}"),
    (NOT_LATIN_1, None),
    (None, f"Bearer {NOT_LATIN_1}"),
    (NOT_ASCII, f"Bearer {NOT_LATIN_1}"),
], ids=["header", "bearer", "header-wide", "bearer-wide", "both"])
async def test_a_non_ascii_webhook_secret_is_refused_not_a_crash(secret, header, authorization):
    with pytest.raises(HTTPException) as refused:
        await verify_webhook_secret(x_webhook_secret=header, authorization=authorization)
    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_the_right_secret_still_passes_next_to_a_non_ascii_one(secret):
    await verify_webhook_secret(x_webhook_secret=NOT_ASCII, authorization=f"Bearer {SECRET}")
    await verify_webhook_secret(x_webhook_secret=SECRET, authorization=None)


@pytest.mark.asyncio
async def test_a_non_ascii_configured_secret_still_works(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", NOT_ASCII)
    await verify_webhook_secret(x_webhook_secret=_as_the_server_decodes(NOT_ASCII), authorization=None)
    with pytest.raises(HTTPException) as refused:
        await verify_webhook_secret(x_webhook_secret="cafe-y", authorization=None)
    assert refused.value.status_code == 403


# ── /ws/events/{run_id}, legacy shared-secret path ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [NOT_ASCII, NOT_LATIN_1], ids=["latin-1", "wide"])
async def test_a_non_ascii_live_event_secret_is_refused_not_a_crash(wired, value):  # noqa: F811
    with pytest.raises(HTTPException) as refused:
        await _call(x_webhook_secret=value)
    assert refused.value.status_code == 403
    assert wired.published == []


@pytest.mark.asyncio
async def test_the_right_live_event_secret_still_passes(wired):  # noqa: F811
    result = await _call(x_webhook_secret=SECRET)
    assert result["accepted"] is True
    assert len(wired.published) == 1
