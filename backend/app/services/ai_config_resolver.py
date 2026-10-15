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

Stored settings the runtime obeys (AIC-04 / AIC-01 / AIC-02)
------------------------------------------------------------
``analysis_mode``, ``deep_investigation_enabled``, ``finetune_enabled`` and
``timeout_seconds`` follow DB -> env in BOTH directions (a stored ``False``
overrides an env ``True`` and vice versa). Each comes with a ``*_source``
("ai_config" | "env_default") decided by ONE pure helper,
:func:`stored_or_default`, which ``routers/app_settings._load_ai_config`` uses
too, so ``GET /settings/ai`` and the runtime can never disagree. An invalid
stored value (``"false"``, ``0``, ``"turbo"``, a timeout outside 5..1800) is
ignored: the env default wins and the source says ``env_default``.

Results are cached in Redis for 60 seconds to avoid per-request DB queries,
under a VERSIONED key (``config:ai_effective:v2``): an old-build pod keeps
writing the v1 key without the new fields during a rolling deploy, and the new
code must never read such an entry.

Usage:
    from app.services.ai_config_resolver import get_effective_ai_config, invalidate_ai_config_cache
    config = await get_effective_ai_config()
    # config["provider"], config["model"], config["api_key"], etc.
"""
import json
import structlog
from typing import Any, Callable, Mapping, TypeVar

from app.core.config import settings

logger = structlog.get_logger("services.ai_config_resolver")

# v2 (W04): the payload gained analysis_mode / deep_investigation_enabled /
# finetune_enabled and four ``*_source`` keys. A pod on the previous build keeps
# writing the v1 key without them during a rolling deploy, so the new code
# reads and writes only v2, and invalidation deletes both.
_CACHE_KEY = "config:ai_effective:v2"
_LEGACY_CACHE_KEYS: tuple[str, ...] = ("config:ai_effective",)
_CACHE_TTL = 60  # seconds

# The non-expiring accelerator for the CONFIGURED analysis mode (AIC-04). Only
# a value that came from the stored row is ever written here (by
# ``PUT /settings/ai`` and by ``analysis_router.resolve_configured_analysis_mode``
# on a miss), so a hit always means "stored". Deleted by
# :func:`invalidate_ai_config_cache`.
ANALYSIS_MODE_CACHE_KEY = "config:analysis_mode"

# ONE provenance vocabulary for every ``*_source`` field of the effective config.
SOURCE_AI_CONFIG = "ai_config"      # a valid value is stored in app_settings["ai_config"]
SOURCE_ENV_DEFAULT = "env_default"  # nothing valid stored; the Settings default applies

ANALYSIS_MODES: tuple[str, ...] = ("llm", "ml", "rules", "auto")
TIMEOUT_SECONDS_MIN = 5
TIMEOUT_SECONDS_MAX = 1800

_T = TypeVar("_T")

# Provenance values for ``offline_mode_source``.
OFFLINE_SOURCE_ENV = "env"              # forced by AI_OFFLINE_MODE in the environment
OFFLINE_SOURCE_OVERRIDE = "override"    # env permits egress; the DB setting turned it off
OFFLINE_SOURCE_NONE = "not_offline"     # egress permitted

# Provenance values for ``confidence_threshold_source`` (US-15.2). Duplicated
# as literals rather than imported from ``confidence_gate`` — that module
# imports this one, and a module-level import back would be circular. The
# values are asserted equal in tests/test_ai_trust_provenance.py. Aliases of
# the shared vocabulary above.
CONFIDENCE_SOURCE_AI_CONFIG = SOURCE_AI_CONFIG
CONFIDENCE_SOURCE_ENV_DEFAULT = SOURCE_ENV_DEFAULT


# ── stored value or env default (one helper for the runtime AND the API) ─────


def valid_analysis_mode(value: Any) -> str | None:
    """A stored analysis mode, normalised, or None when it is not one of ANALYSIS_MODES."""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    return mode if mode in ANALYSIS_MODES else None


def valid_bool(value: Any) -> bool | None:
    """A real ``bool`` only. ``"false"`` and ``0`` are NOT booleans: a hand-edited
    row holding the string "false" would otherwise read as truthy."""
    return value if isinstance(value, bool) else None


def valid_timeout_seconds(value: Any) -> int | None:
    """An ``int`` (not a ``bool``) in TIMEOUT_SECONDS_MIN..TIMEOUT_SECONDS_MAX."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if TIMEOUT_SECONDS_MIN <= value <= TIMEOUT_SECONDS_MAX else None


