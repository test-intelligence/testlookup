"""GAP-B03: SAML sign-in reads identity only from what the IdP really signed.

``app/services/sso_service.parse_saml_response`` verifies the XML-DSig
signature (signxml, an algorithm allow-list) and extracts the claims from the
element signxml returns -- never from the document around it. That is the
whole defence against XML Signature Wrapping: an attacker keeps a genuinely
signed element intact and places an unsigned assertion where a naive reader
looks. The existing real-signature tests (``regression/test_saml_slo.py``)
only accept a good response and refuse another key's; they pass unchanged
when the parser reads the unverified tree.

Here, with a throwaway IdP key and signxml as ``test_saml_slo.py`` signs:

* a NameID edited after signing is refused;
* a signed assertion moved aside next to an unsigned one never yields the
  attacker's NameID;
* a signature over a DIFFERENT IdP message (a signed SLO LogoutRequest, same
  key) can't vouch for an unsigned assertion -- the case that turns
  "claims from the whole document" into a sign-in as anyone;
* rsa-sha1 and a sha1 digest are refused although the signature is valid
  (built by hand: signxml refuses to sign with SHA-1);
* entity declarations never put a file's content (or an expansion) into a claim.

Every assertion is exclusive-c14n signed, as IdPs sign an assertion that
travels inside a Response.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("lxml")
pytest.importorskip("signxml")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from lxml import etree  # noqa: E402
from signxml import XMLSigner  # noqa: E402

from app.services.sso_service import parse_saml_response  # noqa: E402

IDP = "https://idp.example.test/realms/testlookup"
ALICE = "alice@example.test"
MALLORY = "mallory@example.test"
DS = "http://www.w3.org/2000/09/xmldsig#"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
NS = 'xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
FILE_MARKER = "ci-saml-xxe-marker-test-only"
ENTITY_MARKER = "ci-saml-entity-expanded-test-only"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return key, cert


@pytest.fixture(scope="module")
def idp():
    key, cert = _keypair()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key, cert, pem, cert.public_bytes(serialization.Encoding.PEM).decode()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assertion(name_id: str, assertion_id: str) -> str:
    return (
        f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{assertion_id}" '
        f'Version="2.0" IssueInstant="{_now()}"><saml:Issuer>{IDP}</saml:Issuer>'
        f"<saml:Subject><saml:NameID>{name_id}</saml:NameID></saml:Subject>"
        '<saml:AttributeStatement><saml:Attribute Name="groups"><saml:AttributeValue>qa-leads'
        "</saml:AttributeValue></saml:Attribute></saml:AttributeStatement></saml:Assertion>"
    )


def _sign(idp, xml: str) -> str:
    """Enveloped rsa-sha256 signature by signxml, exclusive c14n (what an IdP sends)."""
    _key, _cert, pem, cert_pem = idp
    signer = XMLSigner(
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm=EXC_C14N,
    )
    return etree.tostring(
        signer.sign(etree.fromstring(xml.encode()), key=pem, cert=cert_pem)
    ).decode()


def _sign_by_hand(idp, xml: str, algorithm: str, digest: str) -> str:
    """The same enveloped signature, built without signxml (which refuses to SIGN with SHA-1)."""
    key, cert, _pem, _cert_pem = idp
    uris = {
        "rsa-sha1": ("http://www.w3.org/2000/09/xmldsig#rsa-sha1", hashes.SHA1()),
        "rsa-sha256": (
            "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            hashes.SHA256(),
        ),
    }
    digests = {
        "sha1": ("http://www.w3.org/2000/09/xmldsig#sha1", hashlib.sha1),
        "sha256": ("http://www.w3.org/2001/04/xmlenc#sha256", hashlib.sha256),
    }
    sig_uri, sig_hash = uris[algorithm]
    dig_uri, dig_fn = digests[digest]
    element = etree.fromstring(xml.encode())
    digest_value = base64.b64encode(
        dig_fn(etree.tostring(element, method="c14n", exclusive=True)).digest()
    )

    def sub(parent, tag, **attrs):
        return etree.SubElement(parent, f"{{{DS}}}{tag}", **attrs)

    signature = etree.Element(f"{{{DS}}}Signature", nsmap={"ds": DS})
    signed_info = sub(signature, "SignedInfo")
    sub(signed_info, "CanonicalizationMethod", Algorithm=EXC_C14N)
    sub(signed_info, "SignatureMethod", Algorithm=sig_uri)
    reference = sub(signed_info, "Reference", URI="#" + element.get("ID"))
    transforms = sub(reference, "Transforms")
    sub(
        transforms,
        "Transform",
        Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature",
    )
    sub(transforms, "Transform", Algorithm=EXC_C14N)
    sub(reference, "DigestMethod", Algorithm=dig_uri)
    sub(reference, "DigestValue").text = digest_value.decode()
    signature_value = sub(signature, "SignatureValue")
    x509_data = sub(sub(signature, "KeyInfo"), "X509Data")
    sub(x509_data, "X509Certificate").text = base64.b64encode(
        cert.public_bytes(serialization.Encoding.DER)
    ).decode()
    element.insert(1, signature)  # after the Issuer, where SAML puts it
    signature_value.text = base64.b64encode(
        key.sign(
            etree.tostring(signed_info, method="c14n", exclusive=True),
            padding.PKCS1v15(),
            sig_hash,
        )
    ).decode()
    return etree.tostring(element).decode()


def _response(*inner: str, doctype: str = "") -> str:
    return (
        f'{doctype}<samlp:Response {NS} ID="_resp" Version="2.0" IssueInstant="{_now()}">'
        f"<saml:Issuer>{IDP}</saml:Issuer>{''.join(inner)}</samlp:Response>"
    )


def _parse(idp, document: str) -> dict:
    return parse_saml_response(
        base64.b64encode(document.encode()).decode(), idp_certificate_pem=idp[3]
    )


def _never_mallory(idp, document: str) -> str:
    """Refused, or signed in as alice. Returns which, for the caller to pin."""
    try:
        parsed = _parse(idp, document)
    except ValueError:
        return "refused"
    assert parsed["name_id"] != MALLORY, f"signed in as the attacker: {parsed}"
    assert parsed["name_id"] == ALICE, parsed
    return "alice"


# ── the control: what a real IdP sends parses ───────────────────────────────


def test_an_assertion_signed_inside_a_response_parses(idp):
    parsed = _parse(idp, _response(_sign(idp, _assertion(ALICE, "_a1"))))

    assert parsed["name_id"] == ALICE
    assert parsed["attributes"]["groups"] == ["qa-leads"]
    assert parsed["assertion_id"] == "_a1"


# ── tampering and wrapping ──────────────────────────────────────────────────


def test_a_name_id_edited_after_signing_is_refused(idp):
    tampered = _response(_sign(idp, _assertion(ALICE, "_a1"))).replace(ALICE, MALLORY)

    with pytest.raises(ValueError, match="signature verification failed"):
        _parse(idp, tampered)


def _wrapped(idp, layout: str) -> str:
    signed = _sign(idp, _assertion(ALICE, "_a1"))
    forged = _assertion(MALLORY, "_forged")
    if layout == "signed in Extensions, forged after":
        return _response(f"<samlp:Extensions>{signed}</samlp:Extensions>", forged)
    if layout == "forged first, signed in Extensions":
        return _response(forged, f"<samlp:Extensions>{signed}</samlp:Extensions>")
    if layout == "signed inside the forged assertion's Advice":
        return _response(
            forged.replace(
                "<saml:Subject>",
                f"<saml:Advice>{signed}</saml:Advice><saml:Subject>",
                1,
            )
        )
    raise AssertionError(layout)


@pytest.mark.parametrize(
    "layout",
    [
        "signed in Extensions, forged after",
        "forged first, signed in Extensions",
        "signed inside the forged assertion's Advice",
    ],
)
def test_a_wrapped_assertion_never_signs_in_the_attacker(idp, layout):
    # Today the claims come from the signed element, so this is alice; a refusal would be safe too.
    assert _never_mallory(idp, _wrapped(idp, layout)) == "alice"


def test_a_signature_over_another_idp_message_cannot_vouch_for_an_assertion(idp):
    """The IdP signs its SLO LogoutRequests with the same key. One of those, tucked
    into Extensions, verifies -- but it is not an assertion, so the only assertion
    in the document (the attacker's, unsigned) must never be read."""
    logout_request = (
        f'<samlp:LogoutRequest {NS} ID="_slo1" Version="2.0" IssueInstant="{_now()}">'
        f"<saml:Issuer>{IDP}</saml:Issuer><saml:NameID>{ALICE}</saml:NameID></samlp:LogoutRequest>"
    )
    document = _response(
        f"<samlp:Extensions>{_sign(idp, logout_request)}</samlp:Extensions>",
        _assertion(MALLORY, "_forged"),
    )

    try:
        parsed = _parse(idp, document)
    except ValueError as exc:
        assert "No Assertion found" in str(exc), exc
    else:
        pytest.fail(
            f"an unsigned assertion was trusted: signed in as {parsed['name_id']}"
        )


# ── the algorithm allow-list ────────────────────────────────────────────────


def test_the_hand_built_signature_is_accepted_with_sha256(idp):
    """Control for the SHA-1 cases: the hand-built signer makes signatures the parser accepts."""
    signed = _sign_by_hand(idp, _assertion(ALICE, "_a1"), "rsa-sha256", "sha256")

    assert _parse(idp, _response(signed))["name_id"] == ALICE


@pytest.mark.parametrize(
    "algorithm, digest", [("rsa-sha1", "sha1"), ("rsa-sha256", "sha1")]
)
def test_a_sha1_signature_is_refused_although_it_verifies(idp, algorithm, digest):
    from signxml import (
        DigestAlgorithm,
        SignatureConfiguration,
        SignatureMethod,
        XMLVerifier,
    )

    signed = _sign_by_hand(idp, _assertion(ALICE, "_a1"), algorithm, digest)
    # Cryptographically sound: a verifier that allows SHA-1 accepts it ...
    XMLVerifier().verify(
        etree.fromstring(signed.encode()),
        x509_cert=idp[3],
        expect_config=SignatureConfiguration(
            signature_methods=frozenset(
                {SignatureMethod.RSA_SHA1, SignatureMethod.RSA_SHA256}
            ),
            digest_algorithms=frozenset({DigestAlgorithm.SHA1, DigestAlgorithm.SHA256}),
        ),
    )

    # ... and the sign-in parser refuses it on the algorithm alone.
    with pytest.raises(ValueError, match="signature verification failed"):
        _parse(idp, _response(signed))


# ── entity declarations ─────────────────────────────────────────────────────


@pytest.fixture
def marker_url(tmp_path):
    marker = tmp_path / "idp-secret.txt"
    marker.write_text(FILE_MARKER, encoding="utf-8")
    return marker.resolve().as_uri()


def _refused_without(idp, document: str, *secrets: str) -> None:
    with pytest.raises(ValueError) as caught:
        _parse(idp, document)
    for secret in secrets:
        assert secret not in str(caught.value), caught.value


def test_an_external_entity_in_the_name_id_is_refused_and_never_read(idp, marker_url):
    doctype = f'<!DOCTYPE samlp:Response [<!ENTITY xxe SYSTEM "{marker_url}">]>'
    document = _response(_sign(idp, _assertion(ALICE, "_a1")), doctype=doctype).replace(
        ALICE, "&xxe;"
    )

    _refused_without(idp, document, FILE_MARKER)


def test_an_internal_entity_in_the_name_id_is_refused_and_never_expanded(idp):
    doctype = f'<!DOCTYPE samlp:Response [<!ENTITY who "{ENTITY_MARKER}">]>'
    document = _response(_sign(idp, _assertion(ALICE, "_a1")), doctype=doctype).replace(
        ALICE, "&who;"
    )

    _refused_without(idp, document, ENTITY_MARKER)


def test_an_entity_outside_the_signed_assertion_never_reaches_a_claim(idp, marker_url):
    """The signed assertion is intact; the entity sits in an unsigned sibling."""
    doctype = f'<!DOCTYPE samlp:Response [<!ENTITY xxe SYSTEM "{marker_url}">]>'
    document = _response(
        '<samlp:Extensions><n:note xmlns:n="urn:example:test">&xxe;</n:note></samlp:Extensions>',
        _sign(idp, _assertion(ALICE, "_a1")),
        doctype=doctype,
    )
    try:
        parsed = _parse(idp, document)
    except ValueError as exc:
        assert FILE_MARKER not in str(exc)
    else:
        assert parsed["name_id"] == ALICE
        assert FILE_MARKER not in repr(parsed)


def test_a_ten_level_entity_expansion_is_refused(idp):
    entities = '<!ENTITY e0 "laugh">' + "".join(
        f'<!ENTITY e{level} "{("&e" + str(level - 1) + ";") * 10}">'
        for level in range(1, 11)
    )
    document = _response(
        _sign(idp, _assertion(ALICE, "_a1")),
        doctype=f"<!DOCTYPE samlp:Response [{entities}]>",
    ).replace(ALICE, "&e10;")

    _refused_without(idp, document, "laughlaugh")
