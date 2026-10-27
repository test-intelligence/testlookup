"""SAML single logout, IdP-initiated (W20 ADM-08c).

The IdP sends a ``LogoutRequest`` to ``/api/v1/sso/slo`` when the user signs out there:

* **HTTP-Redirect** (``GET ?SAMLRequest=…&RelayState=…&SigAlg=…&Signature=…``): the signature covers the
  query string as received (SAML Bindings 3.4.4.1), so it is checked over the raw, still-encoded octets;
* **HTTP-POST** (form ``SAMLRequest``): the ``LogoutRequest`` itself carries an XML-DSig signature, checked by
  the same verifier as the ACS (``_verify_xml_signature``), and claims are read only from the signed element.

Either way it must be signed by the configured IdP certificate with a SHA-2 algorithm, come from the configured
IdP (``Issuer``), be addressed here (``Destination``, when present), be current (``IssueInstant`` within 10
minutes, ``NotOnOrAfter`` not passed) and be seen once (its ``ID`` is claimed in the replay cache). Only then is
the ``NameID`` mapped through ``federated_identities`` to a user whose sessions are ended.

The answer is an UNSIGNED ``LogoutResponse`` (HTTP-Redirect) to the configured ``idp_slo_url``: the owner's
default is IdP-initiated SLO with no SP key management (SP-initiated SLO, which needs an SP signing key, is out).
"""
from __future__ import annotations

import base64
import secrets
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote_plus, urlencode

import defusedxml.ElementTree as SafeET

from app.core.config import settings

_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_MAX_REQUEST_BYTES = 200_000
_CLOCK_SKEW = timedelta(minutes=10)
REPLAY_TTL_SECONDS = 1200
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"

# Redirect-binding SigAlg values accepted (SHA-2 only; rsa-sha1 is refused).
_SIG_ALGS = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": ("rsa", "sha256"),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": ("rsa", "sha384"),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": ("rsa", "sha512"),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256": ("ec", "sha256"),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384": ("ec", "sha384"),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512": ("ec", "sha512"),
}


def slo_url() -> str:
    return f"{settings.SAML_BASE_URL.rstrip('/')}/api/v1/sso/slo"


def _raw_query_params(raw_query: str) -> dict[str, str]:
    """name -> the value AS RECEIVED (still URL-encoded); the first occurrence wins."""
    out: dict[str, str] = {}
    for part in raw_query.split("&"):
        name, _, value = part.partition("=")
        if name and name not in out:
            out[name] = value
    return out


def _verify_redirect_signature(raw_query: str, idp_certificate_pem: str) -> bytes:
    """The inflated LogoutRequest XML after the Redirect-binding signature verified; ``ValueError`` otherwise."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    raw = _raw_query_params(raw_query)
    if "SAMLRequest" not in raw:
        raise ValueError("no SAMLRequest")
    if "Signature" not in raw or "SigAlg" not in raw:
        raise ValueError("the LogoutRequest is not signed")
    sig_alg = unquote_plus(raw["SigAlg"])
    if sig_alg not in _SIG_ALGS:
        raise ValueError(f"signature algorithm {sig_alg!r} is not allowed")
    # SAML Bindings 3.4.4.1: SAMLRequest=..[&RelayState=..]&SigAlg=.., the values exactly as sent.
    octets = f"SAMLRequest={raw['SAMLRequest']}"
    if "RelayState" in raw:
        octets += f"&RelayState={raw['RelayState']}"
    octets += f"&SigAlg={raw['SigAlg']}"
    try:
        signature = base64.b64decode(unquote_plus(raw["Signature"]), validate=True)
    except ValueError as exc:
        raise ValueError("the signature is not base64") from exc
    kind, digest = _SIG_ALGS[sig_alg]
    algorithm = {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512()}[digest]
    key = x509.load_pem_x509_certificate(idp_certificate_pem.encode()).public_key()
    try:
        if kind == "rsa" and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, octets.encode("ascii"), padding.PKCS1v15(), algorithm)
        elif kind == "ec" and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, octets.encode("ascii"), ec.ECDSA(algorithm))
        else:
            raise ValueError("the signature algorithm does not match the IdP certificate's key")
    except InvalidSignature as exc:
        raise ValueError("the LogoutRequest signature does not verify against the IdP certificate") from exc
    encoded = unquote_plus(raw["SAMLRequest"])
    if len(encoded) > _MAX_REQUEST_BYTES:
        raise ValueError("SAMLRequest too large")
    try:
        inflater = zlib.decompressobj(-15)
        xml = inflater.decompress(base64.b64decode(encoded, validate=True), _MAX_REQUEST_BYTES)
    except (ValueError, zlib.error) as exc:
        raise ValueError("SAMLRequest is not DEFLATE + base64") from exc
    if inflater.unconsumed_tail:
        raise ValueError("SAMLRequest too large")
    return xml


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"bad timestamp {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _claims(root) -> dict:
    """The LogoutRequest's fields from an (already trusted) element — ElementTree or lxml."""
    if root.tag != f"{{{_SAMLP}}}LogoutRequest":
        raise ValueError("not a LogoutRequest")
    issuer = root.find(f"{{{_SAML}}}Issuer")
    name_id = root.find(f"{{{_SAML}}}NameID")
    if name_id is None or not (name_id.text or "").strip():
        raise ValueError("the LogoutRequest has no NameID (an encrypted NameID is not supported)")
    return {
        "id": root.get("ID"),
        "issue_instant": root.get("IssueInstant"),
        "destination": root.get("Destination"),
        "not_on_or_after": root.get("NotOnOrAfter"),
        "issuer": (issuer.text or "").strip() if issuer is not None else None,
        "name_id": name_id.text.strip(),
        "session_indexes": [(e.text or "").strip() for e in root.findall(f"{{{_SAMLP}}}SessionIndex")],
    }


