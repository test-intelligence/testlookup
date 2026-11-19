"""Wrong password / wrong second factor on signed-in routes counts toward the account lock.

The rule: ``POST /auth/change-password``, ``POST /auth/mfa/disable`` and
``POST /auth/mfa/recovery-codes`` count a wrong password and a wrong second
factor with the same per-account lockout as sign-in, answer 429 with
``Retry-After`` once the account is locked, and reset the counter on success.
All three paths are also in the per-address rate-limit table.

Why: these routes answered 400 forever and counted nothing, so the holder of a
stolen access token could guess the account password without limit and then
guess 6-digit codes until MFA was switched off.
"""
# ruff: noqa: F811 - pytest fixtures imported from tests.test_mfa are requested by name
from __future__ import annotations

import pyotp
import pytest

from app.core.security import verify_password
from tests.test_mfa import (  # noqa: F401 - fixtures are used by name
    PASSWORD,
    _redis,
    _secret_key,
    _session_issuer_clock,
    client,
    db,
    enroll,
    login,
    make_user,
    session_factory,
    set_policy,
)

pytestmark = pytest.mark.regression

NEW_PASSWORD = "a-brand-new-passphrase-1"
THRESHOLD = 3


def _fresh_code(secret: str, user) -> str:
    return pyotp.TOTP(secret).at((user.mfa_last_used_step + 1) * 30)


async def _session(db, client, username: str) -> tuple:
    await set_policy(db, lockout_enabled=True, lockout_threshold=THRESHOLD)
    user = await make_user(db, username=username)
    token = (await login(client, user=user)).json()["access_token"]
    return user, {"Authorization": f"Bearer {token}"}


async def _mfa_session(db, client, username: str) -> tuple:
    await set_policy(db, lockout_enabled=True, lockout_threshold=THRESHOLD)
    user = await make_user(db, username=username)
    secret = await enroll(db, user)
    challenge = (await login(client, user=user)).json()["challenge_token"]
    await db.refresh(user)
    token = (
        await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge, "code": _fresh_code(secret, user)},
        )
    ).json()["access_token"]
    await db.refresh(user)
    return user, secret, {"Authorization": f"Bearer {token}"}


async def _assert_locks_after_threshold(db, client, user, headers, path: str, bad_body: dict):
    for _ in range(THRESHOLD - 1):
        resp = await client.post(path, headers=headers, json=bad_body)
        assert resp.status_code == 400, resp.text
    await db.refresh(user)
    assert user.failed_login_attempts == THRESHOLD - 1
    assert user.locked_until is None

    tripped = await client.post(path, headers=headers, json=bad_body)
    assert tripped.status_code == 429, tripped.text
    assert "Retry-After" in tripped.headers
    await db.refresh(user)
    assert user.locked_until is not None


# ── refusals ────────────────────────────────────────────────────────────────


async def test_wrong_current_password_locks_the_account(db, client):
    user, headers = await _session(db, client, "guess-pw")
    await _assert_locks_after_threshold(
        db, client, user, headers, "/api/v1/auth/change-password",
        {"current_password": "not-the-password", "new_password": NEW_PASSWORD},
    )

    # Once locked, even the correct current password is refused, and nothing changes.
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    await db.refresh(user)
    assert verify_password(PASSWORD, user.hashed_password)


@pytest.mark.parametrize("path", ["/api/v1/auth/mfa/disable", "/api/v1/auth/mfa/recovery-codes"])
async def test_wrong_second_factor_locks_the_account(db, client, path):
    user, secret, headers = await _mfa_session(db, client, "guess-code")
    await _assert_locks_after_threshold(
        db, client, user, headers, path, {"password": PASSWORD, "code": "000000"}
    )

    # Once locked, the correct password and a valid code are refused too.
    resp = await client.post(
        path, headers=headers, json={"password": PASSWORD, "code": _fresh_code(secret, user)}
    )
    assert resp.status_code == 429
    await db.refresh(user)
    assert user.mfa_enabled is True


@pytest.mark.parametrize("path", ["/api/v1/auth/mfa/disable", "/api/v1/auth/mfa/recovery-codes"])
async def test_wrong_password_on_the_mfa_routes_locks_the_account(db, client, path):
    user, secret, headers = await _mfa_session(db, client, "guess-mfa-pw")
    await _assert_locks_after_threshold(
        db, client, user, headers, path,
        {"password": "not-the-password", "code": _fresh_code(secret, user)},
    )
    await db.refresh(user)
    assert user.mfa_enabled is True


# ── the legitimate paths ────────────────────────────────────────────────────


async def test_a_correct_password_change_resets_the_counter(db, client):
    user, headers = await _session(db, client, "typo-then-right")
    wrong = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "typo", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Current password is incorrect"
    await db.refresh(user)
    assert user.failed_login_attempts == 1

    ok = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert ok.status_code == 204, ok.text
    await db.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert verify_password(NEW_PASSWORD, user.hashed_password)


async def test_a_correct_password_and_code_still_disable_mfa(db, client):
    user, secret, headers = await _mfa_session(db, client, "dis-ok")
    wrong = await client.post(
        "/api/v1/auth/mfa/disable", headers=headers, json={"password": PASSWORD, "code": "000000"}
    )
    assert wrong.status_code == 400
    await db.refresh(user)
    assert user.failed_login_attempts == 1

    ok = await client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": PASSWORD, "code": _fresh_code(secret, user)},
    )
    assert ok.status_code == 204, ok.text
    await db.refresh(user)
    assert user.mfa_enabled is False
    assert user.failed_login_attempts == 0


async def test_a_correct_password_and_code_still_reissue_recovery_codes(db, client):
    user, secret, headers = await _mfa_session(db, client, "codes-ok")
    wrong = await client.post(
        "/api/v1/auth/mfa/recovery-codes",
        headers=headers,
        json={"password": PASSWORD, "code": "000000"},
    )
    assert wrong.status_code == 400

    ok = await client.post(
        "/api/v1/auth/mfa/recovery-codes",
        headers=headers,
        json={"password": PASSWORD, "code": _fresh_code(secret, user)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["recovery_codes"]
    await db.refresh(user)
    assert user.failed_login_attempts == 0


async def test_lockout_switched_off_counts_nothing(db, client):
    await set_policy(db, lockout_enabled=False, lockout_threshold=THRESHOLD)
    user = await make_user(db, username="no-lockout")
    token = (await login(client, user=user)).json()["access_token"]
    for _ in range(THRESHOLD + 2):
        resp = await client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "typo", "new_password": NEW_PASSWORD},
        )
        assert resp.status_code == 400
    await db.refresh(user)
    assert user.locked_until is None


# ── per-address rate limit ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/change-password",
        "/api/v1/auth/mfa/disable",
        "/api/v1/auth/mfa/recovery-codes",
    ],
)
def test_the_routes_are_rate_limited_per_address(path):
    from app.main import _AUTH_LIMITERS, _AUTH_RATE_LIMITS, app

    assert path in _AUTH_RATE_LIMITS
    limit, message = _AUTH_RATE_LIMITS[path]
    assert limit.endswith("/minute") and int(limit.split("/")[0]) <= 10
    assert message
    assert path in _AUTH_LIMITERS
    assert any(getattr(r, "path", None) == path for r in app.routes), "the table must name a real route"
