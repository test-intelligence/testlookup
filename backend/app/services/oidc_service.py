"""OIDC sign-in — authorization code + PKCE against one configured identity provider (W20 ADM-08b).

The flow (``routers/sso.py``):

1. ``/sso/oidc/login`` (or ``/sso/login-url`` for an OIDC config) mints ``state``, ``nonce`` and a PKCE
   ``code_verifier``, parks them in Redis for 10 minutes (single use) and sends the browser to the IdP's
   ``authorization_endpoint``.
2. ``/sso/oidc/callback`` burns the ``state`` (unknown / used / expired → refused), exchanges the code at the
   ``token_endpoint`` with the client secret and the verifier, and validates the ID token here:
   signature against the IdP's JWKS (RS256 / ES256 only — never ``none`` or an HMAC alg keyed with a public
   key), ``iss`` = the configured issuer, ``aud`` contains the client id (``azp`` when there are several),
   ``exp`` / ``iat`` present and current, ``nonce`` = the one minted in step 1.
3. The claims go through the same ``jit_provision_or_link`` / role mapping as SAML, and the browser is handed
   off through the one-time-code fragment to ``/sso/callback``.

Every outbound call (discovery, token, JWKS) goes through the SSRF guard unless
``SSO_ALLOW_PRIVATE_IDP_ENDPOINTS`` is set (an IdP inside the cluster), with ``follow_redirects=False``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = ("RS256", "ES256")
_KEY_TYPE_FOR_ALG = {"RS256": "RSA", "ES256": "EC"}
DEFAULT_SCOPES = "openid email profile"
DEFAULT_GROUPS_CLAIM = "groups"

_STATE_PREFIX = "oidc:state:"
_STATE_TTL_SECONDS = 600
_STATE_MAX = 128
_HTTP_TIMEOUT_SECONDS = 10.0
_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 600
_JWKS_REFRESH_MIN_SECONDS = 30
_CLOCK_SKEW_SECONDS = 60
_MAX_DOCUMENT_BYTES = 512_000

_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}


class OIDCError(ValueError):
    """A refused OIDC step. The message is for the server log, never the browser."""


def callback_url() -> str:
    """The redirect URI registered with the IdP: the backend's public base + the callback route."""
    return f"{settings.SAML_BASE_URL.rstrip('/')}/api/v1/sso/oidc/callback"


def scopes_for(configured: Optional[str]) -> str:
    """The scopes to ask for: the configured ones, always including ``openid``."""
    parts = (configured or DEFAULT_SCOPES).split()
    if "openid" not in parts:
        parts.insert(0, "openid")
    return " ".join(dict.fromkeys(parts))


def clear_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


# ── HTTP (SSRF-guarded) ──────────────────────────────────────────────────────


def _http_client() -> httpx.AsyncClient:
    from app.core.http_client import get_http_client, get_public_http_client

    return get_http_client() if settings.SSO_ALLOW_PRIVATE_IDP_ENDPOINTS else get_public_http_client()


async def _guard_url(url: str, what: str) -> None:
    parts = urlsplit(url or "")
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise OIDCError(f"{what} is not an absolute http(s) URL: {url!r}")
    if settings.SSO_ALLOW_PRIVATE_IDP_ENDPOINTS:
        return
    if parts.scheme != "https":
        raise OIDCError(f"{what} must use https: {url!r}")
    from app.services.url_safety import assert_public_url

    try:
        await assert_public_url(url)
    except ValueError as exc:
        raise OIDCError(f"{what} blocked by SSRF policy: {exc}") from exc


def _json_body(response: httpx.Response, what: str) -> dict:
    if len(response.content) > _MAX_DOCUMENT_BYTES:
        raise OIDCError(f"{what} is too large")
    try:
        body = response.json()
    except ValueError as exc:
        raise OIDCError(f"{what} is not JSON") from exc
    if not isinstance(body, dict):
        raise OIDCError(f"{what} is not a JSON object")
    return body


