"""
Regression pins for the 2026-05-31 auth / tenancy / security review fixes.

Action plan: ``docs/reviews/auth-tenancy-security/00-action-plan.md``.

Covers:
  Tier 1 — SAML assertion hardening
    * cert is required (fail-closed); unsigned/empty-cert path removed
    * single Assertion / single Subject (XSW residual)
    * enforce_saml_security: missing-Conditions, audience, recipient,
      InResponseTo binding, and single-use replay rejection
  Tier 2 — role ceiling
    * resolve_role_from_groups clamps to SSO_MAX_PROVISIONED_ROLE
    * project-member grant ceiling + self-elevation block
    * last-admin guard
  Tier 3 — secrets at rest
    * read_secret returns None on undecryptable (non-plaintext) rows
    * mask_value reveals at most the last 2 chars

These are unit-level: Redis/DB are faked or the store functions monkeypatched,
so they run without external services.
"""
from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _b64(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


# ── Tier 1: SAML structural hardening (XSW + cert-required) ──────────────────


class TestSamlStructuralHardening:
    def test_cert_required_fail_closed(self):
        pytest.importorskip("defusedxml")
        from app.services.sso_service import parse_saml_response

        body = _b64(
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"></samlp:Response>'
        )
        with pytest.raises(ValueError, match="certificate"):
            parse_saml_response(body, idp_certificate_pem="")

    def test_multiple_assertions_rejected(self):
        pytest.importorskip("defusedxml")
        from app.services.sso_service import parse_saml_response_unverified

        xml = (
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
            '<saml:Assertion><saml:Subject><saml:NameID>a@x.com</saml:NameID></saml:Subject></saml:Assertion>'
            '<saml:Assertion><saml:Subject><saml:NameID>evil@x.com</saml:NameID></saml:Subject></saml:Assertion>'
            '</samlp:Response>'
        )
        with pytest.raises(ValueError, match="exactly one SAML Assertion"):
            parse_saml_response_unverified(_b64(xml))

    def test_multiple_subjects_rejected(self):
        pytest.importorskip("defusedxml")
        from app.services.sso_service import parse_saml_response_unverified

        xml = (
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
            '<saml:Assertion>'
            '<saml:Subject><saml:NameID>a@x.com</saml:NameID></saml:Subject>'
            '<saml:Subject><saml:NameID>evil@x.com</saml:NameID></saml:Subject>'
            '</saml:Assertion>'
            '</samlp:Response>'
        )
        with pytest.raises(ValueError, match="exactly one SAML Subject"):
            parse_saml_response_unverified(_b64(xml))

    def test_audience_extracted(self):
        pytest.importorskip("defusedxml")
        from app.services.sso_service import parse_saml_response_unverified

        xml = (
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
            '<saml:Assertion ID="_a1">'
            '<saml:Subject>'
            '<saml:NameID>a@x.com</saml:NameID>'
            '<saml:SubjectConfirmation>'
            '<saml:SubjectConfirmationData Recipient="https://sp/acs" InResponseTo="_req1"/>'
            '</saml:SubjectConfirmation>'
            '</saml:Subject>'
            '<saml:Conditions NotOnOrAfter="2099-01-01T00:00:00Z">'
            '<saml:AudienceRestriction><saml:Audience>https://sp.test</saml:Audience></saml:AudienceRestriction>'
            '</saml:Conditions>'
            '</saml:Assertion>'
            '</samlp:Response>'
        )
        result = parse_saml_response_unverified(_b64(xml))
        assert result["audiences"] == ["https://sp.test"]
        assert result["recipient"] == "https://sp/acs"
        assert result["in_response_to"] == "_req1"
        assert result["assertion_id"] == "_a1"
        assert result["conditions_present"] is True


# ── Tier 1: enforce_saml_security (binding + anti-replay) ────────────────────


def _valid_parsed() -> dict:
    return {
        "conditions_present": True,
        "audiences": ["https://sp.test"],
        "recipient": "https://sp.test/acs",
        "in_response_to": "_req1",
        "assertion_id": "_a1",
        "not_on_or_after": None,
    }


def _config():
    return SimpleNamespace(
        sp_entity_id="https://sp.test",
        audience=None,
        sp_acs_url="https://sp.test/acs",
    )


def _patch_store(monkeypatch, *, consume=True, fresh=True):
    pytest.importorskip("defusedxml")
    from app.services import sso_service

    async def fake_consume(_rid):
        return consume

    async def fake_claim(_aid, _ttl):
        return fresh

    monkeypatch.setattr(sso_service, "consume_saml_request", fake_consume)
    monkeypatch.setattr(sso_service, "claim_assertion_id", fake_claim)
    return sso_service


class TestEnforceSamlSecurity:
    async def test_valid_assertion_passes(self, monkeypatch):
        svc = _patch_store(monkeypatch, consume=True, fresh=True)
        await svc.enforce_saml_security(_valid_parsed(), _config())  # no raise

    async def test_missing_conditions_rejected(self, monkeypatch):
        svc = _patch_store(monkeypatch)
        parsed = _valid_parsed()
        parsed["conditions_present"] = False
        with pytest.raises(ValueError, match="Conditions"):
            await svc.enforce_saml_security(parsed, _config())

    async def test_missing_audience_rejected(self, monkeypatch):
        svc = _patch_store(monkeypatch)
        parsed = _valid_parsed()
        parsed["audiences"] = []
        with pytest.raises(ValueError, match="AudienceRestriction"):
            await svc.enforce_saml_security(parsed, _config())

    async def test_audience_mismatch_rejected(self, monkeypatch):
        svc = _patch_store(monkeypatch)
        parsed = _valid_parsed()
        parsed["audiences"] = ["https://attacker-sp.example"]
        with pytest.raises(ValueError, match="audience does not match"):
            await svc.enforce_saml_security(parsed, _config())

    async def test_recipient_mismatch_rejected(self, monkeypatch):
        svc = _patch_store(monkeypatch)
        parsed = _valid_parsed()
        parsed["recipient"] = "https://evil.example/acs"
        with pytest.raises(ValueError, match="Recipient does not match"):
            await svc.enforce_saml_security(parsed, _config())

    async def test_in_response_to_not_pending_rejected(self, monkeypatch):
        svc = _patch_store(monkeypatch, consume=False)
        with pytest.raises(ValueError, match="InResponseTo"):
            await svc.enforce_saml_security(_valid_parsed(), _config())

    async def test_replayed_assertion_rejected(self, monkeypatch):
        svc = _patch_store(monkeypatch, consume=True, fresh=False)
        with pytest.raises(ValueError, match="already been used|replay"):
            await svc.enforce_saml_security(_valid_parsed(), _config())

    async def test_idp_initiated_blocked_by_default(self, monkeypatch):
        svc = _patch_store(monkeypatch)
        from app.core.config import settings

        monkeypatch.setattr(settings, "SAML_ALLOW_IDP_INITIATED", False)
        parsed = _valid_parsed()
        parsed["in_response_to"] = None
        with pytest.raises(ValueError, match="InResponseTo"):
            await svc.enforce_saml_security(parsed, _config())

    async def test_idp_initiated_allowed_when_opted_in(self, monkeypatch):
        svc = _patch_store(monkeypatch, fresh=True)
        from app.core.config import settings

        monkeypatch.setattr(settings, "SAML_ALLOW_IDP_INITIATED", True)
        parsed = _valid_parsed()
        parsed["in_response_to"] = None
        await svc.enforce_saml_security(parsed, _config())  # no raise


# ── Tier 2: provisioning role ceiling ────────────────────────────────────────


class TestProvisioningCeiling:
    def test_resolve_role_clamped_to_ceiling(self, monkeypatch):
        pytest.importorskip("defusedxml")
        from app.core.config import settings
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        monkeypatch.setattr(settings, "SSO_MAX_PROVISIONED_ROLE", "QA_LEAD")
        role = resolve_role_from_groups(["admins"], {"admins": "ADMIN"}, UserRole.VIEWER)
        assert role == UserRole.QA_LEAD

    def test_resolve_role_unclamped_default(self, monkeypatch):
        pytest.importorskip("defusedxml")
        from app.core.config import settings
        from app.models.postgres import UserRole
        from app.services.sso_service import resolve_role_from_groups

        monkeypatch.setattr(settings, "SSO_MAX_PROVISIONED_ROLE", "ADMIN")
        role = resolve_role_from_groups(["admins"], {"admins": "ADMIN"}, UserRole.VIEWER)
        assert role == UserRole.ADMIN


# ── Tier 2: project-member grant ceiling + last-admin guard ──────────────────


class TestProjectMemberCeiling:
    def _user(self, role):
        from app.models.postgres import UserRole  # noqa: F401

        return SimpleNamespace(id=uuid.uuid4(), role=role)

    def test_qa_lead_cannot_grant_admin(self):
        from fastapi import HTTPException
        from app.models.postgres import UserRole
        from app.routers.users import _enforce_grant_ceiling

        lead = self._user(UserRole.QA_LEAD)
        with pytest.raises(HTTPException) as exc:
            _enforce_grant_ceiling(lead, UserRole.ADMIN, uuid.uuid4())
        assert exc.value.status_code == 403

    def test_qa_lead_cannot_change_own_role(self):
        from fastapi import HTTPException
        from app.models.postgres import UserRole
        from app.routers.users import _enforce_grant_ceiling

        lead = self._user(UserRole.QA_LEAD)
        with pytest.raises(HTTPException) as exc:
            _enforce_grant_ceiling(lead, UserRole.QA_ENGINEER, lead.id)
        assert exc.value.status_code == 403

    def test_qa_lead_can_grant_within_ceiling(self):
        from app.models.postgres import UserRole
        from app.routers.users import _enforce_grant_ceiling

        lead = self._user(UserRole.QA_LEAD)
        _enforce_grant_ceiling(lead, UserRole.QA_ENGINEER, uuid.uuid4())  # no raise

    def test_admin_can_grant_admin(self):
        from app.models.postgres import UserRole
        from app.routers.users import _enforce_grant_ceiling

        admin = self._user(UserRole.ADMIN)
        _enforce_grant_ceiling(admin, UserRole.ADMIN, uuid.uuid4())  # no raise


class TestLastAdminGuard:
    def _db(self, other_admin_count):
        from tests.conftest import FakeExecuteResult

        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=other_admin_count))
        return db

    async def test_blocks_removing_last_admin(self):
        from fastapi import HTTPException
        from app.models.postgres import UserRole
        from app.routers.users import _assert_not_last_admin

        target = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, is_active=True)
        with pytest.raises(HTTPException) as exc:
            await _assert_not_last_admin(self._db(0), target)
        assert exc.value.status_code == 400

    async def test_allows_when_other_admins_exist(self):
        from app.models.postgres import UserRole
        from app.routers.users import _assert_not_last_admin

        target = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, is_active=True)
        await _assert_not_last_admin(self._db(2), target)  # no raise

    async def test_noop_for_non_admin_target(self):
        from app.models.postgres import UserRole
        from app.routers.users import _assert_not_last_admin

        target = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD, is_active=True)
        await _assert_not_last_admin(self._db(0), target)  # no raise — not an admin


