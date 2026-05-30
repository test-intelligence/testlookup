"""
Runtime AI Configuration Resolver — single source of truth for effective AI config.

LP-1: Merges database-backed settings, secret-backed API keys, and environment
defaults into one effective configuration dict used by get_llm() and all AI paths.

Resolution precedence:
  1. Database overrides (app_settings key "ai_config")
  2. Secret refs (encrypted API keys)
  3. Environment defaults (from config.py)

Results are cached in Redis for 60 seconds to avoid per-request DB queries.

Usage:
    from app.services.ai_config_resolver import get_effective_ai_config, invalidate_ai_config_cache
    config = await get_effective_ai_config()
    # config["provider"], config["model"], config["api_key"], etc.
"""
import json
import structlog
from typing import Any

from app.core.config import settings

logger = structlog.get_logger("services.ai_config_resolver")

_CACHE_KEY = "config:ai_effective"
_CACHE_TTL = 60  # seconds


async def get_effective_ai_config() -> dict[str, Any]:
    """Return the merged effective AI configuration.

    Checks Redis cache first, falls back to DB + secrets + env.
    """
    # 1. Try Redis cache
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cached = await redis.get(_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass  # Redis unavailable — fall through to DB

    # 2. Load from DB + secrets + env
    config = await _load_from_db_and_env()

    # 3. Cache in Redis
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(config))
    except Exception:
        pass  # Redis unavailable — continue without caching

    return config


async def invalidate_ai_config_cache() -> None:
    """Bust the Redis cache so the next get_effective_ai_config() reloads from DB."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.delete(_CACHE_KEY)
        logger.info("ai_config_cache_invalidated")
    except Exception:
        pass


async def _load_from_db_and_env() -> dict[str, Any]:
    """Load AI config from database overrides + secret_refs + environment defaults."""
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AppSetting
    from app.services.secret_service import read_secret
    from sqlalchemy import select

    # Default from env
    config: dict[str, Any] = {
        "provider": settings.LLM_PROVIDER,
        "model": settings.LLM_MODEL,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "base_url": None,
        "offline_mode": settings.AI_OFFLINE_MODE,
        "timeout_seconds": settings.AI_TIMEOUT_SECONDS,
        "openai_api_key": settings.OPENAI_API_KEY,
        "google_api_key": settings.GOOGLE_API_KEY,
        "anthropic_api_key": getattr(settings, "ANTHROPIC_API_KEY", None),
    }

    try:
        async with AsyncSessionLocal() as db:
            # Load DB overrides
            result = await db.execute(
                select(AppSetting).where(AppSetting.key == "ai_config")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                overrides = dict(row.value)
                config["provider"] = overrides.get("llm_provider", config["provider"])
                config["model"] = overrides.get("llm_model", config["model"])
                config["temperature"] = overrides.get("llm_temperature", config["temperature"])
                config["max_tokens"] = overrides.get("llm_max_tokens", config["max_tokens"])
                config["base_url"] = overrides.get("base_url", config["base_url"])
                config["offline_mode"] = overrides.get("ai_offline_mode", config["offline_mode"])
                config["timeout_seconds"] = overrides.get("ai_timeout_seconds", config["timeout_seconds"])

            # Load secrets from secret_refs (override env-only keys)
            for key_name in ("openai_api_key", "google_api_key", "anthropic_api_key"):
                secret = await read_secret(db, "ai_config", key_name)
                if secret:
                    config[key_name] = secret

    except Exception as exc:
        logger.warning("ai_config_db_load_failed", error=str(exc))
        # Fall through with env defaults

    return config
