"""The dual-auth dependency must not launder non-401 auth errors into 401.

``get_current_user_or_api_key`` (and its sibling ``get_api_key_context``) used
to catch **every** ``HTTPException`` from the bearer path and fall through to
the API-key path, ending at a generic 401 "Authentication required".

That swallowed the 503 that fail-closed token revocation raises when Redis is
unreachable (``core/token_revocation.py`` → ``get_current_user``). Because
``bootstrap.register_routers`` injects this dependency router-wide, a Redis
outage reached the SPA as 401 on every authenticated route — logging the user
out and burning a refresh, i.e. exactly the re-login loop fail-closed
revocation exists to avoid.

The rule now: **only a 401 falls through**; everything else propagates
unchanged. These tests drive the real dependency directly, because the
integration ``auth_as`` fixture overrides it wholesale and would never see the
regression.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from app.core import deps, token_revocation  # noqa: E402

_USER_ID = uuid.uuid4()
_PAYLOAD = {"sub": str(_USER_ID), "type": "access", "jti": "jti-1", "iat": 1_900_000_000}


class _FakeRedis:
    """Healthy revocation store with nothing revoked."""

    async def get(self, key):
        return None

    async def set(self, key, value, ex=None):
        return True


def _fake_db(user=None, api_key=None):
    """A session whose ``execute`` answers the ApiKey lookup then the User lookup.

    ``_validate_api_key`` issues two queries in order (ApiKey, then User);
    ``get_current_user`` issues only the User one.
    """
    rows = [r for r in (api_key, user) if r is not None] or [user]
    calls = list(rows)

    async def _execute(*_a, **_kw):
        result = MagicMock()
        value = calls.pop(0) if calls else None
        result.scalar_one_or_none = MagicMock(return_value=value)
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


def _user():
    return SimpleNamespace(id=_USER_ID, is_active=True)


def _api_key(project_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=_USER_ID,
        project_id=project_id,
        is_active=True,
        expires_at=None,
        last_used_at=None,
        name="ci-key",
        scopes=[],
    )


@pytest.fixture(autouse=True)
def _fail_closed(monkeypatch):
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)


def _valid_token(monkeypatch):
    monkeypatch.setattr(deps, "decode_token", lambda _t: dict(_PAYLOAD))


def _invalid_token(monkeypatch):
    """Structurally broken bearer token → ``get_current_user`` answers 401."""
    from jose import JWTError

    def _boom(_t):
        raise JWTError("bad signature")

    monkeypatch.setattr(deps, "decode_token", _boom)


def _redis_down(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(token_revocation, "_redis", _none)


def _redis_up(monkeypatch):
    redis = _FakeRedis()

    async def _get():
        return redis

    monkeypatch.setattr(token_revocation, "_redis", _get)


# ── the headline regression ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revocation_unavailable_propagates_503_and_is_not_laundered_into_401(
    monkeypatch,
):
    """Redis outage through the REAL dual-auth dependency → 503, never 401."""
    _valid_token(monkeypatch)
    _redis_down(monkeypatch)

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user_or_api_key(
            db=_fake_db(user=_user()), bearer_token="tok", x_api_key=None
        )

    assert exc.value.status_code == 503
    assert "cannot be verified" in exc.value.detail
    assert exc.value.headers.get("Retry-After") == "5"


@pytest.mark.asyncio
async def test_503_propagates_even_when_a_valid_api_key_is_also_present(monkeypatch):
    """The API key must not paper over a server-side outage on the bearer path.

    Falling through here would answer 200 for a token we cannot verify — the
    fail-open behaviour that 2026-08-03 deliberately removed.
    """
    _valid_token(monkeypatch)
    _redis_down(monkeypatch)

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user_or_api_key(
            db=_fake_db(user=_user(), api_key=_api_key()),
            bearer_token="tok",
            x_api_key="raw-key",
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_get_api_key_context_also_propagates_503(monkeypatch):
    """The identical swallow existed at the second site — fixed identically."""
    _valid_token(monkeypatch)
    _redis_down(monkeypatch)

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_api_key_context(
            db=_fake_db(user=_user()), bearer_token="tok", x_api_key=None
        )

    assert exc.value.status_code == 503


# ── the behaviour the swallow existed for is preserved ──────────────────────


@pytest.mark.asyncio
async def test_invalid_bearer_still_falls_through_to_a_valid_api_key(monkeypatch):
    _invalid_token(monkeypatch)
    _redis_up(monkeypatch)
    user = _user()

    resolved = await deps.get_current_user_or_api_key(
        db=_fake_db(user=user, api_key=_api_key()),
        bearer_token="garbage",
        x_api_key="raw-key",
    )

    assert resolved is user


@pytest.mark.asyncio
async def test_invalid_bearer_without_api_key_is_still_401(monkeypatch):
    _invalid_token(monkeypatch)
    _redis_up(monkeypatch)

    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user_or_api_key(
            db=_fake_db(user=_user()), bearer_token="garbage", x_api_key=None
        )

    assert exc.value.status_code == 401
    assert "Authentication required" in exc.value.detail


@pytest.mark.asyncio
async def test_no_credentials_at_all_is_still_401(monkeypatch):
    with pytest.raises(deps.HTTPException) as exc:
        await deps.get_current_user_or_api_key(
            db=_fake_db(), bearer_token=None, x_api_key=None
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_token_still_falls_through_to_the_api_key(monkeypatch):
    """A *revoked* token is a 401-class failure, so fall-through still applies.

    This is the boundary case: 401 because the credential is genuinely bad
    (healthy Redis said "revoked") vs. 503 because we could not ask.
    """
    _valid_token(monkeypatch)

    class _RevokingRedis(_FakeRedis):
        async def get(self, key):
            return "1" if key.startswith("auth:revoked_jti:") else None

    redis = _RevokingRedis()

    async def _get():
        return redis

    monkeypatch.setattr(token_revocation, "_redis", _get)
    user = _user()

    resolved = await deps.get_current_user_or_api_key(
        db=_fake_db(user=user, api_key=_api_key()),
        bearer_token="tok",
        x_api_key="raw-key",
    )

    assert resolved is user


# ── happy paths unchanged ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_bearer_still_resolves(monkeypatch):
    _valid_token(monkeypatch)
    _redis_up(monkeypatch)
    user = _user()

    assert (
        await deps.get_current_user_or_api_key(
            db=_fake_db(user=user), bearer_token="tok", x_api_key=None
        )
        is user
    )


@pytest.mark.asyncio
async def test_valid_api_key_without_bearer_still_resolves(monkeypatch):
    user = _user()

    assert (
        await deps.get_current_user_or_api_key(
            db=_fake_db(user=user, api_key=_api_key()),
            bearer_token=None,
            x_api_key="raw-key",
        )
        is user
    )
