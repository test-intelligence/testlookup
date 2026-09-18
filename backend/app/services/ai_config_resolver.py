"""
Runtime AI Configuration Resolver — single source of truth for effective AI config.

LP-1: Merges database-backed settings, secret-backed API keys, and environment
defaults into one effective configuration dict used by get_llm() and all AI paths.

Resolution precedence:
  1. Database overrides (app_settings key "ai_config")
  2. Secret refs (encrypted API keys)
  3. Environment defaults (from config.py)

…with ONE exception, deliberately: ``offline_mode``. See below.

Offline mode is a one-way ratchet, not an override
--------------------------------------------------
``settings.AI_OFFLINE_MODE`` (environment) is a **hard ceiling** on outbound
LLM egress. The database override may only ever make the system *more*
restrictive:

    effective_offline = env_offline OR db_override_offline

Rationale: every other outbound integration (Jira, webhooks, GitHub, GitLab,
Fixer, Investigator) reads ``settings.AI_OFFLINE_MODE`` directly, so the env
var really is a kill switch for them. Before this ratchet, LLM egress alone
honoured a DB override that any ADMIN could flip from ``/settings/ai`` — an
air-gapped operator who set ``AI_OFFLINE_MODE=true`` could still have cloud
API calls leave the box with no environment change. The env var now means the
same thing everywhere.

Provenance is published alongside the flag so the reason is debuggable rather
than mysterious:

  ``offline_mode``            effective boolean — what callers must obey
  ``offline_mode_source``     "env" | "override" | "not_offline"
  ``offline_mode_env_pinned`` True when the environment forces offline

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

# Provenance values for ``offline_mode_source``.
OFFLINE_SOURCE_ENV = "env"              # forced by AI_OFFLINE_MODE in the environment
OFFLINE_SOURCE_OVERRIDE = "override"    # env permits egress; the DB setting turned it off
OFFLINE_SOURCE_NONE = "not_offline"     # egress permitted

# Provenance values for ``confidence_threshold_source`` (US-15.2). Duplicated
# as literals rather than imported from ``confidence_gate`` — that module
# imports this one, and a module-level import back would be circular. The
# values are asserted equal in tests/test_ai_trust_provenance.py.
CONFIDENCE_SOURCE_AI_CONFIG = "ai_config"
CONFIDENCE_SOURCE_ENV_DEFAULT = "env_default"


def env_offline_pinned() -> bool:
    """True when the environment pins offline mode on (the ceiling is active)."""
    return bool(settings.AI_OFFLINE_MODE)


def resolve_offline_mode(db_override: Any = None) -> tuple[bool, str]:
    """Resolve ``(effective_offline, source)`` from the env ceiling + DB override.

    The env var can only tighten, never loosen: ``env OR override``. Callers
    that need to explain the result to a human use the returned source.
    """
    if env_offline_pinned():
        return True, OFFLINE_SOURCE_ENV
    if bool(db_override):
        return True, OFFLINE_SOURCE_OVERRIDE
    return False, OFFLINE_SOURCE_NONE


def apply_offline_ceiling(config: dict[str, Any], *, log_suppressed: bool = False) -> dict[str, Any]:
    """Clamp ``config["offline_mode"]`` to the env ceiling and stamp provenance.

    Applied on BOTH the DB-load path and the Redis cache-hit path: a cache
    entry written by an older build (or by a process that read a different
    environment) must not be able to re-enable egress for up to _CACHE_TTL.
    """
    requested = config.get("offline_mode", settings.AI_OFFLINE_MODE)
    effective, source = resolve_offline_mode(requested)
    if log_suppressed and effective and not requested:
        logger.warning(
            "ai_offline_override_suppressed",
            requested_offline_mode=False,
            effective_offline_mode=True,
            source=source,
            detail=(
                "A stored ai_config override tried to disable offline mode, but "
                "AI_OFFLINE_MODE is true in this deployment's environment. Cloud "
                "LLM egress stays blocked. Set AI_OFFLINE_MODE=false to permit it."
            ),
        )
    config["offline_mode"] = effective
    config["offline_mode_source"] = source
    config["offline_mode_env_pinned"] = env_offline_pinned()
    return config


async def get_effective_ai_config(
    *,
    db: Any = None,
    fresh: bool = False,
) -> dict[str, Any]:
    """Return the merged effective AI configuration.

    Checks Redis cache first, falls back to DB + secrets + env.
    """
    # 1. Try Redis cache. Authority snapshots use the caller's locked database
    # transaction so they cannot observe the post-commit/pre-invalidation gap.
    if not fresh:
        try:
            from app.db.redis_client import get_redis
            redis = get_redis()
            cached = await redis.get(_CACHE_KEY)
            if cached:
                # Re-clamp: the ceiling is an environment property, never a cached one.
                return apply_offline_ceiling(json.loads(cached))
        except Exception:
            pass  # Redis unavailable — fall through to DB

    # 2. Load from DB + secrets + env
    config = await _load_from_db_and_env(db=db)

    # 3. Cache in Redis
    if not fresh:
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


async def _load_from_db_and_env(*, db: Any = None) -> dict[str, Any]:
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
        # US-15.2: the confidence gate every automation reads. Published here
        # (rather than resolved separately) so it shares this resolver's cache
        # and DB-failure fallback. ``_source`` is provenance for the recorded
        # threshold check — see services/confidence_gate.py.
        "confidence_threshold": settings.AI_CONFIDENCE_THRESHOLD,
        "confidence_threshold_source": CONFIDENCE_SOURCE_ENV_DEFAULT,
        "openai_api_key": settings.OPENAI_API_KEY,
        "google_api_key": settings.GOOGLE_API_KEY,
        "anthropic_api_key": getattr(settings, "ANTHROPIC_API_KEY", None),
        "openrouter_api_key": getattr(settings, "OPENROUTER_API_KEY", None),
    }

    async def _merge_from_db(session: Any) -> None:
        # Load DB overrides
        result = await session.execute(
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
            from app.services.confidence_gate import normalize_threshold

            stored_threshold = normalize_threshold(overrides.get("ai_confidence_threshold"))
            if stored_threshold is not None:
                config["confidence_threshold"] = stored_threshold
                config["confidence_threshold_source"] = CONFIDENCE_SOURCE_AI_CONFIG

        for key_name in (
            "openai_api_key", "google_api_key",
            "anthropic_api_key", "openrouter_api_key",
        ):
            secret = await read_secret(session, "ai_config", key_name)
            if secret:
                config[key_name] = secret

    try:
        if db is None:
            async with AsyncSessionLocal() as owned_db:
                await _merge_from_db(owned_db)
        else:
            await _merge_from_db(db)

    except Exception as exc:
        logger.warning("ai_config_db_load_failed", error=str(exc))
        # Fall through with env defaults

    # The env ceiling is applied LAST so no override path can slip past it.
    return apply_offline_ceiling(config, log_suppressed=True)
