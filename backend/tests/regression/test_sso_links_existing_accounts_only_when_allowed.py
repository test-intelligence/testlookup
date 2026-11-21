"""A first SSO sign-in takes over an existing local account only when an administrator allowed it.

The rule, for an identity-provider subject that has no federated link yet:

* an existing account is linked only while the SSO configuration's
  ``link_existing_accounts_by_email`` switch is on (off by default);
* an ADMIN account is never linked, switch or not;
* OIDC links only when the ID token says ``email_verified: true`` (a missing
  claim is not enough);
* SAML picks the existing account by the NameID. An e-mail attribute that names
  another account never selects it, and never creates a second account with the
  same e-mail either;
* a link that is made writes an ``ACCOUNT_LINKED`` identity event.

An unknown e-mail is still provisioned as a new user, and a subject that is
already linked still signs in.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.core.config import settings
from app.models.postgres import (
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    SSOConfiguration,
    SSOProviderType,
    User,
    UserRole,
)
from app.models.schemas import SSOConfigCreate, SSOConfigResponse, SSOConfigUpdate
from app.routers import sso as sso_router
from app.services import oidc_service, sso_service
from app.services.sso_service import jit_provision_or_link

pytestmark = pytest.mark.regression

ADMIN_EMAIL = "admin@corp.example"
MEMBER_EMAIL = "member@corp.example"


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value, all=lambda: [self._value] if self._value else [])


class _Db:
    """Answers the handful of SELECTs the sign-in makes from two in-memory lists."""

    def __init__(self, users=(), links=()):
        self.users = list(users)
        self.links = list(links)
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement):
        try:
            entity = statement.column_descriptions[0]["entity"]
        except (AttributeError, IndexError, KeyError):
            return _Result(None)
        compiled = statement.compile()
        where = str(compiled).split("WHERE", 1)[-1]
        params = compiled.params
        if entity is FederatedIdentity:
            wanted = next(v for k, v in params.items() if k.startswith("external_id"))
            return _Result(next((link for link in self.links if link.external_id == wanted), None))
        if entity is User:
            if "users.username" in where:
                wanted = next(v for k, v in params.items() if k.startswith("username"))
                return _Result(next((u for u in self.users if u.username == wanted), None))
            if "users.email" in where:
                wanted = next(v for k, v in params.items() if k.startswith(("lower", "email")))
                if "lower(users.email)" in where:
                    return _Result(next((u for u in self.users if u.email.lower() == wanted), None))
                return _Result(next((u for u in self.users if u.email == wanted), None))
            wanted = next(v for k, v in params.items() if k.startswith("id"))
            return _Result(next((u for u in self.users if u.id == wanted), None))
        return _Result(None)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, User):
            self.users.append(obj)
        if isinstance(obj, FederatedIdentity):
            self.links.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _obj):
        return None

    def of(self, kind):
        return [obj for obj in self.added if isinstance(obj, kind)]

    def events(self, event_type):
        return [e for e in self.of(IdentityEvent) if e.event_type == event_type]


def _user(email: str, role: UserRole = UserRole.QA_ENGINEER) -> User:
    return User(
        id=uuid.uuid4(), email=email, username=email.split("@")[0].lower(), role=role,
        is_active=True, must_change_password=False, hashed_password="not-a-real-hash",
    )


def _config(*, link: bool = False, oidc: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        provider_type=SSOProviderType.OIDC if oidc else SSOProviderType.SAML,
        group_attribute="groups",
        oidc_groups_claim=None,
        role_mapping={"leads": "QA_LEAD"},
        default_role=UserRole.VIEWER,
        link_existing_accounts_by_email=link,
        idp_certificate="-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----",
        oidc_issuer="https://idp.example.test",
        oidc_client_id="client-1",
    )


# ── The switch exists and is off by default ──────────────────────────────────


def test_the_switch_is_a_required_column_that_defaults_to_off():
    column = SSOConfiguration.__table__.c.link_existing_accounts_by_email
    assert column.nullable is False
    assert column.default.arg is False
    assert "false" in str(column.server_default.arg).lower()


def test_the_switch_is_in_the_create_update_and_read_schemas():
    created = SSOConfigCreate(
        display_name="Corp", idp_entity_id="e", idp_sso_url="https://idp.example.test/sso",
        idp_certificate="c", sp_entity_id="s", sp_acs_url="https://app.example.test/api/v1/sso/acs",
    )
    assert created.link_existing_accounts_by_email is False
    assert SSOConfigUpdate(link_existing_accounts_by_email=True).model_dump(exclude_unset=True) == {
        "link_existing_accounts_by_email": True
    }
    assert SSOConfigResponse.model_fields["link_existing_accounts_by_email"].default is False


# ── Refusals ─────────────────────────────────────────────────────────────────


async def test_an_e_mail_attribute_never_selects_an_admin_account():
    """The finding: NameID x@…, attribute mail = the administrator's address."""
    admin = _user(ADMIN_EMAIL, UserRole.ADMIN)
    db = _Db(users=[admin])
    for link in (False, True):
        with pytest.raises(ValueError):
            await jit_provision_or_link(
                db, _config(link=link), name_id="x@corp.example", attributes={"mail": [ADMIN_EMAIL]},
            )
    assert db.of(FederatedIdentity) == []
    assert db.users == [admin] and admin.role == UserRole.ADMIN