# ── Tier 3: secret-at-rest read fallback + masking ───────────────────────────


class TestSecretReadFallback:
    async def test_undecryptable_encrypted_row_returns_none(self):
        pytest.importorskip("cryptography")
        from tests.conftest import FakeExecuteResult
        from app.services import secret_service

        ref = SimpleNamespace(encrypted_value="not-a-fernet-token", provider="db")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=ref))
        # A tampered/rotated ciphertext must NOT be served verbatim as the secret.
        assert await secret_service.read_secret(db, "ai_config", "openai_api_key") is None

    async def test_legacy_plaintext_row_returned_verbatim(self):
        pytest.importorskip("cryptography")
        from tests.conftest import FakeExecuteResult
        from app.services import secret_service

        ref = SimpleNamespace(encrypted_value="legacy-plain-value", provider="plaintext")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=ref))
        assert await secret_service.read_secret(db, "ai_config", "openai_api_key") == "legacy-plain-value"

    def test_mask_value_reveals_at_most_two_chars(self):
        pytest.importorskip("cryptography")
        from app.services.secret_service import mask_value

        assert mask_value("sk-supersecretvalue99") == "****99"
        assert mask_value("short") == "****"
        assert mask_value("123456789012345") == "****"  # 15 chars fully masked


class TestScimListFilterSafety:
    """Regression: a recognised SCIM predicate carrying an unparseable/empty
    value must return an EMPTY result, never fall through to an unfiltered query.

    e0d4fea added an ``else`` that caught wholly-unrecognised filters, but a
    recognised predicate (``userName eq``) whose value failed extraction (empty
    quotes -> "" falsy, or no quotes -> None) skipped the ``if value:`` body, did
    NOT hit the ``else``, and fell through to an unfiltered SELECT returning the
    entire user directory. A ``matched`` flag now closes that path.
    """

    async def test_empty_quoted_username_filter_returns_empty(self):
        from app.services import scim_service

        db = AsyncMock()
        users, total = await scim_service.scim_list_users(db, filter_str='userName eq ""')
        assert users == []
        assert total == 0
        db.execute.assert_not_awaited()

    async def test_unquoted_username_filter_returns_empty(self):
        from app.services import scim_service

        db = AsyncMock()
        users, total = await scim_service.scim_list_users(db, filter_str="userName eq alice")
        assert users == []
        assert total == 0
        db.execute.assert_not_awaited()

    async def test_unsupported_predicate_returns_empty(self):
        from app.services import scim_service

        db = AsyncMock()
        users, total = await scim_service.scim_list_users(db, filter_str='displayName co "x"')
        assert users == []
        assert total == 0
        db.execute.assert_not_awaited()

    async def test_valid_username_filter_executes_query(self):
        from tests.conftest import FakeExecuteResult
        from app.services import scim_service

        user = SimpleNamespace(id=uuid.uuid4(), username="alice")
        # Count query uses .scalar(); the page query uses .scalars().all().
        # FakeExecuteResult has no scalars(), so build the page result with a
        # MagicMock (matching the repo's other scalars().all() test idiom).
        count_res = FakeExecuteResult(scalar_value=1)
        page_res = MagicMock()
        page_res.scalars.return_value.all.return_value = [user]
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_res, page_res])
        users, total = await scim_service.scim_list_users(db, filter_str='userName eq "alice"')
        assert total == 1
        assert users == [user]
        assert db.execute.await_count == 2


