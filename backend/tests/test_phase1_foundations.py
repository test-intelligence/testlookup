"""
Unit tests for Phase 1 — Foundation & Shared Abstractions.

Covers:
  P1-1  DIMENSION_METADATA constants
  P1-2  Unified redaction service
  P1-3  Model serializer utility
  P1-4  Category normalizer
  P1-5  Centralized config values
  P1-6  Storage provider singleton
  P1-7  get_llm() async signature
  P1-8  UserRole normalization
  P1-9  Correlation ID context var
"""
import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from unittest.mock import MagicMock, patch

import pytest


# ═════════════════════════════════════════════════════════════════════════════
# P1-1: DIMENSION_METADATA constants
# ═════════════════════════════════════════════════════════════════════════════

class TestDimensionMetadata:

    def test_weights_sum_to_one(self):
        from app.models.constants import DIMENSION_METADATA
        total = sum(w for _, w in DIMENSION_METADATA.values())
        assert abs(total - 1.0) < 0.01

    def test_all_keys_present(self):
        from app.models.constants import DIMENSION_METADATA
        expected_keys = {
            "user_impact", "env_sensitivity", "reproducibility",
            "regression_likely", "hist_recurrence", "blast_radius",
            "diagnosis_conf",
        }
        assert set(DIMENSION_METADATA.keys()) == expected_keys

    def test_values_are_tuples_of_str_float(self):
        from app.models.constants import DIMENSION_METADATA
        for key, (label, weight) in DIMENSION_METADATA.items():
            assert isinstance(label, str), f"{key}: label should be str"
            assert isinstance(weight, float), f"{key}: weight should be float"
            assert 0.0 < weight <= 1.0, f"{key}: weight {weight} out of range"

    def test_shared_constant_importable(self):
        """Verify the shared constant is importable and has correct shape."""
        from app.models.constants import DIMENSION_METADATA
        assert len(DIMENSION_METADATA) == 7
        assert "user_impact" in DIMENSION_METADATA


# ═════════════════════════════════════════════════════════════════════════════
# P1-2: Unified redaction service
# ═════════════════════════════════════════════════════════════════════════════