async def test_an_admin_account_is_never_linked_even_with_the_switch_on():
    admin = _user(ADMIN_EMAIL, UserRole.ADMIN)
    db = _Db(users=[admin])
    with pytest.raises(ValueError, match="[Aa]dministrator accounts are never linked"):
        await jit_provision_or_link(db, _config(link=True), name_id=ADMIN_EMAIL, attributes={})
    with pytest.raises(ValueError, match="[Aa]dministrator accounts are never linked"):
        await jit_provision_or_link(
            db, _config(link=True, oidc=True), name_id="sub-1", attributes={"email": [ADMIN_EMAIL]},
            email_verified=True,
        )
    assert db.of(FederatedIdentity) == [] and db.events(IdentityEventType.ACCOUNT_LINKED) == []


async def test_an_existing_account_is_not_linked_while_the_switch_is_off():
    member = _user(MEMBER_EMAIL)
    db = _Db(users=[member])
    with pytest.raises(ValueError, match="ask an administrator to enable linking"):
        await jit_provision_or_link(db, _config(link=False), name_id=MEMBER_EMAIL, attributes={})
    with pytest.raises(ValueError, match="ask an administrator to enable linking"):
        await jit_provision_or_link(
            db, _config(link=False, oidc=True), name_id="sub-1", attributes={"email": [MEMBER_EMAIL]},
            email_verified=True,
        )
    assert db.of(FederatedIdentity) == [] and db.users == [member]


async def test_a_saml_e_mail_attribute_that_differs_from_the_name_id_does_not_link_or_duplicate():
    member = _user(MEMBER_EMAIL)
    db = _Db(users=[member])
    with pytest.raises(ValueError, match="NameID"):
        await jit_provision_or_link(
            db, _config(link=True), name_id="x@corp.example", attributes={"email": [MEMBER_EMAIL]},
        )
    assert db.of(FederatedIdentity) == []
    assert db.users == [member], "a second account with the same e-mail was created"


async def test_an_address_that_differs_only_in_case_is_the_same_account():
    admin = _user("Admin@Corp.example", UserRole.ADMIN)
    db = _Db(users=[admin])
    with pytest.raises(ValueError):
        await jit_provision_or_link(db, _config(link=True), name_id=ADMIN_EMAIL, attributes={})
    assert db.users == [admin] and db.of(FederatedIdentity) == []


@pytest.mark.parametrize("verified", [None, False])
async def test_oidc_does_not_link_without_a_verified_e_mail(verified):
    member = _user(MEMBER_EMAIL)
    db = _Db(users=[member])
    with pytest.raises(ValueError, match="verified"):
        await jit_provision_or_link(
            db, _config(link=True, oidc=True), name_id="sub-1", attributes={"email": [MEMBER_EMAIL]},
            email_verified=verified,
        )
    assert db.of(FederatedIdentity) == []


# ── The legitimate paths ─────────────────────────────────────────────────────


async def test_saml_links_by_name_id_when_the_switch_is_on_and_records_it():
    member = _user(MEMBER_EMAIL, UserRole.QA_ENGINEER)
    db = _Db(users=[member])
    config = _config(link=True)
    user, is_new = await jit_provision_or_link(
        db, config, name_id=MEMBER_EMAIL, attributes={"groups": ["leads"]}, ip_address="203.0.113.9",
    )
    assert user is member and is_new is False
    assert member.role == UserRole.QA_ENGINEER, "the link itself must not change the role"
    (link,) = db.of(FederatedIdentity)
    assert (link.user_id, link.sso_config_id, link.external_id) == (member.id, config.id, MEMBER_EMAIL)
    (event,) = db.events(IdentityEventType.ACCOUNT_LINKED)
    assert (event.user_id, event.sso_config_id, event.ip_address) == (member.id, config.id, "203.0.113.9")
    assert event.detail["external_id"] == MEMBER_EMAIL and event.detail["email"] == MEMBER_EMAIL
    assert db.events(IdentityEventType.JIT_PROVISIONED) == []


async def test_oidc_links_a_verified_e_mail_when_the_switch_is_on():
    member = _user(MEMBER_EMAIL)
    db = _Db(users=[member])
    user, is_new = await jit_provision_or_link(
        db, _config(link=True, oidc=True), name_id="sub-1", attributes={"email": [MEMBER_EMAIL]},
        email_verified=True,
    )
    assert user is member and is_new is False
    (link,) = db.of(FederatedIdentity)
    assert link.external_id == "sub-1"
    (event,) = db.events(IdentityEventType.ACCOUNT_LINKED)
    assert event.detail["external_id"] == "sub-1"


