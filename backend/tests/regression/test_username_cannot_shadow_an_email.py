"""A username can never make another user's sign-in by e-mail fail.

The rules:

* A NEW username may hold only letters, digits, ``.``, ``_`` and ``-``. So it
  cannot be an e-mail address: register, invitation accept, admin create and
  admin rename answer 422 for a username containing ``@``.
* Sign-in looks the typed name up deterministically: an e-mail match wins over
  a username match. An existing pair "user A's e-mail == user B's username"
  signs A in (or answers 401 for a wrong password) and never a 500.
* Existing usernames that contain ``@`` (SCIM often provisions the e-mail as
  the username) keep signing in.
* E-mail uniqueness is compared without regard to case on register, admin
  create and admin update, and a request that clashes with two different
  accounts answers 409, not 500.

Why: sign-in matched "username == X or e-mail == X" and expected one row. An
invitee who chose the administrator's e-mail address as a username made the
administrator's sign-in by e-mail answer 500.
"""
# ruff: noqa: F811 - pytest fixtures imported from tests.test_mfa are requested by name
from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.security import decode_token
from app.models.postgres import User, UserRole
from app.models.schemas import (
    AcceptInvitationRequest,
    AdminCreateUserRequest,
    UpdateUserProfileRequest,
    UserCreate,
)
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

VICTIM_EMAIL = "victim@example.com"
GOOD_PASSWORD = "a-long-enough-passphrase"


async def _usernames(db) -> set[str]:
    return set((await db.execute(select(User.username))).scalars().all())


async def _admin_headers(db, client) -> dict:
    admin = await make_user(db, username="the-admin", role=UserRole.ADMIN)
    token = (await login(client, user=admin)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def open_registration(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REGISTRATION_ENABLED", True)


# ── refusals: a new username cannot be an e-mail address ────────────────────


@pytest.mark.parametrize("bad", [VICTIM_EMAIL, "a@b", "two words", "semi;colon", "slash/name"])
def test_the_four_write_schemas_refuse_the_username(bad):
    with pytest.raises(ValidationError) as refused:
        UserCreate(email="new@example.com", username=bad, password=GOOD_PASSWORD)
    assert "letters, digits" in str(refused.value)
    with pytest.raises(ValidationError):
        AcceptInvitationRequest(token="t", username=bad, password=GOOD_PASSWORD)
    with pytest.raises(ValidationError):
        AdminCreateUserRequest(email="new@example.com", username=bad)
    with pytest.raises(ValidationError):
        UpdateUserProfileRequest(username=bad)


@pytest.mark.parametrize("good", ["alice", "qa_lead", "j.smith-2", "Zoë99"])
def test_ordinary_usernames_are_accepted(good):
    assert UserCreate(email="new@example.com", username=good, password=GOOD_PASSWORD).username == good
    assert AcceptInvitationRequest(token="t", username=good, password=GOOD_PASSWORD).username == good
    assert AdminCreateUserRequest(email="new@example.com", username=good).username == good
    assert UpdateUserProfileRequest(username=good).username == good
    assert UpdateUserProfileRequest().username is None  # an update that does not rename


async def test_no_route_writes_a_user_whose_username_is_someones_email(db, client, open_registration):
    await make_user(db, username="victim", email=VICTIM_EMAIL)
    admin = await _admin_headers(db, client)
    target = await make_user(db, username="renamed-later")
    before = await _usernames(db)

    accept = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": "any-token", "username": VICTIM_EMAIL, "password": GOOD_PASSWORD},
    )
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": "squatter@example.com", "username": VICTIM_EMAIL, "password": GOOD_PASSWORD},
    )
    create = await client.post(
        "/api/v1/users", headers=admin,
        json={"email": "squatter2@example.com", "username": VICTIM_EMAIL},
    )
    rename = await client.patch(
        f"/api/v1/users/{target.id}", headers=admin, json={"username": VICTIM_EMAIL}
    )

    for resp in (accept, register, create, rename):
        assert resp.status_code == 422, resp.text
        assert "letters, digits" in resp.text
    assert await _usernames(db) == before


# ── sign-in is deterministic for rows that already clash ────────────────────


