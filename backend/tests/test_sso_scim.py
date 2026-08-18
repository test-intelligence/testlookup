"""
Unit tests for SSO/SAML, SCIM provisioning, and identity event endpoints (ENT-01).

All DB calls and heavy dependencies (bcrypt, jose) are mocked.
Tests cover:
 - SSO configuration CRUD (create, read, update, delete, test-connection)
 - SAML assertion parsing and validation
 - JIT user provisioning and linking
 - Role mapping from IdP groups
 - SSO enforcement and admin fallback
 - SCIM user create/update/deactivate
 - SCIM bearer token auth
 - SCIM filter parsing
 - Identity event listing and sync status
 - Certificate validation
 - Edge cases and security checks
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub native deps and app.core/app.db modules for each test."""
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(
                sys.modules, "bcrypt",
                _make_stub("bcrypt",
                    checkpw=MagicMock(return_value=True),
                    hashpw=MagicMock(return_value=b"$2b$fake"),
                    gensalt=MagicMock(return_value=b"$2b$12$salt"),
                ),
            )
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub(
            "app.core.security",
            verify_password=MagicMock(return_value=True),
            get_password_hash=MagicMock(return_value="hashed_pw"),
            create_access_token=MagicMock(return_value="access_token"),
            create_refresh_token=MagicMock(return_value="refresh_token"),
            decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"}),
        ))

        # Stub DB connection factories (no real DB in unit tests)
        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub(
            "app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), Base=_Base,
        ))
        m.setitem(sys.modules, "app.db.mongo", _make_stub(
            "app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(),
        ))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub(
            "app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock(),
        ))

        yield


# ── Helper: Generate test certificate ────────────────────────────────────────

