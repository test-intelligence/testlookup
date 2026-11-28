"""SSO / SAML service — configuration management, assertion validation, JIT provisioning."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import defusedxml.ElementTree as SafeET
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from cryptography.x509 import Certificate

# signxml is the XML-DSig verifier. We import it lazily inside the verify
# function so that environments without the package (e.g., minimal dev
# containers, unit tests for unrelated code paths) can still import this
# module. The actual SAML code path will fail CLOSED — refusing to process
# any assertion — if signxml is missing, not silently allowing unsigned
# assertions through.

from app.core.config import settings
from app.core.security import get_password_hash
from app.core.permissions import EXTRA_ROLE_CHOICES, LADDER, job_roles_in, rank_index
from app.models.postgres import (
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    SSOConfiguration,
    SSOEnforcementMode,
    SSOProviderType,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)

_SSO_CONNECTION_TIMEOUT_SECONDS = 5.0


async def probe_idp_endpoint(url: str) -> tuple[bool, str]:
    """Perform a bounded reachability check without following redirects."""
    from app.core.http_client import get_http_client, get_public_http_client
    from app.services.url_safety import assert_public_url, is_unsafe_target_error

    try:
        if not settings.SSO_ALLOW_PRIVATE_IDP_ENDPOINTS:
            await assert_public_url(url)
        client = (
            get_http_client()
            if settings.SSO_ALLOW_PRIVATE_IDP_ENDPOINTS
            else get_public_http_client()
        )
        response = await client.get(
            url,
            timeout=_SSO_CONNECTION_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        if response.status_code < 500:
            return True, f"IdP endpoint reachable (HTTP {response.status_code})"
        return False, f"IdP endpoint returned HTTP {response.status_code}"
    except ValueError as exc:
        return False, f"IdP endpoint blocked by SSRF policy: {exc}"
    except Exception as exc:  # noqa: BLE001 - connection failures become test results
        if is_unsafe_target_error(exc):
            return False, f"IdP endpoint blocked by SSRF policy: {exc}"
        return False, f"IdP endpoint unreachable: {str(exc)[:200]}"


# ── Certificate helpers ──────────────────────────────────────────────────────


def certificate_fingerprint(pem_cert: str) -> str:
    """Return SHA-256 fingerprint of a PEM-encoded X.509 certificate."""
    # Strip PEM headers and decode
    lines = [
        line.strip()
        for line in pem_cert.strip().splitlines()
        if line.strip() and not line.strip().startswith("-----")
    ]
    import base64

    try:
        der_bytes = base64.b64decode("".join(lines))
    except Exception:
        return "invalid-certificate"
    return hashlib.sha256(der_bytes).hexdigest()


def _load_x509_certificate(pem_cert: str) -> "Certificate":
    """Load a PEM certificate while keeping cryptography imports local."""
    from cryptography import x509

    return x509.load_pem_x509_certificate(pem_cert.strip().encode("utf-8"))


def _certificate_validity(cert: "Certificate") -> tuple[datetime, datetime]:
    """Return timezone-aware X.509 validity bounds across cryptography versions."""
    not_before = getattr(cert, "not_valid_before_utc", None)
    if not_before is None:
        not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = getattr(cert, "not_valid_after_utc", None)
    if not_after is None:
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return not_before, not_after


def certificate_expiration(pem_cert: str) -> datetime | None:
    """Return the certificate expiry, or None when the payload cannot be parsed."""
    try:
        _, not_after = _certificate_validity(_load_x509_certificate(pem_cert))
        return not_after
    except (TypeError, ValueError):
        return None


def validate_certificate_format(pem_cert: str) -> tuple[bool, str]:
    """Validate X.509 structure and its current validity window."""
    stripped = pem_cert.strip()
    if not stripped.startswith("-----BEGIN CERTIFICATE-----"):
        return False, "Certificate must start with '-----BEGIN CERTIFICATE-----'"
    if not stripped.endswith("-----END CERTIFICATE-----"):
        return False, "Certificate must end with '-----END CERTIFICATE-----'"
    try:
        cert = _load_x509_certificate(stripped)
    except (TypeError, ValueError):
        return False, "Certificate is not a valid X.509 certificate"
    not_before, not_after = _certificate_validity(cert)
    now = datetime.now(timezone.utc)
    if now < not_before:
        return False, f"Certificate is not valid until {not_before.isoformat()}"
    if now >= not_after:
        return False, f"Certificate expired at {not_after.isoformat()}"
    return True, "Valid"


# ── SAML assertion parsing ───────────────────────────────────────────────────

# SAML namespace map
_NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}


class SignedAssertionRefused(ValueError):
    """A SAML response whose signature verified, refused for what it says.

    ``parse_saml_response`` raises a plain ``ValueError`` for anything it
    refuses before the signature check passes (anyone can send those, so the
    ACS only logs them) and this for a refusal after it (which the ACS audits).
    """


def _verify_xml_signature(xml_bytes: bytes, idp_certificate_pem: str):
    """
    Cryptographically verify an XML-DSig signature on a SAML Response using
    the configured IdP X.509 certificate.

    Returns the signed lxml element — the ONLY node from which the caller
    may safely extract identity claims. This defeats XML Signature Wrapping
    (XSW) attacks where an attacker keeps a signed element intact but wraps
    a forged assertion around it; signxml returns exclusively the signed
    subtree, so claims read from it are guaranteed to be IdP-attested.

    Raises ``ValueError`` on any verification failure, including:
      - signxml not installed (fail-closed; refuse to process the assertion)
      - signature missing, malformed, or broken
      - certificate mismatch with the configured IdP cert
      - algorithm not on the allow-list

    We restrict the allowed digest / signature algorithms to modern
    primitives so a hostile IdP (or a downgrade attack) cannot force the
    verifier to accept SHA-1 or MD5-signed assertions.
    """
    try:
        from lxml import etree  # noqa: PLC0415
        from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod, XMLVerifier  # noqa: PLC0415
        from signxml.exceptions import InvalidSignature, InvalidCertificate  # noqa: PLC0415
    except ImportError as exc:
        # Fail closed: we cannot verify, so we cannot trust the assertion.
        # Operators must install signxml or disable SSO entirely.
        raise ValueError(
            "SAML signature verification unavailable: signxml not installed. "
            "Refusing to process SAML assertion."
        ) from exc

    # lxml parser is DTD/entity-safe by default when we pass these flags.
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(xml_bytes, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Malformed SAML XML: {exc}") from exc

    allowed_digest = {
        DigestAlgorithm.SHA256,
        DigestAlgorithm.SHA384,
        DigestAlgorithm.SHA512,
    }
    allowed_sig = {
        SignatureMethod.RSA_SHA256,
        SignatureMethod.RSA_SHA384,
        SignatureMethod.RSA_SHA512,
        SignatureMethod.ECDSA_SHA256,
        SignatureMethod.ECDSA_SHA384,
        SignatureMethod.ECDSA_SHA512,
    }

    try:
        # signxml 5 (requirements pin 5.1.0): the allow-list is a SignatureConfiguration. The former
        # ``XMLVerifier.VerifyConfig`` does not exist there, so every signed assertion raised and every real
        # SAML sign-in was refused (found in W20 ADM-08c, the first test with a real signature).
        verified = XMLVerifier().verify(
            root,
            x509_cert=idp_certificate_pem,
            # Pin the allow-list so SHA-1 / MD5-based signatures are rejected.
            expect_config=SignatureConfiguration(
                expect_references=1,
                signature_methods=frozenset(allowed_sig),
                digest_algorithms=frozenset(allowed_digest),
            ),
        )
    except (InvalidSignature, InvalidCertificate) as exc:
        raise ValueError(f"SAML signature verification failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — defensive: signxml raises diverse types
        raise ValueError(f"SAML signature verification failed: {exc}") from exc

    signed_xml = getattr(verified, "signed_xml", None)
    if signed_xml is None:
        raise ValueError("SAML signature verification returned no signed subtree")
    return signed_xml


def parse_saml_response(
    saml_response_b64: str,
    idp_certificate_pem: str,
) -> dict:
    """
    Parse a base64-encoded SAML Response, verify its signature, and extract
    identity claims **only from the cryptographically-verified subtree**.

    ``idp_certificate_pem`` is the PEM-encoded X.509 certificate stored on
    the ``SSOConfiguration`` record. It is **required** — a falsy value
    raises immediately so the parser is fail-closed regardless of caller
    (an empty/misconfigured cert can never silently downgrade to an unsigned
    parse). Structural unit tests that need to parse without a real cert must
    call :func:`parse_saml_response_unverified` explicitly.

    Returns a dict with keys: name_id, issuer, attributes, session_index,
    audiences, recipient, in_response_to, assertion_id, not_on_or_after,
    conditions_present. Replay / audience / recipient binding is enforced
    separately by :func:`enforce_saml_security` (which needs the config).

    Raises ValueError on malformed/invalid/unsigned responses, on a missing
    or empty certificate, or when the signed subtree does not contain exactly
    one Assertion / Subject / NameID (XML Signature Wrapping defence). A
    refusal after the signature verified is a :class:`SignedAssertionRefused`.
    """
    import base64

    if not idp_certificate_pem or not str(idp_certificate_pem).strip():
        # Fail closed: without a cert we cannot verify the signature, so we
        # refuse to process the assertion rather than trust unsigned claims.
        raise ValueError(
            "SAML signature verification requires an IdP certificate; "
            "refusing to process assertion without one."
        )

    try:
        xml_bytes = base64.b64decode(saml_response_b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 in SAMLResponse: {exc}") from exc

    # Verify the XML-DSig signature and extract claims ONLY from the signed
    # subtree. Anything outside that tree is untrusted and never read.
    signed = _verify_xml_signature(xml_bytes, idp_certificate_pem)
    try:
        xml_str = _lxml_to_bytes(signed)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to serialize verified SAML XML: {exc}") from exc

    try:
        root = SafeET.fromstring(xml_str)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Malformed SAML XML: {exc}") from exc

    try:
        return _extract_saml_claims(root)
    except ValueError as exc:
        raise SignedAssertionRefused(str(exc)) from exc


def parse_saml_response_unverified(saml_response_b64: str) -> dict:
    """
    Structural-only parse with **no signature verification**.

    This exists exclusively for unit tests that exercise XML extraction in
    isolation. Production code paths (``routers/sso.py``) MUST call
    :func:`parse_saml_response` with the configured IdP certificate.
    """
    import base64

    try:
        xml_bytes = base64.b64decode(saml_response_b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 in SAMLResponse: {exc}") from exc
    try:
        root = SafeET.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Malformed SAML XML: {exc}") from exc
    return _extract_saml_claims(root)


def _extract_saml_claims(root) -> dict:
    """
    Extract and time-validate identity claims from a parsed SAML tree.

    ``root`` is either a ``samlp:Response`` wrapper or a bare ``saml:Assertion``
    (when the IdP signs the assertion directly). Closes the XML Signature
    Wrapping residual by asserting **exactly one** Assertion and **exactly
    one** Subject/NameID inside the (already signature-verified) tree, and by
    reading every claim from that single assertion element.
    """
    # Status check (only present on a Response wrapper)
    status_code = root.find(".//samlp:Status/samlp:StatusCode", _NS)
    if status_code is not None:
        status_value = status_code.get("Value", "")
        if "Success" not in status_value:
            raise ValueError(f"SAML authentication failed with status: {status_value}")

    # Resolve the single Assertion. XSW defence: reject 0 or >1 assertions.
    if root.tag.endswith("Assertion"):
        assertion = root
        nested = root.findall(".//saml:Assertion", _NS)
        if nested:
            raise ValueError("SAML Assertion contains nested assertions — rejecting")
    else:
        assertions = root.findall(".//saml:Assertion", _NS)
        if len(assertions) == 0:
            raise ValueError("No Assertion found in SAML Response")
        if len(assertions) > 1:
            raise ValueError(
                f"Expected exactly one SAML Assertion, found {len(assertions)} — rejecting"
            )
        assertion = assertions[0]

    assertion_id = assertion.get("ID")

    # Issuer — read from the assertion itself (inside the signed subtree).
    issuer_el = assertion.find("saml:Issuer", _NS)
    if issuer_el is None:
        issuer_el = root.find(".//saml:Issuer", _NS)
    issuer = issuer_el.text.strip() if issuer_el is not None and issuer_el.text else None

    # Subject / NameID — assert exactly one Subject and one NameID.
    subjects = assertion.findall(".//saml:Subject", _NS)
    if len(subjects) != 1:
        raise ValueError(
            f"Expected exactly one SAML Subject, found {len(subjects)} — rejecting"
        )
    name_id_els = subjects[0].findall("saml:NameID", _NS)
    if len(name_id_els) != 1:
        raise ValueError(
            f"Expected exactly one SAML NameID, found {len(name_id_els)} — rejecting"
        )
    name_id = name_id_els[0].text.strip() if name_id_els[0].text else None
    if not name_id:
        raise ValueError("No NameID found in SAML Assertion")

    # SubjectConfirmationData carries Recipient / InResponseTo / NotOnOrAfter
    # used for response binding (replay protection).
    recipient = None
    subject_in_response_to = None
    subject_not_on_or_after = None
    scd = subjects[0].find(
        "saml:SubjectConfirmation/saml:SubjectConfirmationData", _NS
    )
    if scd is not None:
        recipient = scd.get("Recipient")
        subject_in_response_to = scd.get("InResponseTo")
        subject_not_on_or_after = scd.get("NotOnOrAfter")

    # InResponseTo may also appear on the Response wrapper.
    response_in_response_to = root.get("InResponseTo") if not root.tag.endswith("Assertion") else None
    in_response_to = response_in_response_to or subject_in_response_to

    # Session index
    authn_stmt = assertion.find(".//saml:AuthnStatement", _NS)
    session_index = authn_stmt.get("SessionIndex") if authn_stmt is not None else None

    # Attributes
    attributes: dict[str, list[str]] = {}
    attr_stmt = assertion.find(".//saml:AttributeStatement", _NS)
    if attr_stmt is not None:
        for attr in attr_stmt.findall("saml:Attribute", _NS):
            attr_name = attr.get("Name", "")
            values = [
                v.text.strip()
                for v in attr.findall("saml:AttributeValue", _NS)
                if v.text
            ]
            if attr_name and values:
                attributes[attr_name] = values

    # Conditions: time-window validation + audience extraction. The presence
    # of <Conditions> is required (enforced by enforce_saml_security); here we
    # validate the window when present and surface what we found.
    conditions = assertion.find(".//saml:Conditions", _NS)
    conditions_present = conditions is not None
    not_on_or_after = None
    audiences: list[str] = []
    if conditions is not None:
        not_before = conditions.get("NotBefore")
        not_on_or_after = conditions.get("NotOnOrAfter")
        now = datetime.now(timezone.utc)
        # Tolerate bounded IdP/SP clock drift so minor NTP skew doesn't reject
        # otherwise-valid assertions at the time-window edges.
        skew = timedelta(seconds=max(0, settings.SAML_CLOCK_SKEW_SECONDS))
        if not_before:
            nb = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if now < nb - skew:
                raise ValueError("SAML Assertion is not yet valid (NotBefore)")
        if not_on_or_after:
            noa = datetime.fromisoformat(not_on_or_after.replace("Z", "+00:00"))
            if now >= noa + skew:
                raise ValueError("SAML Assertion has expired (NotOnOrAfter)")
        for aud_el in conditions.findall(
            "saml:AudienceRestriction/saml:Audience", _NS
        ):
            if aud_el.text and aud_el.text.strip():
                audiences.append(aud_el.text.strip())

    return {
        "name_id": name_id,
        "issuer": issuer,
        "attributes": attributes,
        "session_index": session_index,
        "audiences": audiences,
        "recipient": recipient,
        "in_response_to": in_response_to,
        "assertion_id": assertion_id,
        "not_on_or_after": not_on_or_after,
        "subject_not_on_or_after": subject_not_on_or_after,
        "conditions_present": conditions_present,
    }


def _lxml_to_bytes(element) -> bytes:
    """Serialize an lxml element to XML bytes."""
    from lxml import etree  # noqa: PLC0415
    return etree.tostring(element)


# ── SAML response binding / anti-replay (Redis-backed) ───────────────────────

_SAML_REQUEST_PREFIX = "saml:authnreq:"
_SAML_ASSERTION_PREFIX = "saml:assertion:"
# An SP-initiated login must round-trip through the IdP within this window.
_SAML_REQUEST_TTL_SECONDS = 600  # 10 minutes
# What is stored for a request that was started without a browser cookie
# (SAML_BROWSER_BINDING off). It is not a SHA-256, so no cookie ever matches it.
_SAML_REQUEST_UNBOUND = "1"


def browser_binding_hash(token: str) -> str:
    """What the server keeps of a sign-in cookie: its SHA-256, never the value."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def browser_binding_matches(token: Optional[str], stored_hash: object) -> bool:
    """Whether ``token`` (the cookie the browser sent) is the one whose hash was
    stored when the sign-in started. Constant-time; nothing matches a missing
    cookie or a sign-in that was stored without a hash."""
    if not token or not isinstance(stored_hash, str) or len(stored_hash) != 64:
        return False
    return hmac.compare_digest(browser_binding_hash(token), stored_hash)


