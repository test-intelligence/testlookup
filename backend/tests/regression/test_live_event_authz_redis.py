"""``live_event_authz`` against a Redis that behaves like Redis.

The handler tests in ``test_live_event_tenant_binding.py`` monkeypatch all four
of this module's functions, so they assert the behaviour of their own stubs.
That left 51 of 67 statements in this file unexecuted — the ``SET NX``, the
``if won`` early return, the ``GET``, the bytes decode, the ``except``/``raise``
and the whole credential cache — while 24 tests passed.

That is the same shape as the defect this batch is about: the H2 tests all
asserted file *content*, so a wrong value passed every one of them. Here the
assertions were over stub behaviour, so a wrong implementation would have
passed every one of them. Each test below drives the real code.

The double ``get_redis`` import in the module under test is deliberate (it is
imported inside each function to keep module import cheap), which is why these
patch ``app.db.redis_client.get_redis`` rather than a module attribute.
"""
from __future__ import annotations

import hashlib

import pytest

from app.services import live_event_authz as authz
from app.services.live_event_authz import (
    RUN_PROJECT_TTL_SECONDS,
    STREAMING_KEY_TTL_SECONDS,
    RunProjectBindingUnavailable,
    cached_streaming_project,
    remember_run_project,
    remember_streaming_project,
    resolve_run_project,
    run_project_key,
    streaming_key_cache_key,
)

PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RUN = "build-42"


class FakeRedis:
    """Only the semantics these functions rely on, and they rely on all of it.

    ``set(..., nx=True)`` returns a truthy value only when the key was absent —
    that is what makes the first ``run_start`` the owner — and returns None
    otherwise, which is what redis-py does. Getting this wrong in either
    direction is one of the mutations the stubbed tests could not see.
    """

    def __init__(self, *, raise_on=(), binary=False):
        self.store: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.raise_on = set(raise_on)
        self.binary = binary

    async def set(self, key, value, ex=None, nx=False):
        self.calls.append(("set", key, value, ex, nx))
        if "set" in self.raise_on:
            raise ConnectionError("redis is down")
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def get(self, key):
        self.calls.append(("get", key))
        if "get" in self.raise_on:
            raise ConnectionError("redis is down")
        value = self.store.get(key)
        if value is None:
            return None
        return value.encode() if self.binary else value


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


def _set_calls(fake):
    return [c for c in fake.calls if c[0] == "set"]


# ── remember_run_project: the SET NX that decides the tenant ────────────


@pytest.mark.asyncio
async def test_the_first_run_start_becomes_the_owner(redis):
    assert await remember_run_project(RUN, PROJECT_A) == PROJECT_A
    assert redis.store[run_project_key(RUN)] == PROJECT_A


@pytest.mark.asyncio
async def test_winning_the_bind_costs_one_round_trip(redis):
    """The `if won` early return is a hot-path saving, so pin it.

    Every run_start takes this path. Falling through to a GET as well is
    behaviourally identical — the retry loop reads back what was just written —
    which is why only a call count can tell the difference.
    """
    await remember_run_project(RUN, PROJECT_A)
    assert len(redis.calls) == 1, (
        "a winning bind now costs " + str(len(redis.calls)) + " Redis "
        "round-trips instead of 1"
    )


@pytest.mark.asyncio
async def test_the_bind_is_conditional_and_expiring(redis):
    """NX and a TTL are both load-bearing, so assert the call itself.

    Without NX a second project overwrites the first and the check inverts.
    Without the TTL the binding outlives every run and a reused run_id 409s
    forever.
    """
    await remember_run_project(RUN, PROJECT_A)
    _op, key, value, ex, nx = _set_calls(redis)[0]
    assert key == run_project_key(RUN)
    assert value == PROJECT_A
    assert nx is True
    assert ex == RUN_PROJECT_TTL_SECONDS


@pytest.mark.asyncio
async def test_a_second_project_is_told_who_owns_the_run(redis):
    await remember_run_project(RUN, PROJECT_A)
    assert await remember_run_project(RUN, PROJECT_B) == PROJECT_A
    assert redis.store[run_project_key(RUN)] == PROJECT_A, "the run was stolen"


@pytest.mark.asyncio
async def test_the_owner_re_binding_is_idempotent(redis):
    await remember_run_project(RUN, PROJECT_A)
    assert await remember_run_project(RUN, PROJECT_A) == PROJECT_A


@pytest.mark.asyncio
async def test_an_incumbent_stored_as_bytes_is_decoded(monkeypatch):
    """redis-py returns bytes unless decode_responses is set.

    Skipping the decode makes ``owner != opening_project`` true for the real
    owner, so every legitimate re-run_start 409s. The stub returned str, so no
    existing test could see it.
    """
    fake = FakeRedis(binary=True)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)

    await remember_run_project(RUN, PROJECT_A)
    owner = await remember_run_project(RUN, PROJECT_A)
    assert owner == PROJECT_A
    assert not isinstance(owner, bytes)


@pytest.mark.asyncio
async def test_a_redis_failure_while_binding_fails_closed(monkeypatch):
    """The one write that must not be swallowed."""
    fake = FakeRedis(raise_on=("set",))
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)

    with pytest.raises(RunProjectBindingUnavailable):
        await remember_run_project(RUN, PROJECT_A)


