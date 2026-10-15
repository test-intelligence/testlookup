"""A revocation-check 503 says what actually failed.

On the homelab (2026-10-13) users saw "Token revocation status cannot be
verified right now (revocation store unavailable)" and the log said "Restore
Redis". Redis and PostgreSQL were both healthy: the worker had no free
database connection (QueuePool timeout) for the durable revocation lookup.

The error now records its store (``database`` / ``cache``) and whether the
pool was exhausted; the 503 body and the operator log both name the cause.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.core import token_revocation as tr
from app.core.config import settings


class _Log:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def error(self, event, **kw):
        self.events.append((event, kw))

    def debug(self, *a, **kw):
        pass

    warning = info = debug


@pytest.fixture
def durable(monkeypatch):
    """Staging/production: the durable table is the authority."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_REVOCATION_FAIL_OPEN", False)
    log = _Log()
    monkeypatch.setattr(tr, "logger", log)
    return log


def _refusing(exc):
    return AsyncMock(side_effect=exc)


@pytest.mark.asyncio
async def test_an_exhausted_pool_is_named_as_such(monkeypatch, durable):
    monkeypatch.setattr(tr, "_durable_execute", _refusing(PoolTimeout(
        "QueuePool limit of size 2 overflow 1 reached, connection timed out, timeout 30.00")))
    with pytest.raises(tr.RevocationUnavailable) as caught:
        await tr.is_jti_revoked("jti-1")
    assert (caught.value.store, caught.value.pool_exhausted) == ("database", True)
    detail = tr.unavailable_detail(caught.value)
    assert "no free database connection" in detail and "not a credential problem" in detail
    (event, fields), = [e for e in durable.events if e[0] == "token_revocation_unavailable"]
    assert "QueuePool" in fields["detail"] and "Redis" not in fields["detail"]


@pytest.mark.asyncio
async def test_a_database_failure_names_the_database(monkeypatch, durable):
    monkeypatch.setattr(tr, "_durable_execute", _refusing(OperationalError("SELECT 1", {}, Exception("down"))))
    with pytest.raises(tr.RevocationUnavailable) as caught:
        await tr.is_token_before_cutoff(uuid.uuid4(), 1.0)
    assert (caught.value.store, caught.value.pool_exhausted) == ("database", False)
    assert "could not reach its database" in tr.unavailable_detail(caught.value)
    (event, fields), = [e for e in durable.events if e[0] == "token_revocation_unavailable"]
    assert "PostgreSQL" in fields["detail"]


@pytest.mark.asyncio
async def test_a_cache_failure_inside_the_durable_block_keeps_its_name(monkeypatch, durable):
    # The durable table answers "not revoked"; the legacy Redis marker cannot
    # be read. That is the cache failing, not the database.
    monkeypatch.setattr(tr, "_durable_execute", AsyncMock(return_value=[]))
    monkeypatch.setattr(tr, "_redis", AsyncMock(return_value=None))
    with pytest.raises(tr.RevocationUnavailable) as caught:
        await tr.is_jti_revoked("jti-2")
    assert caught.value.store == "cache"
    assert "token cache" in tr.unavailable_detail(caught.value)
    (event, fields), = [e for e in durable.events if e[0] == "token_revocation_unavailable"]
    assert "Restore Redis" in fields["detail"]


@pytest.mark.asyncio
async def test_a_redis_error_inside_the_durable_block_is_the_cache(monkeypatch, durable):
    monkeypatch.setattr(tr, "_durable_execute", AsyncMock(return_value=[]))

    class _Redis:
        async def get(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr(tr, "_redis", AsyncMock(return_value=_Redis()))
    with pytest.raises(tr.RevocationUnavailable) as caught:
        await tr.is_jti_revoked("jti-3")
    assert caught.value.store == "cache"


@pytest.mark.asyncio
async def test_the_request_503_carries_the_cause(monkeypatch, durable):
    from app.core import deps
    from app.core.security import create_access_token

    monkeypatch.setattr(tr, "_durable_execute", _refusing(PoolTimeout("QueuePool limit reached")))
    token = create_access_token(str(uuid.uuid4()))
    with pytest.raises(HTTPException) as caught:
        await deps.get_current_user(db=AsyncMock(), token=token)
    assert caught.value.status_code == 503
    assert "no free database connection" in caught.value.detail
    assert "revocation store unavailable" not in caught.value.detail
    assert caught.value.headers["Retry-After"] == "5"
