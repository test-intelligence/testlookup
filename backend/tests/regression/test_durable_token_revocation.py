import uuid
from datetime import datetime, timezone

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


@pytest.mark.asyncio
async def test_revoke_all_cutoff_uses_wall_clock_not_transaction_start(monkeypatch):
    """A password transaction may start before a concurrent token is issued.

    PostgreSQL ``now()`` is the transaction-start timestamp, so using it for
    the cutoff can leave that newer token valid.  ``clock_timestamp()`` reads
    the wall clock at the revocation statement and closes that race.  Both the
    insert and conflict-update paths must use the wall clock.
    """
    from app.core import token_revocation

    class Session:
        statement = ""

        async def execute(self, statement, _params):
            self.statement = str(statement)

    async def no_cache():
        return None

    session = Session()
    monkeypatch.setattr(token_revocation, "_redis", no_cache)

    await token_revocation.revoke_all_user_tokens(uuid.uuid4(), db=session)

    normalized = " ".join(session.statement.lower().split())
    assert normalized.count("clock_timestamp()") == 2
    assert "now()" not in normalized


@pytest.mark.asyncio
async def test_revoke_all_copies_returned_durable_cutoff_to_cache(monkeypatch):
    from app.core import token_revocation

    cutoff = datetime(2033, 5, 18, 3, 33, 20, 125000, tzinfo=timezone.utc)

    class Result:
        def scalar_one(self):
            return cutoff

    class Session:
        async def execute(self, _statement, _params):
            return Result()

    cached = {}

    class Cache:
        async def set(self, key, value, *, ex):
            cached.update(key=key, value=value, ex=ex)

    async def redis():
        return Cache()

    monkeypatch.setattr(token_revocation, "_redis", redis)
    user_id = uuid.uuid4()

    await token_revocation.revoke_all_user_tokens(user_id, db=Session())

    assert cached["key"] == f"auth:tokens_valid_from:{user_id}"
    assert cached["value"] == str(cutoff.timestamp())
    assert cached["ex"] >= 60


@pytest.mark.asyncio
async def test_revoke_all_copies_owned_session_cutoff_to_cache(monkeypatch):
    from app.core import token_revocation

    cutoff = datetime(2033, 5, 18, 3, 33, 20, 875000, tzinfo=timezone.utc)

    async def durable(statement, _params):
        assert "returning valid_from" in statement.lower()
        return [(cutoff,)]

    cached = {}

    class Cache:
        async def set(self, key, value, *, ex):
            cached.update(key=key, value=value, ex=ex)

    async def redis():
        return Cache()

    monkeypatch.setattr(token_revocation, "_durable_execute", durable)
    monkeypatch.setattr(token_revocation, "_redis", redis)
    user_id = uuid.uuid4()

    await token_revocation.revoke_all_user_tokens(user_id)

    assert cached["key"] == f"auth:tokens_valid_from:{user_id}"
    assert cached["value"] == str(cutoff.timestamp())