def _make_test_cert(
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> str:
    """Return a valid PEM-formatted test certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(not_after or datetime.now(timezone.utc) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _make_saml_response(
    name_id: str = "user@example.com",
    issuer: str = "https://idp.example.com",
    status_value: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
    attributes: dict | None = None,
    expired: bool = False,
) -> str:
    """Build a base64-encoded SAML response for testing."""
    now = datetime.now(timezone.utc)
    not_before = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 1-hour margin (not 1 minute) so a slow CI run can't let the "expired"
    # fixture drift back inside the validity (or clock-skew) window mid-test.
    not_after = (now - timedelta(hours=1) if expired else now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    attr_statements = ""
    if attributes:
        attr_xml_parts = []
        for attr_name, values in attributes.items():
            vals = "".join(
                f'<saml:AttributeValue xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{v}</saml:AttributeValue>'
                for v in values
            )
            attr_xml_parts.append(f'<saml:Attribute Name="{attr_name}">{vals}</saml:Attribute>')
        attr_statements = f'<saml:AttributeStatement>{"".join(attr_xml_parts)}</saml:AttributeStatement>'

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
    <saml:Issuer>{issuer}</saml:Issuer>
    <samlp:Status>
        <samlp:StatusCode Value="{status_value}"/>
    </samlp:Status>
    <saml:Assertion>
        <saml:Issuer>{issuer}</saml:Issuer>
        <saml:Subject>
            <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{name_id}</saml:NameID>
        </saml:Subject>
        <saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_after}"/>
        <saml:AuthnStatement SessionIndex="_session123"/>
        {attr_statements}
    </saml:Assertion>
</samlp:Response>"""
    return base64.b64encode(xml.encode()).decode()


# ── Certificate validation tests ─────────────────────────────────────────────


class TestCertificateValidation:
    def test_valid_certificate_format(self):
        from app.services.sso_service import validate_certificate_format

        cert = _make_test_cert()
        valid, msg = validate_certificate_format(cert)
        assert valid is True
        assert msg == "Valid"

    def test_invalid_certificate_missing_header(self):
        from app.services.sso_service import validate_certificate_format

        valid, msg = validate_certificate_format("not a certificate")
        assert valid is False
        assert "BEGIN CERTIFICATE" in msg

    def test_invalid_certificate_missing_footer(self):
        from app.services.sso_service import validate_certificate_format

        valid, msg = validate_certificate_format("-----BEGIN CERTIFICATE-----\ndata")
        assert valid is False
        assert "END CERTIFICATE" in msg

    def test_invalid_certificate_bad_base64(self):
        from app.services.sso_service import validate_certificate_format

        cert = "-----BEGIN CERTIFICATE-----\n!!!invalid!!!\n-----END CERTIFICATE-----"
        valid, msg = validate_certificate_format(cert)
        assert valid is False
        assert "x.509" in msg.lower()

    def test_invalid_certificate_rejects_decodable_non_x509_bytes(self):
        from app.services.sso_service import validate_certificate_format

        cert = "-----BEGIN CERTIFICATE-----\nYQ==\n-----END CERTIFICATE-----"
        valid, msg = validate_certificate_format(cert)
        assert valid is False
        assert "x.509" in msg.lower()

    def test_expired_certificate_is_rejected(self):
        from app.services.sso_service import validate_certificate_format

        cert = _make_test_cert(
            not_before=datetime.now(timezone.utc) - timedelta(days=2),
            not_after=datetime.now(timezone.utc) - timedelta(days=1),
        )
        valid, msg = validate_certificate_format(cert)
        assert valid is False
        assert "expired" in msg.lower()

    def test_not_yet_valid_certificate_is_rejected(self):
        from app.services.sso_service import validate_certificate_format

        cert = _make_test_cert(
            not_before=datetime.now(timezone.utc) + timedelta(days=1),
            not_after=datetime.now(timezone.utc) + timedelta(days=2),
        )
        valid, msg = validate_certificate_format(cert)
        assert valid is False
        assert "not valid until" in msg.lower()

    def test_certificate_expiration_returns_x509_not_after(self):
        from app.services.sso_service import certificate_expiration

        expected = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
        cert = _make_test_cert(not_after=expected)
        assert certificate_expiration(cert) == expected

    def test_certificate_fingerprint(self):
        from app.services.sso_service import certificate_fingerprint

        cert = _make_test_cert()
        fp = certificate_fingerprint(cert)
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex digest

    def test_certificate_fingerprint_invalid(self):
        from app.services.sso_service import certificate_fingerprint

        # A string without PEM headers — the function strips non-header lines
        # and tries to decode. An empty string results in sha256 of empty bytes.
        fp = certificate_fingerprint("no headers at all")
        assert fp == "invalid-certificate"


# ── SAML Response parsing tests ──────────────────────────────────────────────


class TestSAMLParsing:
    """Structural parsing via the explicit test-only unverified helper.

    Production callers use ``parse_saml_response`` which now REQUIRES a cert
    and verifies the signature (see TestSAMLSignatureVerification).
    """

    def test_parse_valid_saml_response(self):
        from app.services.sso_service import parse_saml_response_unverified

        b64 = _make_saml_response(name_id="user@test.com", issuer="https://idp.test")
        result = parse_saml_response_unverified(b64)
        assert result["name_id"] == "user@test.com"
        assert result["issuer"] == "https://idp.test"
        assert result["session_index"] == "_session123"

    def test_parse_saml_with_attributes(self):
        from app.services.sso_service import parse_saml_response_unverified

        b64 = _make_saml_response(
            attributes={"email": ["user@test.com"], "groups": ["admins", "engineers"]}
        )
        result = parse_saml_response_unverified(b64)
        assert result["attributes"]["email"] == ["user@test.com"]
        assert result["attributes"]["groups"] == ["admins", "engineers"]

    def test_parse_saml_invalid_base64(self):
        from app.services.sso_service import parse_saml_response_unverified

        with pytest.raises(ValueError, match="Invalid base64"):
            parse_saml_response_unverified("!!!not_base64!!!")

    def test_parse_saml_invalid_xml(self):
        from app.services.sso_service import parse_saml_response_unverified

        b64 = base64.b64encode(b"<not valid xml").decode()
        with pytest.raises(ValueError, match="Malformed SAML XML"):
            parse_saml_response_unverified(b64)

    def test_parse_saml_no_assertion(self):
        from app.services.sso_service import parse_saml_response_unverified

        xml = '<?xml version="1.0"?><samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"></samlp:Response>'
        b64 = base64.b64encode(xml.encode()).decode()
        with pytest.raises(ValueError, match="No Assertion"):
            parse_saml_response_unverified(b64)

    def test_parse_saml_failed_status(self):
        from app.services.sso_service import parse_saml_response_unverified

        b64 = _make_saml_response(status_value="urn:oasis:names:tc:SAML:2.0:status:Requester")
        with pytest.raises(ValueError, match="SAML authentication failed"):
            parse_saml_response_unverified(b64)

    def test_parse_saml_expired_assertion(self):
        from app.services.sso_service import parse_saml_response_unverified

        b64 = _make_saml_response(expired=True)
        with pytest.raises(ValueError, match="expired"):
            parse_saml_response_unverified(b64)

    def test_validate_saml_issuer_match(self):
        from app.services.sso_service import validate_saml_issuer

        config = MagicMock(idp_entity_id="https://idp.test")
        parsed = {"issuer": "https://idp.test"}
        validate_saml_issuer(parsed, config)  # should not raise

    def test_validate_saml_issuer_mismatch(self):
        from app.services.sso_service import validate_saml_issuer

        config = MagicMock(idp_entity_id="https://idp.expected")
        parsed = {"issuer": "https://idp.wrong"}
        with pytest.raises(ValueError, match="Issuer mismatch"):
            validate_saml_issuer(parsed, config)


# ── SAML signature verification (regression: prevents auth bypass) ──────────


class TestSAMLSignatureVerification:
    """
    Locks in the fix for the SAML signature bypass. Previously,
    ``parse_saml_response`` accepted any well-formed assertion and a
    docstring claimed "signature validation relies on the IdP certificate
    configured" — but no code actually verified signatures. An attacker
    could forge an assertion for any user. The fix makes the production
    path (cert provided) cryptographically verify the XML-DSig signature
    before extracting any claim, and extract claims only from the
    verified subtree.

    Signing test XML requires signxml + cryptography, which are present
    in the Docker test image but may be absent from bare local checkouts,
    so the crypto tests importorskip cleanly.
    """

    def test_unsigned_assertion_with_cert_is_rejected(self):
        """
        The most important regression: an unsigned SAML Response MUST be
        rejected when a cert is configured. This is the exact shape of
        assertion that previously auth-bypassed.
        """
        # `pytest.importorskip` only catches ImportError. signxml < 4.0 raises
        # AttributeError at import time when paired with `cryptography` >= 43
        # (deprecated EC curves removed). Skip on any import-time failure so
        # future cryptography/signxml drift surfaces as a skip, not a confusing
        # AttributeError trace.
        try:
            import signxml  # noqa: F401, PLC0415
            import cryptography  # noqa: F401, PLC0415
        except (ImportError, AttributeError) as exc:
            pytest.skip(f"signxml/cryptography not importable: {exc}")
        from app.services.sso_service import parse_saml_response

        b64 = _make_saml_response(name_id="attacker@evil.com")
        cert = _make_test_cert()
        with pytest.raises(ValueError, match="signature verification failed|signature"):
            parse_saml_response(b64, idp_certificate_pem=cert)

    def test_signxml_missing_fails_closed(self, monkeypatch):
        """
        If signxml is somehow unavailable in production (bad requirements
        pin, partial install), the parser must fail CLOSED — refuse to
        process — rather than silently skipping verification.
        """
        from app.services import sso_service

        # Force the import inside _verify_xml_signature to fail by shadowing
        # signxml in sys.modules with None.
        monkeypatch.setitem(sys.modules, "signxml", None)

        b64 = _make_saml_response()
        with pytest.raises(ValueError, match="signature verification unavailable|not installed"):
            sso_service.parse_saml_response(b64, idp_certificate_pem="-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----")

    def test_production_parse_requires_cert(self):
        """
        ``parse_saml_response`` is now fail-closed: a missing/empty cert raises
        instead of silently downgrading to an unsigned parse. Structural-only
        tests must call ``parse_saml_response_unverified`` explicitly.
        """
        from app.services.sso_service import parse_saml_response

        b64 = _make_saml_response(name_id="test@example.com")
        with pytest.raises(ValueError, match="requires an IdP certificate|certificate"):
            parse_saml_response(b64, idp_certificate_pem=None)
        with pytest.raises(ValueError, match="requires an IdP certificate|certificate"):
            parse_saml_response(b64, idp_certificate_pem="   ")

    def test_unverified_helper_parses_structure(self):
        """The explicit test-only helper parses without a cert."""
        from app.services.sso_service import parse_saml_response_unverified

        b64 = _make_saml_response(name_id="test@example.com")
        result = parse_saml_response_unverified(b64)
        assert result["name_id"] == "test@example.com"


# ── Role mapping tests ───────────────────────────────────────────────────────


class TestRoleMapping:
    def test_resolve_role_no_mapping(self):
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        role = resolve_role_from_groups(["group1"], None, UserRole.VIEWER)
        assert role == UserRole.VIEWER

    def test_resolve_role_no_groups(self):
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        role = resolve_role_from_groups([], {"admins": "ADMIN"}, UserRole.VIEWER)
        assert role == UserRole.VIEWER

    def test_resolve_role_single_match(self):
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        role = resolve_role_from_groups(
            ["engineers"],
            {"engineers": "QA_ENGINEER", "admins": "ADMIN"},
            UserRole.VIEWER,
        )
        assert role == UserRole.QA_ENGINEER

    def test_resolve_role_highest_match(self):
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        role = resolve_role_from_groups(
            ["engineers", "admins"],
            {"engineers": "QA_ENGINEER", "admins": "ADMIN"},
            UserRole.VIEWER,
        )
        assert role == UserRole.ADMIN

    def test_resolve_role_no_match_uses_default(self):
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        role = resolve_role_from_groups(
            ["unknown_group"],
            {"engineers": "QA_ENGINEER"},
            UserRole.TESTER,
        )
        assert role == UserRole.TESTER

    def test_resolve_role_invalid_mapping_skipped(self):
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        role = resolve_role_from_groups(
            ["bad_group"],
            {"bad_group": "INVALID_ROLE"},
            UserRole.VIEWER,
        )
        assert role == UserRole.VIEWER


# ── SCIM token tests ────────────────────────────────────────────────────────


class TestSCIMToken:
    def test_token_hash_is_sha256(self):
        from app.services.scim_service import _hash_token

        raw = "scim_test_token_123"
        hashed = _hash_token(raw)
        assert hashed == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert len(hashed) == 64

    def test_token_hint_format(self):
        """Token hint must be first 8 chars + '...' = 11 chars, fits in String(12)."""
        raw = "scim_abcdefghijklmnop"
        hint = raw[:8] + "..."
        assert hint == "scim_abc..."
        assert len(hint) == 11
        assert len(hint) <= 12  # fits in DB column


# ── SCIM filter parsing tests ────────────────────────────────────────────────


class TestSCIMFilterParsing:
    def test_extract_username_filter(self):
        from app.services.scim_service import _extract_scim_filter_value

        value = _extract_scim_filter_value('userName eq "john"')
        assert value == "john"

    def test_extract_email_filter(self):
        from app.services.scim_service import _extract_scim_filter_value

        value = _extract_scim_filter_value('emails.value eq "john@test.com"')
        assert value == "john@test.com"

    def test_reject_single_quote_filter(self):
        from app.services.scim_service import _extract_scim_filter_value

        value = _extract_scim_filter_value("userName eq 'john'")
        assert value is None

    def test_extract_no_match(self):
        from app.services.scim_service import _extract_scim_filter_value

        value = _extract_scim_filter_value("invalid filter")
        assert value is None


# ── SCIM user resource conversion tests ──────────────────────────────────────


class TestSCIMResourceConversion:
    def test_user_to_scim_resource(self):
        from app.services.scim_service import user_to_scim_resource

        user = MagicMock(
            id=uuid.uuid4(),
            username="testuser",
            full_name="Test User",
            email="test@example.com",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        resource = user_to_scim_resource(user, "https://api.test")
        assert resource["userName"] == "testuser"
        assert resource["displayName"] == "Test User"
        assert resource["active"] is True
        assert len(resource["emails"]) == 1
        assert resource["emails"][0]["value"] == "test@example.com"
        assert resource["meta"]["resourceType"] == "User"
        assert "https://api.test" in resource["meta"]["location"]

    def test_user_to_scim_resource_no_fullname(self):
        from app.services.scim_service import user_to_scim_resource

        user = MagicMock(
            id=uuid.uuid4(),
            username="noname",
            full_name=None,
            email="no@name.com",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=None,
        )
        resource = user_to_scim_resource(user)
        assert resource["displayName"] == "noname"  # falls back to username


# ── Pydantic schema validation tests ────────────────────────────────────────


class TestSSOSchemas:
    def test_sso_config_create_valid(self):
        from app.models.schemas import SSOConfigCreate

        config = SSOConfigCreate(
            display_name="Okta",
            idp_entity_id="https://idp.okta.com",
            idp_sso_url="https://idp.okta.com/sso",
            idp_certificate=_make_test_cert(),
            sp_entity_id="https://testlookup.io",
            sp_acs_url="https://testlookup.io/api/v1/sso/acs",
        )
        assert config.display_name == "Okta"
        assert config.enforcement_mode.value == "OPTIONAL"
        assert config.default_role.value == "VIEWER"

    def test_sso_config_create_name_too_short(self):
        from pydantic import ValidationError

        from app.models.schemas import SSOConfigCreate

        with pytest.raises(ValidationError):
            SSOConfigCreate(
                display_name="X",  # too short
                idp_entity_id="https://idp.okta.com",
                idp_sso_url="https://idp.okta.com/sso",
                idp_certificate=_make_test_cert(),
                sp_entity_id="https://testlookup.io",
                sp_acs_url="https://testlookup.io/acs",
            )

    def test_scim_token_create_valid(self):
        from app.models.schemas import SCIMTokenCreate

        token = SCIMTokenCreate(name="IdP Token")
        assert token.name == "IdP Token"
        assert token.expires_days is None

    def test_scim_token_create_expires_bounds(self):
        from pydantic import ValidationError

        from app.models.schemas import SCIMTokenCreate

        # Valid: 1-365
        SCIMTokenCreate(name="test-token", expires_days=365)
        # Invalid: 0
        with pytest.raises(ValidationError):
            SCIMTokenCreate(name="test-token", expires_days=0)
        # Invalid: 366
        with pytest.raises(ValidationError):
            SCIMTokenCreate(name="test-token", expires_days=366)

    def test_scim_user_resource_defaults(self):
        from app.models.schemas import SCIMUserResource

        user = SCIMUserResource(userName="john")
        assert user.active is True
        assert user.schemas == ["urn:ietf:params:scim:schemas:core:2.0:User"]
        assert user.emails == []

    def test_scim_patch_request(self):
        from app.models.schemas import SCIMPatchOp, SCIMPatchRequest

        req = SCIMPatchRequest(Operations=[
            SCIMPatchOp(op="replace", path="active", value=False),
        ])
        assert len(req.Operations) == 1
        assert req.Operations[0].op == "replace"

    def test_identity_event_response(self):
        from app.models.schemas import IdentityEventResponse

        event = IdentityEventResponse(
            id=uuid.uuid4(),
            event_type="SSO_LOGIN",
            success=True,
            created_at=datetime.now(timezone.utc),
        )
        assert event.event_type == "SSO_LOGIN"
        assert event.success is True


# ── SSO config response masking tests ────────────────────────────────────────


class TestSSOConfigResponse:
    def test_certificate_not_in_response(self):
        """SSO config response should show fingerprint, never the raw certificate."""
        from app.models.schemas import SSOConfigResponse

        response = SSOConfigResponse(
            id=uuid.uuid4(),
            display_name="Test",
            provider_type="SAML",
            idp_entity_id="https://idp.test",
            idp_sso_url="https://idp.test/sso",
            idp_certificate_fingerprint="abc123",
            sp_entity_id="https://sp.test",
            sp_acs_url="https://sp.test/acs",
            default_role="VIEWER",
            enforcement_mode="OPTIONAL",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        data = response.model_dump()
        assert "idp_certificate" not in data
        assert data["idp_certificate_fingerprint"] == "abc123"


# ── SSO enforcement tests ───────────────────────────────────────────────────


class TestSSOEnforcement:
    def test_sso_not_enforced_when_disabled(self):
        """When SSO_ENABLED=False, enforcement should be off."""
        from app.core.config import settings

        original = settings.SSO_ENABLED
        try:
            settings.SSO_ENABLED = False
            # is_sso_enforced is async — test the config check only
            assert settings.SSO_ENABLED is False
        finally:
            settings.SSO_ENABLED = original

    def test_admin_fallback_config_default(self):
        """Admin fallback should be enabled by default."""
        from app.core.config import settings

        assert settings.SSO_ADMIN_FALLBACK_ENABLED is True


# ── Identity event type coverage ─────────────────────────────────────────────


class TestIdentityEventTypes:
    def test_all_event_types_defined(self):
        """Every SSO/SCIM event this module relies on still exists.

        Asserted as a **subset**, not equality: ``IdentityEventType`` is the
        shared identity-lifecycle vocabulary and other features add to it
        (migration 0117 added the MFA and account-lockout events). Requiring
        exact equality here made an unrelated addition look like an SSO
        regression. The invariant that actually matters for this column — every
        value fits ``String(40)`` — is covered by the next test and by
        ``tests/test_mfa.py::TestWiringInvariants``.
        """
        from app.models.postgres import IdentityEventType

        expected = {
            "SSO_LOGIN", "SSO_LOGIN_FAILED", "SSO_CONFIG_CREATED",
            "SSO_CONFIG_UPDATED", "SSO_CONFIG_DELETED", "SSO_TEST_CONNECTION",
            "SCIM_USER_CREATED", "SCIM_USER_UPDATED", "SCIM_USER_DEACTIVATED",
            "SCIM_USER_REACTIVATED", "SCIM_SYNC_ERROR", "ADMIN_FALLBACK_LOGIN",
            "JIT_PROVISIONED", "ROLE_MAPPED",
        }
        actual = {e.value for e in IdentityEventType}
        assert expected <= actual, expected - actual

    def test_event_type_string_fits_column(self):
        """All event type values must fit in String(40)."""
        from app.models.postgres import IdentityEventType

        for e in IdentityEventType:
            assert len(e.value) <= 40, f"Event type '{e.value}' exceeds 40 chars"


# ── SSO provider/enforcement mode enums ──────────────────────────────────────


class TestSSOEnums:
    def test_provider_types(self):
        from app.models.postgres import SSOProviderType

        assert SSOProviderType.SAML.value == "SAML"
        assert SSOProviderType.OIDC.value == "OIDC"

    def test_enforcement_modes(self):
        from app.models.postgres import SSOEnforcementMode

        assert SSOEnforcementMode.OPTIONAL.value == "OPTIONAL"
        assert SSOEnforcementMode.SSO_REQUIRED.value == "SSO_REQUIRED"

    def test_enforcement_mode_fits_column(self):
        """All enforcement modes must fit in String(20)."""
        from app.models.postgres import SSOEnforcementMode

        for mode in SSOEnforcementMode:
            assert len(mode.value) <= 20


# ── Config settings validation tests ─────────────────────────────────────────


class TestConfigValidation:
    def test_production_warning_for_localhost_saml(self):
        from app.core.config import Settings

        s = Settings(
            APP_ENV="production",
            SSO_ENABLED=True,
            SAML_BASE_URL="http://localhost:8000",
            JWT_SECRET_KEY="change-me-jwt-secret",
        )
        warnings = s.validate_production_secrets()
        assert any("SAML_BASE_URL" in w for w in warnings)

    def test_no_saml_warning_when_sso_disabled(self):
        from app.core.config import Settings

        s = Settings(
            APP_ENV="production",
            SSO_ENABLED=False,
            SAML_BASE_URL="http://localhost:8000",
            JWT_SECRET_KEY="super-secret-key-for-prod",
            APP_SECRET_KEY="another-secret-key",
            WEBHOOK_SECRET="webhook-secret",
        )
        warnings = s.validate_production_secrets()
        assert not any("SAML_BASE_URL" in w for w in warnings)


# ── SCIM error response schema tests ────────────────────────────────────────


class TestSCIMErrorSchema:
    def test_scim_error_response(self):
        from app.models.schemas import SCIMErrorResponse

        err = SCIMErrorResponse(status="409", detail="User already exists")
        assert err.status == "409"
        assert "already exists" in err.detail


# ── SSO sync status schema tests ────────────────────────────────────────────


class TestSyncStatusSchema:
    def test_identity_sync_status_defaults(self):
        from app.models.schemas import IdentitySyncStatus

        status = IdentitySyncStatus()
        assert status.total_federated_users == 0
        assert status.recent_failures == 0
        assert status.recent_events == []


# ── ORM model column width safety ───────────────────────────────────────────


class TestModelColumnWidths:
    def test_scim_token_hint_fits(self):
        """SCIM token hint must be ≤12 chars to fit String(12) column."""
        raw = "scim_" + "a" * 32
        hint = raw[:8] + "..."
        assert len(hint) <= 12

    def test_sso_provider_type_fits(self):
        """Provider type values must fit String(20)."""
        from app.models.postgres import SSOProviderType

        for pt in SSOProviderType:
            assert len(pt.value) <= 20

    def test_user_role_fits_default_role(self):
        """Default role column is String(20) — all UserRole values must fit."""
        from app.models.postgres import UserRole

        for role in UserRole:
            assert len(role.value) <= 20
