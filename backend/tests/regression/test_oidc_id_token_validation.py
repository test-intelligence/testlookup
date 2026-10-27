"""W20 ADM-08b: OIDC ID-token validation and the login hand-offs — the negative cases are the point.

The backlog marks these mandatory (ID-token validation is a classic auth bypass): ``alg=none``, an HMAC token
keyed with the IdP's PUBLIC key (algorithm confusion), wrong ``aud`` / ``iss``, expired, nonce mismatch or
missing, an unknown ``kid``, several audiences without ``azp``; plus single-use ``state``, the SSRF guard on
discovery, a discovery document naming another issuer, PKCE S256, and the claims → attributes mapping.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.services import oidc_service
from app.services.oidc_service import OIDCError, validate_id_token

ISSUER = "https://idp.example.test/realms/testlookup"
CLIENT = "testlookup"
NONCE = "n-0S6_WzA2Mj"


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(rsa_key):
    pub = rsa_key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "k1", "n": _b64(pub.n), "e": _b64(pub.e)}]}


def _pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _claims(**over):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT, "sub": "u-123", "iat": now, "exp": now + 300, "nonce": NONCE,
              "email": "dana@example.test"}
    for key, value in over.items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return claims


def _sign(key, claims, kid="k1", alg="RS256"):
    return jwt.encode(claims, _pem(key), algorithm=alg, headers={"kid": kid})


def _validate(token, jwks):
    return validate_id_token(token, jwks=jwks, issuer=ISSUER, client_id=CLIENT, nonce=NONCE)


def test_a_valid_token_passes(rsa_key, jwks):
    claims = _validate(_sign(rsa_key, _claims()), jwks)
    assert claims["sub"] == "u-123" and claims["email"] == "dana@example.test"


def test_issuer_trailing_slash_is_not_a_mismatch(rsa_key, jwks):
    assert _validate(_sign(rsa_key, _claims(iss=ISSUER + "/")), jwks)["sub"] == "u-123"


def test_alg_none_is_rejected(jwks):
    token = jwt.encode(_claims(), None, algorithm="none")
    with pytest.raises(OIDCError, match="not allowed"):
        _validate(token, jwks)


def test_hmac_keyed_with_the_public_key_is_rejected(rsa_key, jwks):
    """Algorithm confusion: HS256 'signed' with the IdP's public key PEM must never verify."""
    public_pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "kid": "k1", "typ": "JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(_claims()).encode()).rstrip(b"=")
    import hmac as _hmac

    sig = base64.urlsafe_b64encode(_hmac.new(public_pem, header + b"." + payload, hashlib.sha256).digest()).rstrip(b"=")
    token = (header + b"." + payload + b"." + sig).decode()
    with pytest.raises(OIDCError, match="not allowed"):
        _validate(token, jwks)


@pytest.mark.parametrize("over, why", [
    ({"aud": "someone-else"}, "rejected"),
    ({"iss": "https://evil.example.test"}, "issuer"),
    ({"exp": int(time.time()) - 3600}, "rejected"),
    ({"nonce": "another-login"}, "nonce"),
    ({"nonce": None}, "nonce"),
    ({"exp": None}, "rejected"),
    ({"iat": None}, "rejected"),
    ({"sub": None}, "rejected"),
    ({"aud": [CLIENT, "other"]}, "azp"),
    ({"aud": [CLIENT, "other"], "azp": "other"}, "azp"),
])
def test_bad_claims_are_rejected(rsa_key, jwks, over, why):
    with pytest.raises(OIDCError, match=why):
        _validate(_sign(rsa_key, _claims(**over)), jwks)


def test_several_audiences_with_our_azp_pass(rsa_key, jwks):
    assert _validate(_sign(rsa_key, _claims(aud=[CLIENT, "other"], azp=CLIENT)), jwks)["sub"] == "u-123"


def test_a_token_signed_by_another_key_is_rejected(jwks):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OIDCError, match="rejected"):
        _validate(_sign(other, _claims()), jwks)


def test_an_unknown_kid_is_rejected(rsa_key, jwks):
    with pytest.raises(OIDCError, match="no usable"):
        _validate(_sign(rsa_key, _claims(), kid="rotated"), jwks)


