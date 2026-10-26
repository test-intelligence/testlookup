"""
API integration tests for /api/v1/auth/* — register, login, me, refresh,
logout, change-password, first-time-reset.

These tests run real HTTP requests through the FastAPI app stack and
assert on status codes, response shapes, and that the dependency wiring
calls the right helpers (revocation, refresh-token family revoke).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Hard skip the whole file when integration deps are missing (PyJWT,
# asyncpg). Same pattern used by other auth-adjacent tests in this repo.
pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402
from tests.integration.conftest import fake_execute_result, make_user  # noqa: E402

pytestmark = pytest.mark.asyncio


# ── /me ─────────────────────────────────────────────────────────────────────


async def test_me_returns_authenticated_user(client, auth_as):
    user = auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == user.username
    assert body["role"] in {"QA_ENGINEER", UserRole.QA_ENGINEER.value}


async def test_me_rejects_inactive_user(client, auth_as):
    auth_as(is_active=False)
    resp = await client.get("/api/v1/auth/me")
    # get_current_active_user raises 403 for is_active=False
    assert resp.status_code == 403


# ── /register ───────────────────────────────────────────────────────────────


@pytest.fixture
def registration_open(monkeypatch):
    """Self-registration is off by default; these tests open it explicitly."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "REGISTRATION_ENABLED", True)


async def test_register_is_refused_while_registration_is_off(client, override_db, fake_db):
    """The default: an open sign-up let anyone who could reach the server make an
    account (as QA_ENGINEER, which can create API keys). Nothing is read or written."""
    payload = {"email": "newuser@example.com", "username": "newuser", "full_name": "N", "password": "ValidPass123!"}
    resp = await client.post("/api/v1/auth/register", json=payload)

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "registration_disabled"
    assert fake_db.committed == 0
    assert fake_db.added == []


async def test_auth_options_tell_the_sign_in_page_whether_to_offer_register(client, monkeypatch):
    from app.core.config import settings

    from app.services.notification import email_service

    async def _smtp_off():
        return {"enabled": False}

    monkeypatch.setattr(email_service, "get_smtp_config", _smtp_off)
    resp = await client.get("/api/v1/auth/options")  # public: no token
    assert resp.status_code == 200
    # W17b ADM-02: password_reset_email says whether the login page offers the email form.
    assert resp.json() == {"registration_enabled": False, "password_reset_email": False}

    monkeypatch.setattr(settings, "REGISTRATION_ENABLED", True)
    assert (await client.get("/api/v1/auth/options")).json() == {
        "registration_enabled": True, "password_reset_email": False,
    }


async def test_register_creates_a_viewer_with_must_change_password(
    client, override_db, fake_db, registration_open
):
    """
    When the deployment allows sign-up, a new account is a VIEWER -- what the form
    promises -- with must_change_password=True. It was QA_ENGINEER.
    """
    # First db.execute: lookup of existing user (None → not registered)
    fake_db.set_execute_results([
        fake_execute_result(scalar=None),
    ])

    payload = {
        # ``.local`` is reserved under RFC 2606; pydantic's email-validator
        # rejects it at serialization, so use example.com (also reserved for
        # test use but explicitly permitted by the validator).
        "email": "newuser@example.com",
        "username": "newuser",
        "full_name": "New User",
        "password": "ValidPass123!",
    }
    with patch("app.routers.auth.get_password_hash", return_value="$2b$hashed"):
        resp = await client.post("/api/v1/auth/register", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "newuser@example.com"
    assert body["role"] in {"VIEWER", UserRole.VIEWER.value}
    assert body["must_change_password"] is True
    assert fake_db.committed >= 1  # the new User was committed


async def test_register_rejects_duplicate_email(client, override_db, fake_db, registration_open):
    """If a user already exists with the same email/username, return 409."""
    existing = make_user()
    fake_db.set_execute_results([fake_execute_result(scalar=existing)])

    payload = {
        "email": existing.email,
        "username": "different_username",
        "full_name": "Dup",
        "password": "ValidPass123!",
    }
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409
    assert "already registered" in resp.json().get("detail", "").lower()


async def test_register_rejects_short_password(client, override_db):
    payload = {
        "email": "x@example.com",
        "username": "shortpw",
        "full_name": "X",
        "password": "abc",  # < min_length=8
    }
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 422  # Pydantic validation


# ── /logout (revocation regression) ─────────────────────────────────────────


async def test_logout_revokes_jti_and_refresh_family(client, auth_as, override_db):
    """
    Regression for the 'logout was a no-op' bug: /logout must call both
    revoke_jti (per-token denylist) and _revoke_refresh_family (Postgres).
    """
    auth_as()

    revoke_jti = AsyncMock()
    revoke_family = AsyncMock()

    with patch("app.routers.auth.revoke_jti", revoke_jti), \
         patch("app.routers.auth._revoke_refresh_family", revoke_family), \
         patch(
             "app.routers.auth.decode_token",
             return_value={"jti": "test_jti_abc", "exp": 9_999_999_999},
         ):
        resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer test-token"},
        )

    assert resp.status_code == 204
    revoke_jti.assert_awaited_once()
    revoke_family.assert_awaited_once()
    # The denylist entry should be keyed on the jti we put in the decoded token.
    args, _kwargs = revoke_jti.call_args
    assert args[0] == "test_jti_abc"


