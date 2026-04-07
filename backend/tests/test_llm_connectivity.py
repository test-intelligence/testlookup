"""
Tests for LLM Provider Connectivity (LP-1 through LP-8).

Covers:
  - AI config resolver: DB override vs env fallback
  - AIConfigRead/Update schemas with Anthropic + base_url fields
  - Secret service: anthropic_api_key in SECRET_FIELDS
  - Provider factory: supported providers list includes anthropic
  - Config.LLM_PROVIDER literal includes anthropic
"""
import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LP-1: AI Config Resolver
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.core.config import settings  # noqa: E402


class TestConfigDefaults:
    def test_llm_provider_default(self):
        assert settings.LLM_PROVIDER in ("ollama", "lmstudio", "localai", "vllm", "openai", "gemini", "anthropic")

    def test_anthropic_api_key_exists(self):
        assert hasattr(settings, "ANTHROPIC_API_KEY")

    def test_llm_provider_literal_includes_anthropic(self):
        """The LLM_PROVIDER Literal type must include 'anthropic'."""
        from app.core.config import Settings
        field = Settings.model_fields["LLM_PROVIDER"]
        # The annotation is a Literal — check that anthropic is in the allowed values
        annotation = field.annotation
        assert "anthropic" in str(annotation)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LP-2/3: Schema Updates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import AIConfigRead, AIConfigUpdate  # noqa: E402


class TestAIConfigSchemas:
    def test_read_has_anthropic_key_set(self):
        cfg = AIConfigRead(
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-20250514",
            llm_temperature=0.1,
            llm_max_tokens=4096,
            ai_offline_mode=False,
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
            ai_confidence_threshold=80,
            ai_timeout_seconds=300,
            deep_investigation_enabled=True,
            finetune_enabled=False,
            openai_key_set=False,
            google_key_set=False,
            anthropic_key_set=True,
            analysis_mode="llm",
        )
        assert cfg.anthropic_key_set is True
        assert cfg.llm_provider == "anthropic"

    def test_read_has_base_url(self):
        cfg = AIConfigRead(
            llm_provider="openai",
            llm_model="gpt-4o",
            llm_temperature=0.0,
            llm_max_tokens=4096,
            ai_offline_mode=False,
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            ai_confidence_threshold=80,
            ai_timeout_seconds=300,
            deep_investigation_enabled=True,
            finetune_enabled=False,
            openai_key_set=True,
            google_key_set=False,
            base_url="https://api.openai.com/v1",
            analysis_mode="llm",
        )
        assert cfg.base_url == "https://api.openai.com/v1"

    def test_read_defaults_anthropic_to_false(self):
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
        assert cfg.anthropic_key_set is False
        assert cfg.base_url is None

    def test_update_accepts_anthropic_key(self):
        update = AIConfigUpdate(
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-20250514",
            anthropic_api_key="sk-ant-test-key-123",
        )
        assert update.anthropic_api_key == "sk-ant-test-key-123"

    def test_update_accepts_base_url(self):
        update = AIConfigUpdate(
            base_url="https://custom-gateway.example.com/v1",
        )
        assert update.base_url == "https://custom-gateway.example.com/v1"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LP-3: Secret Service Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.secret_service import SECRET_FIELDS  # noqa: E402


class TestSecretFieldsConfig:
    def test_anthropic_in_ai_config_secrets(self):
        assert "anthropic_api_key" in SECRET_FIELDS["ai_config"]

    def test_openai_still_in_secrets(self):
        assert "openai_api_key" in SECRET_FIELDS["ai_config"]

    def test_google_still_in_secrets(self):
        assert "google_api_key" in SECRET_FIELDS["ai_config"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LP-1: Resolver Module Exists
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResolverModule:
    def test_resolver_importable(self):
        from app.services.ai_config_resolver import get_effective_ai_config, invalidate_ai_config_cache
        assert callable(get_effective_ai_config)
        assert callable(invalidate_ai_config_cache)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LP-5: LLM Factory Supports All Providers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLLMFactoryProviderSupport:
    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self):
        from app.services.llm_factory import get_llm
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            await get_llm(provider="nonexistent")

    @pytest.mark.asyncio
    async def test_anthropic_offline_raises(self):
        """Anthropic should refuse when offline mode is active."""
        from app.services.llm_factory import get_llm
        from unittest.mock import patch, AsyncMock
        with patch("app.services.ai_config_resolver.get_effective_ai_config", new_callable=AsyncMock, return_value={"offline_mode": True, "anthropic_api_key": "key"}):
            with pytest.raises(ValueError, match="AI_OFFLINE_MODE"):
                await get_llm(provider="anthropic")

    @pytest.mark.asyncio
    async def test_openai_offline_raises(self):
        from app.services.llm_factory import get_llm
        from unittest.mock import patch, AsyncMock
        with patch("app.services.ai_config_resolver.get_effective_ai_config", new_callable=AsyncMock, return_value={"offline_mode": True, "openai_api_key": "key"}):
            with pytest.raises(ValueError, match="AI_OFFLINE_MODE"):
                await get_llm(provider="openai")