@pytest.mark.asyncio
async def test_a_key_that_vanishes_mid_bind_is_retried(monkeypatch):
    """SET NX then GET is two round-trips and the key can expire between them.

    Reporting the caller as owner would leave the run UNBOUND behind a success
    response — the exact state the 503 exists to prevent.
    """
    fake = FakeRedis()

    original_set = fake.set
    state = {"first": True}

    async def flaky_set(key, value, ex=None, nx=False):
        if state["first"]:
            state["first"] = False
            fake.calls.append(("set", key, value, ex, nx))
            return None  # lost the race...
        return await original_set(key, value, ex=ex, nx=nx)

    fake.set = flaky_set  # type: ignore[method-assign]
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)

    # ...and the GET finds nothing, because the winner's key already expired.
    assert await remember_run_project(RUN, PROJECT_A) == PROJECT_A
    assert redis_stored(fake) == PROJECT_A


def redis_stored(fake):
    return fake.store.get(run_project_key(RUN))


@pytest.mark.asyncio
async def test_a_bind_that_never_settles_refuses_rather_than_guessing(monkeypatch):
    fake = FakeRedis()

    async def never_wins(key, value, ex=None, nx=False):
        fake.calls.append(("set", key, value, ex, nx))
        return None

    async def always_empty(key):
        fake.calls.append(("get", key))
        return None

    fake.set = never_wins  # type: ignore[method-assign]
    fake.get = always_empty  # type: ignore[method-assign]
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)

    with pytest.raises(RunProjectBindingUnavailable):
        await remember_run_project(RUN, PROJECT_A)
    assert len(_set_calls(fake)) == authz._BIND_ATTEMPTS


@pytest.mark.asyncio
async def test_nothing_to_bind_is_not_an_error(redis):
    assert await remember_run_project("", PROJECT_A) is None
    assert await remember_run_project(RUN, "") is None
    assert redis.calls == []


# ── resolve_run_project: the read, which must fail OPEN ─────────────────


@pytest.mark.asyncio
async def test_resolve_returns_the_bound_project(redis):
    await remember_run_project(RUN, PROJECT_A)
    assert await resolve_run_project(RUN) == PROJECT_A


@pytest.mark.asyncio
async def test_resolve_decodes_bytes(monkeypatch):
    fake = FakeRedis(binary=True)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    await remember_run_project(RUN, PROJECT_A)
    assert await resolve_run_project(RUN) == PROJECT_A


@pytest.mark.asyncio
async def test_an_unknown_run_has_no_opinion(redis):
    assert await resolve_run_project("never-seen") is None


@pytest.mark.asyncio
async def test_resolving_nothing_asks_redis_nothing(redis):
    assert await resolve_run_project("") is None
    assert redis.calls == []


@pytest.mark.asyncio
async def test_a_redis_failure_while_reading_does_not_break_ingestion(monkeypatch):
    """None means "cannot verify", never "verified different".

    Redis is already the transport for these events, so turning a lookup
    failure into a second, separate outage buys nothing.
    """
    fake = FakeRedis(raise_on=("get",))
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    assert await resolve_run_project(RUN) is None


# ── the credential cache ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_verified_credential_is_cached_under_its_digest(redis):
    await remember_streaming_project("qai_secret_key", PROJECT_A)

    _op, key, value, ex, _nx = _set_calls(redis)[0]
    assert key == streaming_key_cache_key("qai_secret_key")
    assert value == PROJECT_A
    assert ex == STREAMING_KEY_TTL_SECONDS, (
        "the cache entry has no expiry, so the documented 30-second revocation "
        "lag is permanent instead"
    )
    assert "qai_secret_key" not in key


def test_the_cache_key_is_a_digest_of_the_credential():
    digest = hashlib.sha256(b"qai_secret_key").hexdigest()
    assert streaming_key_cache_key("qai_secret_key").endswith(digest)


def test_two_credentials_do_not_share_a_cache_slot():
    assert streaming_key_cache_key("key-one") != streaming_key_cache_key("key-two")


@pytest.mark.asyncio
async def test_a_cached_credential_is_returned(redis):
    await remember_streaming_project("k", PROJECT_A)
    assert await cached_streaming_project("k") == PROJECT_A


@pytest.mark.asyncio
async def test_a_cached_credential_is_decoded(monkeypatch):
    fake = FakeRedis(binary=True)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    await remember_streaming_project("k", PROJECT_A)
    assert await cached_streaming_project("k") == PROJECT_A


@pytest.mark.asyncio
async def test_an_uncached_credential_returns_none_so_the_full_check_runs(redis):
    assert await cached_streaming_project("never-seen") is None


@pytest.mark.asyncio
async def test_a_redis_outage_forces_the_full_check_rather_than_a_bypass(monkeypatch):
    """The property that makes this cache safe to have at all."""
    fake = FakeRedis(raise_on=("get",))
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    assert await cached_streaming_project("k") is None


@pytest.mark.asyncio
async def test_a_failed_cache_write_is_not_fatal(monkeypatch):
    """Failing to cache costs latency; it must not cost the request."""
    fake = FakeRedis(raise_on=("set",))
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    await remember_streaming_project("k", PROJECT_A)  # must not raise


@pytest.mark.asyncio
async def test_an_empty_credential_is_never_cached(redis):
    await remember_streaming_project("", PROJECT_A)
    await remember_streaming_project("k", "")
    assert _set_calls(redis) == []
    assert await cached_streaming_project("") is None