@pytest.mark.parametrize("link", [False, True])
async def test_an_unknown_e_mail_is_still_provisioned_with_the_mapped_role(link, monkeypatch):
    monkeypatch.setattr(sso_service, "get_password_hash", lambda _raw: "not-a-real-hash")
    db = _Db(users=[_user(MEMBER_EMAIL)])
    user, is_new = await jit_provision_or_link(
        db, _config(link=link), name_id="new@corp.example", attributes={"groups": ["leads"]},
    )
    assert is_new is True and user.email == "new@corp.example" and user.role == UserRole.QA_LEAD
    assert len(db.of(FederatedIdentity)) == 1
    assert len(db.events(IdentityEventType.JIT_PROVISIONED)) == 1
    assert db.events(IdentityEventType.ACCOUNT_LINKED) == []


async def test_a_subject_that_is_already_linked_still_signs_in():
    admin = _user(ADMIN_EMAIL, UserRole.ADMIN)
    config = _config(link=False)
    config.role_mapping = {"admins": "ADMIN"}
    link = FederatedIdentity(user_id=admin.id, sso_config_id=config.id, external_id=ADMIN_EMAIL)
    db = _Db(users=[admin], links=[link])
    user, is_new = await jit_provision_or_link(db, config, name_id=ADMIN_EMAIL, attributes={"groups": ["admins"]})
    assert user is admin and is_new is False
    assert db.of(FederatedIdentity) == [] and db.events(IdentityEventType.ACCOUNT_LINKED) == []


# ── The refusal reaches the browser as a sign-in error, not a 500 ────────────


def _request(*, method: str = "GET", accept: str = "text/html", cookies: dict | None = None, form: dict | None = None):
    headers = [(b"accept", accept.encode()), (b"host", b"app.example.test")]
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    body = b""
    if form is not None:
        body = "&".join(f"{k}={quote(v)}" for k, v in form.items()).encode()
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))
        headers.append((b"content-length", str(len(body)).encode()))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": method, "scheme": "https", "path": "/", "query_string": b"",
        "headers": headers, "client": ("203.0.113.9", 4321), "server": ("app.example.test", 443),
    }
    return Request(scope, receive)


@pytest.fixture
def sso_on(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")


async def test_the_saml_acs_turns_a_refused_link_into_a_sign_in_error(sso_on, monkeypatch):
    admin = _user(ADMIN_EMAIL, UserRole.ADMIN)
    db = _Db(users=[admin])
    config = _config(link=True)
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    monkeypatch.setattr(
        sso_router, "parse_saml_response",
        lambda *_a, **_k: {"name_id": "x@corp.example", "attributes": {"mail": [ADMIN_EMAIL]}},
    )
    monkeypatch.setattr(sso_router, "validate_saml_issuer", lambda *_a: None)
    monkeypatch.setattr(sso_router, "enforce_saml_security", AsyncMock())
    issue = AsyncMock()
    monkeypatch.setattr(sso_router, "issue_access_jwt", issue)

    with pytest.raises(HTTPException) as browser:
        await sso_router.saml_acs(
            _request(method="POST", form={"SAMLResponse": "QUJD"}), response=Response(), db=db,
        )
    assert browser.value.status_code == 303
    assert browser.value.headers["Location"] == "https://app.example.test/login?sso_error=1"

    with pytest.raises(HTTPException) as api:
        await sso_router.saml_acs(
            _request(method="POST", accept="application/json", form={"SAMLResponse": "QUJD"}),
            response=Response(), db=db,
        )
    assert api.value.status_code == 400
    issue.assert_not_awaited()
    assert db.of(FederatedIdentity) == []
    # A refusal after a verified signature is audited.
    assert len(db.events(IdentityEventType.SSO_LOGIN_FAILED)) == 2


async def test_the_oidc_callback_turns_a_refused_link_into_a_sign_in_error(sso_on, monkeypatch):
    member = _user(MEMBER_EMAIL)
    db = _Db(users=[member])
    config = _config(link=True, oidc=True)
    cookie = "browser-value-for-this-test"
    parked = {
        "config_id": str(config.id), "nonce": "n", "code_verifier": "v", "redirect_uri": "r", "next": None,
        "browser_hash": sso_service.browser_binding_hash(cookie),
    }
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    monkeypatch.setattr(oidc_service, "consume_state", AsyncMock(return_value=parked))
    monkeypatch.setattr(sso_router, "read_secret", AsyncMock(return_value="not-a-real-secret"))
    monkeypatch.setattr(oidc_service, "discover", AsyncMock(return_value={}))
    monkeypatch.setattr(oidc_service, "exchange_code", AsyncMock(return_value={"id_token": "t"}))
    # No email_verified claim: the IdP did not say the address is verified.
    monkeypatch.setattr(
        oidc_service, "validate_with_jwks", AsyncMock(return_value={"sub": "sub-1", "email": MEMBER_EMAIL}),
    )
    issue = AsyncMock()
    monkeypatch.setattr(sso_router, "issue_access_jwt", issue)

    response = await sso_router.oidc_callback(
        _request(cookies={sso_router.OIDC_BINDING_COOKIE: cookie}), code="c", state="s", error=None, db=db,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "https://app.example.test/login?sso_error=1"
    issue.assert_not_awaited()
    assert db.of(FederatedIdentity) == []
    assert len(db.events(IdentityEventType.SSO_LOGIN_FAILED)) == 1
