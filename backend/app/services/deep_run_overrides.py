"""A deep investigation's run configuration (W14 AGT-07).

A deep run may be TIGHTENED for one run, never loosened: ONE
``AgentConfigPatch`` on the Root Cause agent (``RUN_CONFIG_AGENT_ID``: its model
tier, its ``max_failures_analyzed`` cap, its tool allowlist -- the evidence
sources) applied through the resolver's own patch path
(``agent_config_resolver.resolve_for_project(..., patch=...)``, the function and
the refusal text ``POST /agents/{id}/invoke`` uses), plus a subset of the run's
failing results the ingestion stage keeps.

``validate_run_configuration`` is called FIRST by the route (``POST
/deep-investigate/{run_id}`` and the estimate GET): every refusal is a 422
before anything is queued. The worker re-applies the same patch at task start
(``agents/workflow._create_pipeline_run``), where a project config tightened in
between wins (``superseded``).

Commit-free and read-only: one count statement for the subset plus the
resolver's own reads, all on the caller's session. Nothing logs.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_ops_vocab import (
    RUN_CONFIG_AGENT_ID,
    RUN_CONFIG_FAILURES_OUTSIDE,
    RUN_CONFIG_TIER_INVALID,
    RUN_CONFIG_TIGHTEN_ONLY,
)
from app.models.postgres import TestCase, TestRun, TestStatus
from app.services.agent_config_resolver import (
    AgentConfigInvalid,
    ResolvedAgentConfig,
    resolve_for_project,
)
from app.services.agent_config_service import TIER_ORDER, AgentConfigPatch, OverrideRejected

#: The run's failing results -- the ingestion stage's own set.
FAILING_STATUSES: tuple[str, ...] = (TestStatus.FAILED.value, TestStatus.BROKEN.value)

#: ``routers/agent_invoke.py``'s 409 for a stored config no clamp can repair (its words).
CONFIG_INVALID = (
    "The stored configuration of {agent_id} no longer validates. Fix it with "
    "PUT /api/v1/projects/{project_id}/agent-configs/{agent_id}"
)

__all__ = [
    "DeepRunConfiguration",
    "allowed_tiers",
    "build_patch",
    "configuration_read",
    "ledger_summary",
    "resolve_project_config",
    "validate_run_configuration",
]


@dataclass(frozen=True, slots=True)
class DeepRunConfiguration:
    failure_ids: tuple[uuid.UUID, ...] = ()        # () = every failing result
    selected_failures: int | None = None           # the estimate's count (the GET sends a count, not ids)
    max_failures_analyzed: int | None = None
    model_tier: str | None = None
    evidence_sources: tuple[str, ...] | None = None

    def is_empty(self) -> bool:
        """True when nothing is tightened and no subset is named."""
        return (
            not self.failure_ids
            and self.selected_failures is None
            and self.max_failures_analyzed is None
            and self.model_tier is None
            and self.evidence_sources is None
        )


def build_patch(c: DeepRunConfiguration) -> AgentConfigPatch | None:
    """The ONE tighten-only patch on ``RUN_CONFIG_AGENT_ID``; None when nothing is tightened."""
    fields: dict[str, Any] = {}
    if c.model_tier is not None:
        fields["model"] = {"tier": c.model_tier}
    if c.max_failures_analyzed is not None:
        fields["thresholds"] = {"max_failures_analyzed": int(c.max_failures_analyzed)}
    if c.evidence_sources is not None:
        fields["tools"] = {"allowlist": sorted(set(c.evidence_sources))}
    return AgentConfigPatch.model_validate(fields) if fields else None


def _refuse(detail: str | dict[str, Any]) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


async def _resolve(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    patch: AgentConfigPatch | None,
    global_ai_config: Mapping[str, Any],
) -> ResolvedAgentConfig:
    try:
        return await resolve_for_project(
            db, project_id, RUN_CONFIG_AGENT_ID, patch=patch, global_ai_config=global_ai_config,
        )
    except OverrideRejected as exc:
        raise _refuse({"message": RUN_CONFIG_TIGHTEN_ONLY, "errors": list(exc.reasons)}) from None
    except AgentConfigInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": CONFIG_INVALID.format(agent_id=RUN_CONFIG_AGENT_ID, project_id=project_id),
                "errors": list(exc.errors),
            },
        ) from None


async def resolve_project_config(
    db: AsyncSession, project_id: uuid.UUID, *, global_ai_config: Mapping[str, Any],
) -> ResolvedAgentConfig:
    """The Root Cause agent's project configuration, untightened (the read shapes' ``project_*``)."""
    return await _resolve(db, project_id, patch=None, global_ai_config=global_ai_config)


async def _failures_outside(db: AsyncSession, run: TestRun, failure_ids: Sequence[uuid.UUID]) -> int:
    """How many of ``failure_ids`` are NOT failing results of ``run`` (ONE count statement)."""
    wanted = sorted(set(failure_ids), key=str)
    inside = (await db.execute(
        select(func.count(TestCase.id)).where(
            TestCase.test_run_id == run.id,
            TestCase.id.in_(wanted),
            TestCase.status.in_(FAILING_STATUSES),
        )
    )).scalar_one()
    return len(wanted) - int(inside or 0)


async def validate_run_configuration(
    db: AsyncSession,
    run: TestRun,
    c: DeepRunConfiguration,
    *,
    global_ai_config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, ResolvedAgentConfig]:
    """Refuse a bad configuration (422) or answer ``(run_overrides, tightened RCA config)``.

    ``run_overrides`` is what the task receives (``None`` when nothing is
    configured): ``{"failure_ids": [str] | None, "agent_patches":
    {RUN_CONFIG_AGENT_ID: <patch JSON>}}``. The order of refusals: the tier
    word, the subset (one count statement), then the tighten-only resolver.
    """
    if c.model_tier is not None and c.model_tier not in TIER_ORDER:
        raise _refuse(RUN_CONFIG_TIER_INVALID)
    if c.failure_ids:
        outside = await _failures_outside(db, run, c.failure_ids)
        if outside:
            raise _refuse(RUN_CONFIG_FAILURES_OUTSIDE.format(n=outside))
    patch = build_patch(c)
    resolved = await _resolve(db, run.project_id, patch=patch, global_ai_config=global_ai_config)
    if patch is None and not c.failure_ids:
        return None, resolved
    run_overrides: dict[str, Any] = {
        "failure_ids": [str(i) for i in dict.fromkeys(c.failure_ids)] or None,
        "agent_patches": (
            {RUN_CONFIG_AGENT_ID: patch.model_dump(mode="json", exclude_none=True)}
            if patch is not None
            else {}
        ),
    }
    return run_overrides, resolved


def allowed_tiers(project: ResolvedAgentConfig) -> list[str]:
    """The project's tier and every tier below it; only its own when downgrade is off."""
    tier = str(project.config.model.tier)
    if not project.config.override_policy.allow_tier_downgrade or tier not in TIER_ORDER:
        return [tier]
    return list(TIER_ORDER[: TIER_ORDER.index(tier) + 1])


