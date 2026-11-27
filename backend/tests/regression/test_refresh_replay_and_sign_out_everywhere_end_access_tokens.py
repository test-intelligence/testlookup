"""A replayed refresh token, and "sign out everywhere", end every access token of the user.

The rules:

* Replaying a refresh token that was already rotated is the sign that it was
  stolen. The server then revokes the user's refresh tokens AND every access
  token already issued to them, not only the refresh tokens.
* ``POST /auth/logout?all=true`` ends every access token of the user. Plain
  ``POST /auth/logout`` ends only the access token that made the call.
* A refresh token that was merely revoked (by a logout, say) and is presented
  again is refused, but it is not a replay: it must not end a session the user
  has opened since.
* The default access-token lifetime is 30 minutes.
* A refresh token is single-use. Reusing it at any time is a replay and must
  never mint a second successor; browser refreshes are single-flighted so
  legitimate concurrent requests converge on the first rotated pair.

The harness is SQLite, which has no durable revocation table, so the handler's
``revoke_all_user_tokens(user_id, db)`` call is forwarded to the real function
without the transaction: it then writes the cutoff to the (fake) Redis store,
which the real auth dependency reads. Everything else is the mounted app.
"""
# ruff: noqa: F811 - pytest fixtures imported from tests.test_mfa are requested by name
from __future__ import annotations

import asyncio

import pytest

from tests.test_mfa import (  # noqa: F401 - fixtures are used by name
    _redis,
    _secret_key,
    _session_issuer_clock,
    client,
    db,
    login,
    make_user,
    session_factory,
)

pytestmark = pytest.mark.regression


@pytest.fixture
def cutoff_calls(monkeypatch):
    """Record the handler's cutoff calls and let them take effect through Redis."""
    from app.core import token_revocation
    from app.routers import auth as auth_router

    calls: list[tuple] = []

    async def _forward(user_id, db=None):
        calls.append((user_id, db))
        await token_revocation.revoke_all_user_tokens(user_id)

    monkeypatch.setattr(auth_router, "revoke_all_user_tokens", _forward)
    return calls


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _me(client, token: str) -> int:
    return (await client.get("/api/v1/auth/me", headers=_bearer(token))).status_code


async def _refresh(client, refresh_token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


# ── refresh-token replay ────────────────────────────────────────────────────


async def test_a_replay_ends_every_access_token_of_the_user(db, client, cutoff_calls):
    user = await make_user(db, username="replayed")
    bystander = await make_user(db, username="bystander")
    first = (await login(client, user=user)).json()
    other_browser = (await login(client, user=user)).json()
    bystander_token = (await login(client, user=bystander)).json()["access_token"]
    rotated = (await _refresh(client, first["refresh_token"])).json()
    assert await _me(client, first["access_token"]) == 200
    assert await _me(client, rotated["access_token"]) == 200
    assert cutoff_calls == []  # a normal refresh ends nothing

    replay = await _refresh(client, first["refresh_token"])

    assert replay.status_code == 401
    assert [uid for uid, _db in cutoff_calls] == [user.id]
    assert cutoff_calls[0][1] is not None  # written in the handler's own transaction
    for token in (first["access_token"], rotated["access_token"], other_browser["access_token"]):
        assert await _me(client, token) == 401
    # The refresh family is dead as before.
    assert (await _refresh(client, rotated["refresh_token"])).status_code == 401
    # Another user is untouched, and the owner can sign in again.
    assert await _me(client, bystander_token) == 200
    await asyncio.sleep(0.01)  # a new token must postdate the cutoff
    again = (await login(client, user=user)).json()["access_token"]
    assert await _me(client, again) == 200


async def test_a_normal_refresh_leaves_the_previous_access_token_usable(db, client, cutoff_calls):
    user = await make_user(db, username="rotator")
    first = (await login(client, user=user)).json()

    rotated = await _refresh(client, first["refresh_token"])

    assert rotated.status_code == 200
    assert await _me(client, first["access_token"]) == 200
    assert await _me(client, rotated.json()["access_token"]) == 200
    assert cutoff_calls == []


async def test_a_token_revoked_by_logout_is_refused_without_ending_newer_sessions(
    db, client, cutoff_calls
):
    """A stale tab presenting a logged-out refresh token is not a theft signal."""
    user = await make_user(db, username="stale-tab")
    stale = (await login(client, user=user)).json()
    assert (
        await client.post("/api/v1/auth/logout", headers=_bearer(stale["access_token"]))
    ).status_code == 204
    fresh = (await login(client, user=user)).json()["access_token"]

    resp = await _refresh(client, stale["refresh_token"])

    assert resp.status_code == 401
    assert cutoff_calls == []
    assert await _me(client, fresh) == 200


# ── sign out everywhere ─────────────────────────────────────────────────────


async def test_logout_all_ends_the_users_other_access_tokens(db, client, cutoff_calls):
    user = await make_user(db, username="everywhere")
    here = (await login(client, user=user)).json()
    there = (await login(client, user=user)).json()

    resp = await client.post("/api/v1/auth/logout?all=true", headers=_bearer(here["access_token"]))

    assert resp.status_code == 204, resp.text
    assert [uid for uid, _db in cutoff_calls] == [user.id]
    assert await _me(client, here["access_token"]) == 401
    assert await _me(client, there["access_token"]) == 401
    assert (await _refresh(client, there["refresh_token"])).status_code == 401


async def test_plain_logout_ends_only_the_calling_access_token(db, client, cutoff_calls):
    user = await make_user(db, username="one-device")
    here = (await login(client, user=user)).json()["access_token"]
    there = (await login(client, user=user)).json()["access_token"]

    resp = await client.post("/api/v1/auth/logout", headers=_bearer(here))

    assert resp.status_code == 204, resp.text
    assert cutoff_calls == []
    assert await _me(client, here) == 401
    assert await _me(client, there) == 200


# ── lifetime ────────────────────────────────────────────────────────────────


def test_the_default_access_token_lifetime_is_thirty_minutes():
    from app.core.config import Settings

    assert Settings.model_fields["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"].default == 30


# ── immediate duplicate redemption ──────────────────────────────────────────


def test_refresh_token_reuse_leeway_is_not_configurable():
    from app.core.config import Settings

    assert "JWT_REFRESH_REUSE_LEEWAY_SECONDS" not in Settings.model_fields


async def test_immediate_reuse_cannot_create_a_second_renewable_branch(
    db, client, cutoff_calls,
):
    user = await make_user(db, username="twotabs")
    first = (await login(client, user=user)).json()
    tab_a = await _refresh(client, first["refresh_token"])
    assert tab_a.status_code == 200
    assert cutoff_calls == []
    assert await _me(client, tab_a.json()["access_token"]) == 200

    tab_b = await _refresh(client, first["refresh_token"])

    assert tab_b.status_code == 401
    assert [uid for uid, _db in cutoff_calls] == [user.id]
    assert await _me(client, tab_a.json()["access_token"]) == 401
    assert (await _refresh(client, tab_a.json()["refresh_token"])).status_code == 401


async def test_reuse_does_not_revive_a_session_that_was_ended(db, client, cutoff_calls):
    """After a logout the rotated token must not mint a new pair."""
    user = await make_user(db, username="ended")
    first = (await login(client, user=user)).json()
    rotated = (await _refresh(client, first["refresh_token"])).json()
    assert (await client.post("/api/v1/auth/logout", headers=_bearer(rotated["access_token"]))).status_code == 204

    again = await _refresh(client, first["refresh_token"])

    assert again.status_code == 401
