"""A locked account answers every sign-in attempt the same way, whatever the password.

The rule: while an account is locked, ``POST /auth/login`` answers 429 with
``Retry-After`` for a wrong password and for the correct one alike. It used to
answer 401 for a wrong password and 429 for the right one, so the lock told a
guesser which guess was correct and they only had to wait the lock out.

Also pinned: attempts during the lock are not counted and do not extend it,
and once the lock has expired sign-in works as before (200 for the correct
password, a counted 401 for a wrong one).
"""
# ruff: noqa: F811 - pytest fixtures imported from tests.test_mfa are requested by name
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    set_policy,
)

pytestmark = pytest.mark.regression


async def _locked_user(db, client, username: str):
    await set_policy(db, lockout_enabled=True, lockout_threshold=3)
    user = await make_user(db, username=username)
    for _ in range(3):
        assert (await login(client, user=user, password="wrong")).status_code == 401
    await db.refresh(user)
    assert user.locked_until is not None
    return user


async def test_wrong_and_correct_password_look_the_same_while_locked(db, client):
    user = await _locked_user(db, client, "samelock")
    first_until = user.locked_until

    wrong = await login(client, user=user, password="still-wrong")
    right = await login(client, user=user)

    assert wrong.status_code == right.status_code == 429
    assert "Retry-After" in wrong.headers and "Retry-After" in right.headers
    assert wrong.json() == right.json()
    assert "access_token" not in right.json()
    assert "challenge_token" not in right.json()

    # Attempts during the lock are not counted and do not extend it.
    await db.refresh(user)
    assert user.locked_until == first_until
    assert user.failed_login_attempts == 0


async def test_sign_in_works_again_after_the_lock_expires(db, client):
    user = await _locked_user(db, client, "afterlock")
    user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()

    wrong = await login(client, user=user, password="still-wrong")
    assert wrong.status_code == 401
    await db.refresh(user)
    assert user.failed_login_attempts == 1  # counted again

    right = await login(client, user=user)
    assert right.status_code == 200
    assert right.json()["access_token"]


async def test_an_unknown_username_still_answers_401(db, client):
    resp = await login(client, username="nobody-here", password="x")
    assert resp.status_code == 401