async def _get_json(url: str, what: str) -> dict:
    await _guard_url(url, what)
    try:
        response = await _http_client().get(url, timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=False)
    except httpx.HTTPError as exc:
        from app.services.url_safety import is_unsafe_target_error

        if is_unsafe_target_error(exc):
            raise OIDCError(f"{what} blocked by SSRF policy") from exc
        raise OIDCError(f"{what} unreachable: {str(exc)[:200]}") from exc
    if response.status_code != 200:
        raise OIDCError(f"{what} answered HTTP {response.status_code}")
    return _json_body(response, what)


# ── Discovery + JWKS ─────────────────────────────────────────────────────────


async def discover(issuer: str, *, use_cache: bool = True) -> dict:
    """The IdP's ``/.well-known/openid-configuration``, checked: its ``issuer`` is exactly the configured one
    and it names the authorization, token and JWKS endpoints."""
    issuer = (issuer or "").rstrip("/")
    cached = _discovery_cache.get(issuer)
    if use_cache and cached and cached[0] > time.monotonic():
        return cached[1]
    doc = await _get_json(f"{issuer}/.well-known/openid-configuration", "OIDC discovery document")
    if (doc.get("issuer") or "").rstrip("/") != issuer:
        raise OIDCError(f"discovery issuer {doc.get('issuer')!r} does not match the configured {issuer!r}")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise OIDCError(f"discovery document has no {key}")
    _discovery_cache[issuer] = (time.monotonic() + _DISCOVERY_TTL_SECONDS, doc)
    return doc


async def fetch_jwks(jwks_uri: str, *, force: bool = False) -> dict:
    cached = _jwks_cache.get(jwks_uri)
    now = time.monotonic()
    if cached and not force and cached[0] > now:
        return cached[1]
    if cached and force and cached[0] - _JWKS_TTL_SECONDS + _JWKS_REFRESH_MIN_SECONDS > now:
        return cached[1]  # refreshed moments ago — an unknown kid stays unknown
    jwks = await _get_json(jwks_uri, "OIDC JWKS")
    if not isinstance(jwks.get("keys"), list):
        raise OIDCError("JWKS has no keys")
    _jwks_cache[jwks_uri] = (now + _JWKS_TTL_SECONDS, jwks)
    return jwks


# ── PKCE + state ─────────────────────────────────────────────────────────────


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


