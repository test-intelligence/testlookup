"""Application settings management — SMTP, AI, Integrations, Storage configuration.

Security model:
  - GET endpoints: QA_LEAD or higher (secrets are never returned raw — only *_set booleans)
  - PUT endpoints: ADMIN only (audit-logged, secrets stored in secret_refs table)
  - Retention defaults (W22b): ADMIN for every route, reads included; the writes
    need an interactive login (an API key can only read them)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosmtplib
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind, get_current_active_user, require_role
from app.db.postgres import get_db
from app.models.postgres import AppSetting, User, UserRole
from app.services import retention_instance_service, review_gate_setting
from app.services.retention_instance_service import InstancePolicyError
from app.services.review_gate_vocab import GATED_CHANNELS, REVIEW_GATE_ERRORS
from app.services.retention_service import RetentionValidationError
from app.services.secret_service import extract_secrets_from_config, store_secret, strip_secrets_from_config
from app.services.settings_audit_service import get_settings_audit_log, log_settings_change
from app.models.schemas import (
    AIConfigRead,
    AIConfigUpdate,
    EmbeddingIndexRead,
    GatedChannel,
    InstanceEnablePreviewResponse,
    InstanceEnableRequest,
    InstanceRetentionPolicyRead,
    InstanceRetentionPolicyWrite,
    IntegrationsConfigRead,
    IntegrationsConfigUpdate,
    MfaPolicyRead,
    MfaPolicyUpdate,
    ReviewGateRead,
    ReviewGateWrite,
    SmtpConfigRead,
    SmtpConfigUpdate,
    SmtpTestResult,
    StorageConfigRead,
    StorageConfigUpdate,
    WouldRefuseSummary,
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
    except Exception as exc:
        logger.exception("SMTP test failed")
        return SmtpTestResult(
            success=False,
            message=smtp_failure_hint(
                exc, port=int(cfg.get("port", 587)), implicit_tls=bool(cfg.get("tls", True)),
            ),
        )


def smtp_failure_hint(exc: BaseException, *, port: int, implicit_tls: bool) -> str:
    """Say what to change, not just that the test failed.

    Owner report 2026-10-10: Gmail on port 587 with implicit TLS on failed with
    "Failed to send test email. Please verify the SMTP configuration" -- the
    stored password was fine, but the generic text read as "the password did
    not save". The cause was the TLS mode (587 speaks STARTTLS). Never echoes
    credentials: only the exception's class and message are inspected.
    """
    # By class NAME: several test suites put a stub in sys.modules["aiosmtplib"],
    # and an isinstance against a stub's attribute raises instead of matching.
    names = {cls.__name__ for cls in type(exc).__mro__}
    text = f"{type(exc).__name__}: {exc}"
    if "SMTPAuthenticationError" in names:
        return (
            "The server rejected the username or password. Check both; Gmail and Outlook "
            "need an app password, not the account password."
        )
    if "WRONG_VERSION_NUMBER" in text or (implicit_tls and port in (25, 587)):
        return (
            f"The server on port {port} did not start TLS on connect. Port {port} normally uses "
            "STARTTLS: turn implicit TLS off (or use port 465 with implicit TLS), save, and test again."
        )
    if not implicit_tls and port == 465:
        return (
            "Port 465 expects implicit TLS on connect: turn implicit TLS on (or use port 587 "
            "with STARTTLS), save, and test again."
        )
    if names & {"SMTPConnectError", "SMTPServerDisconnected", "SMTPConnectTimeoutError", "OSError"}:
        return f"Could not connect to the mail server on port {port}. Check the host, the port and that it is reachable."
    return "Failed to send test email. Please verify the SMTP configuration and try again."


# ═══════════════════════════════════════════════════════════════════════════════
# AI Configuration
# ═══════════════════════════════════════════════════════════════════════════════

_AI_CONFIG_KEY = "ai_config"

# Derived, not stored: provenance for the offline-mode ceiling and the
# US-15.2 confidence gate, and the faithfulness strategy that actually runs.
# Computed on every read, stripped before persisting — writing a source back
# would make it look like a stored override.
_DERIVED_AI_KEYS = (
    "ai_offline_mode_source",
    "ai_offline_mode_env_pinned",
    "ai_confidence_threshold_source",
    # AIC-04 / AIC-01 / AIC-02 provenance (ai_config_resolver.resolve_stored_toggles).
    "analysis_mode_source",
    "ai_timeout_seconds_source",
    "deep_investigation_enabled_source",
    "finetune_enabled_source",
    "rag_faithfulness_evaluator_effective",
    "rag_faithfulness_evaluator_reason",
)


async def _load_ai_config(db: AsyncSession) -> dict:
    from sqlalchemy import select

    from app.services.ai_config_resolver import (
        CONFIDENCE_SOURCE_AI_CONFIG,
        CONFIDENCE_SOURCE_ENV_DEFAULT,
        env_offline_pinned,
        resolve_offline_mode,
        resolve_stored_toggles,
    )
    from app.services.confidence_gate import normalize_threshold
    from app.services.rag_faithfulness_service import (
        effective_evaluator,
        normalize_evaluator,
    )

    result = await db.execute(select(AppSetting).where(AppSetting.key == _AI_CONFIG_KEY))
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row and row.value else {}
    _stored_threshold = normalize_threshold(overrides.get("ai_confidence_threshold"))
    # analysis_mode / ai_timeout_seconds / deep_investigation_enabled /
    # finetune_enabled + their *_source: the SAME helper the runtime resolver
    # uses, so this page shows exactly what the runtime obeys (an invalid stored
    # value is ignored by both and reported as "env_default").
    toggles = resolve_stored_toggles(overrides)
    # AI_OFFLINE_MODE in the environment is a hard ceiling on outbound LLM
    # egress — the stored override may tighten it, never loosen it. Resolved
    # through the same helper the runtime resolver uses so this page and
    # get_llm() can never disagree about what is actually in force.
    effective_offline, offline_source = resolve_offline_mode(
        overrides.get("ai_offline_mode", settings.AI_OFFLINE_MODE)
    )
    # The stored choice, not the effective one: PUT writes this dict back, and
    # an offline deployment would otherwise overwrite "ragas" with "ollama".
    evaluator = normalize_evaluator(overrides.get("rag_faithfulness_evaluator"))
    evaluator_effective, evaluator_reason = effective_evaluator(
        evaluator, offline=effective_offline
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
        "ai_timeout_seconds": toggles["ai_timeout_seconds"],
        "ai_timeout_seconds_source": toggles["ai_timeout_seconds_source"],
        "deep_investigation_enabled": toggles["deep_investigation_enabled"],
        "deep_investigation_enabled_source": toggles["deep_investigation_enabled_source"],
        "finetune_enabled": toggles["finetune_enabled"],
        "finetune_enabled_source": toggles["finetune_enabled_source"],
        "openai_api_key": overrides.get("openai_api_key") or settings.OPENAI_API_KEY,
        "google_api_key": overrides.get("google_api_key") or settings.GOOGLE_API_KEY,
        "anthropic_api_key": overrides.get("anthropic_api_key") or getattr(settings, "ANTHROPIC_API_KEY", None),
        "openrouter_api_key": overrides.get("openrouter_api_key") or getattr(settings, "OPENROUTER_API_KEY", None),
        "analysis_mode": toggles["analysis_mode"],
        "analysis_mode_source": toggles["analysis_mode_source"],
        "rag_faithfulness_evaluator": evaluator,
        "rag_faithfulness_evaluator_effective": evaluator_effective,
        "rag_faithfulness_evaluator_reason": evaluator_reason,
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


# ── W12 SRCH-02: the embedding index (active / configured / building generation) ──


async def _load_storage_values(db: AsyncSession) -> dict:
    """The stored storage overrides (ONE statement; ``{}`` when there is no row)."""
    from sqlalchemy import select

    result = await db.execute(select(AppSetting).where(AppSetting.key == _STORAGE_KEY))
    row = result.scalar_one_or_none()
    value = getattr(row, "value", None) if row is not None else None
    return dict(value) if isinstance(value, dict) else {}


async def _embedding_index_read(db: AsyncSession, ai_cfg: dict) -> Optional[EmbeddingIndexRead]:
    """``EmbeddingIndexRead`` of the state, or None when anything could not be read. Never raises."""
    from app.services.embedding_index_state import read_embedding_index

    try:
        storage_values = await _load_storage_values(db)
        body = await read_embedding_index(db, ai_config=ai_cfg, storage_values=storage_values)
        return EmbeddingIndexRead.model_validate(body) if body is not None else None
    except Exception:  # noqa: BLE001 - embedding_index: null, never a guess and never a 500
        logger.warning("embedding index state could not be read for the settings page")
        return None


async def _embedder_runtime(db: AsyncSession, merged: dict, updates: dict) -> tuple[bool, str, bool]:
    """``(offline, ollama_base_url, openai_key_set)`` for validating an embedder change.

    Read on the request session (``fresh``: nothing is cached from inside the
    transaction). Only the embedder-change paths call this.
    """
    from app.services.ai_config_resolver import get_effective_ai_config, resolve_offline_mode
    from app.services.embedding_function import ollama_base_url

    effective = await get_effective_ai_config(db=db, fresh=True)
    offline, _source = resolve_offline_mode(merged.get("ai_offline_mode", effective.get("offline_mode")))
    base_url = updates["base_url"] if "base_url" in updates else effective.get("base_url")
    ollama_url = ollama_base_url({"provider": merged.get("llm_provider", effective.get("provider")),
                                  "base_url": base_url})
    key_set = bool(updates.get("openai_api_key") or effective.get("openai_api_key"))
    return bool(offline), ollama_url, key_set


async def _validate_embedder_or_422(spec, *, offline: bool, ollama_url: str, openai_key_set: bool) -> None:
    from app.services.embedding_function import EmbedderRefused, validate_embedder

    try:
        await asyncio.to_thread(
            validate_embedder, spec, offline=offline, ollama_base_url=ollama_url,
            openai_key_set=openai_key_set,
        )
    except EmbedderRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None


async def _stage_generation_request(db: AsyncSession, target_of, current_user: User, changed_fields: list[str]):
    """Lock the state, ask for ``target_of(state)`` and stage it + its audit row.

    Returns ``(before, after)``; the caller commits, then calls
    :func:`_after_generation_request`.
    """
    from datetime import datetime, timezone

    from app.services.embedding_index_state import lock_state, request_target, stage_state

    row, before = await lock_state(db)
    after = request_target(before, target_of(before), actor_id=str(current_user.id),
                           now=datetime.now(timezone.utc))
    stage_state(db, row, after, actor_id=current_user.id)
    await log_settings_change(
        db, "embedding_index", "reembed_requested", current_user, changed_fields=changed_fields,
    )
    return before, after


async def _after_generation_request(db: AsyncSession, before, after) -> None:
    """After the commit: drop the generation caches, queue the build (and the
    retirement of a replaced target). A broker that refuses the build records
    it as failed, so the page never shows a build nobody runs."""
    from app.services.chroma_collections import RETIRE_GRACE_SECONDS, invalidate_generations_cache

    await invalidate_generations_cache()
    target = after.target_generation()
    if target is not None and after.status == "building":
        try:
            from app.worker.tasks import rebuild_embedding_generation

            rebuild_embedding_generation.apply_async(
                kwargs={"generation_tag": target.tag, "fence": after.fence},
            )
        except Exception as exc:  # noqa: BLE001 - say so instead of "building" forever
            logger.warning("embedding generation build could not be queued (%s)", type(exc).__name__)
            from dataclasses import replace

            from app.services.embedding_index_state import lock_state, stage_state

            row, current = await lock_state(db)
            if current.fence == after.fence:
                stage_state(db, row, replace(
                    current, status="failed",
                    last_error=f"the build could not be queued ({type(exc).__name__})",
                ), actor_id=None)
                await db.commit()
                await invalidate_generations_cache()
    if len(after.retiring) > len(before.retiring):
        try:
            from app.worker.tasks import retire_embedding_generations

            retire_embedding_generations.apply_async(countdown=RETIRE_GRACE_SECONDS)
        except Exception as exc:  # noqa: BLE001 - the next switch schedules it again
            logger.warning("embedding generation retirement could not be queued (%s)", type(exc).__name__)


@router.get("/ai", response_model=AIConfigRead)
async def get_ai_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> AIConfigRead:
    cfg = await _load_ai_config(db)

    ml = await _ml_status()
    # Same resolver the gate uses, so this page cannot show "Active" for a
    # feature that answers 503 everywhere. Deployment-wide on purpose
    # (``any_scope``): this checkbox IS the flag's kill switch (PUT writes
    # ``enabled_global``), and the page has no project to apply an allow-list
    # to. Asked without a scope, a project allow-list rendered the switch as
    # off, and saving the form then wrote it off for the allow-listed projects.
    from app.services.feature_flags import is_enabled
    cfg["knowledge_rag_enabled"] = await is_enabled("knowledge_rag", db=db, any_scope=True)
    gate_enabled = await is_enabled("rag_faithfulness_gate", db=db, any_scope=True)

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
        ai_timeout_seconds_source=cfg.get("ai_timeout_seconds_source", "env_default"),
        deep_investigation_enabled=cfg["deep_investigation_enabled"],
        deep_investigation_enabled_source=cfg.get("deep_investigation_enabled_source", "env_default"),
        finetune_enabled=cfg["finetune_enabled"],
        finetune_enabled_source=cfg.get("finetune_enabled_source", "env_default"),
        openai_key_set=bool(cfg.get("openai_api_key")),
        google_key_set=bool(cfg.get("google_api_key")),
        anthropic_key_set=bool(cfg.get("anthropic_api_key")),
        openrouter_key_set=bool(cfg.get("openrouter_api_key")),
        analysis_mode=cfg.get("analysis_mode", "auto"),
        analysis_mode_source=cfg.get("analysis_mode_source", "env_default"),
        knowledge_rag_enabled=cfg.get("knowledge_rag_enabled", False),
        rag_faithfulness_evaluator=cfg["rag_faithfulness_evaluator"],
        rag_faithfulness_evaluator_effective=cfg["rag_faithfulness_evaluator_effective"],
        rag_faithfulness_evaluator_reason=cfg["rag_faithfulness_evaluator_reason"],
        rag_faithfulness_gate_enabled=gate_enabled,
        ml_min_training_samples=settings.ML_MIN_TRAINING_SAMPLES,
        embedding_index=await _embedding_index_read(db, cfg),
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
    # W12 SRCH-02, embedding models only: true for the ACTIVE embedder, false
    # for the one being built (a target); null where it does not apply.
    in_use: Optional[bool] = None


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


class AIModeRead(BaseModel):
    """What every role's screens branch on: the analysis mode, and whether AI
    stays on this deployment."""
    analysis_mode: str
    # The effective offline ceiling (env OR stored override). The AI panel told
    # every reader "Powered by Ollama (local) -- no data leaves your network"
    # from a build-time default, also on deployments that send failure context
    # to a cloud model; the claim is now made only when it is true.
    ai_offline_mode: bool
    # AIC-01: whether deep investigation is offered at all (Settings > AI over
    # env, the value POST /deep-investigate obeys). Every role reads this
    # route, so the deep CTAs can be hidden for everyone; default True so a
    # client reading an older server's payload keeps today's behaviour.
    deep_investigation_enabled: bool = True


@router.get("/ai/mode", response_model=AIModeRead)
async def get_ai_mode(
    _: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AIModeRead:
    """The analysis mode (``rules`` / ``ml`` / ``llm`` / ``auto``), for any signed-in role.

    The sidebar (whether to offer Ask AI) and the chat, release-gate, run
    evidence and pipeline pages need only this, and read it for every role.
    They read ``GET /settings/ai`` (QA lead and above), so a QA engineer,
    tester or viewer got "Requires at least QA_LEAD role" toasts on every
    page and never saw Ask AI (the UX redesign's browser E2E pass). Nothing
    else of the config — providers, models, which keys are set — is here.
    """
    cfg = await _load_ai_config(db)
    return AIModeRead(
        analysis_mode=cfg.get("analysis_mode", "auto"),
        ai_offline_mode=bool(cfg.get("ai_offline_mode")),
        deep_investigation_enabled=bool(cfg.get("deep_investigation_enabled", True)),
    )


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

    from app.services.embedding_index_state import read_state
    from app.services.model_status_service import build_model_status

    # W12 SRCH-02: an Ollama embedding model is required when it is the ACTIVE
    # or the TARGET embedder, not merely configured. An unreadable state keeps
    # the configured-embedder rule (``embedding=None``).
    embedding = None
    state = await read_state(db)
    if state is not None:
        target = state.target_generation()
        embedding = {
            "active": state.active_generation().embedder.as_dict(),
            "target": target.embedder.as_dict() if target is not None else None,
        }
    status = await build_model_status(cfg, embedding=embedding)
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
        ANALYSIS_MODE_CACHE_KEY,
        CONFIDENCE_SOURCE_AI_CONFIG,
        CONFIDENCE_SOURCE_ENV_DEFAULT,
        env_offline_pinned,
        invalidate_ai_config_cache,
        resolve_offline_mode,
        resolve_stored_toggles,
        valid_analysis_mode,
    )
    from app.services.confidence_gate import normalize_threshold
    from app.services.rag_faithfulness_service import effective_evaluator

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

    # W12 SRCH-02: a CHANGED embedder is validated and starts a re-embed into
    # a new generation; reads keep the active one until it finishes. Only a
    # change counts -- the form posts every field back, and an unchanged
    # (legacy) value passes through untouched, whatever it is.
    embedder_change = None
    if "embedding_provider" in updates or "embedding_model" in updates:
        from app.services.embedding_function import (
            EMBEDDING_PROVIDERS,
            PROVIDER_UNSUPPORTED,
            EmbedderSpec,
        )

        new_spec = EmbedderSpec.from_settings(merged.get("embedding_provider"), merged.get("embedding_model"))
        old_spec = EmbedderSpec.from_settings(existing.get("embedding_provider"), existing.get("embedding_model"))
        if new_spec.key != old_spec.key:
            if new_spec.provider not in EMBEDDING_PROVIDERS:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=PROVIDER_UNSUPPORTED)
            if new_spec.provider != "default":
                offline_for_embedder, ollama_url, openai_key_set = await _embedder_runtime(db, merged, updates)
                await _validate_embedder_or_422(
                    new_spec, offline=offline_for_embedder, ollama_url=ollama_url,
                    openai_key_set=openai_key_set,
                )
            merged["embedding_provider"] = new_spec.provider
            merged["embedding_model"] = new_spec.model
            embedder_change = new_spec

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

    generation_request = None
    if embedder_change is not None:
        from app.services.chroma_collections import Generation
        from app.services.embedding_index_state import base_generation

        spec = embedder_change
        generation_request = await _stage_generation_request(
            db,
            lambda state: Generation(prefix=base_generation(state).prefix, embedder=spec),
            current_user,
            ["embedding_provider", "embedding_model"],
        )

    # Audit log
    await log_settings_change(db, _AI_CONFIG_KEY, "updated", current_user, changed_fields=list(updates.keys()))
    await db.commit()
    await invalidate_ai_config_cache()
    if generation_request is not None:
        await _after_generation_request(db, *generation_request)
    # AIC-04: re-seed the analysis-mode accelerator with the STORED mode, with
    # NO expiry (it used to be ex=86400, after which every reader silently fell
    # back to the env value), and whenever a valid mode is stored -- not only
    # when this request changed it: invalidate_ai_config_cache() just deleted
    # the key. analysis_router.resolve_configured_analysis_mode re-seeds it the
    # same way on a miss (flush, restart).
    _stored_mode = valid_analysis_mode(store_value.get("analysis_mode"))
    if _stored_mode is not None:
        try:
            from app.db.redis_client import get_redis
            redis = get_redis()
            await redis.set(ANALYSIS_MODE_CACHE_KEY, _stored_mode)
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
    # Re-resolve rather than echo: report the flag as it now stands -- the
    # switch, deployment-wide, as GET /settings/ai reports it.
    rag_enabled_after = await is_enabled("knowledge_rag", db=db, any_scope=True)
    gate_enabled = await is_enabled("rag_faithfulness_gate", db=db, any_scope=True)

    # Re-resolve rather than echo the request: the response must report what
    # is actually in force after the ceiling, not what was asked for.
    effective_offline, offline_source = resolve_offline_mode(merged["ai_offline_mode"])
    evaluator_effective, evaluator_reason = effective_evaluator(
        merged["rag_faithfulness_evaluator"], offline=effective_offline
    )
    # Provenance of the row just written, through the same helpers a GET uses.
    # After a save every key is stored (the form posts every field and the
    # merge keeps the rest), so these read "ai_config"; the confidence source
    # used to be omitted here, so a save echoed "env_default" (contract §0.9).
    saved_toggles = resolve_stored_toggles(store_value)
    saved_threshold_source = (
        CONFIDENCE_SOURCE_AI_CONFIG
        if normalize_threshold(store_value.get("ai_confidence_threshold")) is not None
        else CONFIDENCE_SOURCE_ENV_DEFAULT
    )

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
        ai_confidence_threshold_source=saved_threshold_source,
        ai_timeout_seconds=merged["ai_timeout_seconds"],
        ai_timeout_seconds_source=saved_toggles["ai_timeout_seconds_source"],
        deep_investigation_enabled=merged["deep_investigation_enabled"],
        deep_investigation_enabled_source=saved_toggles["deep_investigation_enabled_source"],
        finetune_enabled=merged["finetune_enabled"],
        finetune_enabled_source=saved_toggles["finetune_enabled_source"],
        openai_key_set=bool(merged.get("openai_api_key")),
        google_key_set=bool(merged.get("google_api_key")),
        anthropic_key_set=bool(merged.get("anthropic_api_key")),
        openrouter_key_set=bool(merged.get("openrouter_api_key")),
        analysis_mode=merged.get("analysis_mode", "auto"),
        analysis_mode_source=saved_toggles["analysis_mode_source"],
        knowledge_rag_enabled=rag_enabled_after,
        rag_faithfulness_evaluator=merged["rag_faithfulness_evaluator"],
        rag_faithfulness_evaluator_effective=evaluator_effective,
        rag_faithfulness_evaluator_reason=evaluator_reason,
        rag_faithfulness_gate_enabled=gate_enabled,
        ml_min_training_samples=settings.ML_MIN_TRAINING_SAMPLES,
        embedding_index=await _embedding_index_read(db, merged),
        **ml,
    )


@router.get("/ai/embedding-index", response_model=EmbeddingIndexRead)
async def get_embedding_index(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> EmbeddingIndexRead:
    """Which embedder every index uses NOW, what is configured, and any re-embed.

    Deployment-wide. The settings page polls this only while a build runs
    (``GET /settings/ai`` carries the same body on load). 503 when the state
    cannot be read: an unreadable state is never reported as in sync.
    """
    from app.services.embedding_index_state import STATE_UNREADABLE

    try:
        cfg = await _load_ai_config(db)
    except Exception:  # noqa: BLE001 - unreadable, not a guess
        cfg = None
    body = await _embedding_index_read(db, cfg) if cfg is not None else None
    if body is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STATE_UNREADABLE)
    return body


@router.post("/ai/embedding-index/rebuild", response_model=EmbeddingIndexRead)
async def rebuild_embedding_index(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> EmbeddingIndexRead:
    """**Re-embed now**: build the CONFIGURED generation (embedder + Collection prefix).

    ADMIN only (refused to project-bound keys); audited as
    ``settings_audit_log("embedding_index", "reembed_requested")``. 422 when
    the configured generation is already in use and nothing is building, 409
    while a build toward it runs (a failed one may be retried, and so may a
    STALLED one -- ``build_is_stalled``: its worker died), 422 with the
    embedder's sentence when this deployment refuses it.
    """
    from datetime import datetime, timezone

    from app.services.embedding_function import EMBEDDING_PROVIDERS, PROVIDER_UNSUPPORTED
    from app.services.embedding_index_state import (
        REBUILD_ALREADY_RUNNING,
        REBUILD_NOTHING_TO_DO,
        STATE_UNREADABLE,
        build_is_stalled,
        configured_generation,
        lock_state,
        request_target,
        stage_state,
    )

    cfg = await _load_ai_config(db)
    storage_values = await _load_storage_values(db)
    configured, _note = configured_generation(cfg, storage_values)
    # The checks read the LOCKED row, so two clicks cannot both start a build.
    row, before = await lock_state(db)
    target = before.target_generation()
    now = datetime.now(timezone.utc)
    if configured == before.active_generation() and target is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=REBUILD_NOTHING_TO_DO)
    # 409 only while a worker holds (or is about to take) the build; a STALLED
    # one (its worker died, its lease expired) is resumed under a new fence.
    if before.status == "building" and target == configured and not build_is_stalled(before, now=now):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=REBUILD_ALREADY_RUNNING)
    if configured.embedder.provider not in EMBEDDING_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=PROVIDER_UNSUPPORTED)
    if configured.embedder.provider != "default":
        offline, ollama_url, openai_key_set = await _embedder_runtime(db, cfg, {})
        await _validate_embedder_or_422(
            configured.embedder, offline=offline, ollama_url=ollama_url, openai_key_set=openai_key_set,
        )
    after = request_target(before, configured, actor_id=str(current_user.id), now=now)
    stage_state(db, row, after, actor_id=current_user.id)
    await log_settings_change(
        db, "embedding_index", "reembed_requested", current_user,
        changed_fields=["embedding_provider", "embedding_model", "chroma_collection"],
    )
    await db.commit()
    await _after_generation_request(db, before, after)
    logger.info("Re-embed requested by user_id=%s", current_user.id)
    body = await _embedding_index_read(db, cfg)
    if body is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STATE_UNREADABLE)
    return body


# ═══════════════════════════════════════════════════════════════════════════════
# Integrations Configuration
# ═══════════════════════════════════════════════════════════════════════════════

_INTEGRATIONS_KEY = "integrations_config"


async def _load_integrations_config(db: AsyncSession) -> dict:
    from sqlalchemy import select

    result = await db.execute(select(AppSetting).where(AppSetting.key == _INTEGRATIONS_KEY))
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row and row.value else {}
    from app.services.integration_config_service import (
        list_vault_reference_fields,
        resolve_global_integrations,
        resolve_prometheus_settings,
    )

    cfg = await resolve_global_integrations(db, overrides=overrides)
    # W23d AGT-15: the saved-only Prometheus block, from the same row (+ its secret_refs row).
    prometheus = await resolve_prometheus_settings(db, overrides=overrides)
    return {**cfg, **prometheus, "_vault_fields": await list_vault_reference_fields(db)}


def _integrations_read(cfg: dict) -> IntegrationsConfigRead:
    """The read model of the effective integrations config (GET and PUT).

    Secrets are answered as ``*_set`` booleans only. ``environment_fields``
    names the keys whose shown value comes from the process environment
    because they were never saved here (W10 INT-01); never a value.
    """
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
        environment_fields=list(cfg.get("_environment_fields") or []),
        vault_fields=list(cfg.get("_vault_fields") or []),
        # W23d AGT-15: never the token -- only whether one is stored.
        prometheus_enabled=bool(cfg.get("prometheus_enabled")),
        prometheus_base_url=cfg.get("prometheus_base_url"),
        prometheus_token_set=bool(cfg.get("prometheus_bearer_token")),
        prometheus_metric_templates=list(cfg.get("prometheus_metric_templates") or []),
        prometheus_templates_source=str(cfg.get("prometheus_templates_source") or "default"),
    )


#: The saved hosts an outbound integration call sends a credential to, as the
#: URL the egress guard checks (W10 INT-01; ``jira_domain`` is a bare host).
_INTEGRATION_HOST_FIELDS = {
    "splunk_base_url": lambda value: value,
    "ocp_api_url": lambda value: value,
    "jira_domain": lambda value: f"https://{value}",
    "prometheus_base_url": lambda value: value,   # W23d AGT-15
}


async def _integration_host_problems(updates: dict) -> list[dict]:
    """422 items for every saved integration host the egress guard refuses.

    Checked BEFORE anything is written. The blocked address is logged (with
    the field name) and never echoed to the caller.
    """
    from app.core.integration_values import TARGET_NOT_ALLOWED
    from app.services.url_safety import egress_block_reason

    problems: list[dict] = []
    for field_name, as_url in _INTEGRATION_HOST_FIELDS.items():
        value = updates.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        block = await egress_block_reason(as_url(value.strip()))
        if block is None:
            continue
        logger.info("Integrations save refused: field=%s reason=%s", field_name, block)
        problems.append({"loc": ["body", field_name], "msg": TARGET_NOT_ALLOWED, "type": "target_not_allowed"})
    return problems


@router.get("/integrations", response_model=IntegrationsConfigRead)
async def get_integrations_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> IntegrationsConfigRead:
    cfg = await _load_integrations_config(db)
    return _integrations_read(cfg)


@router.put("/integrations", response_model=IntegrationsConfigRead)
async def update_integrations_config(
    payload: IntegrationsConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> IntegrationsConfigRead:
    from sqlalchemy import select
    from app.services.integration_config_service import invalidate_runtime_integrations
    from app.services.secret_service import expire_secret, has_secret

    updates = payload.model_dump(exclude_none=True)
    # INT-02 (D21): the Slack "Default Channel" is accepted and ignored -- an
    # incoming webhook is bound to one channel. Never stored, never audited.
    updates.pop("slack_default_channel", None)

    # INT-01: a loopback / link-local / unspecified host is refused before
    # anything (a secret included) is written.
    host_problems = await _integration_host_problems(updates)
    if host_problems:
        raise HTTPException(status_code=422, detail=host_problems)

    # Store secrets separately
    secret_fields = {
        "jira_api_token",
        "splunk_api_token",
        "ocp_sa_token",
        "slack_webhook_url",
        "teams_webhook_url",
        "github_token",
        "prometheus_bearer_token",  # W23d AGT-15: secret_refs only ("" expires it; a vault: value is a reference)
    }
    for key_name in secret_fields.intersection(updates):
        raw_value = updates[key_name]
        if raw_value == "":
            await expire_secret(db, _INTEGRATIONS_KEY, key_name)
        elif raw_value is not None:
            await store_secret(
                db,
                _INTEGRATIONS_KEY,
                key_name,
                raw_value,
                actor_id=current_user.id,
            )

    # Session factory uses autoflush=False. Flush secret upserts/expirations so
    # the SELECTs below observe the post-update state rather than stale rows.
    await db.flush()
    secret_backed = any(
        [await has_secret(db, _INTEGRATIONS_KEY, key_name) for key_name in secret_fields]
    )

    # INT-01 task 2: persist the SAVED row plus the changes -- never the
    # environment-merged view, or every env value would be frozen into the
    # row as if an admin had typed it, and the environment would stop being
    # the fallback for keys never saved.
    result = await db.execute(select(AppSetting).where(AppSetting.key == _INTEGRATIONS_KEY))
    row = result.scalar_one_or_none()
    saved = dict(row.value) if row and row.value else {}
    store_value = strip_secrets_from_config(_INTEGRATIONS_KEY, {**saved, **updates})
    if updates.get("prometheus_metric_templates") == []:
        # W23d AGT-15: an empty list hands the templates back to the defaults (the key is removed, not stored).
        store_value.pop("prometheus_metric_templates", None)
    if row:
        row.value = store_value
        row.updated_by = current_user.id
        row.is_secret_backed = secret_backed
    else:
        db.add(AppSetting(key=_INTEGRATIONS_KEY, value=store_value, updated_by=current_user.id, is_secret_backed=secret_backed))

    await log_settings_change(db, _INTEGRATIONS_KEY, "updated", current_user, changed_fields=list(updates.keys()))
    await db.commit()
    # This process reads the new values at once; others within 30 s.
    invalidate_runtime_integrations()
    logger.info("Integrations configuration updated by user_id=%s", current_user.id)
    effective = await _load_integrations_config(db)
    return _integrations_read(effective)


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


async def _build_storage_config_read(values: dict, *, active_prefix: Optional[str] = None) -> StorageConfigRead:
    from app.services.chroma_collections import prefix_of

    status = await _get_storage_connection_status()
    chroma_collection = values.get("chroma_collection", settings.CHROMA_COLLECTION)
    # W12 SRCH-02: chroma_collection is the Collection PREFIX. A stored value
    # that is not a valid prefix reads as "no prefix", and the note says so.
    collection_prefix, collection_prefix_note = prefix_of(
        chroma_collection if isinstance(chroma_collection, str) else None
    )
    return StorageConfigRead(
        **status,
        storage_backend=values.get("storage_backend", settings.STORAGE_BACKEND),
        minio_endpoint=values.get("minio_endpoint", settings.MINIO_ENDPOINT),
        minio_bucket_name=values.get("minio_bucket_name", settings.MINIO_BUCKET_NAME),
        minio_use_ssl=values.get("minio_use_ssl", settings.MINIO_USE_SSL),
        chroma_host=values.get("chroma_host", settings.CHROMA_HOST),
        chroma_port=values.get("chroma_port", settings.CHROMA_PORT),
        chroma_collection=chroma_collection,
        collection_prefix=collection_prefix,
        collection_prefix_note=collection_prefix_note,
        active_collection_prefix=active_prefix,
    )


async def _active_collection_prefix(db: AsyncSession) -> Optional[str]:
    """The ACTIVE generation's prefix; None when the state cannot be read (never raises)."""
    from app.services.embedding_index_state import read_state

    state = await read_state(db)
    return state.active_generation().prefix if state is not None else None


@router.get("/storage", response_model=StorageConfigRead)
async def get_storage_config(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> StorageConfigRead:
    from sqlalchemy import select
    result = await db.execute(select(AppSetting).where(AppSetting.key == _STORAGE_KEY))
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row and row.value else {}
    return await _build_storage_config_read(
        overrides, active_prefix=await _active_collection_prefix(db),
    )


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

    # W12 SRCH-02: a CHANGED Collection prefix is validated and starts a
    # re-embed into new collections. An unchanged value (the form posts it
    # back) passes through untouched -- even one stored before W12 that is not
    # a valid prefix, which reads as "no prefix" with a note.
    prefix_change: Optional[str] = None
    if "chroma_collection" in updates:
        from app.services.chroma_collections import PREFIX_INVALID, prefix_change_is_valid, prefix_of

        old_value = str(existing.get("chroma_collection", settings.CHROMA_COLLECTION) or "").strip()
        new_value = str(updates["chroma_collection"] or "").strip()
        if new_value != old_value:
            if not prefix_change_is_valid(new_value):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=PREFIX_INVALID)
            updates["chroma_collection"] = new_value
            if prefix_of(new_value)[0] != prefix_of(old_value)[0]:
                prefix_change = prefix_of(new_value)[0]
    merged = {**existing, **updates}

    if row:
        row.value = merged
        row.updated_by = current_user.id
    else:
        db.add(AppSetting(key=_STORAGE_KEY, value=merged, updated_by=current_user.id))

    generation_request = None
    if prefix_change is not None:
        from app.services.chroma_collections import Generation
        from app.services.embedding_index_state import base_generation

        new_prefix = prefix_change
        generation_request = await _stage_generation_request(
            db,
            lambda state: Generation(prefix=new_prefix, embedder=base_generation(state).embedder),
            current_user,
            ["chroma_collection"],
        )

    await log_settings_change(db, _STORAGE_KEY, "updated", current_user, changed_fields=list(updates.keys()))
    await db.commit()
    from app.services.storage_config_service import invalidate_storage_config_cache

    await invalidate_storage_config_cache()
    if generation_request is not None:
        await _after_generation_request(db, *generation_request)
    logger.info("Storage configuration updated by user_id=%s", current_user.id)
    return await _build_storage_config_read(merged, active_prefix=await _active_collection_prefix(db))


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
    """Return health status for all tracked integration providers.

    The deployment-wide view only (``scope_key = ''``): per-project GitHub /
    GitLab rows (W10 INT-04) are Integration Health's ``?scope=all`` read.
    """
    from sqlalchemy import select as sa_select
    from app.models.postgres import IntegrationHealthCheck
    result = await db.execute(
        sa_select(IntegrationHealthCheck).where(IntegrationHealthCheck.scope_key == "")
    )
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


# ═══════════════════════════════════════════════════════════════════════════════
# Retention defaults — the single-row instance retention policy (W22b ACT-06)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Every retention field a project leaves unset inherits these, field by field
# (``retention_service.get_effective_policy``). ADMIN only — the instance
# default drives the scheduled purge of every inheriting project. The two reads
# answer any ADMIN credential; the two writes need an interactive (JWT) login:
# an API key can read the defaults but never change them. Switching the default
# ON is ONLY ``POST …/enable`` with the preview's hash and the typed phrase
# (D12); switching it off is a plain audited PUT. Every write is audited in
# ``settings_audit_log`` by the service; this router is unscoped (no ledger row).


def _instance_policy_error(exc: InstancePolicyError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _require_interactive_admin(current_user: User) -> None:
    if credential_kind(current_user) != CREDENTIAL_KIND_JWT:
        raise _instance_policy_error(InstancePolicyError("instance_interactive_only", 403))


@router.get("/retention-defaults", response_model=InstanceRetentionPolicyRead)
async def get_retention_defaults(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InstanceRetentionPolicyRead:
    """The instance retention defaults: the stored values (NULL = the code
    default), the code defaults, the effective values, how many projects
    inherit each field, and who switched the default on. Reads only."""
    return InstanceRetentionPolicyRead.model_validate(
        await retention_instance_service.instance_policy_read(db)
    )


@router.get("/retention-defaults/enable-preview", response_model=InstanceEnablePreviewResponse)
async def preview_retention_defaults_enable(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InstanceEnablePreviewResponse:
    """Every project switching the default on would affect (no policy row, or
    no explicit ``enabled``), the hash ``POST …/enable`` must echo and the phrase
    it must type. An instance administrator's deployment-wide view. Writes
    NOTHING."""
    payload = await retention_instance_service.instance_enable_preview(db, now=datetime.now(timezone.utc))
    return InstanceEnablePreviewResponse.model_validate(payload)


@router.put("/retention-defaults", response_model=InstanceRetentionPolicyRead)
async def update_retention_defaults(
    payload: InstanceRetentionPolicyWrite,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InstanceRetentionPolicyRead:
    """Save the instance defaults (``clear`` resets a field to the code
    default). ``enabled: true`` is refused (422 ``enable_via_flow``);
    ``enabled: false`` switches the default off at once. Interactive login only."""
    _require_interactive_admin(current_user)
    try:
        await retention_instance_service.upsert_instance_policy(
            db,
            actor=current_user,
            values=payload.model_dump(exclude={"enabled", "clear"}),
            clear=payload.clear or (),
            enabled=payload.enabled,
        )
    except InstancePolicyError as exc:
        raise _instance_policy_error(exc) from exc
    except RetentionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return InstanceRetentionPolicyRead.model_validate(
        await retention_instance_service.instance_policy_read(db)
    )


@router.post("/retention-defaults/enable", response_model=InstanceRetentionPolicyRead)
async def enable_retention_defaults(
    payload: InstanceEnableRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InstanceRetentionPolicyRead:
    """Switch the instance default ON for every project without its own
    ``enabled`` (D12): the preview's hash must still match (409
    ``preview_stale``) and the confirmation must be the exact phrase (422
    ``confirmation_mismatch``). Interactive login only."""
    _require_interactive_admin(current_user)
    try:
        await retention_instance_service.enable_instance_policy(
            db,
            actor=current_user,
            confirmation=payload.confirmation,
            preview_hash=payload.preview_hash,
            now=datetime.now(timezone.utc),
        )
    except InstancePolicyError as exc:
        raise _instance_policy_error(exc) from exc
    await db.commit()
    return InstanceRetentionPolicyRead.model_validate(
        await retention_instance_service.instance_policy_read(db)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Review gate — the instance enforcement date of the AI-report review gate (W23c AGT-10)
# ═══════════════════════════════════════════════════════════════════════════════
#
# ONE ``app_settings`` row (``review_gate``); no row = unset = audit only (D17).
# ADMIN only. The GET answers any ADMIN credential (an admin API key included)
# and writes nothing; the PUT needs an interactive (JWT) login. The PUT is
# audited in ``settings_audit_log`` (instance settings have no ledger row; this
# router is unscoped). ``gate_enforced()`` reads a 30 s process snapshot: the
# PUT sets this pod's at once and deletes the Redis mirror, so every other pod
# follows within 30 seconds (``review_gate_setting``).


def _review_gate_error(code: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": REVIEW_GATE_ERRORS[code]},
    )


async def _review_gate_read(db: AsyncSession) -> ReviewGateRead:
    """The answer of both routes: read on the request session, never a write.

    The row is read on the request session (its connection is already held by
    the auth dependency's user lookup) and seeds this pod's snapshot, so
    ``enforced`` / ``source`` are this pod's fresh answer without a second
    session. The would-refuse summary is measured from the audit rows, never
    cached; an unreadable audit log reads ``measured: false``.
    """
    now = datetime.now(timezone.utc)
    enforced_from, updated_at = await review_gate_setting.load_setting_record(db)
    review_gate_setting.seed_snapshot(enforced_from)
    env = bool(settings.REVIEW_GATE_ENFORCED)
    enforced, source = review_gate_setting.gate_state(enforced_from, env=env, now=now)
    would_refuse = await review_gate_setting.would_refuse_summary(db, now=now)
    return ReviewGateRead(
        enforced=enforced,
        source=source,
        enforced_from=enforced_from,
        env_override=env,
        updated_at=updated_at,
        would_refuse=WouldRefuseSummary.model_validate(would_refuse),
        channels=[GatedChannel(channel=code, label=label) for code, label in GATED_CHANNELS],
    )


@router.get("/review-gate", response_model=ReviewGateRead)
async def get_review_gate(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ReviewGateRead:
    """When unreviewed AI reports stop leaving TestLookup: the stored date (or
    none = audit only), whether the gate is enforced right now and why, and the
    distribution attempts that would have been withheld in the last 30 days.
    Reads only."""
    return await _review_gate_read(db)


@router.put("/review-gate", response_model=ReviewGateRead)
async def update_review_gate(
    payload: ReviewGateWrite,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ReviewGateRead:
    """Set the enforcement date (an ISO-8601 instant with an offset; past or
    future) or clear it with ``null`` (back to audit only). A past instant
    enforces at once. Interactive login only; audited once per actual change."""
    if credential_kind(current_user) != CREDENTIAL_KIND_JWT:
        raise _review_gate_error("interactive_only", status.HTTP_403_FORBIDDEN)
    enforced_from = payload.enforced_from
    if enforced_from is not None:
        if enforced_from.tzinfo is None or enforced_from.utcoffset() is None:
            raise _review_gate_error("naive_datetime", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            enforced_from = enforced_from.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="enforced_from is out of range.",
            ) from exc
    _before, after = await review_gate_setting.stage_setting(
        db, enforced_from=enforced_from, actor=current_user
    )
    await db.commit()
    await review_gate_setting.after_commit(after)
    return await _review_gate_read(db)