def stored_or_default(
    overrides: Mapping[str, Any] | None,
    key: str,
    default: _T,
    validate: Callable[[Any], _T | None],
) -> tuple[_T, str]:
    """``(value, source)`` for one stored setting.

    A valid stored value wins (source ``ai_config``); an absent or INVALID one
    is ignored and ``default`` applies (source ``env_default``). Precedence is
    DB -> env in both directions: a stored ``False`` overrides an env ``True``.
    """
    if overrides and key in overrides:
        value = validate(overrides[key])
        if value is not None:
            return value, SOURCE_AI_CONFIG
    return default, SOURCE_ENV_DEFAULT


def env_default_analysis_mode() -> str:
    """``settings.ANALYSIS_MODE`` normalised; ``"auto"`` when it is not a valid mode."""
    return valid_analysis_mode(settings.ANALYSIS_MODE) or "auto"


def resolve_stored_toggles(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """The four AIC-04/01/02 settings and their sources, keyed by their STORED
    (``app_settings["ai_config"]``) names. Shared by the runtime resolver and
    ``routers/app_settings._load_ai_config``."""
    mode, mode_source = stored_or_default(
        overrides, "analysis_mode", env_default_analysis_mode(), valid_analysis_mode
    )
    timeout, timeout_source = stored_or_default(
        overrides, "ai_timeout_seconds", settings.AI_TIMEOUT_SECONDS, valid_timeout_seconds
    )
    deep, deep_source = stored_or_default(
        overrides,
        "deep_investigation_enabled",
        bool(settings.DEEP_INVESTIGATION_ENABLED),
        valid_bool,
    )
    finetune, finetune_source = stored_or_default(
        overrides, "finetune_enabled", bool(settings.FINETUNE_ENABLED), valid_bool
    )
    return {
        "analysis_mode": mode,
        "analysis_mode_source": mode_source,
        "ai_timeout_seconds": timeout,
        "ai_timeout_seconds_source": timeout_source,
        "deep_investigation_enabled": deep,
        "deep_investigation_enabled_source": deep_source,
        "finetune_enabled": finetune,
        "finetune_enabled_source": finetune_source,
    }


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
    """Bust the Redis caches so the next read reloads from DB.

    Deletes the v2 effective-config key, the legacy v1 key (an old-build pod
    may still be serving it) and the analysis-mode accelerator, which never
    expires on its own: every application write path that changes
    ``ai_config`` calls this, and ``PUT /settings/ai`` re-seeds the mode right
    after.
    """
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.delete(_CACHE_KEY, *_LEGACY_CACHE_KEYS, ANALYSIS_MODE_CACHE_KEY)
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
        # Strategy for the RAG faithfulness gate: "ollama" (single prompt
        # through get_llm) or "ragas". No env var; only a stored override.
        "rag_faithfulness_evaluator": "ollama",
        "openai_api_key": settings.OPENAI_API_KEY,
        "google_api_key": settings.GOOGLE_API_KEY,
        "anthropic_api_key": getattr(settings, "ANTHROPIC_API_KEY", None),
        "openrouter_api_key": getattr(settings, "OPENROUTER_API_KEY", None),
    }

    def _apply_stored_toggles(overrides: Mapping[str, Any] | None) -> None:
        toggles = resolve_stored_toggles(overrides)
        config["analysis_mode"] = toggles["analysis_mode"]
        config["analysis_mode_source"] = toggles["analysis_mode_source"]
        config["timeout_seconds"] = toggles["ai_timeout_seconds"]
        config["timeout_seconds_source"] = toggles["ai_timeout_seconds_source"]
        config["deep_investigation_enabled"] = toggles["deep_investigation_enabled"]
        config["deep_investigation_enabled_source"] = toggles["deep_investigation_enabled_source"]
        config["finetune_enabled"] = toggles["finetune_enabled"]
        config["finetune_enabled_source"] = toggles["finetune_enabled_source"]

    # analysis_mode / timeout / deep / finetune + their sources: env defaults
    # first (also what a DB failure leaves in force).
    _apply_stored_toggles(None)

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
            # Validated, DB -> env in both directions, with provenance.
            _apply_stored_toggles(overrides)
            config["rag_faithfulness_evaluator"] = overrides.get(
                "rag_faithfulness_evaluator", config["rag_faithfulness_evaluator"]
            )
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
