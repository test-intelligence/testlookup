"""W20 ADM-08b: an OIDC sign-in end to end, against a real database and the fake OIDC IdP.

The real route handlers run (create → activate → ``/sso/oidc/login`` → the IdP's ``/authorize`` → the
``/sso/oidc/callback`` → ``/sso/exchange``); the IdP is ``tests/fakes/fake_integrations.py`` behind an ASGI
transport, so the code exchange, PKCE, the JWKS and the RS256 signature are all real.

* the client secret is stored in ``secret_refs`` and never returned (only ``oidc_client_secret_set``);
* the groups claim maps ``qa-leads`` → QA_LEAD; a new user is JIT-provisioned with a federated identity;
* the state works once (a replayed callback is refused), an ID token for another audience is refused, an
  unsigned one is refused, an IdP error is refused — each with a SSO_LOGIN_FAILED identity event;
* an unverified email never links to an existing local account;
* a SAML config still needs its SAML fields; an OIDC one needs issuer + client id + secret (422).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ISSUER = "http://idp.fake.test/oidc"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Redis:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value.encode() if isinstance(value, str) else value

    async def getdel(self, key):
        return self.data.pop(key, None)


@pytest.fixture
async def world(monkeypatch):
    from app.core.config import settings
    from app.models.postgres import FederatedIdentity, IdentityEvent, SecretRef, SSOConfiguration, User, UserRole
    from app.services import oidc_service
    from tests.fakes.fake_integrations import FakeState, create_app

    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_IDP_ENDPOINTS", True)
    redis = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    state = FakeState(oidc_issuer=ISSUER)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    state.oidc_users = {
        "lead": {"email": f"lead-{marker}@example.test", "name": "Lead", "groups": ["qa-leads"], "email_verified": True},
        "taken": {"email": f"taken-{marker}@example.test", "name": "Taken", "groups": [], "email_verified": False},
    }
    idp = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)), base_url="http://idp.fake.test")
    monkeypatch.setattr(oidc_service, "_http_client", lambda: idp)
    oidc_service.clear_caches()

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = User(id=uuid.uuid4(), email=f"a-{marker}@testlookup.dev", username=f"a-{marker}", full_name="A",
                 hashed_password="x", role=UserRole.ADMIN, is_active=True)
    existing = User(id=uuid.uuid4(), email=f"taken-{marker}@example.test", username=f"t-{marker}", full_name="T",
                    hashed_password="x", role=UserRole.VIEWER, is_active=True)
    async with factory() as db:
        previously_active = (await db.execute(
            select(SSOConfiguration.id).where(SSOConfiguration.is_active.is_(True)))).scalars().all()
        db.add_all([admin, existing])
        await db.commit()
    created: list[uuid.UUID] = []
    try:
        yield SimpleNamespace(factory=factory, admin=admin, existing=existing, idp=idp, state=state,
                              marker=marker, created=created, redis=redis)
    finally:
        async with factory() as db:
            emails = [u["email"] for u in state.oidc_users.values()]
            users = (await db.execute(select(User.id).where(User.email.in_(emails)))).scalars().all()
            await db.execute(delete(IdentityEvent).where(IdentityEvent.sso_config_id.in_(created)))
            await db.execute(delete(FederatedIdentity).where(FederatedIdentity.sso_config_id.in_(created)))
            await db.execute(delete(SSOConfiguration).where(SSOConfiguration.id.in_(created)))
            await db.execute(delete(SecretRef).where(SecretRef.scope == "sso_oidc",
                                                     SecretRef.key_name.in_([str(c) for c in created])))
            from app.models.postgres import RefreshTokenRecord as RefreshToken

            await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_([*users, admin.id])))
            await db.execute(delete(User).where(User.id.in_([*users, admin.id, existing.id])))
            if previously_active:
                from sqlalchemy import update

                await db.execute(update(SSOConfiguration).where(SSOConfiguration.id.in_(previously_active))
                                 .values(is_active=True))
            await db.commit()
        await idp.aclose()
        await engine.dispose()


def _req():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


async def _create_active_oidc(w, **over):
    from app.models.schemas import SSOConfigCreate, SSOConfigUpdate
    from app.routers import sso

    payload = SSOConfigCreate(
        display_name="Keycloak", provider_type="OIDC", oidc_issuer=ISSUER + "/", oidc_client_id="testlookup",
        oidc_client_secret="fake-oidc-secret", role_mapping={"qa-leads": "QA_LEAD"}, default_role="VIEWER", **over,
    )
    async with w.factory() as db:
        created = await sso.create_sso_config(payload, _req(), current_user=w.admin, db=db)
        w.created.append(created.id)
        active = await sso.update_sso_config(created.id, SSOConfigUpdate(is_active=True), _req(),
                                             current_user=w.admin, db=db)
    return active


async def _login_and_authorize(w, username: str) -> tuple[str, str]:
    """(code, state) the IdP sent back for ``username``."""
    from app.routers import sso

    async with w.factory() as db:
        start = await sso.oidc_login(next="/runs", db=db)
    assert start.status_code == 302
    idp_url = start.headers["location"]
    assert idp_url.startswith(ISSUER + "/authorize?")
    answer = await w.idp.get(idp_url + f"&login_hint={username}")
    assert answer.status_code == 302, answer.text
    back = urlsplit(answer.headers["location"])
    assert back.path == "/api/v1/sso/oidc/callback"
    q = {k: v[0] for k, v in parse_qs(back.query).items()}
    return q["code"], q["state"]


async def _callback(w, **params):
    from app.routers import sso

    async with w.factory() as db:
        return await sso.oidc_callback(_req(), db=db, **params)


async def _failures(w, config_id) -> list[str]:
    from app.models.postgres import IdentityEvent

    async with w.factory() as db:
        rows = (await db.execute(select(IdentityEvent).where(
            IdentityEvent.sso_config_id == config_id, IdentityEvent.success.is_(False)))).scalars().all()
    return [str((r.detail or {}).get("error")) for r in rows]


async def test_sign_in_maps_the_group_and_hands_off_once(world):
    from app.models.postgres import FederatedIdentity, SecretRef, User
    from app.routers import sso

    config = await _create_active_oidc(world)
    assert config.oidc_client_secret_set is True
    assert config.oidc_issuer == ISSUER  # trailing slash trimmed
    assert "fake-oidc-secret" not in config.model_dump_json()
    async with world.factory() as db:
        ref = (await db.execute(select(SecretRef).where(SecretRef.scope == "sso_oidc",
                                                        SecretRef.key_name == str(config.id)))).scalar_one()
        assert ref.encrypted_value and "fake-oidc-secret" not in ref.encrypted_value
        status = await sso.get_sso_status(db=db)
    assert status["provider_type"] == "OIDC" and status["display_name"] == "Keycloak"

    code, state = await _login_and_authorize(world, "lead")
    done = await _callback(world, code=code, state=state)
    assert done.status_code == 303
    location = done.headers["location"]
    assert "/sso/callback#code=" in location and "next=%2Fruns" in location
    handoff = unquote(location.split("#code=")[1].split("&")[0])
    signed_in = await sso.exchange_sso_code(SimpleNamespace(code=handoff))
    assert signed_in.user.role == "QA_LEAD" and signed_in.is_new_user is True
    assert signed_in.user.email == f"lead-{world.marker}@example.test"
    async with world.factory() as db:
        user = (await db.execute(select(User).where(User.email == signed_in.user.email))).scalar_one()
        fed = (await db.execute(select(FederatedIdentity).where(FederatedIdentity.user_id == user.id))).scalar_one()
    assert fed.external_id == "fake-sub-lead" and fed.external_groups == ["qa-leads"]

    # The same state again (a replayed callback) is refused, and so is the used code.
    replay = await _callback(world, code=code, state=state)
    assert replay.status_code == 303 and replay.headers["location"].endswith("/login?sso_error=1")
    assert any("state unknown" in e for e in await _failures(world, config.id))


async def test_tokens_the_idp_should_not_have_sent_are_refused(world):
    config = await _create_active_oidc(world)
    for override, reason in (({"aud": "someone-else"}, "rejected"), ({"alg": "none"}, "not allowed"),
                             ({"nonce": "another-login"}, "nonce"), ({"iss": "http://evil.test"}, "issuer")):
        code, state = await _login_and_authorize(world, "lead")
        await world.idp.post("/_fake/oidc/next-token", json=override)
        refused = await _callback(world, code=code, state=state)
        assert refused.headers["location"].endswith("/login?sso_error=1"), override
        assert any(reason in e for e in await _failures(world, config.id)), (override, await _failures(world, config.id))


async def test_an_idp_error_and_a_wrong_secret_are_refused(world):
    from app.models.schemas import SSOConfigUpdate
    from app.routers import sso

    config = await _create_active_oidc(world)
    _, state = await _login_and_authorize(world, "lead")
    refused = await _callback(world, state=state, error="access_denied")
    assert refused.headers["location"].endswith("/login?sso_error=1")
    async with world.factory() as db:
        await sso.update_sso_config(config.id, SSOConfigUpdate(oidc_client_secret="not-the-secret"), _req(),
                                    current_user=world.admin, db=db)
    code, state = await _login_and_authorize(world, "lead")
    refused = await _callback(world, code=code, state=state)
    assert refused.headers["location"].endswith("/login?sso_error=1")
    errors = await _failures(world, config.id)
    assert any("access_denied" in e for e in errors) and any("HTTP 401" in e for e in errors)


async def test_an_unverified_email_never_takes_over_an_existing_account(world):
    from app.models.postgres import FederatedIdentity

    config = await _create_active_oidc(world)
    code, state = await _login_and_authorize(world, "taken")
    refused = await _callback(world, code=code, state=state)
    assert refused.headers["location"].endswith("/login?sso_error=1")
    assert any("not verified" in e for e in await _failures(world, config.id))
    async with world.factory() as db:
        links = (await db.execute(select(FederatedIdentity).where(
            FederatedIdentity.user_id == world.existing.id))).scalars().all()
    assert links == []


async def test_create_needs_the_fields_of_its_type():
    from pydantic import ValidationError

    from app.models.schemas import SSOConfigCreate

    with pytest.raises(ValidationError, match="oidc_client_secret"):
        SSOConfigCreate(display_name="Keycloak", provider_type="OIDC", oidc_issuer=ISSUER, oidc_client_id="c")
    with pytest.raises(ValidationError, match="idp_certificate"):
        SSOConfigCreate(display_name="Okta SAML", provider_type="SAML", idp_entity_id="e", idp_sso_url="u",
                        sp_entity_id="s", sp_acs_url="a")


async def test_the_check_constraint_keeps_a_saml_row_complete(world):
    from sqlalchemy.exc import IntegrityError

    from app.models.postgres import SSOConfiguration

    async with world.factory() as db:
        db.add(SSOConfiguration(display_name="half", provider_type="SAML", idp_entity_id="e"))
        with pytest.raises(IntegrityError, match="ck_sso_config_fields_by_provider"):
            await db.commit()