# ── /change-password (revocation regression) ────────────────────────────────


async def test_change_password_revokes_all_user_tokens(client, auth_as):
    """
    Regression for the 'password change leaves tokens valid' bug:
    /change-password must call revoke_all_user_tokens AND _revoke_refresh_family.
    """
    user = auth_as()

    revoke_all = AsyncMock()
    revoke_family = AsyncMock()

    async def _fake_db():
        from tests.integration.conftest import FakeAsyncSession
        yield FakeAsyncSession()

    from app.core.deps import get_db
    from app.main import app as fastapi_app
    fastapi_app.dependency_overrides[get_db] = _fake_db

    try:
        with patch("app.routers.auth.revoke_all_user_tokens", revoke_all), \
             patch("app.routers.auth._revoke_refresh_family", revoke_family), \
             patch("app.routers.auth.verify_password", return_value=True), \
             patch("app.routers.auth.get_password_hash", return_value="$2b$new"):
            resp = await client.post(
                "/api/v1/auth/change-password",
                json={"current_password": "OldPass!", "new_password": "NewPass!"},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 204
    assert revoke_all.await_count == 1
    assert revoke_all.await_args.args[0] == user.id
    revoke_family.assert_awaited_once()


async def test_change_password_rejects_wrong_current(client, auth_as, override_db):
    auth_as()
    with patch("app.routers.auth.verify_password", return_value=False):
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong", "new_password": "NewPass!"},
        )
    assert resp.status_code == 400
    assert "current password" in resp.json()["detail"].lower()


# ── /first-time-reset ──────────────────────────────────────────────────────


async def test_first_time_reset_only_when_must_change_password(client, auth_as, override_db):
    """A user without must_change_password must get 403 from /first-time-reset."""
    auth_as()  # default must_change_password=False
    resp = await client.post(
        "/api/v1/auth/first-time-reset",
        json={"new_password": "Brand!New1", "confirm_password": "Brand!New1"},
    )
    assert resp.status_code == 403


async def test_first_time_reset_revokes_bootstrap_token(client, override_db):
    """The bootstrap JWT used to call first-time-reset must be revoked after."""
    from app.core.deps import get_current_active_user
    from app.main import app as fastapi_app

    user = make_user(must_change_password=True)

    async def _current():
        return user

    fastapi_app.dependency_overrides[get_current_active_user] = _current
    revoke_all = AsyncMock()
    revoke_family = AsyncMock()
    try:
        with patch("app.routers.auth.revoke_all_user_tokens", revoke_all), \
             patch("app.routers.auth._revoke_refresh_family", revoke_family), \
             patch("app.routers.auth.get_password_hash", return_value="$2b$new"):
            resp = await client.post(
                "/api/v1/auth/first-time-reset",
                json={"new_password": "Brand!New1", "confirm_password": "Brand!New1"},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_current_active_user, None)

    assert resp.status_code == 204
    assert revoke_all.await_count == 1
    assert revoke_all.await_args.args[0] == user.id
    revoke_family.assert_awaited_once()
    # must_change_password should have been cleared on the user
    assert user.must_change_password is False
