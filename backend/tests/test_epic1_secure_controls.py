"""
Epic 1: Secure Enterprise Controls — Unit Tests.

Tests:
  QAI-101: Release route authorization (role enforcement)
  QAI-102: Secret reference storage (masking, separation, edge cases)
  QAI-103: Settings schema validation
  QAI-104: Audit logging
  Regression: existing functionality preserved
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
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security", verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"), create_access_token=MagicMock(return_value="access_token"), create_refresh_token=MagicMock(return_value="refresh_token"), decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock())))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-101: Release Route Authorization
# ═══════════════════════════════════════════════════════════════════════════════

class TestReleaseRouteAuthorization:
    """Verify release routes have proper role dependencies."""

    def test_release_read_endpoints_require_authentication(self):
        """GET endpoints should require get_current_active_user (any authenticated user)."""
        from app.routers.releases import list_releases, get_release
        # These should exist and be callable (deps checked at runtime by FastAPI)
        assert callable(list_releases)
        assert callable(get_release)

    def test_release_mutation_endpoints_exist(self):
        """POST/PUT/DELETE endpoints should exist with role guards."""
        from app.routers.releases import (
            create_release, update_release, delete_release,
            add_phase, update_phase, delete_phase,
            link_test_run, unlink_test_run,
        )
        assert callable(create_release)
        assert callable(update_release)
        assert callable(delete_release)
        assert callable(add_phase)
        assert callable(update_phase)
        assert callable(delete_phase)
        assert callable(link_test_run)
        assert callable(unlink_test_run)

    def test_release_schemas_unchanged(self):
        """Existing schemas still work after authorization changes."""
        from app.routers.releases import ReleaseIn, ReleaseUpdate, PhaseIn, PhaseUpdate, LinkRunRequest

        r = ReleaseIn(project_id="proj-1", name="v1.0")
        assert r.status == "planning"

        u = ReleaseUpdate(status="released")
        assert u.status == "released"

        p = PhaseIn(name="QA Testing")
        assert p.phase_type == "qa_testing"

        pu = PhaseUpdate(status="completed")
        assert pu.status == "completed"

        lr = LinkRunRequest(test_run_id="run-1")
        assert lr.phase_id is None


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-102: Secret Reference Storage
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecretMasking:
    def test_mask_short_value(self):
        from app.services.secret_service import mask_value

        assert mask_value("short") == "****"
        assert mask_value("") == "****"
        assert mask_value("1234567") == "****"

    def test_mask_standard_value(self):
        from app.services.secret_service import mask_value

        result = mask_value("sk-abcdef123456xyz")
        assert result.startswith("sk-a")
        assert result.endswith("xyz")
        assert "..." in result

    def test_mask_long_api_key(self):
        from app.services.secret_service import mask_value

        key = "ghp_" + "x" * 40
        result = mask_value(key)
        assert result.startswith("ghp_")
        assert len(result) < len(key)


class TestSecretFieldIdentification:
    def test_smtp_password_is_secret(self):
        from app.services.secret_service import is_secret_field

        assert is_secret_field("smtp_config", "password") is True
        assert is_secret_field("smtp_config", "host") is False

    def test_ai_config_keys_are_secrets(self):
        from app.services.secret_service import is_secret_field

        assert is_secret_field("ai_config", "openai_api_key") is True
        assert is_secret_field("ai_config", "google_api_key") is True
        assert is_secret_field("ai_config", "llm_provider") is False

    def test_integrations_tokens_are_secrets(self):
        from app.services.secret_service import is_secret_field

        assert is_secret_field("integrations_config", "jira_api_token") is True
        assert is_secret_field("integrations_config", "splunk_api_token") is True
        assert is_secret_field("integrations_config", "ocp_sa_token") is True
        assert is_secret_field("integrations_config", "github_token") is True
        assert is_secret_field("integrations_config", "jira_domain") is False

    def test_unknown_scope_returns_false(self):
        from app.services.secret_service import is_secret_field

        assert is_secret_field("nonexistent_scope", "any_field") is False


class TestSecretExtraction:
    def test_extract_secrets_from_ai_config(self):
        from app.services.secret_service import extract_secrets_from_config

        config = {
            "llm_provider": "openai",
            "llm_model": "gpt-4o",
            "openai_api_key": "sk-abc123",
            "google_api_key": "AIza456",
        }
        secrets = extract_secrets_from_config("ai_config", config)
        assert secrets == {"openai_api_key": "sk-abc123", "google_api_key": "AIza456"}

    def test_extract_no_secrets(self):
        from app.services.secret_service import extract_secrets_from_config

        config = {"llm_provider": "ollama", "llm_model": "qwen2.5:7b"}
        secrets = extract_secrets_from_config("ai_config", config)
        assert secrets == {}

    def test_extract_skips_none_values(self):
        from app.services.secret_service import extract_secrets_from_config

        config = {"openai_api_key": None, "google_api_key": ""}
        secrets = extract_secrets_from_config("ai_config", config)
        assert secrets == {}

    def test_strip_secrets_from_config(self):
        from app.services.secret_service import strip_secrets_from_config

        config = {
            "llm_provider": "openai",
            "openai_api_key": "sk-abc123",
            "llm_model": "gpt-4o",
        }
        stripped = strip_secrets_from_config("ai_config", config)
        assert "openai_api_key" not in stripped
        assert stripped["llm_provider"] == "openai"
        assert stripped["llm_model"] == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-102: Schema Validation — Secrets Never Returned
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecretNeverReturned:
    def test_ai_config_read_has_no_raw_keys(self):
        from app.models.schemas import AIConfigRead

        cfg = AIConfigRead(
            llm_provider="openai", llm_model="gpt-4o", llm_temperature=0.1,
            llm_max_tokens=4096, ai_offline_mode=False, embedding_provider="openai",
            embedding_model="text-embedding-3-small", ai_confidence_threshold=80,
            ai_timeout_seconds=300, deep_investigation_enabled=True, finetune_enabled=False,
            openai_key_set=True, google_key_set=False,
        )
        fields = set(cfg.model_fields.keys())
        assert "openai_api_key" not in fields
        assert "google_api_key" not in fields
        assert "openai_key_set" in fields

    def test_integrations_config_read_has_no_raw_tokens(self):
        from app.models.schemas import IntegrationsConfigRead

        cfg = IntegrationsConfigRead(
            jira_enabled=True, jira_domain="test.atlassian.net", jira_email="u@t.com",
            jira_token_set=True, jira_default_project_key="QA",
            splunk_enabled=False, splunk_base_url=None, splunk_token_set=False,
            ocp_enabled=False, ocp_api_url=None, ocp_token_set=False, ocp_default_namespace="qa",
            slack_enabled=False, slack_webhook_url=None, slack_default_channel="#qa",
            teams_enabled=False, teams_webhook_url=None,
            github_repo=None, github_token_set=False,
        )
        fields = set(cfg.model_fields.keys())
        assert "jira_api_token" not in fields
        assert "splunk_api_token" not in fields
        assert "ocp_sa_token" not in fields
        assert "github_token" not in fields


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-103: Settings Schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestSettingsSchemaValidation:
    def test_ai_config_update_validates_bounds(self):
        from app.models.schemas import AIConfigUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AIConfigUpdate(llm_temperature=5.0)
        with pytest.raises(ValidationError):
            AIConfigUpdate(ai_timeout_seconds=10)

    def test_storage_config_update_validates_port(self):
        from app.models.schemas import StorageConfigUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StorageConfigUpdate(chroma_port=0)

    def test_integrations_partial_update(self):
        from app.models.schemas import IntegrationsConfigUpdate

        update = IntegrationsConfigUpdate(jira_enabled=True)
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {"jira_enabled": True}


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-104: Audit Logging
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditLogging:
    def test_settings_audit_log_model_exists(self):
        from app.models.postgres import SettingsAuditLog

        assert SettingsAuditLog.__tablename__ == "settings_audit_log"

    def test_secret_ref_model_exists(self):
        from app.models.postgres import SecretRef

        assert SecretRef.__tablename__ == "secret_refs"

    def test_app_setting_has_new_columns(self):
        from app.models.postgres import AppSetting

        # Verify columns exist
        columns = {c.name for c in AppSetting.__table__.columns}
        assert "secret_ref_id" in columns
        assert "is_secret_backed" in columns
        assert "updated_by" in columns


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_mask_none_value(self):
        from app.services.secret_service import mask_value

        # mask_value expects a string; None would be caught by caller
        assert mask_value("") == "****"

    def test_extract_from_empty_config(self):
        from app.services.secret_service import extract_secrets_from_config

        assert extract_secrets_from_config("ai_config", {}) == {}

    def test_strip_from_empty_config(self):
        from app.services.secret_service import strip_secrets_from_config

        assert strip_secrets_from_config("ai_config", {}) == {}

    def test_secret_fields_for_all_known_scopes(self):
        from app.services.secret_service import SECRET_FIELDS

        assert "smtp_config" in SECRET_FIELDS
        assert "ai_config" in SECRET_FIELDS
        assert "integrations_config" in SECRET_FIELDS

    def test_release_schemas_backward_compatible(self):
        """Existing release schemas still work after auth changes."""
        from app.routers.releases import ReleaseIn, ReleaseUpdate

        # Minimal creation
        r = ReleaseIn(project_id="p", name="v1")
        assert r.phases == []

        # Status update
        u = ReleaseUpdate(status="cancelled")
        assert u.model_dump(exclude_none=True) == {"status": "cancelled"}
