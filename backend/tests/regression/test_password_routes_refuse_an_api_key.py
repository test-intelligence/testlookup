"""An API key cannot change its owner's password or sign its owner out.

The rule: ``POST /auth/first-time-reset``, ``POST /auth/change-password`` and
``POST /auth/logout`` are for an interactive (password / SSO) session only. A
request authenticated with an API key answers 403 and changes nothing.

Why: an API key is exempt from the forced-password-change gate (so an admin
reset does not break a pipeline), and ``first-time-reset`` asks for no current
password. A leaked key whose owner was still on a temporary password could
therefore set the owner's password and become a full interactive login.
``change-password`` also told a key holder whether a guessed password was right.

The key keeps working on ordinary routes, and a real session still changes its
password and signs out.
"""
# ruff: noqa: F811 - pytest fixtures imported from tests.test_mfa are requested by name
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.core.security import verify_password
from app.models.postgres import ApiKey, RefreshTokenRecord
from tests.test_mfa import (  # noqa: F401 - fixtures are used by name
    PASSWORD,
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

NEW_PASSWORD = "a-brand-new-passphrase-1"


async def _key_for(db, user, raw: str, scopes: list[str]) -> dict:
    db.add(
        ApiKey(
            user_id=user.id,
            name="ci",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_hint=raw[:8] + "...",
            scopes=scopes,
            is_active=True,
        )
    )
    await db.commit()
    return {"X-API-Key": raw}


async def _live_refresh_tokens(db, user) -> int:
    rows = await db.execute(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.user_id == user.id, RefreshTokenRecord.revoked_at.is_(None)
        )
    )
    return len(rows.scalars().all())


@pytest.mark.parametrize("scopes", [[], ["project:write"]], ids=["unscoped", "project-write"])
async def test_an_api_key_is_refused_on_the_password_routes_and_logout(db, client, scopes):
    user = await make_user(db, username="svc-owner", must_change_password=True)
    session = (await login(client, user=user)).json()  # the owner has a live session
    assert session["access_token"]
    key = await _key_for(db, user, "qai_fake_key_for_password_routes", scopes)
    hash_before = user.hashed_password

    # Control: the key is a working credential on an ordinary route.
    assert (await client.get("/api/v1/auth/me", headers=key)).status_code == 200

    reset = await client.post(
        "/api/v1/auth/first-time-reset",
        headers=key,
        json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    change_wrong = await client.post(
        "/api/v1/auth/change-password",
        headers=key,
        json={"current_password": "not-the-password", "new_password": NEW_PASSWORD},
    )
    change_right = await client.post(
        "/api/v1/auth/change-password",
        headers=key,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    # logout needs a bearer token to be present; any string will do next to a key.
    logout = await client.post(
        "/api/v1/auth/logout", headers={**key, "Authorization": "Bearer not-a-real-token"}
    )

    for resp in (reset, change_wrong, change_right, logout):
        assert resp.status_code == 403, resp.text
        assert "interactive session" in resp.json()["detail"]
    # The wrong and the right current password look the same: no oracle.
    assert change_wrong.json() == change_right.json()

    await db.refresh(user)
    assert user.hashed_password == hash_before
    assert user.must_change_password is True
    assert await _live_refresh_tokens(db, user) == 1
    assert (await login(client, user=user, password=NEW_PASSWORD)).status_code == 401


async def test_a_session_on_a_temporary_password_still_completes_the_reset(db, client):
    user = await make_user(db, username="first-timer", must_change_password=True)
    token = (await login(client, user=user)).json()["access_token"]

    resp = await client.post(
        "/api/v1/auth/first-time-reset",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    assert resp.status_code == 204, resp.text
    await db.refresh(user)
    assert user.must_change_password is False
    assert verify_password(NEW_PASSWORD, user.hashed_password)
    assert (await login(client, user=user, password=NEW_PASSWORD)).status_code == 200


async def test_a_session_still_changes_its_password_and_signs_out(db, client):
    user = await make_user(db, username="regular")
    token = (await login(client, user=user)).json()["access_token"]
    changed = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204, changed.text

    fresh = (await login(client, user=user, password=NEW_PASSWORD)).json()["access_token"]
    out = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {fresh}"})
    assert out.status_code == 204, out.text
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh}"})
    ).status_code == 401


def test_an_unknown_credential_kind_is_refused():
    """A user object that did not come through the auth dependencies is not interactive."""
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.core.deps import (
        CREDENTIAL_KIND_JWT,
        _bind_credential_kind,
        require_interactive_session,
    )

    request = SimpleNamespace(scope={"path": "/api/v1/auth/change-password"})
    with pytest.raises(HTTPException) as refused:
        asyncio.run(require_interactive_session(request, SimpleNamespace()))
    assert refused.value.status_code == 403

    user = _bind_credential_kind(SimpleNamespace(), CREDENTIAL_KIND_JWT)
    assert asyncio.run(require_interactive_session(request, user)) is user
