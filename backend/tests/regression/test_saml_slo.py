"""W20 ADM-08c: IdP-initiated SAML single logout — real signatures, both bindings.

* HTTP-Redirect: the RSA-SHA256 signature over ``SAMLRequest=..&RelayState=..&SigAlg=..`` verifies with the IdP
  certificate; unsigned, signed by another key, rsa-sha1, or a tampered SAMLRequest are refused.
* HTTP-POST: an enveloped XML-DSig signature (signxml) verifies; an unsigned or re-signed-by-another-key request
  is refused, and claims are read from the signed element only.
* Issuer, Destination, IssueInstant, NotOnOrAfter are checked; the LogoutResponse goes to the IdP's SLO URL.
* The route: a valid request revokes the NameID's refresh tokens + writes the access-token cutoff + an
  SSO_LOGOUT event and redirects with a Success LogoutResponse; a replay, a bad signature, an OIDC or SLO-less
  config are refused and change nothing; the metadata advertises /sso/slo only when the IdP has an SLO URL.
"""
from __future__ import annotations

import base64
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.services import saml_slo

IDP = "https://idp.example.test/realms/testlookup"
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30)).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="module")
def idp():
    return _keypair()


@pytest.fixture(scope="module")
def other():
    return _keypair()


def _request_xml(*, issuer=IDP, name_id="dana@example.test", destination=None, instant=None, req_id=None,
                 not_on_or_after=None) -> bytes:
    instant = instant or datetime.now(timezone.utc)
    attrs = f'ID="{req_id or "_" + uuid.uuid4().hex}" Version="2.0" IssueInstant="{instant.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    if destination:
        attrs += f' Destination="{destination}"'
    if not_on_or_after:
        attrs += f' NotOnOrAfter="{not_on_or_after.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    return (
        f'<samlp:LogoutRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" {attrs}>'
        f"<saml:Issuer>{issuer}</saml:Issuer><saml:NameID>{name_id}</saml:NameID>"
        f"<samlp:SessionIndex>s-1</samlp:SessionIndex></samlp:LogoutRequest>"
    ).encode()


def _redirect_query(xml: bytes, key, *, sig_alg=RSA_SHA256, relay="rs-1", sign=True) -> str:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    encoded = base64.b64encode(c.compress(xml) + c.flush()).decode()
    parts = [f"SAMLRequest={quote(encoded, safe='')}"]
    if relay:
        parts.append(f"RelayState={quote(relay, safe='')}")
    parts.append(f"SigAlg={quote(sig_alg, safe='')}")
    query = "&".join(parts)
    if sign:
        digest = hashes.SHA1() if sig_alg == RSA_SHA1 else hashes.SHA256()  # noqa: S303 - the refused case
        sig = key.sign(query.encode(), padding.PKCS1v15(), digest)
        query += f"&Signature={quote(base64.b64encode(sig).decode(), safe='')}"
    return query


def _post_body(xml: bytes, key, cert_pem: str) -> str:
    pytest.importorskip("lxml")
    pytest.importorskip("signxml")
    from lxml import etree
    from signxml import XMLSigner

    root = etree.fromstring(xml)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signed = XMLSigner(signature_algorithm="rsa-sha256", digest_algorithm="sha256").sign(root, key=pem, cert=cert_pem)
    return base64.b64encode(etree.tostring(signed)).decode()


# ── signatures ────────────────────────────────────────────────────────────────


def test_a_signed_redirect_request_verifies(idp):
    key, cert = idp
    claims = saml_slo.verify_logout_request(binding="redirect", idp_certificate_pem=cert,
                                            raw_query=_redirect_query(_request_xml(), key))
    assert claims["name_id"] == "dana@example.test" and claims["issuer"] == IDP and claims["session_indexes"] == ["s-1"]


@pytest.mark.parametrize("make, why", [
    (lambda key: _redirect_query(_request_xml(), key, sign=False), "not signed"),
    (lambda key: _redirect_query(_request_xml(), key, sig_alg=RSA_SHA1), "not allowed"),
    (lambda key: _redirect_query(_request_xml(), key).replace("SAMLRequest=", "SAMLRequest=A", 1), "does not verify"),
    (lambda key: _redirect_query(_request_xml(), key).replace("RelayState=rs-1", "RelayState=rs-2"), "does not verify"),
])
def test_bad_redirect_requests_are_refused(idp, make, why):
    _, cert = idp
    with pytest.raises(ValueError, match=why):
        saml_slo.verify_logout_request(binding="redirect", idp_certificate_pem=cert, raw_query=make(idp[0]))