async def start_login(config: Any, *, next_path: Optional[str]) -> str:
    """Mint state / nonce / PKCE for ``config``, park them (single use, 10 min) and return the IdP URL."""
    from app.db.redis_client import get_redis

    doc = await discover(config.oidc_issuer)
    await _guard_url(doc["authorization_endpoint"], "OIDC authorization endpoint")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    redirect_uri = callback_url()
    await get_redis().set(
        f"{_STATE_PREFIX}{state}",
        json.dumps({
            "config_id": str(config.id),
            "nonce": nonce,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "next": next_path,
        }),
        ex=_STATE_TTL_SECONDS,
    )
    query = urlencode({
        "response_type": "code",
        "client_id": config.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes_for(config.oidc_scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    endpoint = doc["authorization_endpoint"]
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"


async def consume_state(state: Optional[str]) -> Optional[dict]:
    """The parked login for ``state``, at most once (GETDEL). None when unknown, used or expired.
    A Redis error raises — the callback fails closed."""
    if not state or len(state) > _STATE_MAX:
        return None
    from app.db.redis_client import get_redis
    from app.services.sso_service import _getdel

    raw = await _getdel(get_redis(), f"{_STATE_PREFIX}{state}")
    return json.loads(raw) if raw else None


# ── Token exchange + ID-token validation ─────────────────────────────────────


async def exchange_code(
    doc: dict, *, code: str, code_verifier: str, redirect_uri: str, client_id: str, client_secret: str,
) -> dict:
    """POST the code to the token endpoint (``client_secret_basic``); the token response, with an id_token."""
    token_endpoint = doc["token_endpoint"]
    await _guard_url(token_endpoint, "OIDC token endpoint")
    try:
        response = await _http_client().post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            # RFC 6749 §2.3.1: form-encode the id and secret before Basic.
            auth=(quote(client_id, safe=""), quote(client_secret, safe="")),
            headers={"Accept": "application/json"},
            timeout=_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise OIDCError(f"token endpoint unreachable: {str(exc)[:200]}") from exc
    body = _json_body(response, "token response") if response.content else {}
    if response.status_code != 200:
        raise OIDCError(f"token endpoint answered HTTP {response.status_code}: {str(body.get('error'))[:100]}")
    if not isinstance(body.get("id_token"), str):
        raise OIDCError("token response has no id_token")
    return body


def _select_key(jwks: dict, header: dict) -> Optional[jwt.PyJWK]:
    alg = header["alg"]
    kid = header.get("kid")
    want_kty = _KEY_TYPE_FOR_ALG[alg]
    candidates = []
    for raw in jwks.get("keys") or []:
        if not isinstance(raw, dict) or raw.get("kty") != want_kty:
            continue
        if raw.get("use") not in (None, "sig"):
            continue
        if raw.get("alg") not in (None, alg):
            continue
        if kid is not None and raw.get("kid") != kid:
            continue
        candidates.append(raw)
    if len(candidates) != 1:
        return None  # no key, or an ambiguous kid-less match
    try:
        return jwt.PyJWK(candidates[0], algorithm=alg)
    except jwt.PyJWTError:
        return None


def validate_id_token(
    id_token: str, *, jwks: dict, issuer: str, client_id: str, nonce: str, leeway: int = _CLOCK_SKEW_SECONDS,
) -> dict:
    """The ID token's claims after every check, or ``OIDCError``. Pure: the JWKS is passed in."""
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OIDCError(f"ID token is malformed: {exc}") from exc
    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise OIDCError(f"ID token algorithm {alg!r} is not allowed")
    key = _select_key(jwks, header)
    if key is None:
        raise OIDCError(f"no usable {alg} signing key for kid {header.get('kid')!r}")
    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=[alg],
            audience=client_id,
            leeway=leeway,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(f"ID token rejected: {exc}") from exc
    # Compared here, not by PyJWT, so a trailing "/" on either side is not a mismatch (as in discover()).
    if not isinstance(claims.get("iss"), str) or claims["iss"].rstrip("/") != issuer.rstrip("/"):
        raise OIDCError(f"ID token issuer {claims.get('iss')!r} is not the configured issuer")
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
        raise OIDCError("ID token has several audiences and azp is not this client")
    if "azp" in claims and claims["azp"] != client_id:
        raise OIDCError("ID token azp is not this client")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
        raise OIDCError("ID token nonce does not match this login")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OIDCError("ID token has no subject")
    return claims


async def validate_with_jwks(
    id_token: str, doc: dict, *, issuer: str, client_id: str, nonce: str,
) -> dict:
    """``validate_id_token`` against the IdP's JWKS, refetching once when the kid is new (key rotation)."""
    jwks = await fetch_jwks(doc["jwks_uri"])
    try:
        return validate_id_token(id_token, jwks=jwks, issuer=issuer, client_id=client_id, nonce=nonce)
    except OIDCError as exc:
        if "no usable" not in str(exc):
            raise
    jwks = await fetch_jwks(doc["jwks_uri"], force=True)
    return validate_id_token(id_token, jwks=jwks, issuer=issuer, client_id=client_id, nonce=nonce)


def claims_to_attributes(claims: dict, groups_claim: Optional[str]) -> tuple[str, dict[str, list[str]], Optional[bool]]:
    """(email, SAML-shaped attributes, email_verified) for ``jit_provision_or_link``."""
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise OIDCError("ID token carries no email claim (add the 'email' scope)")
    attributes: dict[str, list[str]] = {"email": [email]}
    name = claims.get("name") or claims.get("preferred_username")
    if isinstance(name, str) and name:
        attributes["name"] = [name]
    key = groups_claim or DEFAULT_GROUPS_CLAIM
    raw_groups = claims.get(key)
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    if isinstance(raw_groups, list):
        attributes[key] = [str(g) for g in raw_groups if isinstance(g, (str, int)) and str(g)]
    verified = claims.get("email_verified")
    if isinstance(verified, str):
        verified = verified.lower() == "true"
    return email, attributes, verified if isinstance(verified, bool) else None