def test_es256_is_accepted_with_an_ec_key():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_numbers()
    ec_jwks = {"keys": [{"kty": "EC", "crv": "P-256", "kid": "e1", "x": _b64(pub.x), "y": _b64(pub.y)}]}
    token = jwt.encode(_claims(), _pem(key), algorithm="ES256", headers={"kid": "e1"})
    assert _validate(token, ec_jwks)["sub"] == "u-123"


def test_an_rs256_header_never_selects_an_ec_key(rsa_key):
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_numbers()
    ec_jwks = {"keys": [{"kty": "EC", "crv": "P-256", "kid": "k1", "x": _b64(pub.x), "y": _b64(pub.y)}]}
    with pytest.raises(OIDCError, match="no usable"):
        _validate(_sign(rsa_key, _claims()), ec_jwks)


# ── claims → attributes, scopes, PKCE ─────────────────────────────────────────


def test_claims_map_to_jit_attributes():
    email, attrs, verified = oidc_service.claims_to_attributes(
        {"email": "dana@example.test", "name": "Dana", "roles": ["qa-leads", "staff"], "email_verified": False},
        "roles",
    )
    assert email == "dana@example.test" and verified is False
    assert attrs == {"email": ["dana@example.test"], "name": ["Dana"], "roles": ["qa-leads", "staff"]}


def test_a_token_without_email_is_refused():
    with pytest.raises(OIDCError, match="email"):
        oidc_service.claims_to_attributes({"sub": "x"}, None)


def test_scopes_always_include_openid():
    assert oidc_service.scopes_for(None) == "openid email profile"
    assert oidc_service.scopes_for("email groups") == "openid email groups"


def test_pkce_challenge_is_s256_of_the_verifier():
    verifier, challenge = oidc_service.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128


# ── state is single use; discovery is guarded ─────────────────────────────────


class _Redis:
    """SET + GETDEL, the two calls the state store makes."""

    def __init__(self):
        self.data: dict[str, bytes] = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value.encode() if isinstance(value, str) else value

    async def getdel(self, key):
        return self.data.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


async def test_state_works_once(redis, monkeypatch):
    from types import SimpleNamespace

    async def fake_discover(issuer, use_cache=True):
        return {"authorization_endpoint": "https://idp.example.test/auth", "token_endpoint": "x", "jwks_uri": "y"}

    async def no_guard(url, what):
        return None

    monkeypatch.setattr(oidc_service, "discover", fake_discover)
    monkeypatch.setattr(oidc_service, "_guard_url", no_guard)
    config = SimpleNamespace(id="cfg-1", oidc_issuer=ISSUER, oidc_client_id=CLIENT, oidc_scopes=None)
    url = await oidc_service.start_login(config, next_path="/runs")
    from urllib.parse import parse_qs, urlsplit

    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["code_challenge_method"] == "S256" and q["response_type"] == "code" and q["client_id"] == CLIENT
    parked = await oidc_service.consume_state(q["state"])
    assert parked["nonce"] == q["nonce"] and parked["next"] == "/runs" and parked["config_id"] == "cfg-1"
    assert await oidc_service.consume_state(q["state"]) is None  # replay
    assert await oidc_service.consume_state("never-minted") is None
    assert await oidc_service.consume_state("x" * 500) is None


async def test_discovery_refuses_a_private_issuer(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_IDP_ENDPOINTS", False)
    oidc_service.clear_caches()
    with pytest.raises(OIDCError, match="SSRF"):
        await oidc_service.discover("https://127.0.0.1/realms/x")
    with pytest.raises(OIDCError, match="https"):
        await oidc_service.discover("http://idp.example.test/realms/x")


async def test_discovery_refuses_a_document_for_another_issuer(monkeypatch):
    import httpx

    from app.core.config import settings

    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_IDP_ENDPOINTS", True)
    oidc_service.clear_caches()

    def handler(request):
        return httpx.Response(200, json={"issuer": "https://evil.example.test", "authorization_endpoint": "a",
                                         "token_endpoint": "t", "jwks_uri": "j"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(oidc_service, "_http_client", lambda: client)
    with pytest.raises(OIDCError, match="does not match"):
        await oidc_service.discover(ISSUER)