async def remember_saml_request(
    request_id: str, ttl: int = _SAML_REQUEST_TTL_SECONDS, *, browser_hash: Optional[str] = None,
) -> None:
    """Persist a freshly-minted SP-initiated request id so the ACS can later
    bind the IdP's response to it (single-use), together with the hash of the
    cookie set in the browser that started the sign-in (``browser_hash``)."""
    if not request_id:
        return
    from app.db.redis_client import get_redis

    redis = get_redis()
    await redis.set(f"{_SAML_REQUEST_PREFIX}{request_id}", browser_hash or _SAML_REQUEST_UNBOUND, ex=ttl)


async def consume_saml_request(request_id: str) -> Optional[str]:
    """Atomically consume a stored SP-initiated request id.

    Returns what was stored with it (the browser cookie's hash, or the
    "started without a cookie" marker) when the id existed and was pending; it
    is now burned, so it can never be replayed. Returns None otherwise. Fails
    CLOSED — a Redis error raises so the ACS rejects the assertion rather than
    silently skipping the binding check.
    """
    if not request_id:
        return None
    from app.db.redis_client import get_redis

    redis = get_redis()
    stored = await _getdel(redis, f"{_SAML_REQUEST_PREFIX}{request_id}")
    if isinstance(stored, bytes):
        stored = stored.decode("utf-8", "replace")
    return stored if isinstance(stored, str) and stored else None


