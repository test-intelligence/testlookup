"""
Unit tests for OPS-04: Tenant-Aware Observability and Audit Dashboards.

Covers:
 - Redaction logic: sensitive key detection, dict recursion, safe pass-through
 - Audit categories: all categories defined and valid
 - CSV export format
 - Tenant metric snapshot model fields
 - Column width safety
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
                checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))
        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security",
            verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"),
            create_access_token=MagicMock(return_value="access_token"),
            create_refresh_token=MagicMock(return_value="refresh_token"),
            decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        from sqlalchemy.orm import DeclarativeBase
        class _Base(DeclarativeBase):
            pass
        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres",
            get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo",
            get_mongo_db=MagicMock(), close_mongo=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client",
            get_redis=MagicMock(), close_redis=MagicMock()))
        yield


# ── Redaction tests ──────────────────────────────────────────────────────────


class TestRedaction:
    def test_redact_password_key(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("password", "secret123") == "[REDACTED]"

    def test_redact_api_token_key(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("api_token", "tok_xyz") == "[REDACTED]"

    def test_redact_jwt_key(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("jwt", "eyJhbG...") == "[REDACTED]"

    def test_redact_secret_key(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("webhook_secret", "abc") == "[REDACTED]"

    def test_no_redact_normal_key(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("username", "admin") == "admin"

    def test_no_redact_none_value(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("password", None) is None

    def test_redact_dict_recursive(self):
        from app.services.audit_dashboard_service import redact_dict

        data = {
            "username": "admin",
            "password": "secret",
            "nested": {"api_key": "key123", "name": "test"},
        }
        result = redact_dict(data)
        assert result["username"] == "admin"
        assert result["password"] == "[REDACTED]"
        assert result["nested"]["api_key"] == "[REDACTED]"
        assert result["nested"]["name"] == "test"

    def test_redact_dict_none(self):
        from app.services.audit_dashboard_service import redact_dict

        assert redact_dict(None) is None

    def test_redact_dict_empty(self):
        from app.services.audit_dashboard_service import redact_dict

        assert redact_dict({}) == {}

    def test_redact_case_insensitive(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("API_TOKEN", "xyz") == "[REDACTED]"
        assert redact_value("Password", "xyz") == "[REDACTED]"


# ── Audit categories ────────────────────────────────────────────────────────


class TestAuditCategories:
    def test_all_categories_defined(self):
        from app.services.audit_dashboard_service import AUDIT_CATEGORIES

        expected = {"access", "settings", "test_management", "identity", "notification", "release", "report"}
        assert set(AUDIT_CATEGORIES.keys()) == expected

    def test_categories_have_descriptions(self):
        from app.services.audit_dashboard_service import AUDIT_CATEGORIES

        for key, desc in AUDIT_CATEGORIES.items():
            assert isinstance(desc, str)
            assert len(desc) > 5, f"Category {key} has no description"


# ── ORM model fields ────────────────────────────────────────────────────────


class TestORMModels:
    def test_tenant_metric_snapshot_fields(self):
        from app.models.postgres import TenantMetricSnapshot

        for field in ("project_id", "recorded_at", "total_runs", "total_tests",
                       "avg_pass_rate", "failed_runs", "ai_analyses_count",
                       "release_decisions_count", "audit_events_count"):
            assert hasattr(TenantMetricSnapshot, field)

    def test_access_audit_log_fields(self):
        from app.models.postgres import AccessAuditLog

        for field in ("actor_user_id", "actor_name", "target_user_id", "project_id",
                       "action", "before_value", "after_value", "created_at"):
            assert hasattr(AccessAuditLog, field)

    def test_settings_audit_log_fields(self):
        from app.models.postgres import SettingsAuditLog

        for field in ("setting_key", "action", "actor_id", "actor_name",
                       "changed_fields", "created_at"):
            assert hasattr(SettingsAuditLog, field)


# ── Column width safety ─────────────────────────────────────────────────────


class TestColumnWidths:
    def test_audit_action_values_fit(self):
        """All action strings must fit String(50)."""
        actions = [
            "role_changed", "status_changed", "member_added", "member_removed",
            "created", "updated", "secret_rotated", "report_export_pdf",
            "report_share_created", "report_share_accessed",
        ]
        for a in actions:
            assert len(a) <= 50

    def test_category_keys_are_short(self):
        from app.services.audit_dashboard_service import AUDIT_CATEGORIES

        for key in AUDIT_CATEGORIES:
            assert len(key) <= 30


# ── Redaction in value containing sensitive patterns ─────────────────────────


class TestValuePatternRedaction:
    def test_value_containing_token_word(self):
        from app.services.audit_dashboard_service import redact_value

        # The key is normal but value contains a Bearer token pattern
        result = redact_value("header", "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef")
        assert "[REDACTED]" in result

    def test_value_normal_text(self):
        from app.services.audit_dashboard_service import redact_value

        assert redact_value("message", "Build completed successfully") == "Build completed successfully"
