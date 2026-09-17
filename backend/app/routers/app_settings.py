"""Application settings management — SMTP, AI, Integrations, Storage configuration.

Security model:
  - GET endpoints: QA_LEAD or higher (secrets are never returned raw — only *_set booleans)
  - PUT endpoints: ADMIN only (audit-logged, secrets stored in secret_refs table)
"""
import asyncio
import logging
from typing import Optional

import aiosmtplib
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import AppSetting, User, UserRole
from app.services.secret_service import extract_secrets_from_config, store_secret, strip_secrets_from_config
from app.services.settings_audit_service import get_settings_audit_log, log_settings_change
from app.models.schemas import (
    AIConfigRead,
    AIConfigUpdate,
    IntegrationsConfigRead,
    IntegrationsConfigUpdate,
    MfaPolicyRead,
    MfaPolicyUpdate,
    SmtpConfigRead,
    SmtpConfigUpdate,
    SmtpTestResult,
    StorageConfigRead,
    StorageConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settings", tags=["Settings"])

_SMTP_KEY = "smtp_config"
_SMTP_SECRET_FIELD = "password"


def _build_smtp_config_read(
    *,
    enabled: bool,
    host: str,
    port: int,
    user: str | None,
    from_address: str,
    implicit_tls: bool,
    has_secret_material: bool,
) -> SmtpConfigRead:
    smtp_payload = {
        "enabled": enabled,
        "host": host,
        "port": port,
        "user": user,
        "from_address": from_address,
        "implicit_tls": implicit_tls,
        "password_set": has_secret_material,
    }
    return SmtpConfigRead.model_validate(smtp_payload)


async def _load_smtp_row(db: AsyncSession) -> dict:
    """Return the stored SMTP config dict, falling back to env-var defaults.
    Password is read from secret_refs (never from app_settings.value)."""
    from sqlalchemy import select
    from app.services.secret_service import read_secret

    result = await db.execute(select(AppSetting).where(AppSetting.key == _SMTP_KEY))
    row = result.scalar_one_or_none()

    if row and row.value:
        cfg = dict(row.value)
        # Read password from secret_refs (encrypted)
        secret_value = await read_secret(db, _SMTP_KEY, _SMTP_SECRET_FIELD)
        cfg[_SMTP_SECRET_FIELD] = secret_value or cfg.get(_SMTP_SECRET_FIELD) or settings.SMTP_PASSWORD
        return cfg

    # Env-var defaults
    return {
        "enabled": settings.SMTP_ENABLED,
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "user": settings.SMTP_USER,
        _SMTP_SECRET_FIELD: settings.SMTP_PASSWORD,
        "from_address": settings.SMTP_FROM,
        "tls": settings.SMTP_TLS,
    }


@router.get("/smtp", response_model=SmtpConfigRead)
async def get_smtp_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> SmtpConfigRead:
    """Return current SMTP configuration (password is never returned — only a boolean flag)."""
    from app.services.secret_service import has_secret

    cfg = await _load_smtp_row(db)
    # Check secret_refs for password existence (not the raw value)
    smtp_secret_present = await has_secret(db, _SMTP_KEY, _SMTP_SECRET_FIELD) or bool(settings.SMTP_PASSWORD)
    return _build_smtp_config_read(
        enabled=cfg.get("enabled", False),
        host=cfg.get("host", "localhost"),
        port=cfg.get("port", 587),
        user=cfg.get("user") or None,
        from_address=cfg.get("from_address", "noreply@testlookup.io"),
        implicit_tls=cfg.get("tls", True),
        has_secret_material=smtp_secret_present,
    )


@router.put("/smtp", response_model=SmtpConfigRead)
async def update_smtp_config(
    payload: SmtpConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SmtpConfigRead:
    """Persist SMTP configuration to the database. Password is stored in secret_refs, not in app_settings."""
    from sqlalchemy import select
    from app.services.secret_service import expire_secret, has_secret

    # Preserve the tri-state contract: None keeps the stored secret, an empty
    # string clears it, and a non-empty value replaces it.
    stored_password_set = await has_secret(db, _SMTP_KEY, _SMTP_SECRET_FIELD)
    if payload.password == "":
        await expire_secret(db, _SMTP_KEY, _SMTP_SECRET_FIELD)
        stored_password_set = False
    elif payload.password is not None:
        await store_secret(db, _SMTP_KEY, _SMTP_SECRET_FIELD, payload.password, actor_id=current_user.id)
        stored_password_set = True

    # Store non-secret metadata only
    new_value = {
        "enabled": payload.enabled,
        "host": payload.host,
        "port": payload.port,
        "user": payload.user,
        "from_address": payload.from_address,
        "tls": payload.implicit_tls,
        # password is NOT stored here — it's in secret_refs
    }

    result = await db.execute(select(AppSetting).where(AppSetting.key == _SMTP_KEY))
    row = result.scalar_one_or_none()
    if row:
        row.value = new_value
        row.updated_by = current_user.id
        row.is_secret_backed = True
    else:
        db.add(AppSetting(key=_SMTP_KEY, value=new_value, updated_by=current_user.id, is_secret_backed=True))

    await log_settings_change(
        db,
        _SMTP_KEY,
        "updated",
        current_user,
        changed_fields=list(new_value.keys()) + ([_SMTP_SECRET_FIELD] if payload.password is not None else []),
    )
    await db.commit()

    logger.info("SMTP configuration updated by user_id=%s", current_user.id)
    return _build_smtp_config_read(
        enabled=payload.enabled,
        host=payload.host,
        port=payload.port,
        user=payload.user or None,
        from_address=payload.from_address,
        implicit_tls=payload.implicit_tls,
        has_secret_material=stored_password_set or bool(settings.SMTP_PASSWORD),
    )


@router.post("/smtp/test", response_model=SmtpTestResult)
async def test_smtp_config(
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> SmtpTestResult:
    """Send a test email to the current user's address using the stored SMTP config."""
    cfg = await _load_smtp_row(db)

    if not cfg.get("enabled"):
        return SmtpTestResult(success=False, message="SMTP is disabled. Enable it first.")

    from email.mime.text import MIMEText

    msg = MIMEText(
        "This is a test email from TestLookup to verify your SMTP configuration.",
        "plain",
    )
    msg["Subject"] = "TestLookup — SMTP Test"
    msg["From"] = cfg.get("from_address", "noreply@testlookup.io")
    msg["To"] = current_user.email

    from app.services.notification.email_service import smtp_host

    # The relay every real send dials (code review + QA of H10). The button
    # fell back to localhost instead, so with no stored host it tested -- and,
    # offline, approved -- a relay no real send uses.
    host = smtp_host(cfg)
    # Re-audit H10 (code review): the test email reached the stored relay, with
    # the stored credentials, while every real notification to it was refused.
    from app.services.notification.egress import (
        OfflineEgressBlocked,
        assert_delivery_allowed_async,
        assert_recipients_allowed_async,
    )

    try:
        # The stored relay is the deployment's own: the allow-list applies.
        await assert_delivery_allowed_async("SMTP", host, deployment_wide=True)
        # Re-audit N25: the recipient rule applies to the test email too.
        await assert_recipients_allowed_async(host, current_user.email)
    except OfflineEgressBlocked as exc:
        return SmtpTestResult(success=False, message=str(exc))

    try:
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=int(cfg.get("port", 587)),
            username=cfg.get("user") or None,
            password=cfg.get(_SMTP_SECRET_FIELD) or None,
            use_tls=bool(cfg.get("tls", True)),
            start_tls=not bool(cfg.get("tls", True)),
        )
        logger.info("SMTP test email sent for user_id=%s", current_user.id)
        return SmtpTestResult(success=True, message=f"Test email sent to {current_user.email}")
    except Exception:
        logger.exception("SMTP test failed")
        return SmtpTestResult(
            success=False,
            message="Failed to send test email. Please verify the SMTP configuration and try again.",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AI Configuration
# ═══════════════════════════════════════════════════════════════════════════════

_AI_CONFIG_KEY = "ai_config"

# Derived, not stored: provenance for the offline-mode ceiling and the
# US-15.2 confidence gate. Computed on every read, stripped before persisting
# — writing a source back would make it look like a stored override.
_DERIVED_AI_KEYS = (
    "ai_offline_mode_source",
    "ai_offline_mode_env_pinned",
    "ai_confidence_threshold_source",
)


async def _load_ai_config(db: AsyncSession) -> dict:
    from sqlalchemy import select

    from app.services.ai_config_resolver import (
        CONFIDENCE_SOURCE_AI_CONFIG,
        CONFIDENCE_SOURCE_ENV_DEFAULT,
        env_offline_pinned,
        resolve_offline_mode,
    )
    from app.services.confidence_gate import normalize_threshold

    result = await db.execute(select(AppSetting).where(AppSetting.key == _AI_CONFIG_KEY))
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row and row.value else {}
    _stored_threshold = normalize_threshold(overrides.get("ai_confidence_threshold"))
    # AI_OFFLINE_MODE in the environment is a hard ceiling on outbound LLM
    # egress — the stored override may tighten it, never loosen it. Resolved
    # through the same helper the runtime resolver uses so this page and
    # get_llm() can never disagree about what is actually in force.
    effective_offline, offline_source = resolve_offline_mode(
        overrides.get("ai_offline_mode", settings.AI_OFFLINE_MODE)
    )
    return {
        "ai_offline_mode": effective_offline,
        "ai_offline_mode_source": offline_source,
        "ai_offline_mode_env_pinned": env_offline_pinned(),
        "llm_provider": overrides.get("llm_provider", settings.LLM_PROVIDER),
        "llm_model": overrides.get("llm_model", settings.LLM_MODEL),
        "llm_temperature": overrides.get("llm_temperature", settings.LLM_TEMPERATURE),
        "llm_max_tokens": overrides.get("llm_max_tokens", settings.LLM_MAX_TOKENS),
        "embedding_provider": overrides.get("embedding_provider", settings.EMBEDDING_PROVIDER),
        "embedding_model": overrides.get("embedding_model", settings.EMBEDDING_MODEL),
        # US-15.2: the automation confidence gate. A stored value that fails
        # validation is dropped (not surfaced as if it were in force) — the
        # runtime resolver drops it the same way, so this page and the gate
        # can never disagree about which number automations obey.
        "ai_confidence_threshold": (
            _stored_threshold
            if _stored_threshold is not None
            else settings.AI_CONFIDENCE_THRESHOLD
        ),
        "ai_confidence_threshold_source": (
            CONFIDENCE_SOURCE_AI_CONFIG
            if _stored_threshold is not None
            else CONFIDENCE_SOURCE_ENV_DEFAULT
        ),
        "ai_timeout_seconds": overrides.get("ai_timeout_seconds", settings.AI_TIMEOUT_SECONDS),
        "deep_investigation_enabled": overrides.get("deep_investigation_enabled", settings.DEEP_INVESTIGATION_ENABLED),
        "finetune_enabled": overrides.get("finetune_enabled", settings.FINETUNE_ENABLED),
        "openai_api_key": overrides.get("openai_api_key") or settings.OPENAI_API_KEY,
        "google_api_key": overrides.get("google_api_key") or settings.GOOGLE_API_KEY,
        "anthropic_api_key": overrides.get("anthropic_api_key") or getattr(settings, "ANTHROPIC_API_KEY", None),
        "openrouter_api_key": overrides.get("openrouter_api_key") or getattr(settings, "OPENROUTER_API_KEY", None),
        "analysis_mode": overrides.get("analysis_mode", settings.ANALYSIS_MODE),
        # knowledge_rag_enabled is deliberately absent: it lives in the
        # feature_flags row, not here. Keeping a copy in ai_config is what let
        # this page advertise "Active" for a feature the gate had switched off.
    }


async def _ml_status() -> dict:
    """Best-effort ML tier status for AIConfigRead (never raises).

    Includes the AI-F1 honesty fields: how many human-provenance labels
    exist and whether the deployed model is bootstrap (LLM-imitating) or
    actually calibrated on human corrections.
    """
    status = {
        "ml_model_available": False,
        "ml_model_accuracy": None,
        "ml_training_sample_count": 0,
        "ml_human_label_count": 0,
        "ml_human_label_floor": settings.ML_HUMAN_LABEL_FLOOR,
        "ml_maturity": "not_trained",
    }
    try:
        from app.services.ml.classifier import MLClassifier
        from app.services.ml.label_provenance import model_maturity_from_metadata
        status["ml_model_available"] = MLClassifier.is_available()
        info = MLClassifier.get_model_info()
        status["ml_model_accuracy"] = info.get("accuracy")
        status["ml_training_sample_count"] = info.get("sample_count", 0)
        status["ml_maturity"] = model_maturity_from_metadata(
            info, settings.ML_HUMAN_LABEL_FLOOR,
        )
    except Exception:
        pass
    try:
        from app.services.ml.trainer import (
            get_human_label_count,
            get_training_sample_count,
        )
        status["ml_training_sample_count"] = max(
            status["ml_training_sample_count"], await get_training_sample_count()
        )
        status["ml_human_label_count"] = await get_human_label_count()
    except Exception:
        pass
    return status


@router.get("/ai", response_model=AIConfigRead)
async def get_ai_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> AIConfigRead:
    cfg = await _load_ai_config(db)

    ml = await _ml_status()
    # Same resolver the gate uses, so this page cannot show "Active" for a
    # feature that answers 503.
    from app.services.feature_flags import is_enabled
    cfg["knowledge_rag_enabled"] = await is_enabled("knowledge_rag", db=db)

    return AIConfigRead(
        llm_provider=cfg["llm_provider"],
        llm_model=cfg["llm_model"],
        llm_temperature=cfg["llm_temperature"],
        llm_max_tokens=cfg["llm_max_tokens"],
        ai_offline_mode=cfg["ai_offline_mode"],
        ai_offline_mode_source=cfg["ai_offline_mode_source"],
        ai_offline_mode_env_pinned=cfg["ai_offline_mode_env_pinned"],
        embedding_provider=cfg["embedding_provider"],
        embedding_model=cfg["embedding_model"],
        ai_confidence_threshold=cfg["ai_confidence_threshold"],
        ai_confidence_threshold_source=cfg["ai_confidence_threshold_source"],
        ai_timeout_seconds=cfg["ai_timeout_seconds"],
        deep_investigation_enabled=cfg["deep_investigation_enabled"],
        finetune_enabled=cfg["finetune_enabled"],
        openai_key_set=bool(cfg.get("openai_api_key")),
        google_key_set=bool(cfg.get("google_api_key")),
        anthropic_key_set=bool(cfg.get("anthropic_api_key")),
        openrouter_key_set=bool(cfg.get("openrouter_api_key")),
        analysis_mode=cfg.get("analysis_mode", "auto"),
        knowledge_rag_enabled=cfg.get("knowledge_rag_enabled", False),
        **ml,
    )


class RequiredModel(BaseModel):
    """One model the effective configuration depends on."""
    name: str
    # Plain str, not a Literal — a strict enum over a free-form value 422s
    # the whole response the moment a new purpose is added upstream.
    purpose: str          # "llm" | "embedding" | "classifier"
    present: bool
    # Runtime-aware fix for a missing model. Null when the model is present
    # OR when Ollama is unreachable (fix connectivity before pulling).
    remedy: Optional[str] = None


class FallbackChainEntry(BaseModel):
    """One analysis tier and whether it can actually run right now."""
    mode: str             # "ml" | "llm" | "rules"
    available: bool
    # Why it is unavailable — or, on an available tier, a caveat (e.g. a
    # self-hosted provider whose model presence TestLookup cannot verify).
    reason: Optional[str] = None
    # The same distinction, machine-readable, so UI badges branch on this
    # instead of re-deriving the cause from unrelated fields. The settings page
    # used to guess from `ollama_reachable` and so labelled a missing OpenRouter
    # API key "Model Missing". One of: ok | unreachable | model_missing |
    # no_api_key | offline_blocked | unverifiable | unknown_provider | not_trained.
    reason_code: Optional[str] = None


class AIModelStatusRead(BaseModel):
    """Live model presence + fallback-chain state (US-13.2).

    ``ollama_reachable=False`` and "model missing" are deliberately
    separate signals: an unreachable daemon is a connectivity problem,
    an empty/incomplete model list on a reachable daemon is a model-pack
    import problem. The UI must not collapse them.
    """
    ollama_reachable: bool
    ollama_error: Optional[str] = None
    ollama_base_url: str
    installed_models: list[str]
    required: list[RequiredModel]
    fallback_chain: list[FallbackChainEntry]
    offline_mode: bool
    llm_provider: str
    analysis_mode: str
    checked_at: str


@router.get("/ai/model-status", response_model=AIModelStatusRead)
async def get_ai_model_status(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> AIModelStatusRead:
    """Probe the configured model backend and report what will really run.

    Read-only and best-effort: an unreachable Ollama, an unreadable
    settings row, or a broken ML model directory all degrade into honest
    fields rather than a 500 — an operator debugging an air-gapped install
    needs this page to render precisely when things are broken.
    """
    try:
        cfg = await _load_ai_config(db)
    except Exception:
        logger.warning("model-status: AI config load failed — falling back to env defaults", exc_info=True)
        cfg = {}

    from app.services.model_status_service import build_model_status
    status = await build_model_status(cfg)
    return AIModelStatusRead.model_validate(status)


@router.put("/ai", response_model=AIConfigRead)
async def update_ai_config(
    payload: AIConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> AIConfigRead:
    from sqlalchemy import select

    from app.services.agent_authority_lock import lock_global_agent_authority
    from app.services.ai_config_resolver import (
        env_offline_pinned,
        invalidate_ai_config_cache,
        resolve_offline_mode,
    )

    await lock_global_agent_authority(db)
    existing = await _load_ai_config(db)
    updates = payload.model_dump(exclude_none=True)

    # AI_OFFLINE_MODE is a hard ceiling, and a rejected write is more honest
    # than a silently-ignored one: an operator who unticks "Offline Mode" on a
    # deployment that pins it in the environment must be told the click did
    # nothing, not left believing cloud LLM is now enabled.
    if updates.get("ai_offline_mode") is False and env_offline_pinned():
        logger.warning(
            "AI offline-mode disable rejected — pinned by AI_OFFLINE_MODE (user_id=%s)",
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Offline mode is pinned on by AI_OFFLINE_MODE in this deployment's "
                "environment and cannot be disabled from the API. Set AI_OFFLINE_MODE=false "
                "in the backend environment and restart to permit cloud LLM egress."
            ),
        )

    merged = {**existing, **updates}

    # The Knowledge-RAG switch on this page drives the feature_flags row that
    # actually gates the endpoints — it does not get its own copy in ai_config.
    # Two stores is exactly what let this page report "Active" while
    # /knowledge-sources answered 503 and told the operator to come here.
    #
    # Routed through feature_flags.update_flag rather than
    # feature_flag_service.set_flag because only the former invalidates the
    # in-process and Redis caches that is_enabled() reads; without that, the
    # switch appears not to work for up to 30 seconds after it is flipped.
    rag_requested = updates.pop("knowledge_rag_enabled", None)
    merged.pop("knowledge_rag_enabled", None)
    if rag_requested is not None:
        from app.services.feature_flags import create_flag, get_flag, update_flag
        if await get_flag(db, "knowledge_rag") is None:
            await create_flag(
                db,
                key="knowledge_rag",
                description="RAG-backed knowledge grounding for test case generation.",
                enabled_global=bool(rag_requested),
                enabled_projects=None,
                enabled_roles=None,
                rollout_percent=100,
                actor=current_user,
            )
        else:
            await update_flag(
                db,
                key="knowledge_rag",
                updates={"enabled_global": bool(rag_requested)},
                actor=current_user,
            )

    # Store secrets separately in secret_refs
    secrets = extract_secrets_from_config(_AI_CONFIG_KEY, updates)
    for key_name, raw_value in secrets.items():
        await store_secret(db, _AI_CONFIG_KEY, key_name, raw_value, actor_id=current_user.id)

    # Store only non-secret metadata in app_settings. The offline provenance
    # fields are derived from the environment on every read — persisting them
    # would freeze a snapshot of one process's env into the shared row.
    #
    # Note `merged["ai_offline_mode"]` is the EFFECTIVE value, so on an
    # env-pinned deployment any PUT converges the stored row to True. That is
    # deliberate: the row then agrees with reality, and the failure mode of
    # un-pinning the environment later is "still offline until you toggle it",
    # which is the safe direction.
    store_value = strip_secrets_from_config(_AI_CONFIG_KEY, merged)
    for _derived in _DERIVED_AI_KEYS:
        store_value.pop(_derived, None)
    result = await db.execute(select(AppSetting).where(AppSetting.key == _AI_CONFIG_KEY))
    row = result.scalar_one_or_none()
    if row:
        row.value = store_value
        row.updated_by = current_user.id
        row.is_secret_backed = bool(secrets)
    else:
        db.add(AppSetting(key=_AI_CONFIG_KEY, value=store_value, updated_by=current_user.id, is_secret_backed=bool(secrets)))

    # Audit log
    await log_settings_change(db, _AI_CONFIG_KEY, "updated", current_user, changed_fields=list(updates.keys()))
    await db.commit()
    await invalidate_ai_config_cache()
    # Cache in Redis for fast sync reads by analysis_router / knowledge services
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        if "analysis_mode" in updates:
            await redis.set("config:analysis_mode", updates["analysis_mode"], ex=86400)
    except Exception:
        pass  # Redis cache is best-effort

    logger.info("AI configuration updated by user_id=%s (fields: %s)", current_user.id, list(updates.keys()))

    # ML model status for response
    ml = await _ml_status()
    # The flag caches must be dropped AFTER the commit above, not just inside
    # update_flag: that runs mid-transaction, and any reader in the window
    # between it and the commit re-populates the caches from the old committed
    # row — which then stands for the 30s TTL. Verified live: without this the
    # write landed in Postgres while GET /settings/ai and the gate both kept
    # answering the previous value.
    from app.services.feature_flags import invalidate_flag_cache, is_enabled
    if rag_requested is not None:
        await invalidate_flag_cache("knowledge_rag")
    # Re-resolve rather than echo: report the flag as it now stands.
    rag_enabled_after = await is_enabled("knowledge_rag", db=db)

    # Re-resolve rather than echo the request: the response must report what
    # is actually in force after the ceiling, not what was asked for.
    effective_offline, offline_source = resolve_offline_mode(merged["ai_offline_mode"])

    return AIConfigRead(
        llm_provider=merged["llm_provider"],
        llm_model=merged["llm_model"],
        llm_temperature=merged["llm_temperature"],
        llm_max_tokens=merged["llm_max_tokens"],
        ai_offline_mode=effective_offline,
        ai_offline_mode_source=offline_source,
        ai_offline_mode_env_pinned=env_offline_pinned(),
        embedding_provider=merged["embedding_provider"],
        embedding_model=merged["embedding_model"],
        ai_confidence_threshold=merged["ai_confidence_threshold"],
        ai_timeout_seconds=merged["ai_timeout_seconds"],
        deep_investigation_enabled=merged["deep_investigation_enabled"],
        finetune_enabled=merged["finetune_enabled"],
        openai_key_set=bool(merged.get("openai_api_key")),
        google_key_set=bool(merged.get("google_api_key")),
        anthropic_key_set=bool(merged.get("anthropic_api_key")),
        openrouter_key_set=bool(merged.get("openrouter_api_key")),
        analysis_mode=merged.get("analysis_mode", "auto"),
        knowledge_rag_enabled=rag_enabled_after,
        **ml,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Integrations Configuration
# ═══════════════════════════════════════════════════════════════════════════════

_INTEGRATIONS_KEY = "integrations_config"


async def _load_integrations_config(db: AsyncSession) -> dict:
    from sqlalchemy import select
    from app.services.secret_service import read_secret

    result = await db.execute(select(AppSetting).where(AppSetting.key == _INTEGRATIONS_KEY))
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row and row.value else {}
    # Updates strip these values from app_settings and persist them encrypted in
    # secret_refs. Read that authority before falling back to legacy inline
    # values or environment configuration.
    jira_token = await read_secret(db, _INTEGRATIONS_KEY, "jira_api_token")
    splunk_token = await read_secret(db, _INTEGRATIONS_KEY, "splunk_api_token")
    ocp_token = await read_secret(db, _INTEGRATIONS_KEY, "ocp_sa_token")
    github_token = await read_secret(db, _INTEGRATIONS_KEY, "github_token")
    from app.services.integration_config_service import resolve_global_notification_webhooks
    notification_cfg = await resolve_global_notification_webhooks(db, overrides=overrides)
    return {
        "jira_enabled": overrides.get("jira_enabled", settings.JIRA_ENABLED),
        "jira_domain": overrides.get("jira_domain", settings.JIRA_DOMAIN),
        "jira_email": overrides.get("jira_email", settings.JIRA_EMAIL),
        "jira_api_token": jira_token or overrides.get("jira_api_token") or settings.JIRA_API_TOKEN,
        "jira_default_project_key": overrides.get("jira_default_project_key", settings.JIRA_DEFAULT_PROJECT_KEY),
        "splunk_enabled": overrides.get("splunk_enabled", settings.SPLUNK_ENABLED),
        "splunk_base_url": overrides.get("splunk_base_url", settings.SPLUNK_BASE_URL),
        "splunk_api_token": splunk_token or overrides.get("splunk_api_token") or settings.SPLUNK_API_TOKEN,
        "ocp_enabled": overrides.get("ocp_enabled", settings.OCP_ENABLED),
        "ocp_api_url": overrides.get("ocp_api_url", settings.OCP_API_URL),
        "ocp_sa_token": ocp_token or overrides.get("ocp_sa_token") or settings.OCP_SA_TOKEN,
        "ocp_default_namespace": overrides.get("ocp_default_namespace", settings.OCP_DEFAULT_NAMESPACE),
        "slack_enabled": notification_cfg["slack_enabled"],
        "slack_webhook_url": notification_cfg["slack_webhook_url"],
        "slack_default_channel": overrides.get("slack_default_channel", settings.SLACK_DEFAULT_CHANNEL),
        "teams_enabled": notification_cfg["teams_enabled"],
        "teams_webhook_url": notification_cfg["teams_webhook_url"],
        "github_repo": overrides.get("github_repo", settings.GITHUB_REPO),
        "github_token": github_token or overrides.get("github_token") or settings.GITHUB_TOKEN,
    }


@router.get("/integrations", response_model=IntegrationsConfigRead)
async def get_integrations_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> IntegrationsConfigRead:
    cfg = await _load_integrations_config(db)
    return IntegrationsConfigRead(
        jira_enabled=cfg["jira_enabled"],
        jira_domain=cfg["jira_domain"],
        jira_email=cfg["jira_email"],
        jira_token_set=bool(cfg.get("jira_api_token")),
        jira_default_project_key=cfg["jira_default_project_key"],
        splunk_enabled=cfg["splunk_enabled"],
        splunk_base_url=cfg["splunk_base_url"],
        splunk_token_set=bool(cfg.get("splunk_api_token")),
        ocp_enabled=cfg["ocp_enabled"],
        ocp_api_url=cfg["ocp_api_url"],
        ocp_token_set=bool(cfg.get("ocp_sa_token")),
        ocp_default_namespace=cfg["ocp_default_namespace"],
        slack_enabled=cfg["slack_enabled"],
        slack_webhook_url=None,
        slack_webhook_set=bool(cfg["slack_webhook_url"]),
        slack_default_channel=cfg["slack_default_channel"],
        teams_enabled=cfg["teams_enabled"],
        teams_webhook_url=None,
        teams_webhook_set=bool(cfg["teams_webhook_url"]),
        github_repo=cfg["github_repo"],
        github_token_set=bool(cfg.get("github_token")),
    )


@router.put("/integrations", response_model=IntegrationsConfigRead)
async def update_integrations_config(
    payload: IntegrationsConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> IntegrationsConfigRead:
    from sqlalchemy import select
    existing = await _load_integrations_config(db)
    updates = payload.model_dump(exclude_none=True)
    merged = {**existing, **updates}

    # Store secrets separately
    secrets = extract_secrets_from_config(_INTEGRATIONS_KEY, updates)
    for key_name, raw_value in secrets.items():
        await store_secret(db, _INTEGRATIONS_KEY, key_name, raw_value, actor_id=current_user.id)

    store_value = strip_secrets_from_config(_INTEGRATIONS_KEY, merged)
    result = await db.execute(select(AppSetting).where(AppSetting.key == _INTEGRATIONS_KEY))
    row = result.scalar_one_or_none()
    if row:
        row.value = store_value
        row.updated_by = current_user.id
        row.is_secret_backed = bool(secrets)
    else:
        db.add(AppSetting(key=_INTEGRATIONS_KEY, value=store_value, updated_by=current_user.id, is_secret_backed=bool(secrets)))

    await log_settings_change(db, _INTEGRATIONS_KEY, "updated", current_user, changed_fields=list(updates.keys()))
    await db.commit()
    logger.info("Integrations configuration updated by user_id=%s", current_user.id)
    return IntegrationsConfigRead(
        jira_enabled=merged["jira_enabled"],
        jira_domain=merged["jira_domain"],
        jira_email=merged["jira_email"],
        jira_token_set=bool(merged.get("jira_api_token")),
        jira_default_project_key=merged["jira_default_project_key"],
        splunk_enabled=merged["splunk_enabled"],
        splunk_base_url=merged["splunk_base_url"],
        splunk_token_set=bool(merged.get("splunk_api_token")),
        ocp_enabled=merged["ocp_enabled"],
        ocp_api_url=merged["ocp_api_url"],
        ocp_token_set=bool(merged.get("ocp_sa_token")),
        ocp_default_namespace=merged["ocp_default_namespace"],
        slack_enabled=merged["slack_enabled"],
        slack_webhook_url=None,
        slack_webhook_set=bool(merged["slack_webhook_url"]),
        slack_default_channel=merged["slack_default_channel"],
        teams_enabled=merged["teams_enabled"],
        teams_webhook_url=None,
        teams_webhook_set=bool(merged["teams_webhook_url"]),
        github_repo=merged["github_repo"],
        github_token_set=bool(merged.get("github_token")),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Data & Storage Configuration
# ═══════════════════════════════════════════════════════════════════════════════

_STORAGE_KEY = "storage_config"
_STORAGE_PROBE_TIMEOUT_SECONDS = 2.0


async def _get_storage_connection_status() -> dict[str, bool]:
    """Probe the infrastructure badges shown by the Storage settings page.

    PostgreSQL has already answered the ``app_settings`` query before this
    helper is called. MongoDB and Redis are optional to this request, so a
    failed or timed-out probe must render them as disconnected without making
    the configuration endpoint unavailable.
    """
    from app.db.mongo import get_mongo_db
    from app.db.redis_client import get_redis

    async def probe_mongo() -> bool:
        try:
            await asyncio.wait_for(
                get_mongo_db().command("ping"),
                timeout=_STORAGE_PROBE_TIMEOUT_SECONDS,
            )
            return True
        except Exception:  # noqa: BLE001 - status probe reports failure as False
            return False

    async def probe_redis() -> bool:
        try:
            await asyncio.wait_for(
                get_redis().ping(),
                timeout=_STORAGE_PROBE_TIMEOUT_SECONDS,
            )
            return True
        except Exception:  # noqa: BLE001 - status probe reports failure as False
            return False

    mongo_connected, redis_connected = await asyncio.gather(probe_mongo(), probe_redis())
    return {
        "postgres_connected": True,
        "mongo_connected": mongo_connected,
        "redis_connected": redis_connected,
    }


async def _build_storage_config_read(values: dict) -> StorageConfigRead:
    status = await _get_storage_connection_status()
    return StorageConfigRead(
        **status,
        storage_backend=values.get("storage_backend", settings.STORAGE_BACKEND),
        minio_endpoint=values.get("minio_endpoint", settings.MINIO_ENDPOINT),
        minio_bucket_name=values.get("minio_bucket_name", settings.MINIO_BUCKET_NAME),
        minio_use_ssl=values.get("minio_use_ssl", settings.MINIO_USE_SSL),
        chroma_host=values.get("chroma_host", settings.CHROMA_HOST),
        chroma_port=values.get("chroma_port", settings.CHROMA_PORT),
        chroma_collection=values.get("chroma_collection", settings.CHROMA_COLLECTION),
    )


@router.get("/storage", response_model=StorageConfigRead)
async def get_storage_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> StorageConfigRead:
    from sqlalchemy import select
    result = await db.execute(select(AppSetting).where(AppSetting.key == _STORAGE_KEY))
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row and row.value else {}
    return await _build_storage_config_read(overrides)


@router.put("/storage", response_model=StorageConfigRead)
async def update_storage_config(
    payload: StorageConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> StorageConfigRead:
    from sqlalchemy import select
    result = await db.execute(select(AppSetting).where(AppSetting.key == _STORAGE_KEY))
    row = result.scalar_one_or_none()
    existing = dict(row.value) if row and row.value else {}
    updates = payload.model_dump(exclude_none=True)
    merged = {**existing, **updates}

    if row:
        row.value = merged
        row.updated_by = current_user.id
    else:
        db.add(AppSetting(key=_STORAGE_KEY, value=merged, updated_by=current_user.id))

    await log_settings_change(db, _STORAGE_KEY, "updated", current_user, changed_fields=list(updates.keys()))
    await db.commit()
    from app.services.storage_config_service import invalidate_storage_config_cache

    await invalidate_storage_config_cache()
    logger.info("Storage configuration updated by user_id=%s", current_user.id)
    return await _build_storage_config_read(merged)


# ═══════════════════════════════════════════════════════════════════════════════
# Settings Audit Log
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/audit-log")
async def get_audit_log(
    setting_key: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Return recent settings change audit entries."""
    return await get_settings_audit_log(db, setting_key=setting_key, limit=limit)


# ═══════════════════════════════════════════════════════════════════════════════
# Feature Flags
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureFlagUpdate(BaseModel):
    enabled: bool
    scope: str = "global"
    config: Optional[dict] = None
    description: Optional[str] = None


@router.get("/flags")
async def list_feature_flags(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List all feature flags."""
    from app.services.feature_flag_service import get_all_flags
    return await get_all_flags(db)


@router.put("/flags/{flag_key}")
async def update_feature_flag(
    flag_key: str,
    body: FeatureFlagUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a feature flag. Requires ADMIN."""
    from app.services.feature_flag_service import set_flag
    result = await set_flag(db, flag_key, body.enabled, body.scope, body.config, body.description)
    changed_fields = ["enabled_global"]
    if body.description is not None:
        changed_fields.append("description")
    await log_settings_change(
        db, f"feature_flag:{flag_key}", "updated", current_user, changed_fields,
    )
    await db.commit()
    # This compatibility route writes the same authority rows as the canonical
    # feature-flag API. Invalidate only after commit so all replicas observe the
    # new row and no concurrent reader can repopulate Redis from old data.
    from app.services.feature_flags import invalidate_flag_cache
    await invalidate_flag_cache(flag_key)
    return result


@router.delete("/flags/{flag_key}", status_code=204)
async def remove_feature_flag(
    flag_key: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a feature flag. Requires ADMIN."""
    from app.services.feature_flag_service import delete_flag
    deleted = await delete_flag(db, flag_key)
    if deleted:
        await log_settings_change(
            db, f"feature_flag:{flag_key}", "deleted", current_user, ["enabled_global"],
        )
    await db.commit()
    from app.services.feature_flags import invalidate_flag_cache
    await invalidate_flag_cache(flag_key)


# ═══════════════════════════════════════════════════════════════════════════════
# MFA + Account Lockout Policy (0117)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Stored as an ``app_settings`` row keyed ``mfa_policy``, following the
# ``_AI_CONFIG_KEY`` pattern above rather than the ``SSOConfiguration``
# single-active-row table. The SSO table exists because a SAML config is a
# large object (certificates, endpoints, role maps) whose enforcement mode is
# one field on it; this policy is four scalars with no accompanying object, so
# a table would buy a single-active-row lifecycle we would never use. Routing
# it through ``app_settings`` also inherits ``log_settings_change`` for free.

@router.get("/mfa-policy", response_model=MfaPolicyRead)
async def get_mfa_policy(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> MfaPolicyRead:
    """Read the workspace MFA + lockout policy. QA_LEAD or higher."""
    from app.services.mfa_service import load_policy

    policy = await load_policy(db)
    return MfaPolicyRead.model_validate(policy.as_dict())


@router.put("/mfa-policy", response_model=MfaPolicyRead)
async def update_mfa_policy(
    payload: MfaPolicyUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MfaPolicyRead:
    """Update the workspace MFA + lockout policy. ADMIN only, audit-logged."""
    from app.models.postgres import IdentityEventType
    from app.services.mfa_service import MfaPolicy, load_policy, save_policy
    from app.services.sso_service import log_identity_event

    current = await load_policy(db)
    updates = payload.model_dump(exclude_none=True)
    updates.pop("clear_required_for_role", None)

    role_value = updates.pop("required_for_role", None)
    if payload.clear_required_for_role:
        # Explicit null: MFA required for *everyone*, which is stricter than
        # any role floor and therefore cannot be expressed by omitting a field.
        resolved_role = None
    elif role_value is not None:
        resolved_role = role_value.value if hasattr(role_value, "value") else str(role_value)
    else:
        resolved_role = current.required_for_role

    merged = MfaPolicy(
        require_mfa=updates.get("require_mfa", current.require_mfa),
        required_for_role=resolved_role,
        lockout_enabled=updates.get("lockout_enabled", current.lockout_enabled),
        lockout_threshold=updates.get("lockout_threshold", current.lockout_threshold),
        lockout_duration_minutes=updates.get(
            "lockout_duration_minutes", current.lockout_duration_minutes
        ),
    )
    await save_policy(db, merged, actor_id=current_user.id)

    changed = sorted(
        k for k, v in merged.as_dict().items() if current.as_dict().get(k) != v
    )
    await log_settings_change(db, "mfa_policy", "updated", current_user, changed)
    # Also on the identity trail — an auditor asking "when was MFA made
    # mandatory, and by whom" looks at identity_events, not the settings log.
    await log_identity_event(
        db,
        IdentityEventType.MFA_POLICY_UPDATED,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={"before": current.as_dict(), "after": merged.as_dict()},
    )
    await db.commit()
    logger.info("MFA policy updated by user_id=%s fields=%s", current_user.id, changed)
    return MfaPolicyRead.model_validate(merged.as_dict())


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Health
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/integrations/health")
async def get_integration_health(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Return health status for all tracked integration providers."""
    from sqlalchemy import select as sa_select
    from app.models.postgres import IntegrationHealthCheck
    result = await db.execute(sa_select(IntegrationHealthCheck))
    return [
        {
            "provider": h.provider,
            "status": h.status,
            "last_checked_at": h.last_checked_at.isoformat() if h.last_checked_at else None,
            "message": h.message,
            "response_ms": h.response_ms,
        }
        for h in result.scalars().all()
    ]
