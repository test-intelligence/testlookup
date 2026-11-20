"""Sign-in rate limits are counted in one place, and one account name has its own ceiling.

The rules:

* In production and staging the rate limiter keeps its counters in Redis
  (``REDIS_URL``), so "10 sign-ins a minute per address" is one figure for
  every worker process and every pod, not one figure each. In development the
  counters stay in process memory (no Redis needed for a local run or the unit
  tests). ``RATE_LIMIT_SHARED_STORAGE`` overrides the choice either way.
* The Redis client the limiter uses gives up quickly (short timeouts, one
  attempt), and when Redis cannot be reached the limiter falls back to
  in-memory counters with the same limits. A Redis outage never turns the
  limit off and never makes a request wait long.
* A ``REDIS_URL`` the limiter cannot use never stops the application from
  starting: the limiter stays in memory and says so in the log.
* ``POST /api/v1/auth/login`` also counts attempts per typed account name
  (``LOGIN_ATTEMPTS_PER_USERNAME_PER_MINUTE``), whatever address they come
  from, before the account is looked up. It does not depend on the account
  lockout, answers the same for a name that does not exist, and a counter
  that cannot be reached lets the sign-in through (the per-address limit and
  the lockout still apply).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis
from fastapi import HTTPException
from limits.storage import MemoryStorage
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from app import main
from app.core.config import settings
from app.routers import auth as auth_router

pytestmark = pytest.mark.regression

LOGIN = "/api/v1/auth/login"


def _request(address: str, path: str = LOGIN) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "client": (address, 40000),
        "server": ("testserver", 80), "scheme": "http", "app": SimpleNamespace(state=SimpleNamespace()),
    })


def _production(monkeypatch, mode: str = "auto") -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "RATE_LIMIT_SHARED_STORAGE", mode)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6399/0")


# ── where the counters live ──────────────────────────────────────────────────


@pytest.mark.parametrize("env", ["production", "staging"])
def test_production_and_staging_count_in_redis(monkeypatch, env):
    _production(monkeypatch)
    monkeypatch.setattr(settings, "APP_ENV", env)

    kwargs = main._rate_limit_storage_kwargs()

    assert kwargs["storage_uri"] == settings.REDIS_URL
    assert kwargs["in_memory_fallback_enabled"] is True


def test_development_counts_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_SHARED_STORAGE", "auto")

    assert main._rate_limit_storage_kwargs() == {}
    assert isinstance(main._build_limiter()._storage, MemoryStorage)


def test_the_setting_overrides_the_environment(monkeypatch):
    _production(monkeypatch, mode="off")
    assert main._rate_limit_storage_kwargs() == {}

    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_SHARED_STORAGE", "on")
    assert main._rate_limit_storage_kwargs()["storage_uri"] == settings.REDIS_URL


def test_the_production_limiter_is_built_on_redis_with_a_fallback(monkeypatch):
    _production(monkeypatch)

    limiter = main._build_limiter()

    assert limiter._storage_uri == settings.REDIS_URL
    assert not isinstance(limiter._storage, MemoryStorage)
    assert limiter._in_memory_fallback_enabled is True
    assert isinstance(limiter._fallback_storage, MemoryStorage)


def test_the_redis_client_gives_up_quickly(monkeypatch):
    """Short timeouts and a single attempt: redis-py's defaults are 5 s and retries."""
    _production(monkeypatch)

    options = main._rate_limit_storage_kwargs()["storage_options"]
    client_kwargs = main._build_limiter()._storage.storage.connection_pool.connection_kwargs

    assert 0 < options["socket_connect_timeout"] <= 1
    assert 0 < options["socket_timeout"] <= 1
    assert client_kwargs["socket_connect_timeout"] == options["socket_connect_timeout"]
    assert client_kwargs["socket_timeout"] == options["socket_timeout"]
    assert client_kwargs["retry"]._retries == 0


def test_a_redis_url_the_limiter_cannot_use_does_not_stop_startup(monkeypatch):
    _production(monkeypatch)
    monkeypatch.setattr(settings, "REDIS_URL", "not-a-storage://nowhere")
    recorder = MagicMock()

    with patch.object(main, "logger", recorder):
        limiter = main._build_limiter()

    assert isinstance(limiter._storage, MemoryStorage)
    assert recorder.warning.call_args.args[0] == "rate_limit_shared_storage_unusable"


# ── the limit itself, across "workers" ───────────────────────────────────────


def _two_workers(monkeypatch):
    """Two limiters built the production way, pointed at one shared store."""
    _production(monkeypatch)
    shared = MemoryStorage()
    monkeypatch.setattr("slowapi.extension.storage_from_string", lambda *_a, **_k: shared)
    workers = []
    for _ in range(2):
        limiter = main._build_limiter()
        workers.append(main._build_auth_limiters(limiter)[LOGIN][0])
    return workers


@pytest.mark.asyncio
async def test_ten_sign_ins_spread_over_two_workers_pass_and_the_eleventh_is_refused(monkeypatch):
    first, second = _two_workers(monkeypatch)
    address = "203.0.113.9"

    for attempt in range(10):
        await (first, second)[attempt % 2](_request(address))

    with pytest.raises(RateLimitExceeded):
        await first(_request(address))
    with pytest.raises(RateLimitExceeded):
        await second(_request(address))
    # Another address still has its own budget.
    await second(_request("203.0.113.10"))