async def test_an_existing_clash_signs_the_email_owner_in_and_never_500s(db, client):
    victim = await make_user(db, username="victim", email=VICTIM_EMAIL)
    # The state a deployment may already be in: written before the rule existed.
    squatter = await make_user(
        db, username=VICTIM_EMAIL, email="squatter@example.com", password="squatter-password-1"
    )

    by_email = await login(client, username=VICTIM_EMAIL)
    assert by_email.status_code == 200, by_email.text
    assert decode_token(by_email.json()["access_token"])["sub"] == str(victim.id)

    wrong = await login(client, username=VICTIM_EMAIL, password="not-the-password")
    assert wrong.status_code == 401

    # The e-mail owner wins: the other account's password does not open it...
    assert (
        await login(client, username=VICTIM_EMAIL, password="squatter-password-1")
    ).status_code == 401
    # ...and that account still signs in with its own e-mail address.
    own = await login(client, username="squatter@example.com", password="squatter-password-1")
    assert own.status_code == 200
    assert decode_token(own.json()["access_token"])["sub"] == str(squatter.id)
    # The victim's username still works too.
    assert (await login(client, username="victim")).status_code == 200


async def test_an_existing_username_with_an_at_sign_still_signs_in(db, client):
    scim_style = await make_user(db, username="scim.user@example.com", email="scim.user@example.com")
    odd = await make_user(db, username="legacy@name", email="legacy@example.com")

    first = await login(client, username="scim.user@example.com")
    second = await login(client, username="legacy@name")

    assert first.status_code == 200 and second.status_code == 200
    assert decode_token(first.json()["access_token"])["sub"] == str(scim_style.id)
    assert decode_token(second.json()["access_token"])["sub"] == str(odd.id)


async def test_an_ordinary_username_registers_and_signs_in(db, client, open_registration):
    created = await client.post(
        "/api/v1/auth/register",
        json={"email": "newbie@example.com", "username": "new.bie_1", "password": GOOD_PASSWORD},
    )
    assert created.status_code == 201, created.text
    assert (await login(client, username="new.bie_1", password=GOOD_PASSWORD)).status_code == 200
    assert (await login(client, username="newbie@example.com", password=GOOD_PASSWORD)).status_code == 200


# ── e-mail uniqueness ignores case ──────────────────────────────────────────


async def test_register_refuses_a_case_variant_of_an_existing_email(db, client, open_registration):
    await make_user(db, username="victim", email=VICTIM_EMAIL)
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "Victim@Example.com", "username": "someone-else", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 409, resp.text
    assert "someone-else" not in await _usernames(db)


async def test_register_clashing_with_two_accounts_is_a_409_not_a_500(db, client, open_registration):
    await make_user(db, username="victim", email=VICTIM_EMAIL)
    await make_user(db, username="taken-name", email="other@example.com")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": VICTIM_EMAIL, "username": "taken-name", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 409, resp.text


async def test_admin_create_refuses_a_case_variant_and_a_double_clash(db, client):
    await make_user(db, username="victim", email=VICTIM_EMAIL)
    await make_user(db, username="taken-name", email="other@example.com")
    admin = await _admin_headers(db, client)

    variant = await client.post(
        "/api/v1/users", headers=admin, json={"email": "VICTIM@example.com", "username": "brand-new"}
    )
    double = await client.post(
        "/api/v1/users", headers=admin, json={"email": VICTIM_EMAIL, "username": "taken-name"}
    )

    assert variant.status_code == 409, variant.text
    assert double.status_code == 409, double.text
    assert "brand-new" not in await _usernames(db)


async def test_admin_update_refuses_a_case_variant_of_another_users_email(db, client):
    await make_user(db, username="victim", email=VICTIM_EMAIL)
    target = await make_user(db, username="target", email="target@example.com")
    admin = await _admin_headers(db, client)

    clash = await client.patch(
        f"/api/v1/users/{target.id}", headers=admin, json={"email": "Victim@Example.com"}
    )
    assert clash.status_code == 409, clash.text
    await db.refresh(target)
    assert target.email == "target@example.com"

    # A user's own e-mail may change case, and a free address is accepted.
    own_case = await client.patch(
        f"/api/v1/users/{target.id}", headers=admin, json={"email": "Target@example.com"}
    )
    assert own_case.status_code == 200, own_case.text
    free = await client.patch(
        f"/api/v1/users/{target.id}", headers=admin, json={"email": "free@example.com"}
    )
    assert free.status_code == 200, free.text
