import uuid

import pytest


@pytest.mark.asyncio
async def test_jti_revocation_uses_durable_authority(monkeypatch):
    from app.core import token_revocation
    calls = []

    async def durable(statement, params):
        calls.append((statement, params))
        return [(1,)]

    monkeypatch.setattr(token_revocation, "_durable_execute", durable)
    assert await token_revocation.is_jti_revoked("jti-1") is True
    assert "auth_token_revocations" in calls[0][0]


@pytest.mark.asyncio
async def test_jti_write_is_committed_before_cache_write(monkeypatch):
    from app.core import token_revocation
    events = []

    async def durable(statement, params):
        events.append("durable")
        return []

    class Cache:
        async def set(self, *_args, **_kwargs):
            events.append("cache")

    monkeypatch.setattr(token_revocation, "_durable_execute", durable)
    async def redis():
        return Cache()
    monkeypatch.setattr(token_revocation, "_redis", redis)
    await token_revocation.revoke_jti("jti-commit", 60)
    assert events == ["durable", "cache"]


@pytest.mark.asyncio
async def test_cutoff_survives_cache_eviction(monkeypatch):
    from app.core import token_revocation
    cutoff = 2_000_000_000

    async def durable(statement, params):
        return [(type("Stamp", (), {"timestamp": lambda self: cutoff})(),)]

    monkeypatch.setattr(token_revocation, "_durable_execute", durable)
    assert await token_revocation.is_token_before_cutoff(uuid.uuid4(), cutoff - 1) is True


@pytest.mark.asyncio
async def test_durable_cutoff_preserves_subsecond_ordering(monkeypatch):
    from app.core import token_revocation
    cutoff = 2_000_000_000.125

    async def durable(statement, params):
        return [(type("Stamp", (), {"timestamp": lambda self: cutoff})(),)]

    monkeypatch.setattr(token_revocation, "_durable_execute", durable)
    uid = uuid.uuid4()

    assert await token_revocation.is_token_before_cutoff(uid, 2_000_000_000.100) is True
    assert await token_revocation.is_token_before_cutoff(uid, 2_000_000_000.750) is False
