"""Centralized provider and prompt-privacy policy for LLM invocations.

The factory is the only supported construction boundary for chat models.  This
module keeps provider capability metadata and deployment policy in one place so
new agents do not each invent an offline/egress rule.  The environment remains
the hard offline ceiling; an optional provider allowlist can further restrict
both local and hosted providers without changing agent code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger("services.llm_policy")

LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "localai", "vllm"})
REMOTE_PROVIDERS = frozenset({"openai", "gemini", "anthropic", "openrouter"})
KNOWN_PROVIDERS = LOCAL_PROVIDERS | REMOTE_PROVIDERS


class LLMPolicyViolation(ValueError):
    """Raised before model construction or invocation when policy denies it."""


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    residency: str
    supports_offline: bool
    requires_secret: bool


PROVIDER_PROFILES: dict[str, ProviderProfile] = {
    **{
        provider: ProviderProfile(
            provider=provider,
            residency="local",
            supports_offline=True,
            requires_secret=False,
        )
        for provider in LOCAL_PROVIDERS
    },
    **{
        provider: ProviderProfile(
            provider=provider,
            residency="remote",
            supports_offline=False,
            requires_secret=True,
        )
        for provider in REMOTE_PROVIDERS
    },
}


def _setting_text(name: str) -> str:
    value = getattr(settings, name, "")
    return value.strip() if isinstance(value, str) else ""


def configured_provider_allowlist() -> frozenset[str]:
    """Return the normalized optional allowlist; empty means no extra filter."""
    raw = _setting_text("AI_LLM_PROVIDER_ALLOWLIST")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def provider_profile(provider: str) -> ProviderProfile:
    normalized = str(provider or "").strip().lower()
    try:
        return PROVIDER_PROFILES[normalized]
    except KeyError as exc:
        raise LLMPolicyViolation(f"Unknown LLM provider: '{normalized}'") from exc


def enforce_provider_policy(
    provider: str,
    *,
    offline: bool,
    base_url: str | None = None,
) -> ProviderProfile:
    """Fail closed before constructing a provider client.

    ``AI_OFFLINE_MODE`` remains the non-bypassable egress ceiling.  Operators
    may additionally set ``AI_LLM_PROVIDER_ALLOWLIST`` to a comma-separated
    set of approved provider identifiers. A supplied base URL must be an
    absolute HTTP(S) origin and may be restricted with
    ``AI_LLM_ALLOWED_BASE_URLS``. Provider clients still own connection
    validation and secrets never enter this policy result.
    """
    profile = provider_profile(provider)
    allowlist = configured_provider_allowlist()
    if allowlist and profile.provider not in allowlist:
        raise LLMPolicyViolation(
            f"LLM provider '{profile.provider}' is not present in AI_LLM_PROVIDER_ALLOWLIST"
        )
    if profile.residency == "remote" and offline:
        raise LLMPolicyViolation(
            f"AI_OFFLINE_MODE=true but LLM_PROVIDER={profile.provider} — refusing to call external API"
        )
    if base_url:
        parsed = urlparse(str(base_url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LLMPolicyViolation("LLM base_url must be an absolute HTTP(S) origin")
        allowed_origins = _setting_text("AI_LLM_ALLOWED_BASE_URLS")
        if allowed_origins:
            candidate = f"{parsed.scheme}://{parsed.netloc}".lower()
            allowed = {
                item.strip().rstrip("/").lower()
                for item in allowed_origins.split(",")
                if item.strip()
            }
            if candidate not in allowed:
                raise LLMPolicyViolation("LLM base_url is not present in AI_LLM_ALLOWED_BASE_URLS")
    return profile


def sanitize_invocation(value: Any, *, stats: dict[str, int] | None = None) -> Any:
    """Recursively sanitize strings in a model invocation without raw logging."""
    from app.services.privacy_service import sanitize_for_llm

    counters = stats if stats is not None else {"redacted_strings": 0}
    if isinstance(value, str):
        safe = sanitize_for_llm(value)
        if safe != value:
            counters["redacted_strings"] = int(counters.get("redacted_strings", 0)) + 1
        return safe
    if isinstance(value, dict):
        return {key: sanitize_invocation(item, stats=counters) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_invocation(item, stats=counters) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_invocation(item, stats=counters) for item in value)

    # LangChain BaseMessage is a Pydantic model.  Copying only content keeps
    # role/tool metadata intact while ensuring message text crosses the same
    # redaction boundary as plain strings.
    try:
        from langchain_core.messages import BaseMessage

        if isinstance(value, BaseMessage):
            content = sanitize_invocation(value.content, stats=counters)
            if hasattr(value, "model_copy"):
                return value.model_copy(update={"content": content})
            return value.copy(update={"content": content})
        # ChatPromptValue and StringPromptValue are common chain boundaries;
        # copy their text/message payloads instead of allowing an opaque prompt
        # object to bypass the sanitizer.
        if hasattr(value, "messages"):
            messages = sanitize_invocation(value.messages, stats=counters)
            if hasattr(value, "model_copy"):
                return value.model_copy(update={"messages": messages})
            if hasattr(value, "copy"):
                return value.copy(update={"messages": messages})
        if hasattr(value, "text") and isinstance(value.text, str):
            text = sanitize_invocation(value.text, stats=counters)
            if hasattr(value, "model_copy"):
                return value.model_copy(update={"text": text})
            if hasattr(value, "copy"):
                return value.copy(update={"text": text})
    except Exception as exc:
        raise LLMPolicyViolation("llm_prompt_sanitization_failed") from exc
    if hasattr(value, "to_messages") or hasattr(value, "to_string"):
        raise LLMPolicyViolation("llm_prompt_sanitization_unsupported")
    return value


def record_invocation_audit(
    *,
    provider: str,
    model: str,
    stats: dict[str, int],
) -> None:
    """Record bounded, secret-free invocation metadata in the active context."""
    from app.services.pipeline_budget_service import get_pipeline_budget_context

    context = get_pipeline_budget_context()
    event = {
        "provider": str(provider)[:40],
        "model": str(model)[:120],
        "redacted_strings": max(int(stats.get("redacted_strings", 0)), 0),
    }
    logger.info("llm_invocation_policy", extra=event)
    if context is not None:
        events = context.get("llm_privacy_events")
        if not isinstance(events, list):
            events = []
        context["llm_privacy_events"] = [*events[-99:], event]
