"""Resolve an agent's effective configuration (architecture E4.2, sections 4.1 and 4.3).

Four layers, each allowed only to tighten the one above it::

    env ceiling        AI_OFFLINE_MODE, AI_LLM_PROVIDER_ALLOWLIST, the attempt and
                       timeout ceilings, AI_PIPELINE_DEADLINE_SECONDS
    global ai_config   provider, model, temperature, max_tokens, base_url, and an
                       offline override that can only turn egress off
    project AgentConfig  the stored ``agent_configs`` row, or the defaults
    request patch      ``AgentConfigPatch``, applied by ``apply_patch``

The offline clamp runs HERE, at resolve time, and not only when a config is
written: a project that stored a cloud provider before the environment flipped
to offline must not get that provider back. A tier whose provider the current
policy refuses falls back to the global ``ai_config`` model when that one is
permitted, and otherwise has no endpoint at all. Every change the resolver makes
is recorded in ``clamps`` with the layer that forced it.

A stored row is also clamped to ceilings lowered after it was written. A row
that no longer validates for any other reason (a tool that was removed, say) is
not guessed at: ``AgentConfigInvalid`` is raised and the caller refuses to run.

The resolved object never carries API keys; they stay in ``ai_config``.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Mapping, Optional, TypeGuard

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import agent_config_service as configs
from app.services.agent_config_service import AgentConfigPatch, AgentConfigV1, apply_patch, default_config
from app.services.ai_config_resolver import apply_offline_ceiling, get_effective_ai_config
from app.services.llm_policy_service import (
    LLMPolicyViolation,
    REMOTE_PROVIDERS,
    configured_provider_allowlist,
    enforce_provider_policy,
    enforce_provider_policy_async,
)

LAYER_ENV = "env"
LAYER_AI_CONFIG = "ai_config"
LAYER_PROJECT = "project"
LAYER_REQUEST = "request"

TIERS: tuple[str, ...] = ("slm", "llm")


class AgentConfigInvalid(ValueError):
    """The stored configuration no longer validates, for a reason a clamp cannot fix."""

    def __init__(self, agent_id: str, errors: list[str]):
        self.agent_id = agent_id
        self.errors = errors
        super().__init__(f"{agent_id}: " + "; ".join(errors))


class Clamp(BaseModel):
    """One value the resolver changed, and which layer forced the change."""

    field: str
    layer: str
    requested: Any = None
    effective: Any = None
    reason: str


class ResolvedEndpoint(BaseModel):
    provider: str
    model: str
    temperature: float
    max_tokens: int
    base_url: Optional[str] = None
    source: str = Field(description="project: the agent config's own block; ai_config: inherited from the global config")


class ResolvedAgentConfig(BaseModel):
    agent_id: str
    source: str = Field(description="default when the project has no row")
    config_version: int
    patched: bool = False
    config: AgentConfigV1
    endpoints: dict[str, Optional[ResolvedEndpoint]]
    offline_mode: bool
    offline_mode_source: str
    offline_mode_env_pinned: bool
    clamps: list[Clamp] = Field(default_factory=list)


# -- env ceilings on a stored document ---------------------------------------------------


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _clamp_to_ceilings(doc: dict[str, Any], clamps: list[Clamp]) -> None:
    """Lower a stored document's attempts and timeout to today's environment."""
    deadline = int(settings.AI_PIPELINE_DEADLINE_SECONDS)
    timeout_limit = min(int(settings.AGENT_MAX_TIMEOUT_CEILING), deadline)
    timeout = doc.get("timeout_seconds")
    if _is_int(timeout) and timeout > timeout_limit:
        clamps.append(Clamp(
            field="timeout_seconds", layer=LAYER_ENV, requested=timeout, effective=timeout_limit,
            reason=f"AGENT_MAX_TIMEOUT_CEILING={settings.AGENT_MAX_TIMEOUT_CEILING}, "
                   f"AI_PIPELINE_DEADLINE_SECONDS={deadline}",
        ))
        doc["timeout_seconds"] = timeout = timeout_limit
    retry = doc.get("retry")
    if not isinstance(retry, dict) or not _is_int(retry.get("max_attempts")):
        return
    attempts = retry["max_attempts"]
    limit = int(settings.AGENT_MAX_ATTEMPTS_CEILING)
    reason = f"AGENT_MAX_ATTEMPTS_CEILING={limit}"
    if _is_int(timeout) and timeout > 0 and deadline // timeout < limit:
        limit = max(1, deadline // timeout)
        reason = f"attempts x timeout_seconds must fit AI_PIPELINE_DEADLINE_SECONDS={deadline}"
    if attempts > limit:
        clamps.append(Clamp(field="retry.max_attempts", layer=LAYER_ENV, requested=attempts, effective=limit, reason=reason))
        retry["max_attempts"] = limit


# -- provider policy per tier ----------------------------------------------------------------


def _policy_refusal(provider: str, ai: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    """``(layer, reason)`` when current policy refuses ``provider``, else ``None``.

    ``base_url`` is not passed here, so this makes no DNS lookup; the async
    resolver checks endpoint residency separately.
    """
    offline = bool(ai.get("offline_mode"))
    try:
        enforce_provider_policy(provider, offline=offline, base_url=None)
    except LLMPolicyViolation as exc:
        allowlist = configured_provider_allowlist()
        if allowlist and provider not in allowlist:
            return LAYER_ENV, str(exc)
        if offline and provider in REMOTE_PROVIDERS and not ai.get("offline_mode_env_pinned"):
            return LAYER_AI_CONFIG, str(exc)
        return LAYER_ENV, str(exc)
    return None


def _global_endpoint(ai: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    provider = str(ai.get("provider") or "").strip().lower()
    model = str(ai.get("model") or "").strip()
    if not provider or not model:
        return None
    return {
        "provider": provider,
        "model": model,
        "temperature": float(ai.get("temperature") or 0.0),
        "max_tokens": int(ai.get("max_tokens") or 1024),
        "base_url": ai.get("base_url") or None,
        "source": LAYER_AI_CONFIG,
    }


def _endpoint(tier: str, config: AgentConfigV1, ai: Mapping[str, Any], clamps: list[Clamp]) -> Optional[ResolvedEndpoint]:
    fallback = _global_endpoint(ai)
    block = getattr(config.model, tier)
    if block is None:
        candidate = fallback
    else:
        candidate = {
            "provider": block.provider,
            "model": block.model,
            "temperature": block.temperature,
            "max_tokens": block.max_tokens,
            # Endpoints come only from the environment and the global config, and
            # only for the provider they were configured for.
            "base_url": fallback["base_url"] if fallback and fallback["provider"] == block.provider else None,
            "source": LAYER_PROJECT,
        }
    if candidate is None:
        return None
    refusal = _policy_refusal(candidate["provider"], ai)
    if refusal is None:
        return ResolvedEndpoint(**candidate)
    layer, reason = refusal
    replacement = None
    if candidate["source"] == LAYER_PROJECT and fallback is not None and _policy_refusal(fallback["provider"], ai) is None:
        replacement = fallback
    clamps.append(Clamp(
        field=f"model.{tier}.provider", layer=layer, requested=candidate["provider"],
        effective=replacement["provider"] if replacement else None, reason=reason,
    ))
    return ResolvedEndpoint(**replacement) if replacement else None


# -- resolution ------------------------------------------------------------------------------------


def resolve(
    agent_id: str,
    *,
    global_ai_config: Mapping[str, Any],
    stored: Optional[Mapping[str, Any]] = None,
    config_version: int = 0,
    patch: Optional[AgentConfigPatch] = None,
) -> ResolvedAgentConfig:
    """The effective configuration of one agent. Pure: no I/O and no DNS.

    ``stored`` is the project's document with ``enabled`` and ``mode`` merged in,
    or ``None`` for the defaults. Raises ``AgentConfigInvalid`` for a stored row
    no clamp can repair, and ``OverrideRejected`` for a patch that loosens.
    """
    clamps: list[Clamp] = []
    # env > global: re-clamp even a dict that was clamped when it was cached.
    ai = apply_offline_ceiling(dict(global_ai_config or {}))

    if stored is None:
        source = "default"
        config = default_config(agent_id)
    else:
        source = LAYER_PROJECT
        doc = copy.deepcopy(dict(stored))
        doc["agent_id"] = agent_id
        _clamp_to_ceilings(doc, clamps)
        try:
            config = AgentConfigV1.model_validate(doc)
        except ValidationError as exc:
            raise AgentConfigInvalid(agent_id, [str(err.get("msg", "")) for err in exc.errors()]) from exc

    if patch is not None:
        config = apply_patch(config, patch)

    endpoints = {tier: _endpoint(tier, config, ai, clamps) for tier in TIERS}
    return ResolvedAgentConfig(
        agent_id=agent_id,
        source=source,
        config_version=int(config_version),
        patched=patch is not None,
        config=config,
        endpoints=endpoints,
        offline_mode=bool(ai["offline_mode"]),
        offline_mode_source=str(ai["offline_mode_source"]),
        offline_mode_env_pinned=bool(ai["offline_mode_env_pinned"]),
        clamps=clamps,
    )


async def resolve_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_id: str,
    *,
    patch: Optional[AgentConfigPatch] = None,
) -> ResolvedAgentConfig:
    """``resolve`` against the stored row and the live global config, plus endpoint residency."""
    row = await configs.get_config_row(db, project_id, agent_id)
    stored: Optional[dict[str, Any]] = None
    version = 0
    if row is not None:
        stored = {**dict(row.config or {}), "enabled": bool(row.enabled), "mode": row.mode}
        version = int(row.config_version)
    resolved = resolve(
        agent_id,
        global_ai_config=await get_effective_ai_config(),
        stored=stored,
        config_version=version,
        patch=patch,
    )
    for tier, endpoint in list(resolved.endpoints.items()):
        if endpoint is None or not endpoint.base_url:
            continue
        try:
            await enforce_provider_policy_async(endpoint.provider, offline=resolved.offline_mode, base_url=endpoint.base_url)
        except LLMPolicyViolation as exc:
            resolved.endpoints[tier] = None
            resolved.clamps.append(Clamp(
                field=f"model.{tier}.base_url", layer=LAYER_ENV, requested=endpoint.provider, effective=None, reason=str(exc),
            ))
    return resolved


def invocation_refusal(resolved: ResolvedAgentConfig, *, project_id: uuid.UUID) -> Optional[str]:
    """Why this project may not invoke the agent, or ``None``.

    ``AgentConfigV1`` already refuses an enabled agent whose mode cannot run its
    capability, so a resolved config only needs its ``enabled`` flag checked.
    """
    if resolved.config.enabled:
        return None
    return (
        f"{resolved.agent_id} is disabled for this project. Enable it with "
        f"PUT /api/v1/projects/{project_id}/agent-configs/{resolved.agent_id}"
    )


__all__ = [
    "AgentConfigInvalid",
    "Clamp",
    "LAYER_AI_CONFIG",
    "LAYER_ENV",
    "LAYER_PROJECT",
    "LAYER_REQUEST",
    "ResolvedAgentConfig",
    "ResolvedEndpoint",
    "invocation_refusal",
    "resolve",
    "resolve_for_project",
]