@pytest.mark.asyncio
async def test_without_shared_storage_each_worker_counts_alone(monkeypatch):
    """The control: what the shared store changes."""
    _production(monkeypatch, mode="off")
    first = main._build_auth_limiters(main._build_limiter())[LOGIN][0]
    second = main._build_auth_limiters(main._build_limiter())[LOGIN][0]

    for _ in range(10):
        await first(_request("203.0.113.11"))
        await second(_request("203.0.113.11"))


@pytest.mark.asyncio
async def test_an_unreachable_redis_falls_back_to_memory_and_still_limits(monkeypatch):
    _production(monkeypatch)
    attempts = [0]

    def refuse(self):
        attempts[0] += 1
        raise redis.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(redis.connection.Connection, "connect", refuse)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    limited = main._build_auth_limiters(main._build_limiter())[LOGIN][0]

    for _ in range(10):
        await limited(_request("203.0.113.12"))
    with pytest.raises(RateLimitExceeded):
        await limited(_request("203.0.113.12"))

    assert attempts[0] >= 1, "the Redis store was never tried"
    # One attempt per try, and a dead store is not retried on every request.
    assert attempts[0] <= 6


# ── one account name, many addresses ─────────────────────────────────────────


def _name_limit(monkeypatch, per_minute: int) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_USERNAME_PER_MINUTE", per_minute)


def test_one_account_name_is_bounded_whatever_the_address(monkeypatch):
    _name_limit(monkeypatch, 3)
    name = f"user-{uuid.uuid4().hex}"

    for _ in range(3):
        auth_router.enforce_login_name_limit(name)
    with pytest.raises(HTTPException) as refused:
        auth_router.enforce_login_name_limit(name)

    assert refused.value.status_code == 429
    assert int(refused.value.headers["Retry-After"]) >= 1
    assert "sign-in attempts" in refused.value.detail
    # Case and surrounding spaces do not open a second budget.
    with pytest.raises(HTTPException):
        auth_router.enforce_login_name_limit(f"  {name.upper()} ")
    # Another name is not affected.
    auth_router.enforce_login_name_limit(f"user-{uuid.uuid4().hex}")


def test_zero_turns_the_name_limit_off(monkeypatch):
    _name_limit(monkeypatch, 0)
    name = f"user-{uuid.uuid4().hex}"
    for _ in range(50):
        auth_router.enforce_login_name_limit(name)


def test_development_keeps_the_relaxed_ceiling(monkeypatch):
    _name_limit(monkeypatch, 3)
    monkeypatch.setattr(settings, "APP_ENV", "development")
    name = f"user-{uuid.uuid4().hex}"
    for _ in range(50):
        auth_router.enforce_login_name_limit(name)


def test_a_counter_that_cannot_be_reached_lets_the_sign_in_through(monkeypatch):
    _name_limit(monkeypatch, 1)
    broken = SimpleNamespace(limiter=SimpleNamespace(hit=MagicMock(side_effect=ConnectionError("down"))))
    monkeypatch.setattr(main, "limiter", broken)
    recorder = MagicMock()

    with patch.object(auth_router, "logger", recorder):
        auth_router.enforce_login_name_limit("someone")
        auth_router.enforce_login_name_limit("someone")

    assert recorder.warning.called


def test_the_typed_name_is_never_the_storage_key(monkeypatch):
    _name_limit(monkeypatch, 5)
    hit = MagicMock(return_value=True)
    monkeypatch.setattr(main, "limiter", SimpleNamespace(limiter=SimpleNamespace(hit=hit)))

    auth_router.enforce_login_name_limit("Pat@Example.com")

    identifiers = [str(part) for part in hit.call_args.args[1:]]
    assert all("pat" not in part.lower() or part == "login-name" for part in identifiers)
    assert any(len(part) == 64 for part in identifiers)


@pytest.mark.asyncio
async def test_the_login_route_refuses_before_it_looks_the_account_up(monkeypatch):
    _name_limit(monkeypatch, 2)
    monkeypatch.setattr(auth_router, "verify_password", lambda *_a: False)
    monkeypatch.setattr(auth_router.mfa_service, "load_policy", AsyncMock(return_value=SimpleNamespace()))
    name = f"nobody-{uuid.uuid4().hex}"
    db = MagicMock()
    no_user = MagicMock()
    no_user.scalars.return_value.first.return_value = None
    db.execute = AsyncMock(return_value=no_user)
    form = SimpleNamespace(username=name, password="wrong-password")

    async def attempt():
        return await auth_router.login(request=_request("203.0.113.20"), form_data=form, db=db)

    for _ in range(2):
        with pytest.raises(HTTPException) as wrong:
            await attempt()
        assert wrong.value.status_code == 401
    lookups = db.execute.await_count

    with pytest.raises(HTTPException) as refused:
        await attempt()

    assert refused.value.status_code == 429
    assert db.execute.await_count == lookups, "the account was looked up after the limit was reached"
