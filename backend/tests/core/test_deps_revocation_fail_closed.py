"""
``get_current_user`` must fail CLOSED when the revocation store is unreachable
(security fix 2026-08-03).

The old behaviour skipped the revocation check on a Redis outage, so a token
that had been logged out — or killed by a password change — kept working for
the rest of its 12-hour lifetime. The verdict is now:

  * revoked token          → 401 (unchanged)
  * healthy Redis, clean   → the user (unchanged)
  * Redis unreachable      → **503**, not 401

503 is the load-bearing part. The credentials may be perfectly valid; it is
the *server* that cannot verify them, and a 401 would send the SPA into a
re-login loop that cannot succeed while blaming the user's password for a
cache outage.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from app.core import deps, token_revocation  # noqa: E402


_USER_ID = uuid.uuid4()
_PAYLOAD = {"sub": str(_USER_ID), "type": "access", "jti": "jti-1", "iat": 1_900_000_000}


class _FakeRedis:
    def __init__(self, store: dict[str, str] | None = None):
        self.store = store or {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)
        return True


def _fake_db(user=None):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=user)
    db.execute = AsyncMock(return_value=result)
    return db


async def _durable_unreachable(statement, params):
    raise ConnectionError("durable revocation store not under test")


@pytest.fixture(autouse=True)
def _decode(monkeypatch):
    """Every test below presents a structurally valid access token.

    The durable (Postgres) revocation store is pinned unreachable: left real,
    it answered whenever an app engine could reach a migrated database (CI's
    backend step, or after another file set the engine up), and a missing
    Redis then meant "not revoked" instead of the outage these tests assert.
    The durable path has its own tests (test_durable_token_revocation).
    """
    monkeypatch.setattr(token_revocation, "_durable_execute", _durable_unreachable)
    monkeypatch.setattr(deps, "decode_token", lambda _t: dict(_PAYLOAD))
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)


def _install_redis(monkeypatch, redis):
    async def _get():
        return redis

    monkeypatch.setattr(token_revocation, "_redis", _get)


# ── the outage verdict ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_down_returns_503_not_401(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(token_revocation, "_redis", _no_redis)
    db = _fake_db(user=MagicMock())

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user(db=db, token="tok")

    assert exc.value.status_code == 503
    assert "cannot be verified" in exc.value.detail
    assert exc.value.headers.get("Retry-After") == "5"
    # It never got as far as looking the user up.
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_escape_hatch_downgrades_503_to_normal_service(monkeypatch):
    """AUTH_REVOCATION_FAIL_OPEN=true is the operator's recovery lever."""
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", True)

    async def _no_redis():
        return None

    monkeypatch.setattr(token_revocation, "_redis", _no_redis)
    user = MagicMock()
    db = _fake_db(user=user)

    assert await deps.get_current_user(db=db, token="tok") is user


# ── the normal verdicts are unchanged ───────────────────────────────────────


@pytest.mark.asyncio
async def test_revoked_jti_still_401s_when_redis_is_healthy(monkeypatch):
    _install_redis(monkeypatch, _FakeRedis({"auth:revoked_jti:jti-1": "1"}))
    db = _fake_db(user=MagicMock())

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user(db=db, token="tok")

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_token_before_user_cutoff_still_401s(monkeypatch):
    _install_redis(
        monkeypatch,
        _FakeRedis({f"auth:tokens_valid_from:{_USER_ID}": str(_PAYLOAD["iat"] + 1)}),
    )
    db = _fake_db(user=MagicMock())

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user(db=db, token="tok")

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_clean_token_still_resolves_when_redis_is_healthy(monkeypatch):
    _install_redis(monkeypatch, _FakeRedis())
    user = MagicMock()
    db = _fake_db(user=user)

    assert await deps.get_current_user(db=db, token="tok") is user