async def _getdel(redis, key: str):
    """Read and delete ``key`` in one step, so two concurrent callers can never
    both take the same single-use value."""
    try:
        return await redis.getdel(key)
    except AttributeError:
        # redis-py without GETDEL (< 4.0): do get+delete atomically server-side
        # via EVAL (supported since Redis 2.6).
        return await redis.eval(
            "local v = redis.call('get', KEYS[1]); "
            "if v then redis.call('del', KEYS[1]) end; return v",
            1,
            key,
        )


# ── SP-initiated login: the AuthnRequest (HTTP-Redirect binding) ─────────────
#
# /sso/login-url used to send the browser to the bare IdP SSO URL. With no
# AuthnRequest the IdP's response carried no InResponseTo, so the ACS refused
# it unless SAML_ALLOW_IDP_INITIATED was on, and the request id it minted was
# never seen by the IdP.

_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_HTTP_POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
_NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
_RELAY_STATE_MAX = 512
ElementTree.register_namespace("samlp", _SAMLP_NS)
ElementTree.register_namespace("saml", _SAML_NS)


def build_authn_request(
    *,
    request_id: str,
    destination: str,
    acs_url: str,
    sp_entity_id: str,
    issue_instant: Optional[datetime] = None,
) -> bytes:
    """The SP's ``<samlp:AuthnRequest>``.

    Its ``ID`` is the request id the ACS later consumes, so the IdP's
    ``InResponseTo`` binds its response to this login; the IdP posts that
    response to ``acs_url`` (HTTP-POST binding). Unsigned: IdPs that require
    signed requests need an SP key, which this deployment does not hold.
    """
    instant = (issue_instant or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = ElementTree.Element(
        f"{{{_SAMLP_NS}}}AuthnRequest",
        {
            "ID": request_id,
            "Version": "2.0",
            "IssueInstant": instant,
            "Destination": destination,
            "AssertionConsumerServiceURL": acs_url,
            "ProtocolBinding": _HTTP_POST_BINDING,
        },
    )
    ElementTree.SubElement(root, f"{{{_SAML_NS}}}Issuer").text = sp_entity_id
    ElementTree.SubElement(
        root, f"{{{_SAMLP_NS}}}NameIDPolicy", {"Format": _NAMEID_EMAIL, "AllowCreate": "true"}
    )
    return ElementTree.tostring(root)


def encode_redirect_binding(xml: bytes) -> str:
    """HTTP-Redirect binding: raw DEFLATE, then base64 (SAML Bindings 3.4.4.1)."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.b64encode(compressor.compress(xml) + compressor.flush()).decode("ascii")


def safe_relay_state(raw: object) -> Optional[str]:
    """Where the app lands after sign-in: an app-relative path, or nothing.

    A RelayState round-trips through the IdP, so the ACS checks it again when it
    comes back: never an absolute or protocol-relative URL (an open redirect).
    """
    if not isinstance(raw, str) or not raw or len(raw) > _RELAY_STATE_MAX:
        return None
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    return raw


def saml_redirect_url(idp_sso_url: str, authn_request: bytes, relay_state: Optional[str] = None) -> str:
    """The IdP SSO URL carrying the encoded AuthnRequest (and RelayState)."""
    params = {"SAMLRequest": encode_redirect_binding(authn_request)}
    if relay_state:
        params["RelayState"] = relay_state
    separator = "&" if urlsplit(idp_sso_url).query else ("" if idp_sso_url.endswith("?") else "?")
    return f"{idp_sso_url}{separator}{urlencode(params)}"


# ── The browser's way back into the app ──────────────────────────────────────
#
# The IdP form-POSTs its response to /sso/acs. That request comes from the
# browser, so the ACS cannot answer it with JSON: the tokens are parked here
# under a one-time code and the browser is sent to the SPA, which exchanges the
# code (once, within a minute) for them. Tokens never ride in a URL.

_SSO_HANDOFF_PREFIX = "sso:handoff:"
_SSO_HANDOFF_TTL_SECONDS = 60
_SSO_HANDOFF_CODE_MAX = 128


async def stash_sso_login(payload: dict) -> str:
    """Park a completed SSO sign-in under a new one-time code and return the code."""
    from app.db.redis_client import get_redis

    code = secrets.token_urlsafe(32)
    await get_redis().set(
        f"{_SSO_HANDOFF_PREFIX}{code}", json.dumps(payload), ex=_SSO_HANDOFF_TTL_SECONDS
    )
    return code


async def claim_sso_login(code: str) -> Optional[dict]:
    """The parked sign-in for ``code``, at most once; None when unknown, used or expired."""
    if not code or len(code) > _SSO_HANDOFF_CODE_MAX:
        return None
    from app.db.redis_client import get_redis

    raw = await _getdel(get_redis(), f"{_SSO_HANDOFF_PREFIX}{code}")
    return json.loads(raw) if raw else None


def _saml_instant(raw: str, what: str) -> datetime:
    """A SAML ``xs:dateTime`` attribute as an aware UTC datetime (ValueError when it is not one)."""
    try:
        instant = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"SAML Assertion {what} is not a valid time") from exc
    return instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)


def _assertion_ttl_seconds(expires_at: datetime, skew: timedelta) -> int:
    """Seconds to retain an assertion id in the single-use cache: a minute past
    the last moment the assertion is accepted (its expiry plus the clock skew),
    so a replay can never outlive its record. ``enforce_saml_security`` has
    already bounded the expiry by SAML_MAX_ASSERTION_LIFETIME_SECONDS."""
    remaining = expires_at + skew - datetime.now(timezone.utc)
    return max(1, int(remaining.total_seconds()) + 60)


async def claim_assertion_id(assertion_id: str, ttl: int) -> bool:
    """Atomically claim an assertion id as used. Returns True if this is the
    first time we've seen it (fresh), False if it's a replay. Fails CLOSED."""
    from app.db.redis_client import get_redis

    redis = get_redis()
    ok = await redis.set(
        f"{_SAML_ASSERTION_PREFIX}{assertion_id}", "1", ex=max(1, ttl), nx=True
    )
    return bool(ok)


async def enforce_saml_security(
    parsed: dict, config: SSOConfiguration, browser_token: Optional[str] = None,
) -> None:
    """Validate the response-binding and anti-replay properties of a
    signature-verified assertion. Raises ValueError on any failure.

    ``browser_token`` is the sign-in cookie the browser sent to the ACS.

    Checks, in order:
      1. ``<Conditions>`` present with a ``NotOnOrAfter``; the assertion stops
         being usable (the earlier of that and ``SubjectConfirmationData``'s
         ``NotOnOrAfter``) within SAML_MAX_ASSERTION_LIFETIME_SECONDS plus the
         clock skew.
      2. ``AudienceRestriction`` contains this SP (blocks cross-SP reuse).
      3. ``Recipient`` is present and matches the configured ACS URL.
      4. ``InResponseTo`` matches and consumes a pending SP-initiated request,
         and the response came back through the browser that started it (or,
         when ``InResponseTo`` is absent, IdP-initiated must be explicitly
         enabled; such a response has no starting browser).
      5. The assertion ID has not been seen before (single-use replay cache).
    """
    if not parsed.get("conditions_present"):
        raise ValueError(
            "SAML Assertion has no <Conditions> — refusing an assertion with no bounded validity window"
        )

    # 1. A bounded, short validity window.
    if not parsed.get("not_on_or_after"):
        raise ValueError(
            "SAML Assertion has no NotOnOrAfter in <Conditions> — refusing an assertion that never expires"
        )
    now = datetime.now(timezone.utc)
    skew = timedelta(seconds=max(0, settings.SAML_CLOCK_SKEW_SECONDS))
    expires_at = _saml_instant(parsed["not_on_or_after"], "Conditions NotOnOrAfter")
    if parsed.get("subject_not_on_or_after"):
        expires_at = min(
            expires_at,
            _saml_instant(parsed["subject_not_on_or_after"], "SubjectConfirmationData NotOnOrAfter"),
        )
    if now >= expires_at + skew:
        raise ValueError("SAML Assertion has expired (NotOnOrAfter)")
    max_lifetime = timedelta(seconds=settings.SAML_MAX_ASSERTION_LIFETIME_SECONDS)
    if expires_at - now > max_lifetime + skew:
        raise ValueError(
            "SAML Assertion stays valid for longer than SAML_MAX_ASSERTION_LIFETIME_SECONDS "
            f"({settings.SAML_MAX_ASSERTION_LIFETIME_SECONDS} s, plus the clock skew) allows; shorten the "
            "assertion lifetime at the identity provider or raise the setting"
        )

    # 2. Audience restriction must name this SP.
    audiences = set(parsed.get("audiences") or [])
    expected = {a for a in (config.audience, config.sp_entity_id) if a}
    if not audiences:
        raise ValueError("SAML Assertion has no AudienceRestriction")
    if expected and not (audiences & expected):
        raise ValueError("SAML Assertion audience does not match this service provider")

    # 3. Recipient (SubjectConfirmationData) must be present and match the ACS URL.
    recipient = parsed.get("recipient")
    expected_acs = (config.sp_acs_url or "").rstrip("/")
    if not recipient:
        raise ValueError("SAML Assertion has no Recipient in SubjectConfirmationData")
    if expected_acs and recipient.rstrip("/") != expected_acs:
        raise ValueError("SAML Assertion Recipient does not match the configured ACS URL")

    # 4. Response binding (anti-replay leg #1): consume the SP-initiated request,
    #    then require the cookie of the browser that started it. The request id
    #    is spent first, so a refused response cannot be tried with another cookie.
    in_response_to = parsed.get("in_response_to")
    if in_response_to:
        stored = await consume_saml_request(in_response_to)
        if not stored:
            raise ValueError(
                "SAML Assertion InResponseTo does not match a pending login request (possible replay)"
            )
        if settings.SAML_BROWSER_BINDING and not browser_binding_matches(browser_token, stored):
            raise ValueError(
                "SAML response did not come back through the browser that started the sign-in "
                "(the sign-in cookie is missing or different)"
            )
    elif not settings.SAML_ALLOW_IDP_INITIATED:
        raise ValueError(
            "SAML Assertion is missing InResponseTo and IdP-initiated SSO is disabled"
        )

    # 5. Single-use assertion id (anti-replay leg #2 — universal).
    assertion_id = parsed.get("assertion_id")
    if not assertion_id:
        raise ValueError("SAML Assertion has no ID — cannot enforce single-use")
    ttl = _assertion_ttl_seconds(expires_at, skew)
    if not await claim_assertion_id(assertion_id, ttl):
        raise ValueError("SAML Assertion has already been used (replay detected)")


def validate_saml_issuer(parsed: dict, config: SSOConfiguration) -> None:
    """Validate that the SAML issuer matches the configured IdP entity ID."""
    if parsed.get("issuer") != config.idp_entity_id:
        raise ValueError(
            f"Issuer mismatch: expected '{config.idp_entity_id}', got '{parsed.get('issuer')}'"
        )


# Role hierarchy for picking the highest match / applying the ceiling.
# The five ranks (``permissions.LADDER``). SSO and SCIM provision a rank; job
# roles are added as extra roles, never as the primary role.
_ROLE_ORDER = list(LADDER)


def _ladder_index(role) -> int:
    """Rank position of a ladder role; ``ValueError`` for anything else (a job role is not a rank)."""
    role = role if isinstance(role, UserRole) else UserRole(role)
    if role not in _ROLE_ORDER:
        raise ValueError(f"{role.value} is not a rank")
    return rank_index(role)


def _provisioning_ceiling() -> UserRole:
    """The configured maximum role an IdP/SCIM provision may grant."""
    raw = (settings.SSO_MAX_PROVISIONED_ROLE or "ADMIN").strip()
    try:
        ceiling = UserRole(raw)
        if ceiling in _ROLE_ORDER:
            return ceiling
    except (ValueError, KeyError):
        pass
    logger.warning(
        "Invalid SSO_MAX_PROVISIONED_ROLE=%r — defaulting provisioning ceiling to ADMIN",
        raw,
    )
    return UserRole.ADMIN


def resolve_role_from_groups(
    groups: list[str],
    role_mapping: dict | None,
    default_role: UserRole,
) -> UserRole:
    """Map IdP group memberships to the highest matching internal role.

    The resolved role is clamped to ``SSO_MAX_PROVISIONED_ROLE`` so a
    misconfigured ``role_mapping``/``default_role`` cannot mint an account
    above the configured ceiling via an IdP login or SCIM provision. A grant
    of QA_LEAD or higher (and any clamp) is logged at WARNING for audit.
    """
    if not role_mapping or not groups:
        resolved = default_role
    else:
        best_idx = -1
        for group in groups:
            mapped = role_mapping.get(group)
            if mapped:
                try:
                    role = UserRole(mapped)
                    idx = _ladder_index(role)
                    if idx > best_idx:
                        best_idx = idx
                except (ValueError, KeyError):
                    continue
        resolved = _ROLE_ORDER[best_idx] if best_idx >= 0 else default_role

    if resolved not in _ROLE_ORDER:
        logger.warning(
            "IdP/SCIM default role %s is not a rank — provisioning VIEWER", getattr(resolved, "value", resolved),
        )
        resolved = UserRole.VIEWER

    ceiling = _provisioning_ceiling()
    try:
        if _ladder_index(resolved) > _ladder_index(ceiling):
            logger.warning(
                "IdP/SCIM provisioning resolved role %s exceeds ceiling %s — clamping",
                resolved.value, ceiling.value,
            )
            resolved = ceiling
    except ValueError:
        pass

    try:
        if _ladder_index(resolved) >= _ladder_index(UserRole.QA_LEAD):
            logger.warning(
                "IdP/SCIM provisioning granting elevated role %s (groups=%s)",
                resolved.value, groups,
            )
    except ValueError:
        pass

    return resolved


def mapping_names_job_roles(role_mapping: dict | None) -> bool:
    """Does the mapping give any group a job role? Only then does the IdP manage a user's extra roles."""
    return bool(job_roles_in(list((role_mapping or {}).values())))


def resolve_additional_roles_from_groups(groups: list[str], role_mapping: dict | None) -> list[str]:
    """Extra job roles the user's groups map to (enterprise roles E00-S05), in enum order.

    A group mapped to RELEASE_MANAGER / PROJECT_MANAGER / PRODUCT_MANAGER /
    DEVELOPER adds that role beside the rank ``resolve_role_from_groups`` picks;
    it never competes for the rank. AGENT and anything unknown are ignored.
    """
    if not role_mapping or not groups:
        return []
    mapped = [role_mapping.get(group) for group in groups]
    return [role.value for role in job_roles_in([m for m in mapped if m])]


def _extra_role_sources(groups: list[str], role_mapping: dict | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for group in groups:
        held = job_roles_in([(role_mapping or {}).get(group)])
        if held:
            out.setdefault(held[0].value, []).append(group)
    return {role.value: out[role.value] for role in EXTRA_ROLE_CHOICES if role.value in out}


def preview_role_from_groups(
    groups: list[str],
    role_mapping: dict | None,
    default_role: UserRole,
) -> dict:
    """W20 ADM-08a: what ``resolve_role_from_groups`` would grant, with why — no logging, no side effects.

    The settings page previews a mapping before it is saved: the role, the groups that matched, whether the
    default role applied, and whether ``SSO_MAX_PROVISIONED_ROLE`` clamped the result.
    """
    best_idx, matched = -1, []
    for group in groups:
        mapped = (role_mapping or {}).get(group)
        if not mapped:
            continue
        try:
            idx = _ladder_index(UserRole(mapped))
        except (ValueError, KeyError):
            continue
        matched.append(group)
        best_idx = max(best_idx, idx)
    unclamped = _ROLE_ORDER[best_idx] if best_idx >= 0 else default_role
    ceiling = _provisioning_ceiling()
    clamped = unclamped in _ROLE_ORDER and _ladder_index(unclamped) > _ladder_index(ceiling)
    role = ceiling if clamped else unclamped
    extra_sources = _extra_role_sources(groups, role_mapping)
    return {
        "role": role.value,
        "matched_groups": matched,
        "source": "mapping" if best_idx >= 0 else "default",
        "ceiling": ceiling.value,
        "clamped": clamped,
        "additional_roles": list(extra_sources),
        "additional_matched_groups": extra_sources,
    }


# ── SSO Configuration CRUD ──────────────────────────────────────────────────


async def get_active_sso_config(db: AsyncSession) -> Optional[SSOConfiguration]:
    """Return the first active SSO configuration, or None."""
    result = await db.execute(
        select(SSOConfiguration)
        .where(SSOConfiguration.is_active == True)  # noqa: E712
        .limit(1)
    )
    return result.scalar_one_or_none()


async def is_sso_enforced(db: AsyncSession) -> bool:
    """Check if any active SSO config enforces SSO_REQUIRED mode."""
    if not settings.SSO_ENABLED:
        return False
    config = await get_active_sso_config(db)
    if config is None:
        return False
    return config.enforcement_mode == SSOEnforcementMode.SSO_REQUIRED


# ── JIT Provisioning ────────────────────────────────────────────────────────


async def jit_provision_or_link(
    db: AsyncSession,
    config: SSOConfiguration,
    name_id: str,
    attributes: dict[str, list[str]],
    ip_address: str | None = None,
    email_verified: Optional[bool] = None,
) -> tuple[User, bool]:
    """
    Just-In-Time provision a new user or link an existing user from SAML assertion.

    W20 ADM-08b: an OIDC sign-in passes ``name_id`` = the ID token's ``sub``, its claims
    as ``attributes`` and ``email_verified``; the groups come from ``oidc_groups_claim``.

    A subject with no federated link yet whose e-mail belongs to an existing local account
    is refused (``ValueError``) unless the configuration's ``link_existing_accounts_by_email``
    is on, and then still refused when:

    * the account is an ADMIN (never linked: whoever controls that e-mail at the IdP would
      become an administrator);
    * OIDC: the ID token does not say ``email_verified: true`` (a missing claim is not enough);
    * SAML: the account's e-mail is not the NameID. An e-mail *attribute* never selects an
      existing account, because many IdPs let a user edit it.

    A refusal creates nothing: no link and no second account with the same e-mail.

    Returns (user, is_new_user).
    """
    # Extract user attributes from SAML
    email = name_id  # NameID is typically the email
    # Try to get email from attributes
    for attr_name in ("email", "Email", "mail", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
        if attr_name in attributes:
            email = attributes[attr_name][0]
            break

    display_name = None
    for attr_name in ("displayName", "name", "cn", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"):
        if attr_name in attributes:
            display_name = attributes[attr_name][0]
            break

    # Extract groups for role mapping
    groups: list[str] = []
    if config.provider_type == SSOProviderType.OIDC:
        group_key: Optional[str] = config.oidc_groups_claim or "groups"
    else:
        group_key = config.group_attribute
    if group_key and group_key in attributes:
        groups = attributes[group_key]

    # Check for existing federated identity
    result = await db.execute(
        select(FederatedIdentity).where(
            FederatedIdentity.sso_config_id == config.id,
            FederatedIdentity.external_id == name_id,
        )
    )
    fed_identity = result.scalar_one_or_none()

    is_new_user = False

    if fed_identity:
        # Existing federated link — update and return user
        fed_identity.external_email = email
        fed_identity.external_display_name = display_name
        fed_identity.external_groups = groups
        fed_identity.last_login_at = datetime.now(timezone.utc)

        user_result = await db.execute(select(User).where(User.id == fed_identity.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            raise ValueError("Federated identity references a deleted user")

        # Update role if role mapping changed
        new_role = resolve_role_from_groups(groups, config.role_mapping, config.default_role)
        if user.role != new_role:
            old_role = user.role
            user.role = new_role
            await _log_identity_event(
                db,
                IdentityEventType.ROLE_MAPPED,
                user_id=user.id,
                sso_config_id=config.id,
                detail={"old_role": str(old_role), "new_role": str(new_role), "groups": groups},
                ip_address=ip_address,
            )
        # Extra job roles follow the IdP only when the mapping names a job role; otherwise an
        # administrator manages them and a sign-in leaves them alone.
        if mapping_names_job_roles(config.role_mapping):
            old_extras = [r.value for r in job_roles_in(getattr(user, "additional_roles", None))]
            new_extras = resolve_additional_roles_from_groups(groups, config.role_mapping)
            if old_extras != new_extras:
                user.additional_roles = new_extras or None
                await _log_identity_event(
                    db,
                    IdentityEventType.ROLE_MAPPED,
                    user_id=user.id,
                    sso_config_id=config.id,
                    detail={"old_additional_roles": old_extras, "new_additional_roles": new_extras, "groups": groups},
                    ip_address=ip_address,
                )
    else:
        # No link yet. Which existing account, if any, does this sign-in name?
        # OIDC: the e-mail claim. SAML: the NameID only.
        is_oidc = config.provider_type == SSOProviderType.OIDC
        user = await _existing_user_by_email(db, email if is_oidc else name_id)
        if not is_oidc and email.lower() != name_id.lower():
            claimed = await _existing_user_by_email(db, email)
            if claimed is not None and (user is None or claimed.id != user.id):
                raise ValueError(
                    "An account with this e-mail already exists, but the sign-in's NameID is a different value, "
                    "so the account was not linked. Ask an administrator to have the identity provider send the "
                    "e-mail address as the SAML NameID"
                )

        if user is not None:
            if user.role == UserRole.ADMIN:
                raise ValueError(
                    "An account with this e-mail already exists and is an administrator account. "
                    "Administrator accounts are never linked to an SSO identity automatically; "
                    "sign in with the account's password"
                )
            if not getattr(config, "link_existing_accounts_by_email", False):
                raise ValueError(
                    "An account with this e-mail already exists; ask an administrator to enable linking "
                    "for this SSO configuration"
                )
            if is_oidc and email_verified is not True:
                raise ValueError(
                    "An account with this e-mail already exists, and the identity provider did not say the "
                    "e-mail is verified, so the account was not linked. Verify the e-mail at the identity "
                    "provider, or ask an administrator"
                )
            await _log_identity_event(
                db,
                IdentityEventType.ACCOUNT_LINKED,
                user_id=user.id,
                sso_config_id=config.id,
                detail={
                    "external_id": name_id,
                    "email": user.email,
                    "role": str(getattr(user.role, "value", user.role)),
                    "provider_type": "OIDC" if is_oidc else "SAML",
                },
                ip_address=ip_address,
            )

        if user is None:
            # JIT provision new user
            import secrets

            role = resolve_role_from_groups(groups, config.role_mapping, config.default_role)
            # Generate a username from email (before the @)
            username_base = email.split("@")[0].lower().replace(" ", "_")[:50]
            # Ensure uniqueness
            username = username_base
            counter = 1
            while True:
                existing = await db.execute(select(User).where(User.username == username))
                if existing.scalar_one_or_none() is None:
                    break
                username = f"{username_base}_{counter}"
                counter += 1

            user = User(
                email=email,
                username=username,
                full_name=display_name,
                # SSO users get a random password they'll never use
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                role=role,
                additional_roles=resolve_additional_roles_from_groups(groups, config.role_mapping) or None,
                is_active=True,
                must_change_password=False,  # SSO users don't need password reset
            )
            db.add(user)
            await db.flush()  # get the user.id
            is_new_user = True

            await _log_identity_event(
                db,
                IdentityEventType.JIT_PROVISIONED,
                user_id=user.id,
                sso_config_id=config.id,
                detail={"email": email, "role": str(role), "groups": groups},
                ip_address=ip_address,
            )

        # Create federated identity link
        fed_identity = FederatedIdentity(
            user_id=user.id,
            sso_config_id=config.id,
            external_id=name_id,
            external_email=email,
            external_display_name=display_name,
            external_groups=groups,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(fed_identity)

    return user, is_new_user


async def _existing_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """The local account with this e-mail, compared without case (as account creation does)."""
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.lower()).order_by(User.created_at, User.id).limit(1)
    )
    return result.scalar_one_or_none()


# ── Identity Event logging ──────────────────────────────────────────────────


async def _log_identity_event(
    db: AsyncSession,
    event_type: IdentityEventType,
    user_id: uuid.UUID | None = None,
    sso_config_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_name: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> IdentityEvent:
    """Persist an identity lifecycle event."""
    event = IdentityEvent(
        event_type=event_type,
        user_id=user_id,
        sso_config_id=sso_config_id,
        actor_id=actor_id,
        actor_name=actor_name,
        detail=detail,
        ip_address=ip_address,
        success=success,
        error_message=error_message,
    )
    db.add(event)
    return event


async def log_identity_event(
    db: AsyncSession,
    event_type: IdentityEventType,
    user_id: uuid.UUID | None = None,
    sso_config_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_name: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> IdentityEvent:
    """Public wrapper that also flushes the event to the session."""
    event = await _log_identity_event(
        db, event_type, user_id, sso_config_id, actor_id, actor_name,
        detail, ip_address, success, error_message,
    )
    await db.flush()
    return event


# ── Sync status aggregation ─────────────────────────────────────────────────


async def get_sync_status(db: AsyncSession, sso_config_id: uuid.UUID | None = None) -> dict:
    """Aggregate identity sync health metrics."""
    # Count federated users
    fed_query = select(func.count(FederatedIdentity.id))
    if sso_config_id:
        fed_query = fed_query.where(FederatedIdentity.sso_config_id == sso_config_id)
    fed_count = (await db.execute(fed_query)).scalar() or 0

    # Last SSO login
    login_query = (
        select(IdentityEvent.created_at)
        .where(IdentityEvent.event_type == IdentityEventType.SSO_LOGIN)
        .where(IdentityEvent.success == True)  # noqa: E712
        .order_by(IdentityEvent.created_at.desc())
        .limit(1)
    )
    if sso_config_id:
        login_query = login_query.where(IdentityEvent.sso_config_id == sso_config_id)
    last_login = (await db.execute(login_query)).scalar_one_or_none()

    # Last SCIM sync
    scim_query = (
        select(IdentityEvent.created_at)
        .where(
            IdentityEvent.event_type.in_([
                IdentityEventType.SCIM_USER_CREATED,
                IdentityEventType.SCIM_USER_UPDATED,
                IdentityEventType.SCIM_USER_DEACTIVATED,
            ])
        )
        .order_by(IdentityEvent.created_at.desc())
        .limit(1)
    )
    if sso_config_id:
        scim_query = scim_query.where(IdentityEvent.sso_config_id == sso_config_id)
    last_scim = (await db.execute(scim_query)).scalar_one_or_none()

    # Recent failures (last 24h)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    fail_query = (
        select(func.count(IdentityEvent.id))
        .where(IdentityEvent.success == False)  # noqa: E712
        .where(IdentityEvent.created_at >= cutoff)
    )
    if sso_config_id:
        fail_query = fail_query.where(IdentityEvent.sso_config_id == sso_config_id)
    recent_failures = (await db.execute(fail_query)).scalar() or 0

    return {
        "total_federated_users": fed_count,
        "last_sso_login_at": last_login,
        "last_scim_sync_at": last_scim,
        "recent_failures": recent_failures,
    }
