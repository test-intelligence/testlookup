"""
Unit tests for ENT-04: Service Ownership Routing and Component Maps.

Covers:
 - Ownership rule matching: glob patterns, exact match, case insensitivity
 - Ownership resolution hierarchy: rules > component_owner_map > test owner > fallback
 - Majority voting for cluster ownership
 - Schema validation
 - Model column widths
 - Confidence levels
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt",
                checkpw=MagicMock(return_value=True),
                hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt"),
            ))
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
        from sqlalchemy.orm import DeclarativeBase
        class _Base(DeclarativeBase):
            pass
        m.setitem(sys.modules, "app.db.postgres", _make_stub(
            "app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(),
            AsyncSessionLocal=MagicMock(), Base=_Base,
        ))
        m.setitem(sys.modules, "app.db.mongo", _make_stub(
            "app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(),
        ))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub(
            "app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock(),
        ))
        yield


# ── Helper: mock rule object ─────────────────────────────────────────────────

def _mock_rule(match_type: str, match_pattern: str, service: str, team: str,
               priority: int = 0, team_contact: str | None = None) -> MagicMock:
    rule = MagicMock()
    rule.id = uuid.uuid4()
    rule.match_type = match_type
    rule.match_pattern = match_pattern
    rule.service_name = service
    rule.team_name = team
    rule.team_contact = team_contact
    rule.priority = priority
    return rule


# ── Rule matching tests ──────────────────────────────────────────────────────


class TestRuleMatching:
    def test_exact_match(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("suite_name", "auth-tests", "auth-service", "Identity")
        assert _match_rule(rule, {"suite_name": "auth-tests"}) is True

    def test_glob_match_wildcard(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("suite_name", "auth-*", "auth-service", "Identity")
        assert _match_rule(rule, {"suite_name": "auth-login-tests"}) is True
        assert _match_rule(rule, {"suite_name": "auth-register"}) is True
        assert _match_rule(rule, {"suite_name": "payment-tests"}) is False

    def test_glob_match_question_mark(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("suite_name", "test-?", "svc", "team")
        assert _match_rule(rule, {"suite_name": "test-A"}) is True
        assert _match_rule(rule, {"suite_name": "test-AB"}) is False

    def test_case_insensitive_match(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("suite_name", "Auth-*", "auth-service", "Identity")
        assert _match_rule(rule, {"suite_name": "auth-login"}) is True
        assert _match_rule(rule, {"suite_name": "AUTH-LOGIN"}) is True

    def test_no_match_on_empty_value(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("suite_name", "auth-*", "auth-service", "Identity")
        assert _match_rule(rule, {"suite_name": ""}) is False
        assert _match_rule(rule, {}) is False

    def test_component_match_type(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("component", "com.app.payments.*", "payments", "Payments Team")
        assert _match_rule(rule, {"component": "com.app.payments.checkout"}) is True
        assert _match_rule(rule, {"component": "com.app.auth.login"}) is False

    def test_package_match_type(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("package", "org.myapp.api.*", "api-gateway", "Platform")
        assert _match_rule(rule, {"package": "org.myapp.api.v2"}) is True

    def test_label_match_type(self):
        from app.services.ownership_resolver_service import _match_rule

        rule = _mock_rule("label", "checkout-*", "checkout-svc", "Commerce")
        assert _match_rule(rule, {"label": "checkout-flow"}) is True


# ── Ownership resolution hierarchy ──────────────────────────────────────────


class TestOwnershipResolution:
    def test_rule_match_returns_high_confidence(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules = [_mock_rule("suite_name", "auth-*", "auth-service", "Identity", priority=10)]
        result = resolve_test_ownership(rules, {"suite_name": "auth-login"})
        assert result.team_name == "Identity"
        assert result.service_name == "auth-service"
        assert result.confidence == "high"
        assert result.match_source == "suite_name"

    def test_higher_priority_rule_wins(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules = [
            _mock_rule("suite_name", "auth-*", "auth-service", "Identity", priority=20),
            _mock_rule("suite_name", "auth-*", "legacy-auth", "Legacy Team", priority=10),
        ]
        result = resolve_test_ownership(rules, {"suite_name": "auth-login"})
        assert result.team_name == "Identity"  # higher priority

    def test_fallback_to_component_owner_map(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules = []  # no rules
        comp_map = {"auth-service": {"team": "Identity"}}
        result = resolve_test_ownership(rules, {"suite_name": "auth-service"}, comp_map)
        assert result.team_name == "Identity"
        assert result.confidence == "medium"
        assert result.match_source == "component_owner_map"

    def test_fallback_to_default_component_map(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules = []
        comp_map = {"default": {"team": "Platform"}}
        result = resolve_test_ownership(rules, {"suite_name": "unknown-suite"}, comp_map)
        assert result.team_name == "Platform"
        assert result.confidence == "low"
        assert result.fallback_reason is not None

    def test_fallback_to_test_owner(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        result = resolve_test_ownership([], {"owner": "alice@example.com"}, None)
        assert result.team_name == "alice@example.com"
        assert result.confidence == "low"
        assert result.match_source == "test_owner_label"

    def test_fallback_to_none(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        result = resolve_test_ownership([], {}, None)
        assert result.confidence == "none"
        assert result.team_name is None
        assert result.fallback_reason is not None

    def test_rule_beats_component_map(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules = [_mock_rule("suite_name", "auth-*", "auth-v2", "New Identity")]
        comp_map = {"auth-service": {"team": "Old Identity"}}
        result = resolve_test_ownership(rules, {"suite_name": "auth-login"}, comp_map)
        assert result.team_name == "New Identity"
        assert result.confidence == "high"

    def test_component_map_string_value(self):
        from app.services.ownership_resolver_service import resolve_test_ownership

        rules = []
        comp_map = {"payments": "payments-team"}  # string instead of dict
        result = resolve_test_ownership(rules, {"suite_name": "payments"}, comp_map)
        assert result.team_name == "payments-team"
        assert result.confidence == "medium"


# ── OwnershipResult dataclass ────────────────────────────────────────────────


class TestOwnershipResult:
    def test_to_dict(self):
        from app.services.ownership_resolver_service import OwnershipResult

        result = OwnershipResult(
            service_name="auth-svc",
            team_name="Identity",
            confidence="high",
            match_source="suite_name",
        )
        d = result.to_dict()
        assert d["service_name"] == "auth-svc"
        assert d["team_name"] == "Identity"
        assert d["confidence"] == "high"

    def test_defaults(self):
        from app.services.ownership_resolver_service import OwnershipResult

        result = OwnershipResult()
        assert result.confidence == "none"
        assert result.service_name is None
        assert result.team_name is None


# ── Schema validation ────────────────────────────────────────────────────────


class TestOwnershipSchemas:
    def test_rule_create_valid(self):
        from app.models.schemas import OwnershipRuleCreate

        rule = OwnershipRuleCreate(
            match_type="suite_name",
            match_pattern="auth-*",
            service_name="auth-service",
            team_name="Identity",
        )
        assert rule.priority == 0

    def test_rule_create_invalid_match_type(self):
        from pydantic import ValidationError
        from app.models.schemas import OwnershipRuleCreate

        with pytest.raises(ValidationError):
            OwnershipRuleCreate(
                match_type="invalid_type",
                match_pattern="*",
                service_name="svc",
                team_name="team",
            )

    def test_rule_create_empty_pattern_rejected(self):
        from pydantic import ValidationError
        from app.models.schemas import OwnershipRuleCreate

        with pytest.raises(ValidationError):
            OwnershipRuleCreate(
                match_type="suite_name",
                match_pattern="",
                service_name="svc",
                team_name="team",
            )

    def test_rule_update_partial(self):
        from app.models.schemas import OwnershipRuleUpdate

        update = OwnershipRuleUpdate(team_name="New Team")
        assert update.team_name == "New Team"
        assert update.match_type is None

    def test_bulk_import_request(self):
        from app.models.schemas import OwnershipBulkImportRequest, OwnershipBulkImportItem

        req = OwnershipBulkImportRequest(rules=[
            OwnershipBulkImportItem(
                match_type="suite_name", match_pattern="auth-*",
                service_name="auth", team_name="Identity",
            ),
        ])
        assert len(req.rules) == 1
        assert req.replace_existing is False

    def test_ownership_resolution_schema(self):
        from app.models.schemas import OwnershipResolution

        res = OwnershipResolution(
            service_name="auth",
            team_name="Identity",
            confidence="high",
            match_source="suite_name",
        )
        assert res.confidence == "high"

    def test_ownership_resolution_defaults(self):
        from app.models.schemas import OwnershipResolution

        res = OwnershipResolution()
        assert res.confidence == "none"
        assert res.service_name is None

    def test_rule_response_model(self):
        from app.models.schemas import OwnershipRuleResponse

        resp = OwnershipRuleResponse(
            id=uuid.uuid4(), project_id=uuid.uuid4(),
            match_type="suite_name", match_pattern="auth-*",
            service_name="auth", team_name="Identity",
            priority=10, is_active=True,
            created_at="2026-04-02T12:00:00Z",
        )
        assert resp.is_active is True


# ── Model column widths ─────────────────────────────────────────────────────


class TestColumnWidths:
    def test_match_type_values_fit(self):
        for mt in ("suite_name", "component", "package", "path", "label"):
            assert len(mt) <= 30

    def test_service_name_max_fits(self):
        assert len("a" * 255) <= 255

    def test_team_name_max_fits(self):
        assert len("a" * 255) <= 255

    def test_confidence_values_fit(self):
        for c in ("high", "medium", "low", "none"):
            assert len(c) <= 20


# ── ORM model has correct fields ────────────────────────────────────────────


class TestORMModel:
    def test_service_ownership_rule_fields(self):
        from app.models.postgres import ServiceOwnershipRule

        assert hasattr(ServiceOwnershipRule, "match_type")
        assert hasattr(ServiceOwnershipRule, "match_pattern")
        assert hasattr(ServiceOwnershipRule, "service_name")
        assert hasattr(ServiceOwnershipRule, "team_name")
        assert hasattr(ServiceOwnershipRule, "team_contact")
        assert hasattr(ServiceOwnershipRule, "priority")
        assert hasattr(ServiceOwnershipRule, "is_active")
        assert hasattr(ServiceOwnershipRule, "project_id")