def test_a_redirect_request_signed_by_another_key_is_refused(idp, other):
    with pytest.raises(ValueError, match="does not verify"):
        saml_slo.verify_logout_request(binding="redirect", idp_certificate_pem=idp[1],
                                       raw_query=_redirect_query(_request_xml(), other[0]))


def test_a_signed_post_request_verifies(idp):
    key, cert = idp
    claims = saml_slo.verify_logout_request(binding="post", idp_certificate_pem=cert,
                                            form_saml_request=_post_body(_request_xml(), key, cert))
    assert claims["name_id"] == "dana@example.test"


def test_unsigned_or_foreign_post_requests_are_refused(idp, other):
    _, cert = idp
    with pytest.raises(ValueError, match="signature"):
        saml_slo.verify_logout_request(binding="post", idp_certificate_pem=cert,
                                       form_saml_request=base64.b64encode(_request_xml()).decode())
    with pytest.raises(ValueError, match="signature"):
        saml_slo.verify_logout_request(binding="post", idp_certificate_pem=cert,
                                       form_saml_request=_post_body(_request_xml(), other[0], other[1]))


# ── the request's own fields ──────────────────────────────────────────────────


def _claims(**over):
    base = {"id": "_r1", "issuer": IDP, "destination": None, "issue_instant": datetime.now(timezone.utc).isoformat(),
            "not_on_or_after": None, "name_id": "x", "session_indexes": []}
    base.update(over)
    return base


@pytest.mark.parametrize("over, why", [
    ({"issuer": "https://evil.example.test"}, "issuer"),
    ({"destination": "https://elsewhere.example.test/slo"}, "destination"),
    ({"issue_instant": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}, "not current"),
    ({"issue_instant": None}, "not current"),
    ({"not_on_or_after": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}, "expired"),
    ({"id": None}, "no ID"),
])
def test_bad_fields_are_refused(over, why):
    with pytest.raises(ValueError, match=why):
        saml_slo.check_logout_request(_claims(**over), idp_entity_id=IDP)


def test_our_own_destination_passes():
    saml_slo.check_logout_request(_claims(destination=saml_slo.slo_url()), idp_entity_id=IDP)


def test_the_logout_response_is_a_success_for_the_request():
    url = saml_slo.logout_response_url(idp_slo_url="https://idp.example.test/slo?x=1", in_response_to="_r1",
                                       sp_entity_id="https://app/saml", relay_state="rs-1")
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://idp.example.test/slo?x=1&") and q["RelayState"] == ["rs-1"]
    xml = zlib.decompress(base64.b64decode(q["SAMLResponse"][0]), -15).decode()
    assert 'InResponseTo="_r1"' in xml and "status:Success" in xml and "https://app/saml" in xml


# ── the route ─────────────────────────────────────────────────────────────────


class _Redis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


@pytest.fixture
def route(monkeypatch, idp):
    from app.core.config import settings
    from app.routers import sso

    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    redis = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    config = SimpleNamespace(id=uuid.uuid4(), provider_type="SAML", idp_entity_id=IDP, idp_certificate=idp[1],
                             idp_slo_url="https://idp.example.test/slo", sp_entity_id="https://app/saml")
    monkeypatch.setattr(sso, "get_active_sso_config", AsyncMock(return_value=config))
    revoke_family, cutoff, event = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(sso, "_revoke_refresh_family", revoke_family)
    monkeypatch.setattr(sso, "revoke_all_user_tokens", cutoff)
    monkeypatch.setattr(sso, "log_identity_event", event)
    user_id = uuid.uuid4()
    found = MagicMock()
    found.scalar_one_or_none.return_value = SimpleNamespace(user_id=user_id)
    db = MagicMock()
    db.execute = AsyncMock(return_value=found)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return SimpleNamespace(sso=sso, config=config, db=db, user_id=user_id, revoke_family=revoke_family,
                           cutoff=cutoff, event=event)


def _get(query: str):
    from starlette.requests import Request

    return Request({"type": "http", "method": "GET", "path": "/api/v1/sso/slo", "query_string": query.encode(),
                    "headers": [], "client": ("127.0.0.1", 1)})


