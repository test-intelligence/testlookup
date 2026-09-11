"""Every auth dependency must record WHICH credential authenticated the request.

Before this, a ``User`` returned by ``get_current_user_or_api_key`` carried no
trace of its credential. The only marker was the request-local API-key project
binding — and that is ``None`` for a *user-scoped* API key, making it
indistinguishable from a JWT. A later MFA gate must exempt API keys (a key
embedded in CI can never answer a challenge), so "user-scoped key" reading as
"JWT" would have MFA-gated CI.

``deps.credential_kind(user)`` now answers ``"jwt"`` / ``"api_key"`` / ``None``.
This is plumbing only — no route reads it yet, and no route behaves differently.
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


class _HealthyRedis:
    async def get(self, key):
        return None

    async def set(self, key, value, ex=None):
        return True


async def _durable_unreachable(statement, params):
    raise ConnectionError("durable revocation store not under test")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # Pin the durable (Postgres) revocation store: left real, it answered
    # only when an app engine could reach a migrated database (CI's backend
    # step), and then decided the verdict instead of the Redis store these
    # tests set up. Unreachable = the Redis-only behaviour they were written
    # for; the durable path has its own tests (test_durable_token_revocation).
    monkeypatch.setattr(token_revocation, "_durable_execute", _durable_unreachable)
    monkeypatch.setattr(token_revocation.settings, "AUTH_REVOCATION_FAIL_OPEN", False)
    monkeypatch.setattr(deps, "decode_token", lambda _t: dict(_PAYLOAD))
    redis = _HealthyRedis()

    async def _get():
        return redis

    monkeypatch.setattr(token_revocation, "_redis", _get)


def _fake_db(*rows):
    queue = list(rows)

    async def _execute(*_a, **_kw):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(
            return_value=queue.pop(0) if queue else None
        )
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


def _user():
    return SimpleNamespace(id=_USER_ID, is_active=True)


def _api_key(project_id=None, scopes=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=_USER_ID,
        project_id=project_id,
        is_active=True,
        expires_at=None,
        last_used_at=None,
        name="ci-key",
        scopes=scopes if scopes is not None else [],
    )


@pytest.mark.asyncio
async def test_jwt_request_reports_jwt():
    user = await deps.get_current_user_or_api_key(
        db=_fake_db(_user()), bearer_token="tok", x_api_key=None
    )
    assert deps.credential_kind(user) == deps.CREDENTIAL_KIND_JWT


@pytest.mark.asyncio
async def test_project_scoped_api_key_reports_api_key():
    project_id = uuid.uuid4()
    user = await deps.get_current_user_or_api_key(
        db=_fake_db(_api_key(project_id), _user()),
        bearer_token=None,
        x_api_key="raw-key",
    )
    assert deps.credential_kind(user) == deps.CREDENTIAL_KIND_API_KEY
    # The pre-existing project binding is untouched.
    assert deps._api_key_bound_project(user) == project_id


@pytest.mark.asyncio
async def test_user_scoped_api_key_reports_api_key_not_jwt():
    """The case that was previously indistinguishable from a JWT."""
    user = await deps.get_current_user_or_api_key(
        db=_fake_db(_api_key(None), _user()), bearer_token=None, x_api_key="raw-key"
    )
    assert deps._api_key_bound_project(user) is None  # the old, ambiguous marker
    assert deps.credential_kind(user) == deps.CREDENTIAL_KIND_API_KEY


@pytest.mark.asyncio
async def test_api_key_context_tuple_carries_the_kind_too():
    user, project_id = await deps.get_api_key_context(
        db=_fake_db(_api_key(None), _user()), bearer_token=None, x_api_key="raw-key"
    )
    assert project_id is None
    assert deps.credential_kind(user) == deps.CREDENTIAL_KIND_API_KEY


@pytest.mark.asyncio
async def test_streaming_context_reports_api_key_without_binding_the_project():
    """Streaming is API-key only; it must report the kind but keep its project
    binding on the context, not on the user (unchanged behaviour)."""
    project_id = uuid.uuid4()
    ctx = await deps.get_streaming_api_key_context(
        db=_fake_db(_api_key(project_id, scopes=["stream:write"]), _user()),
        x_api_key="raw-key",
    )
    assert ctx.project_id == project_id
    assert deps.credential_kind(ctx.user) == deps.CREDENTIAL_KIND_API_KEY
    assert deps._api_key_bound_project(ctx.user) is None


def test_unauthenticated_user_object_reports_none():
    """A User loaded by a service (or a test fixture that overrides the
    dependency) is 'unknown', never silently 'jwt'."""
    assert deps.credential_kind(SimpleNamespace(id=_USER_ID)) is None


def test_garbage_attribute_value_reports_none():
    stray = SimpleNamespace(id=_USER_ID)
    setattr(stray, deps._CREDENTIAL_KIND_ATTR, "saml")
    assert deps.credential_kind(stray) is None