def configuration_read(
    resolved: ResolvedAgentConfig,
    project: ResolvedAgentConfig,
    c: DeepRunConfiguration,
    failing_results: int,
) -> dict[str, Any]:
    """A ``DeepRunConfigurationRead`` body: the effective values beside the project's."""
    selected: Optional[int] = len(set(c.failure_ids)) if c.failure_ids else c.selected_failures
    cap = int(resolved.config.thresholds.max_failures_analyzed)
    in_scope = selected if selected is not None else int(failing_results)
    return {
        "selected_failures": selected,
        "failures_analysed": max(0, min(in_scope, cap)),
        "max_failures_analyzed": cap,
        "project_max_failures_analyzed": int(project.config.thresholds.max_failures_analyzed),
        "model_tier": str(resolved.config.model.tier),
        "project_model_tier": str(project.config.model.tier),
        "allowed_tiers": allowed_tiers(project),
        "evidence_sources": list(resolved.config.tools.allowlist),
        "project_evidence_sources": list(project.config.tools.allowlist),
        "tool_narrowing_allowed": bool(project.config.override_policy.allow_tool_narrowing),
    }


def ledger_summary(run_overrides: dict[str, Any] | None) -> str:
    """One line for the ``analysis.triggered`` row: ``12 failures · tier slm · at most 10 analysed``."""
    if not run_overrides:
        return "every failure · project configuration"
    parts: list[str] = []
    failure_ids = run_overrides.get("failure_ids") or []
    parts.append(f"{len(failure_ids)} failures" if failure_ids else "every failure")
    patch = (run_overrides.get("agent_patches") or {}).get(RUN_CONFIG_AGENT_ID) or {}
    tier = (patch.get("model") or {}).get("tier")
    if tier:
        parts.append(f"tier {tier}")
    cap = (patch.get("thresholds") or {}).get("max_failures_analyzed")
    if cap is not None:
        parts.append(f"at most {cap} analysed")
    tools = patch.get("tools")
    if isinstance(tools, dict):
        parts.append(f"{len(tools.get('allowlist') or [])} evidence sources")
    return " · ".join(parts)[:200]
