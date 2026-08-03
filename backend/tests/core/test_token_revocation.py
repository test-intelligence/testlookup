"""
Unit tests for app.core.token_revocation — the JWT denylist backing
/auth/logout, /auth/change-password, /auth/first-time-reset.
"""
import uuid
from datetime import datetime, timezone

import pytest
import structlog.testing

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


async def _no_redis():
    """Stand-in for ``_redis()`` during a Redis outage."""
    return None


class _BrokenRedis:
    """Redis client whose calls raise — connection lost mid-flight."""

    async def set(self, *_a, **_kw):
        raise ConnectionError("redis gone")

    async def get(self, *_a, **_kw):
        raise ConnectionError("redis gone")


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
async def test_is_jti_revoked_fails_CLOSED_when_redis_missing(monkeypatch):
    """Security fix 2026-08-03: "cannot verify" must never mean "not revoked".

    This used to return False (fail open), so a logged-out token kept working
    for the remainder of JWT_ACCESS_TOKEN_EXPIRE_MINUTES during a Redis outage.
    """
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)

    with pytest.raises(token_revocation.RevocationUnavailable):
        await token_revocation.is_jti_revoked("any")


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
async def test_user_cutoff_fails_CLOSED_when_redis_missing(monkeypatch):
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)

    with pytest.raises(token_revocation.RevocationUnavailable):
        await token_revocation.is_token_before_cutoff(uuid.uuid4(), 12345)


# ── fail-closed: the outage paths, precisely ────────────────────────────────


@pytest.mark.asyncio
async def test_read_path_fails_closed_when_the_connection_breaks_mid_call(monkeypatch):
    """A raising client is the same verdict as no client: unknown ⇒ reject."""
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)

    async def _broken():
        return _BrokenRedis()

    monkeypatch.setattr(token_revocation, "_redis", _broken)

    with pytest.raises(token_revocation.RevocationUnavailable):
        await token_revocation.is_jti_revoked("abc")
    with pytest.raises(token_revocation.RevocationUnavailable):
        await token_revocation.is_token_before_cutoff(uuid.uuid4(), 12345)


@pytest.mark.asyncio
async def test_outage_logs_an_error_with_kwargs(monkeypatch):
    """A silent security downgrade is what got us here — this must be loud."""
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(token_revocation.RevocationUnavailable):
            await token_revocation.is_jti_revoked("abc")

    events = [e for e in logs if e["event"] == "token_revocation_unavailable"]
    assert len(events) == 1
    assert events[0]["log_level"] == "error"
    # kwargs, never stdlib positional %s (backend.structlog-positional-args)
    assert events[0]["operation"] == "is_jti_revoked"


@pytest.mark.asyncio
async def test_env_escape_hatch_restores_fail_open_and_says_so(monkeypatch):
    """AUTH_REVOCATION_FAIL_OPEN is the operator's documented recovery lever.

    Environment-only, default False, and it logs an ERROR on every use so it
    can never quietly become the norm.
    """
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", True)
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)

    with structlog.testing.capture_logs() as logs:
        assert await token_revocation.is_jti_revoked("any") is False
        assert await token_revocation.is_token_before_cutoff(uuid.uuid4(), 12345) is False

    events = [e for e in logs if e["event"] == "token_revocation_failed_open"]
    assert len(events) == 2
    assert all(e["log_level"] == "error" for e in events)


@pytest.mark.asyncio
async def test_empty_jti_never_hits_the_store(monkeypatch):
    """No jti claim = nothing to look up; that is not an outage."""
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)

    assert await token_revocation.is_jti_revoked("") is False


# ── write path stays best-effort, but loud ──────────────────────────────────


@pytest.mark.asyncio
async def test_write_path_does_not_raise_but_logs_error(monkeypatch):
    """revoke_* must not 500 /auth/logout — the Postgres work still has to commit.

    The residual gap (a logout issued DURING an outage is never recorded) is
    documented in architecture/SECURITY.md §7.
    """
    monkeypatch.setattr(token_revocation, "_redis", _no_redis)

    with structlog.testing.capture_logs() as logs:
        await token_revocation.revoke_jti("abc", ttl_seconds=600)
        await token_revocation.revoke_all_user_tokens(uuid.uuid4())

    events = [e for e in logs if e["event"] == "token_revocation_write_failed"]
    assert len(events) == 2
    assert {e["operation"] for e in events} == {"revoke_jti", "revoke_all_user_tokens"}