def verify_logout_request(
    *, binding: str, idp_certificate_pem: str, raw_query: str = "", form_saml_request: Optional[str] = None,
) -> dict:
    """The LogoutRequest's claims after the signature verified (``binding`` = "redirect" | "post")."""
    if not idp_certificate_pem or not idp_certificate_pem.strip():
        raise ValueError("no IdP certificate is configured")
    if binding == "redirect":
        xml = _verify_redirect_signature(raw_query, idp_certificate_pem)
        try:
            root = SafeET.fromstring(xml)
        except Exception as exc:  # noqa: BLE001 - defusedxml raises several types
            raise ValueError(f"malformed LogoutRequest: {exc}") from exc
        return _claims(root)
    if binding == "post":
        if not form_saml_request or len(form_saml_request) > _MAX_REQUEST_BYTES:
            raise ValueError("missing or oversized SAMLRequest")
        try:
            xml = base64.b64decode(form_saml_request, validate=True)
        except ValueError as exc:
            raise ValueError("SAMLRequest is not base64") from exc
        from app.services.sso_service import _verify_xml_signature

        # Claims come only from the signed subtree, and it must be the LogoutRequest itself (XSW).
        return _claims(_verify_xml_signature(xml, idp_certificate_pem))
    raise ValueError(f"unknown binding {binding!r}")


def check_logout_request(claims: dict, *, idp_entity_id: Optional[str], now: Optional[datetime] = None) -> None:
    """Issuer, Destination, freshness. ``ValueError`` on any mismatch."""
    now = now or datetime.now(timezone.utc)
    if not claims.get("id"):
        raise ValueError("the LogoutRequest has no ID")
    if claims.get("issuer") != idp_entity_id:
        raise ValueError(f"issuer {claims.get('issuer')!r} is not the configured IdP")
    if claims.get("destination") and claims["destination"] != slo_url():
        raise ValueError(f"destination {claims['destination']!r} is not this SP's SLO URL")
    issued = _parse_time(claims.get("issue_instant"))
    if issued is None or abs(now - issued) > _CLOCK_SKEW:
        raise ValueError("the LogoutRequest is not current (IssueInstant)")
    expires = _parse_time(claims.get("not_on_or_after"))
    if expires is not None and now >= expires + timedelta(seconds=60):
        raise ValueError("the LogoutRequest has expired (NotOnOrAfter)")


def logout_response_url(*, idp_slo_url: str, in_response_to: str, sp_entity_id: Optional[str],
                        relay_state: Optional[str]) -> str:
    """The IdP's SLO URL carrying an unsigned Success LogoutResponse (HTTP-Redirect binding)."""
    from xml.sax.saxutils import quoteattr, escape

    from app.services.sso_service import encode_redirect_binding

    instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        f'<samlp:LogoutResponse xmlns:samlp="{_SAMLP}" xmlns:saml="{_SAML}" ID="_{secrets.token_hex(16)}" '
        f'Version="2.0" IssueInstant="{instant}" Destination={quoteattr(idp_slo_url)} '
        f"InResponseTo={quoteattr(in_response_to)}>"
        f"<saml:Issuer>{escape(sp_entity_id or settings.SAML_SP_ENTITY_ID)}</saml:Issuer>"
        f'<samlp:Status><samlp:StatusCode Value="{STATUS_SUCCESS}"/></samlp:Status>'
        f"</samlp:LogoutResponse>"
    )
    params = {"SAMLResponse": encode_redirect_binding(xml.encode())}
    if relay_state:
        params["RelayState"] = relay_state[:512]
    return f"{idp_slo_url}{'&' if '?' in idp_slo_url else '?'}{urlencode(params)}"
