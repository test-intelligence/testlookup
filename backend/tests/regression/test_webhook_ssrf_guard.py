"""Regression: outbound webhook delivery was an SSRF sink.

Bug pinned (review/webhook-service, 2026-06-01):

``deliver`` POSTed to a customer-supplied ``target_url`` (schema validated only
``^https?://``) and stored the response, so a QA_LEAD could point a subscription
at ``http://169.254.169.254/`` (cloud metadata), ``http://127.0.0.1/`` (internal
app endpoints), or any private host — SSRF + response exfiltration. Fix: an
``_is_safe_public_url`` guard rejects targets resolving to non-public addresses,
enforced at create/update (422) and at delivery time (FAILED, no POST). Hosts
that don't resolve are allowed (no SSRF reach; the POST fails naturally).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import webhook_service as svc  # noqa: E402


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8000/admin",               # loopback
        "http://localhost:9000/",                    # loopback by name
        "http://10.0.0.5/hook",                      # private
        "http://192.168.1.10/hook",                  # private
        "http://0.0.0.0/",                           # unspecified
        "ftp://example.com/x",                       # bad scheme
    ],
)
def test_unsafe_targets_are_blocked(url):
    safe, reason = svc._is_safe_public_url(url)
    assert safe is False, f"{url} must be rejected as SSRF-unsafe"
    assert reason


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/hooks",   # public, resolves
        "https://old.example/hook",    # reserved TLD → NXDOMAIN → allowed
        "https://example.test/hook",   # reserved TLD → NXDOMAIN → allowed
    ],
)
def test_public_or_unresolvable_targets_allowed(url):
    safe, _ = svc._is_safe_public_url(url)
    assert safe is True, f"{url} must be allowed (public or non-reachable)"


@pytest.mark.asyncio
async def test_deliver_blocks_unsafe_target_without_posting():
    """The egress guard: a delivery whose subscription points at a private
    address is marked FAILED and never POSTed (covers DNS-rebinding and rows
    that predate the create-time check)."""
    sub = SimpleNamespace(
        id=uuid.uuid4(),
        enabled=True,
        has_secret=False,
        target_url="http://169.254.169.254/latest/meta-data/",
        project_id=uuid.uuid4(),
        max_retries=5,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=sub.id,
        event_type="run.completed",
        event_payload={},
        status="PENDING",
        attempt_count=0,
        error=None,
    )

    class _Res:
        def __init__(self, val=None, *, rowcount=0):
            self._val = val
            self.rowcount = rowcount

        def scalar_one_or_none(self):
            return self._val

    class _FakeDB:
        async def execute(self, stmt):
            self._n = getattr(self, "_n", 0) + 1
            if self._n == 1:
                return _Res(delivery)
            if self._n == 2:
                return _Res(sub)

            # Token-fenced outcome persistence uses Core UPDATEs rather than
            # mutating the selected ORM object. Mirror the successful CAS so
            # the fixture still verifies both the durable failure state and
            # the absence of provider I/O.
            if getattr(getattr(stmt, "table", None), "name", None) == "webhook_deliveries":
                params = stmt.compile().params
                for key in (
                    "status",
                    "error",
                    "dispatch_token",
                    "dispatch_lease_expires_at",
                    "next_dispatch_at",
                ):
                    if key in params:
                        setattr(delivery, key, params[key])
            return _Res(rowcount=1)

        async def commit(self):
            pass

        async def rollback(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    post_spy = AsyncMock()

    with patch.object(svc, "AsyncSessionLocal", lambda: _FakeDB()), \
         patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch("httpx.AsyncClient.post", post_spy):
        result = await svc.deliver(delivery.id)

    post_spy.assert_not_called()           # never egressed
    assert result.get("error") == "blocked_unsafe_target"
    assert delivery.status == "FAILED"
    assert "non-public address" in (delivery.error or "")
