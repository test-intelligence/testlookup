"""SAML SP-initiated login, end to end through the routes.

Three things kept it from working (open-source docs pass, 2026-10-11):

* ``/sso/login-url`` sent the browser to the bare IdP SSO URL. No AuthnRequest
  went out, so the IdP's response had no ``InResponseTo`` and the ACS refused
  it unless ``SAML_ALLOW_IDP_INITIATED`` was on; the request id it remembered
  was never seen by the IdP.
* ``/sso/acs`` answered the IdP's *browser* form POST with JSON tokens, so a
  user who did get through looked at a JSON page and was never signed in.
* The SP metadata advertised ``/sso/slo``, which has no handler.

The assertion's signature checks are the service's own tests; here they are
stubbed so the routes' wiring is what is under test.
"""
from __future__ import annotations

import base64
import uuid
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("defusedxml")

import defusedxml.ElementTree as SafeET  # noqa: E402

from tests.integration.conftest import make_user  # noqa: E402

pytestmark = pytest.mark.asyncio

APP = "https://app.example"
IDP_SSO = "https://idp.example/sso"


class _Redis:
    """Just enough Redis for single-use keys: SET with a TTL, and GETDEL."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def getdel(self, key):
        return self.store.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


@pytest.fixture
def sso(monkeypatch, redis):
    from app.core.config import settings
    from app.routers import sso as sso_router

    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", APP)
    config = SimpleNamespace(
        id=uuid.uuid4(),
        idp_sso_url=IDP_SSO,
        idp_entity_id="https://idp.example",
        idp_certificate="-----BEGIN CERTIFICATE-----x-----END CERTIFICATE-----",
        sp_entity_id=f"{APP}/saml",
        sp_acs_url=f"{APP}/api/v1/sso/acs",
        audience=None,
    )
    monkeypatch.setattr(sso_router, "get_active_sso_config", AsyncMock(return_value=config))
    return config


def _authn_request(redirect_url: str):
    query = parse_qs(urlsplit(redirect_url).query)
    xml = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15)
    return SafeET.fromstring(xml), query


# ── /sso/login-url ───────────────────────────────────────────────────────────


async def test_login_url_sends_an_authn_request_bound_to_the_remembered_id(client, sso, redis):
    resp = await client.get("/api/v1/sso/login-url", params={"next": "/runs?status=failed"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["redirect_url"].startswith(f"{IDP_SSO}?SAMLRequest=")
    request, query = _authn_request(body["redirect_url"])
    assert request.tag == "{urn:oasis:names:tc:SAML:2.0:protocol}AuthnRequest"
    # The id the ACS will consume is the id the IdP answers with (InResponseTo).
    assert request.get("ID") == body["request_id"]
    assert f"saml:authnreq:{body['request_id']}" in redis.store
    assert request.get("Destination") == IDP_SSO
    assert request.get("AssertionConsumerServiceURL") == sso.sp_acs_url
    assert request.get("ProtocolBinding") == "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
    issuer = request.find("{urn:oasis:names:tc:SAML:2.0:assertion}Issuer")
    assert issuer is not None and issuer.text == sso.sp_entity_id
    assert query["RelayState"] == ["/runs?status=failed"]


@pytest.mark.parametrize("unsafe", ["https://evil.example/", "//evil.example/x", "/\\evil.example", "runs", "/a\nb"])
async def test_login_url_never_carries_a_relay_state_that_leaves_the_app(client, sso, unsafe):
    resp = await client.get("/api/v1/sso/login-url", params={"next": unsafe})
    _, query = _authn_request(resp.json()["redirect_url"])
    assert "RelayState" not in query


async def test_an_idp_sso_url_with_its_own_query_keeps_it(client, sso):
    sso.idp_sso_url = f"{IDP_SSO}?tenant=7"
    resp = await client.get("/api/v1/sso/login-url")
    assert resp.json()["redirect_url"].startswith(f"{IDP_SSO}?tenant=7&SAMLRequest=")


async def test_metadata_advertises_no_single_logout_url_it_cannot_serve(client):
    body = (await client.get("/api/v1/sso/metadata")).json()
    assert "slo_url" not in body
    assert body["acs_url"].endswith("/api/v1/sso/acs")


# ── /sso/acs → /sso/callback → /sso/exchange ──────────────────────────────────


@pytest.fixture
def assertion_ok(monkeypatch, fake_db):
    """A signature-verified, bound assertion for an active user (the service's
    checks are their own tests)."""
    from app.routers import sso as sso_router

    user = make_user()
    user.username = "sso-user"
    monkeypatch.setattr(sso_router, "parse_saml_response", lambda *_a, **_k: {"name_id": "u@example.com", "attributes": {}})
    monkeypatch.setattr(sso_router, "validate_saml_issuer", lambda *_a: None)
    monkeypatch.setattr(sso_router, "enforce_saml_security", AsyncMock())
    monkeypatch.setattr(sso_router, "jit_provision_or_link", AsyncMock(return_value=(user, False)))
    monkeypatch.setattr(sso_router, "issue_access_jwt", AsyncMock(return_value="access-1"))
    monkeypatch.setattr(sso_router, "issue_refresh_token", AsyncMock(return_value="refresh-1"))
    monkeypatch.setattr(sso_router, "log_identity_event", AsyncMock())
    return user


BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def test_the_idps_browser_post_lands_in_the_app_and_the_code_works_once(client, sso, assertion_ok):
    resp = await client.post(
        "/api/v1/sso/acs", data={"SAMLResponse": "PHNhbWw+", "RelayState": "/runs/r1?tab=analysis"}, headers=BROWSER,
    )
    assert resp.status_code == 303, resp.text
    location = resp.headers["location"]
    assert location.startswith(f"{APP}/sso/callback#code=")
    # Tokens never ride in the URL; only the one-time code, in the fragment.
    assert "access-1" not in location and "refresh-1" not in location
    fragment = parse_qs(urlsplit(location).fragment)
    assert unquote(fragment["next"][0]) == "/runs/r1?tab=analysis"

    first = await client.post("/api/v1/sso/exchange", json={"code": fragment["code"][0]})
    assert first.status_code == 200, first.text
    assert first.json()["access_token"] == "access-1"
    assert first.json()["refresh_token"] == "refresh-1"
    assert first.json()["user"]["username"] == "sso-user"

    again = await client.post("/api/v1/sso/exchange", json={"code": fragment["code"][0]})
    assert again.status_code == 400


async def test_a_relay_state_that_leaves_the_app_is_dropped_on_the_way_back(client, sso, assertion_ok):
    resp = await client.post(
        "/api/v1/sso/acs", data={"SAMLResponse": "PHNhbWw+", "RelayState": "https://evil.example/"}, headers=BROWSER,
    )
    assert resp.status_code == 303
    assert "next" not in parse_qs(urlsplit(resp.headers["location"]).fragment)


async def test_a_refused_assertion_sends_the_browser_back_to_sign_in(client, sso, assertion_ok, monkeypatch):
    from app.routers import sso as sso_router

    monkeypatch.setattr(sso_router, "enforce_saml_security", AsyncMock(side_effect=ValueError("InResponseTo mismatch")))
    resp = await client.post("/api/v1/sso/acs", data={"SAMLResponse": "PHNhbWw+"}, headers=BROWSER)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{APP}/login?sso_error=1"
    # The specific reason stays server-side.
    assert "InResponseTo" not in resp.headers["location"]


async def test_an_api_client_still_gets_json(client, sso, assertion_ok, monkeypatch):
    ok = await client.post("/api/v1/sso/acs", data={"SAMLResponse": "PHNhbWw+"}, headers={"Accept": "application/json"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"] == "access-1"

    from app.routers import sso as sso_router

    monkeypatch.setattr(sso_router, "enforce_saml_security", AsyncMock(side_effect=ValueError("replay")))
    refused = await client.post("/api/v1/sso/acs", data={"SAMLResponse": "PHNhbWw+"}, headers={"Accept": "application/json"})
    assert refused.status_code == 401


async def test_an_unknown_code_is_refused(client, sso):
    resp = await client.post("/api/v1/sso/exchange", json={"code": "not-a-code"})
    assert resp.status_code == 400
