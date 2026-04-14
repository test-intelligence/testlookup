"""
Unit tests for app.core.token_revocation — the JWT denylist backing
/auth/logout, /auth/change-password, /auth/first-time-reset.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core import token_revocation


class _FakeRedis:
    """Minimal in-memory async Redis supporting set(ex=)/get."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = str(value) if not isinstance(value, str) else value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def get(self, key):
        return self.store.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    """Install a fake Redis into the revocation module."""
    fake = _FakeRedis()

    async def _get_fake():
        return fake

    monkeypatch.setattr(token_revocation, "_redis", _get_fake)
    return fake


# ── jti denylist ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_jti_writes_denylist_entry_with_ttl(fake_redis):
    await token_revocation.revoke_jti("abc123", ttl_seconds=600)
    assert "auth:revoked_jti:abc123" in fake_redis.store
    assert fake_redis.ttls["auth:revoked_jti:abc123"] == 600


@pytest.mark.asyncio
async def test_is_jti_revoked_true_after_revoke(fake_redis):
    await token_revocation.revoke_jti("abc123", ttl_seconds=600)
    assert await token_revocation.is_jti_revoked("abc123") is True


@pytest.mark.asyncio
async def test_is_jti_revoked_false_for_unknown_jti(fake_redis):
    assert await token_revocation.is_jti_revoked("never_revoked") is False


@pytest.mark.asyncio
async def test_revoke_jti_with_zero_ttl_clamped_to_one(fake_redis):
    await token_revocation.revoke_jti("jti", ttl_seconds=0)
    assert fake_redis.ttls["auth:revoked_jti:jti"] == 1


@pytest.mark.asyncio
async def test_is_jti_revoked_fails_open_when_redis_missing(monkeypatch):
    async def _no_redis():
        return None
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)
    # Fail-open — caller should NOT reject a request just because cache is down.
    assert await token_revocation.is_jti_revoked("any") is False


# ── user-level cutoff (password change / compromise response) ───────────────


@pytest.mark.asyncio
async def test_revoke_all_user_tokens_sets_cutoff(fake_redis):
    uid = uuid.uuid4()
    before = int(datetime.now(timezone.utc).timestamp())
    await token_revocation.revoke_all_user_tokens(uid)
    after = int(datetime.now(timezone.utc).timestamp())
    key = f"auth:tokens_valid_from:{uid}"
    assert key in fake_redis.store
    stored = int(fake_redis.store[key])
    assert before <= stored <= after


@pytest.mark.asyncio
async def test_token_before_cutoff_rejects_old_iat(fake_redis):
    uid = uuid.uuid4()
    await token_revocation.revoke_all_user_tokens(uid)
    cutoff = int(fake_redis.store[f"auth:tokens_valid_from:{uid}"])

    # Token issued one second before the cutoff → rejected
    assert await token_revocation.is_token_before_cutoff(uid, cutoff - 1) is True


@pytest.mark.asyncio
async def test_token_after_cutoff_accepted(fake_redis):
    """Brand-new token issued after the cutoff must NOT be rejected."""
    uid = uuid.uuid4()
    await token_revocation.revoke_all_user_tokens(uid)
    cutoff = int(fake_redis.store[f"auth:tokens_valid_from:{uid}"])

    assert await token_revocation.is_token_before_cutoff(uid, cutoff + 10) is False


@pytest.mark.asyncio
async def test_no_cutoff_means_no_revocation(fake_redis):
    uid = uuid.uuid4()
    # Never revoked — any iat (or even None) passes.
    assert await token_revocation.is_token_before_cutoff(uid, 12345) is False
    assert await token_revocation.is_token_before_cutoff(uid, None) is False


@pytest.mark.asyncio
async def test_legacy_token_without_iat_rejected_if_cutoff_exists(fake_redis):
    """
    A legacy access token (issued before we added `iat`) must be rejected
    if the user has any revocation cutoff, since we cannot prove it was
    issued after the cutoff.
    """
    uid = uuid.uuid4()
    await token_revocation.revoke_all_user_tokens(uid)
    assert await token_revocation.is_token_before_cutoff(uid, None) is True


@pytest.mark.asyncio
async def test_user_cutoff_fails_open_when_redis_missing(monkeypatch):
    async def _no_redis():
        return None
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)
    assert await token_revocation.is_token_before_cutoff(uuid.uuid4(), 12345) is False