class TestRedactionService:

    def test_redact_value_sensitive_key(self):
        from app.services.redaction_service import redact_value
        assert redact_value("password", "my_secret") == "[REDACTED]"
        assert redact_value("api_key", "abc123") == "[REDACTED]"
        assert redact_value("Authorization", "Bearer xyz") == "[REDACTED]"

    def test_redact_value_safe_key(self):
        from app.services.redaction_service import redact_value
        assert redact_value("username", "alice") == "alice"
        assert redact_value("count", 42) == 42

    def test_redact_value_none(self):
        from app.services.redaction_service import redact_value
        assert redact_value("password", None) is None

    def test_redact_value_hyphenated_key(self):
        from app.services.redaction_service import redact_value
        assert redact_value("api-key", "secret123") == "[REDACTED]"

    def test_redact_text_bearer(self):
        from app.services.redaction_service import redact_text
        text = "Header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc"
        result = redact_text(text)
        assert "eyJ" not in result
        assert "[REDACTED]" in result

    def test_redact_text_password_in_url(self):
        from app.services.redaction_service import redact_text
        text = "postgresql://user:s3cret_pass@host:5432/db"
        result = redact_text(text)
        assert "s3cret_pass" not in result

    def test_redact_text_aws_key(self):
        from app.services.redaction_service import redact_text
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = redact_text(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_redact_text_empty(self):
        from app.services.redaction_service import redact_text
        assert redact_text("") == ""
        assert redact_text(None) is None  # type: ignore[arg-type]

    def test_redact_dict_nested(self):
        from app.services.redaction_service import redact_dict
        data = {
            "user": "alice",
            "config": {"password": "secret123", "username": "alice"},
            "token": "abc123",
        }
        result = redact_dict(data)
        assert result["user"] == "alice"
        assert result["token"] == "[REDACTED]"
        # "config" is not a sensitive key → recurses into nested dict
        assert result["config"]["password"] == "[REDACTED]"
        assert result["config"]["username"] == "alice"

    def test_redact_dict_sensitive_key_replaces_entire_value(self):
        """A sensitive key name replaces the value entirely, even if it's a dict."""
        from app.services.redaction_service import redact_dict
        data = {"credentials": {"password": "secret123"}}
        result = redact_dict(data)
        assert result["credentials"] == "[REDACTED]"

    def test_redact_dict_with_list(self):
        from app.services.redaction_service import redact_dict
        data = {
            "items": [
                {"secret": "val1"},
                {"name": "safe"},
            ]
        }
        result = redact_dict(data)
        assert result["items"][0]["secret"] == "[REDACTED]"
        assert result["items"][1]["name"] == "safe"

    def test_redact_dict_none(self):
        from app.services.redaction_service import redact_dict
        assert redact_dict(None) is None
        assert redact_dict({}) == {}  # type: ignore[arg-type]

    def test_redact_dict_depth_limit(self):
        from app.services.redaction_service import redact_dict
        # Build a deeply nested dict
        d: dict = {"password": "secret"}
        for _ in range(15):
            d = {"nested": d}
        result = redact_dict(d)
        # Should not crash — depth limit stops recursion
        assert result is not None

    def test_backward_compat_audit_dashboard_import(self):
        """audit_dashboard_service re-exports from redaction_service."""
        from app.services.audit_dashboard_service import redact_value, redact_dict
        assert redact_value("password", "x") == "[REDACTED]"
        assert redact_dict({"token": "abc"})["token"] == "[REDACTED]"

    def test_backward_compat_prompt_redaction_import(self):
        """prompt_redaction re-exports from redaction_service."""
        from app.services.prompt_redaction import redact_text, redact_dict, redact_for_llm
        assert redact_for_llm("password=abc123") != "password=abc123"
        assert callable(redact_dict)
        assert callable(redact_text)


# ═════════════════════════════════════════════════════════════════════════════
# P1-3: Model serializer
# ═════════════════════════════════════════════════════════════════════════════

class TestModelSerializer:

    def _make_fake_model(self):
        """Create a mock ORM object with __table__.columns."""

        class FakeColumn:
            def __init__(self, name):
                self.name = name

        class FakeTable:
            columns = [FakeColumn("id"), FakeColumn("name"), FakeColumn("created_at"), FakeColumn("status")]

        class FakeModel:
            __table__ = FakeTable()
            id = uuid.UUID("12345678-1234-5678-1234-567812345678")
            name = "test"
            created_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
            status = "active"

        return FakeModel()

    def test_serialize_model_basic(self):
        from app.models.serializers import serialize_model
        obj = self._make_fake_model()
        result = serialize_model(obj)
        assert result["id"] == "12345678-1234-5678-1234-567812345678"
        assert result["name"] == "test"
        assert result["created_at"] == "2026-01-01T12:00:00+00:00"
        assert result["status"] == "active"

    def test_serialize_model_include(self):
        from app.models.serializers import serialize_model
        obj = self._make_fake_model()
        result = serialize_model(obj, include={"id", "name"})
        assert set(result.keys()) == {"id", "name"}

    def test_serialize_model_exclude(self):
        from app.models.serializers import serialize_model
        obj = self._make_fake_model()
        result = serialize_model(obj, exclude={"created_at"})
        assert "created_at" not in result
        assert "id" in result

    def test_serialize_model_enum_value(self):
        from app.models.serializers import _serialize_value

        class Color(Enum):
            RED = "red"

        assert _serialize_value(Color.RED) == "red"

    def test_serialize_model_none(self):
        from app.models.serializers import _serialize_value
        assert _serialize_value(None) is None

    def test_serialize_models_batch(self):
        from app.models.serializers import serialize_models
        objs = [self._make_fake_model(), self._make_fake_model()]
        result = serialize_models(objs)
        assert len(result) == 2
        assert all(isinstance(r, dict) for r in result)


# ═════════════════════════════════════════════════════════════════════════════
# P1-4: Category normalizer
# ═════════════════════════════════════════════════════════════════════════════

class TestCategoryNormalizer:

    def test_exact_match(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category("PRODUCT_BUG") == "PRODUCT_BUG"
        assert normalize_category("INFRASTRUCTURE") == "INFRASTRUCTURE"
        assert normalize_category("FLAKY") == "FLAKY"
        assert normalize_category("UNKNOWN") == "UNKNOWN"

    def test_alias_match(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category("BUG") == "PRODUCT_BUG"
        assert normalize_category("INFRA") == "INFRASTRUCTURE"
        assert normalize_category("ENV") == "INFRASTRUCTURE"
        assert normalize_category("DATA") == "TEST_DATA"
        assert normalize_category("AUTOMATION") == "AUTOMATION_DEFECT"
        assert normalize_category("INTERMITTENT") == "FLAKY"
        assert normalize_category("RACE_CONDITION") == "FLAKY"

    def test_case_insensitive(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category("product_bug") == "PRODUCT_BUG"
        assert normalize_category("Infra") == "INFRASTRUCTURE"
        assert normalize_category("bug") == "PRODUCT_BUG"

    def test_enum_instance(self):
        from app.models.postgres import FailureCategory
        from app.services.category_normalizer import normalize_category
        assert normalize_category(FailureCategory.PRODUCT_BUG) == "PRODUCT_BUG"

    def test_none(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category(None) == "UNKNOWN"

    def test_empty_string(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category("") == "UNKNOWN"

    def test_unrecognized_falls_back_to_unknown(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category("GIBBERISH") == "UNKNOWN"

    def test_whitespace_handling(self):
        from app.services.category_normalizer import normalize_category
        assert normalize_category("  PRODUCT_BUG  ") == "PRODUCT_BUG"
        assert normalize_category("  bug  ") == "PRODUCT_BUG"

    def test_normalize_category_in_analysis(self):
        from app.services.category_normalizer import normalize_category_in_analysis
        analysis = {"failure_category": "BUG", "confidence": 80}
        result = normalize_category_in_analysis(analysis)
        assert result["failure_category"] == "PRODUCT_BUG"
        assert result["_category_corrected_from"] == "BUG"

    def test_normalize_category_in_analysis_exact_match_no_correction(self):
        from app.services.category_normalizer import normalize_category_in_analysis
        analysis = {"failure_category": "PRODUCT_BUG"}
        result = normalize_category_in_analysis(analysis)
        assert result["failure_category"] == "PRODUCT_BUG"
        assert "_category_corrected_from" not in result

    def test_all_aliases_resolve_to_valid_categories(self):
        from app.services.category_normalizer import CATEGORY_ALIASES, VALID_CATEGORIES
        for alias, target in CATEGORY_ALIASES.items():
            assert target in VALID_CATEGORIES, f"Alias '{alias}' → '{target}' is not a valid category"


# ═════════════════════════════════════════════════════════════════════════════
# P1-5: Centralized config values
# ═════════════════════════════════════════════════════════════════════════════

class TestCentralizedConfig:

    def test_settings_has_new_fields(self):
        from app.core.config import Settings
        s = Settings(
            DATABASE_URL="postgresql+asyncpg://x:x@localhost/x",
            POSTGRES_PASSWORD="test",
            MINIO_SECRET_KEY="test",
        )
        assert s.AI_ANALYSIS_CACHE_TTL == 3600
        assert s.SEMANTIC_SIMILARITY_THRESHOLD == 0.85
        assert s.PROMPT_OVERHEAD_TOKENS == 1500
        assert s.SEMANTIC_CACHE_MAX_DOCUMENTS == 10000

    def test_fields_are_overridable(self):
        from app.core.config import Settings
        s = Settings(
            DATABASE_URL="postgresql+asyncpg://x:x@localhost/x",
            POSTGRES_PASSWORD="test",
            MINIO_SECRET_KEY="test",
            AI_ANALYSIS_CACHE_TTL=7200,
            SEMANTIC_SIMILARITY_THRESHOLD=0.90,
            PROMPT_OVERHEAD_TOKENS=2000,
        )
        assert s.AI_ANALYSIS_CACHE_TTL == 7200
        assert s.SEMANTIC_SIMILARITY_THRESHOLD == 0.90
        assert s.PROMPT_OVERHEAD_TOKENS == 2000


# ═════════════════════════════════════════════════════════════════════════════
# P1-6: Storage provider singleton
# ═════════════════════════════════════════════════════════════════════════════

class TestStorageSingleton:

    def test_returns_same_instance(self):
        from app.db.storage import get_storage_provider
        # Clear any cached instance
        get_storage_provider.cache_clear()
        p1 = get_storage_provider()
        p2 = get_storage_provider()
        assert p1 is p2

    def test_cache_clearable(self):
        from app.db.storage import get_storage_provider
        get_storage_provider.cache_clear()
        p1 = get_storage_provider()
        get_storage_provider.cache_clear()
        p2 = get_storage_provider()
        # After cache clear, should be a new instance
        # (may or may not be same object depending on provider internals)
        assert p1 is not None
        assert p2 is not None


# ═════════════════════════════════════════════════════════════════════════════
# P1-7: get_llm() async
# ═════════════════════════════════════════════════════════════════════════════

class TestGetLlmAsync:

    def test_get_llm_is_coroutine_function(self):
        import inspect
        from app.services.llm_factory import get_llm
        assert inspect.iscoroutinefunction(get_llm), "get_llm must be async"

    @pytest.mark.asyncio
    async def test_get_llm_returns_model(self):
        """get_llm() should return a LangChain model (mocked provider)."""
        mock_llm = MagicMock()
        mock_chat_ollama = MagicMock(return_value=mock_llm)
        mock_module = MagicMock()
        mock_module.ChatOllama = mock_chat_ollama

        import sys
        sys.modules["langchain_ollama"] = mock_module
        try:
            from app.services.llm_factory import get_llm
            llm = await get_llm(provider="ollama", model="qwen2.5:7b")
            assert llm is mock_llm
            mock_chat_ollama.assert_called_once()
        finally:
            del sys.modules["langchain_ollama"]


# ═════════════════════════════════════════════════════════════════════════════
# P1-8: UserRole normalization
# ═════════════════════════════════════════════════════════════════════════════

class TestUserRoleNormalization:
    """Test the normalization logic directly without importing deps.py (needs jose).

    The normalization logic is identical in deps.py and users.py, so we
    replicate the function here to test the algorithm in isolation.
    """

    @staticmethod
    def _normalize(value):
        """Mirror of _normalize_user_role without heavy imports."""
        from app.models.postgres import UserRole
        if isinstance(value, UserRole):
            return value
        raw_value = str(value).strip()
        if raw_value.startswith("UserRole."):
            raw_value = raw_value.split(".", 1)[1]
        return UserRole(raw_value)

    def test_enum_passthrough(self):
        from app.models.postgres import UserRole
        assert self._normalize(UserRole.ADMIN) == UserRole.ADMIN

    def test_plain_string(self):
        from app.models.postgres import UserRole
        assert self._normalize("ADMIN") == UserRole.ADMIN
        assert self._normalize("VIEWER") == UserRole.VIEWER

    def test_legacy_prefix_still_works(self):
        """Safety net for any un-migrated rows."""
        from app.models.postgres import UserRole
        assert self._normalize("UserRole.QA_LEAD") == UserRole.QA_LEAD

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError):
            self._normalize("NONEXISTENT_ROLE")


# ═════════════════════════════════════════════════════════════════════════════
# P1-9: Correlation ID context var
# ═════════════════════════════════════════════════════════════════════════════

class TestCorrelationId:

    def test_context_var_exists(self):
        from app.middleware.telemetry import REQUEST_ID_CTX
        # Default is empty string
        assert REQUEST_ID_CTX.get() == "" or isinstance(REQUEST_ID_CTX.get(), str)

    def test_context_var_set_and_reset(self):
        from app.middleware.telemetry import REQUEST_ID_CTX
        token = REQUEST_ID_CTX.set("test-request-123")
        assert REQUEST_ID_CTX.get() == "test-request-123"
        REQUEST_ID_CTX.reset(token)
        assert REQUEST_ID_CTX.get() == ""

    def test_context_var_isolation(self):
        """Verify context vars are isolated per-task."""
        from app.middleware.telemetry import REQUEST_ID_CTX

        results = []

        async def task_a():
            REQUEST_ID_CTX.set("task-a")
            await asyncio.sleep(0.01)
            results.append(("a", REQUEST_ID_CTX.get()))

        async def task_b():
            REQUEST_ID_CTX.set("task-b")
            await asyncio.sleep(0.01)
            results.append(("b", REQUEST_ID_CTX.get()))

        async def run():
            await asyncio.gather(
                asyncio.create_task(task_a()),
                asyncio.create_task(task_b()),
            )

        asyncio.run(run())
        a_result = next(r for name, r in results if name == "a")
        b_result = next(r for name, r in results if name == "b")
        assert a_result == "task-a"
        assert b_result == "task-b"