def _assertion_xml(*, not_before=None, not_on_or_after=None):
    """Minimal Response/Assertion with a <Conditions> time window, for exercising
    the clock-skew tolerance in parse_saml_response_unverified."""
    conds_attrs = ""
    if not_before:
        conds_attrs += f' NotBefore="{not_before}"'
    if not_on_or_after:
        conds_attrs += f' NotOnOrAfter="{not_on_or_after}"'
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
        '<saml:Assertion ID="_a1">'
        '<saml:Subject><saml:NameID>a@x.com</saml:NameID></saml:Subject>'
        f'<saml:Conditions{conds_attrs}>'
        '<saml:AudienceRestriction><saml:Audience>https://sp.test</saml:Audience>'
        '</saml:AudienceRestriction>'
        '</saml:Conditions>'
        '</saml:Assertion>'
        '</samlp:Response>'
    )


class TestSamlClockSkew:
    """_extract_saml_claims applies settings.SAML_CLOCK_SKEW_SECONDS to the
    NotBefore / NotOnOrAfter window, so small IdP/SP drift doesn't reject a valid
    assertion, while drift beyond the tolerance still fails closed.
    """

    def test_recently_expired_within_skew_passes(self, monkeypatch):
        pytest.importorskip("defusedxml")
        from datetime import datetime, timedelta, timezone
        from app.core.config import settings
        from app.services.sso_service import parse_saml_response_unverified

        monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 120, raising=False)
        noa = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        parsed = parse_saml_response_unverified(_b64(_assertion_xml(not_on_or_after=noa)))
        assert parsed["assertion_id"] == "_a1"  # parsed, not rejected

    def test_expired_beyond_skew_rejected(self, monkeypatch):
        pytest.importorskip("defusedxml")
        from datetime import datetime, timedelta, timezone
        from app.core.config import settings
        from app.services.sso_service import parse_saml_response_unverified

        monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 0, raising=False)
        noa = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with pytest.raises(ValueError, match="expired"):
            parse_saml_response_unverified(_b64(_assertion_xml(not_on_or_after=noa)))

    def test_not_yet_valid_within_skew_passes(self, monkeypatch):
        pytest.importorskip("defusedxml")
        from datetime import datetime, timedelta, timezone
        from app.core.config import settings
        from app.services.sso_service import parse_saml_response_unverified

        monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 120, raising=False)
        nb = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        parsed = parse_saml_response_unverified(_b64(_assertion_xml(not_before=nb)))
        assert parsed["assertion_id"] == "_a1"

    def test_not_yet_valid_beyond_skew_rejected(self, monkeypatch):
        pytest.importorskip("defusedxml")
        from datetime import datetime, timedelta, timezone
        from app.core.config import settings
        from app.services.sso_service import parse_saml_response_unverified

        monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 0, raising=False)
        nb = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with pytest.raises(ValueError, match="not yet valid"):
            parse_saml_response_unverified(_b64(_assertion_xml(not_before=nb)))


class TestSsoMaxRoleValidation:
    """SSO_MAX_PROVISIONED_ROLE is validated at settings construction: a typo
    fails fast instead of silently degrading to the ADMIN ceiling at runtime.
    """

    def test_invalid_role_rejected(self):
        from pydantic import ValidationError
        from app.core.config import Settings

        with pytest.raises(ValidationError, match="SSO_MAX_PROVISIONED_ROLE"):
            Settings(SSO_MAX_PROVISIONED_ROLE="SUPERADMIN")

    def test_valid_role_normalised_to_upper(self):
        from app.core.config import Settings

        assert Settings(SSO_MAX_PROVISIONED_ROLE="qa_lead").SSO_MAX_PROVISIONED_ROLE == "QA_LEAD"
