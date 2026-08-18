"""
Unit tests for app settings schemas and configuration endpoints.

Tests:
  - AI Config schema validation and defaults
  - Integrations schema — tokens never returned
  - Storage schema — read-only fields preserved
  - Partial update semantics (None = keep existing)
  - Edge cases: empty payloads, max/min bounds
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock

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
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None)))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# AI Configuration Schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestAIConfigRead:
    def test_all_fields_present(self):
        from app.models.schemas import AIConfigRead

        cfg = AIConfigRead(
            llm_provider="ollama",
            llm_model="qwen2.5:7b",
            llm_temperature=0.1,
            llm_max_tokens=4096,
            ai_offline_mode=True,
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
            ai_confidence_threshold=80,
            ai_timeout_seconds=300,
            deep_investigation_enabled=True,
            finetune_enabled=False,
            openai_key_set=False,
            google_key_set=False,
            analysis_mode="auto",
        )
        assert cfg.llm_provider == "ollama"
        assert cfg.ai_offline_mode is True
        assert cfg.openai_key_set is False

    def test_api_keys_are_booleans_not_values(self):
        """Verify keys are exposed as set/not-set booleans, never raw values."""
        from app.models.schemas import AIConfigRead

        cfg = AIConfigRead(
            llm_provider="openai", llm_model="gpt-4o", llm_temperature=0.0,
            llm_max_tokens=4096, ai_offline_mode=False, embedding_provider="openai",
            embedding_model="text-embedding-3-small", ai_confidence_threshold=80,
            ai_timeout_seconds=300, deep_investigation_enabled=True, finetune_enabled=False,
            openai_key_set=True, google_key_set=True, analysis_mode="llm",
        )
        # The schema never exposes raw keys
        assert not hasattr(cfg, "openai_api_key")
        assert not hasattr(cfg, "google_api_key")
        assert cfg.openai_key_set is True


class TestAIConfigUpdate:
    def test_partial_update_none_keeps_existing(self):
        from app.models.schemas import AIConfigUpdate

        update = AIConfigUpdate(llm_model="gpt-4o")
        dumped = update.model_dump(exclude_none=True)
        assert "llm_model" in dumped
        assert "llm_provider" not in dumped  # None → excluded

    def test_temperature_validation(self):
        from app.models.schemas import AIConfigUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AIConfigUpdate(llm_temperature=3.0)  # max 2.0

        with pytest.raises(ValidationError):
            AIConfigUpdate(llm_temperature=-1.0)  # min 0.0

    def test_max_tokens_validation(self):
        from app.models.schemas import AIConfigUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AIConfigUpdate(llm_max_tokens=100)  # min 256

    def test_confidence_threshold_bounds(self):
        from app.models.schemas import AIConfigUpdate
        from pydantic import ValidationError

        AIConfigUpdate(ai_confidence_threshold=0)  # OK
        AIConfigUpdate(ai_confidence_threshold=100)  # OK
        with pytest.raises(ValidationError):
            AIConfigUpdate(ai_confidence_threshold=101)

    def test_timeout_bounds(self):
        from app.models.schemas import AIConfigUpdate
        from pydantic import ValidationError

        AIConfigUpdate(ai_timeout_seconds=30)  # min OK
        AIConfigUpdate(ai_timeout_seconds=1800)  # max OK
        with pytest.raises(ValidationError):
            AIConfigUpdate(ai_timeout_seconds=10)  # too low

    def test_empty_update_produces_no_fields(self):
        from app.models.schemas import AIConfigUpdate

        update = AIConfigUpdate()
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Integrations Configuration Schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrationsConfigRead:
    def test_tokens_are_boolean_flags(self):
        from app.models.schemas import IntegrationsConfigRead

        cfg = IntegrationsConfigRead(
            jira_enabled=True, jira_domain="company.atlassian.net",
            jira_email="user@company.com", jira_token_set=True,
            jira_default_project_key="QA", splunk_enabled=False,
            splunk_base_url=None, splunk_token_set=False,
            ocp_enabled=False, ocp_api_url=None, ocp_token_set=False,
            ocp_default_namespace="qa-testing", slack_enabled=True,
            slack_webhook_url=None, slack_webhook_set=True,
            slack_default_channel="#qa", teams_enabled=False,
            teams_webhook_url=None, teams_webhook_set=False, github_repo="org/repo",
            github_token_set=True,
        )
        # Verify tokens are booleans, not raw values
        assert cfg.jira_token_set is True
        assert cfg.splunk_token_set is False
        assert cfg.slack_webhook_set is True
        assert not hasattr(cfg, "jira_api_token")


class TestIntegrationsConfigUpdate:
    def test_partial_update(self):
        from app.models.schemas import IntegrationsConfigUpdate

        update = IntegrationsConfigUpdate(jira_enabled=True, jira_domain="new.atlassian.net")
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {"jira_enabled": True, "jira_domain": "new.atlassian.net"}

    def test_token_field_present_for_update(self):
        from app.models.schemas import IntegrationsConfigUpdate

        update = IntegrationsConfigUpdate(jira_api_token="new-token-value")
        dumped = update.model_dump(exclude_none=True)
        assert "jira_api_token" in dumped


# ═══════════════════════════════════════════════════════════════════════════════
# Storage Configuration Schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestStorageConfigRead:
    def test_all_fields(self):
        from app.models.schemas import StorageConfigRead

        cfg = StorageConfigRead(
            storage_backend="minio",
            postgres_connected=True, mongo_connected=False, redis_connected=True,
            minio_endpoint="localhost:9000", minio_bucket_name="test-telemetry", minio_use_ssl=False,
            chroma_host="localhost", chroma_port=8001, chroma_collection="testlookup_embeddings",
        )
        assert cfg.storage_backend == "minio"
        assert cfg.postgres_connected is True
        assert cfg.mongo_connected is False
        assert cfg.chroma_collection == "testlookup_embeddings"


@pytest.mark.asyncio
async def test_storage_connection_status_reports_failed_probes(monkeypatch: pytest.MonkeyPatch):
    """A dependency outage must not be published as a green status badge."""
    from app.routers import app_settings

    mongo = MagicMock()
    mongo.command = AsyncMock(side_effect=ConnectionError("mongo unavailable"))
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(sys.modules["app.db.mongo"], "get_mongo_db", lambda: mongo)
    monkeypatch.setattr(sys.modules["app.db.redis_client"], "get_redis", lambda: redis)

    status = await app_settings._get_storage_connection_status()

    assert status == {
        "postgres_connected": True,
        "mongo_connected": False,
        "redis_connected": True,
    }


class TestStorageConfigUpdate:
    def test_partial_update(self):
        from app.models.schemas import StorageConfigUpdate

        update = StorageConfigUpdate(chroma_host="chromadb.internal", chroma_port=8888)
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {"chroma_host": "chromadb.internal", "chroma_port": 8888}

    def test_port_validation(self):
        from app.models.schemas import StorageConfigUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StorageConfigUpdate(chroma_port=0)  # min 1

        with pytest.raises(ValidationError):
            StorageConfigUpdate(chroma_port=70000)  # max 65535

    def test_empty_update(self):
        from app.models.schemas import StorageConfigUpdate

        update = StorageConfigUpdate()
        assert update.model_dump(exclude_none=True) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# SMTP Schemas (existing — regression guard)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmtpSchemas:
    def test_smtp_read_has_password_set_flag(self):
        from app.models.schemas import SmtpConfigRead

        cfg = SmtpConfigRead(
            enabled=True, host="smtp.gmail.com", port=465,
            user="user@gmail.com", from_address="noreply@testlookup.io",
            implicit_tls=True, password_set=True,
        )
        assert cfg.password_set is True
        assert not hasattr(cfg, "password")

    def test_smtp_update_port_validation(self):
        from app.models.schemas import SmtpConfigUpdate
        from pydantic import ValidationError

        SmtpConfigUpdate(port=1)  # min OK
        SmtpConfigUpdate(port=65535)  # max OK
        with pytest.raises(ValidationError):
            SmtpConfigUpdate(port=0)