async def test_a_valid_request_ends_the_sessions_and_answers_success(route, idp):
    query = _redirect_query(_request_xml(req_id="_req-1"), idp[0])
    answer = await route.sso.saml_single_logout(_get(query), db=route.db)
    assert answer.status_code == 302
    location = answer.headers["location"]
    assert location.startswith("https://idp.example.test/slo?SAMLResponse=") and "RelayState=rs-1" in location
    route.revoke_family.assert_awaited_once_with(route.db, route.user_id, reason="saml_slo")
    route.cutoff.assert_awaited_once_with(route.user_id, route.db)
    assert route.event.await_args.args[1] == "SSO_LOGOUT"
    route.db.commit.assert_awaited_once()

    # The same LogoutRequest again: refused, nothing more revoked.
    with pytest.raises(HTTPException) as caught:
        await route.sso.saml_single_logout(_get(query), db=route.db)
    assert caught.value.status_code == 400
    assert route.revoke_family.await_count == 1


async def test_a_bad_signature_changes_nothing(route, idp, other):
    with pytest.raises(HTTPException) as caught:
        await route.sso.saml_single_logout(_get(_redirect_query(_request_xml(), other[0])), db=route.db)
    assert caught.value.status_code == 400
    route.revoke_family.assert_not_awaited()
    route.cutoff.assert_not_awaited()
    route.event.assert_not_awaited()


@pytest.mark.parametrize("change", [{"idp_slo_url": None}, {"provider_type": "OIDC"}])
async def test_without_an_slo_url_or_for_oidc_there_is_no_slo(route, idp, change):
    for name, value in change.items():
        setattr(route.config, name, value)
    with pytest.raises(HTTPException) as caught:
        await route.sso.saml_single_logout(_get(_redirect_query(_request_xml(), idp[0])), db=route.db)
    assert caught.value.status_code == 404


async def test_metadata_advertises_slo_only_with_an_idp_slo_url(route):
    with_slo = await route.sso.get_sp_metadata(db=route.db)
    assert with_slo["single_logout_url"].endswith("/api/v1/sso/slo")
    route.config.idp_slo_url = None
    assert "single_logout_url" not in await route.sso.get_sp_metadata(db=route.db)


def test_the_new_event_type_fits_the_column():
    from app.models.postgres import IdentityEventType

    assert len(IdentityEventType.SSO_LOGOUT.value) <= 40


def test_urlencode_is_used_for_the_answer():  # guard: the RelayState is encoded, never raw
    url = saml_slo.logout_response_url(idp_slo_url="https://i/slo", in_response_to="_r", sp_entity_id=None,
                                       relay_state="a&b=c")
    assert "RelayState=" + urlencode({"x": "a&b=c"})[2:] in url


# ── the ACS verifier with a REAL signature (signxml 5: SignatureConfiguration) ────────────────────────────


def _signed_saml_response(key, cert_pem: str, *, sig="rsa-sha256", digest="sha256") -> str:
    pytest.importorskip("lxml")
    pytest.importorskip("signxml")
    from lxml import etree
    from signxml import XMLSigner

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_resp1" Version="2.0" IssueInstant="{now}">'
        f"<saml:Issuer>{IDP}</saml:Issuer>"
        f'<saml:Assertion ID="_a1" Version="2.0" IssueInstant="{now}"><saml:Issuer>{IDP}</saml:Issuer>'
        "<saml:Subject><saml:NameID>dana@example.test</saml:NameID></saml:Subject>"
        '<saml:AttributeStatement><saml:Attribute Name="groups"><saml:AttributeValue>qa-leads</saml:AttributeValue>'
        "</saml:Attribute></saml:AttributeStatement></saml:Assertion></samlp:Response>"
    )
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signed = XMLSigner(signature_algorithm=sig, digest_algorithm=digest).sign(etree.fromstring(xml.encode()), key=pem, cert=cert_pem)
    return base64.b64encode(etree.tostring(signed)).decode()


def test_a_really_signed_saml_response_parses(idp):
    """Before the fix every signed assertion raised (XMLVerifier.VerifyConfig is not signxml 5 API)."""
    from app.services.sso_service import parse_saml_response

    parsed = parse_saml_response(_signed_saml_response(*idp), idp_certificate_pem=idp[1])
    assert parsed["name_id"] == "dana@example.test" and parsed["attributes"]["groups"] == ["qa-leads"]


def test_a_saml_response_signed_by_another_key_is_refused(idp, other):
    from app.services.sso_service import parse_saml_response

    with pytest.raises(ValueError, match="signature verification failed"):
        parse_saml_response(_signed_saml_response(*other), idp_certificate_pem=idp[1])
