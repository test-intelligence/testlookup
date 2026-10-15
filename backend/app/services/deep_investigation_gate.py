"""AIC-01: one gate for every path that starts a deep investigation.

The Settings > AI "Deep Investigation" checkbox (``deep_investigation_enabled``)
and the analysis mode used to be read from the ENVIRONMENT only by the single
trigger route, and not at all by the bulk route, the generic pipeline trigger
(``workflow_id: "deep"``) or a manual retry that re-runs a deep pipeline. A
stored "off" therefore changed nothing, and the UI offered a button that the
runtime either refused for a different reason or ran anyway.

Every one of those routes now calls :func:`require_deep_investigation` BEFORE
anything is queued. The decision reads the effective configuration (Settings >
AI over env, in both directions) through the request's own session only: a
guarded handler holds one connection of a 2+1 pool, and a second session here
stalls for 30 s (``tests/regression/test_deep_investigation_uses_one_connection.py``).

Not gated on purpose: a same-id RESUME (it replays accepted work under its
frozen snapshot), single-stage ``agent_invoke`` calls, and cancelling.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from fastapi import HTTPException, status

from app.core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

REASON_DISABLED = "disabled"
REASON_RULES_MODE = "rules_mode"

# Exact texts (contract W04 §2.2). ``disabled`` wins over ``rules_mode``.
MESSAGE_DISABLED = (
    "Deep investigation is disabled in Settings › AI. Ask an administrator to enable it."
)
MESSAGE_RULES_MODE = "Deep investigation requires LLM or Auto mode. Current mode: rules."


class DeepGateDecision(NamedTuple):
    allowed: bool
    reason_code: str | None  # "disabled" | "rules_mode" | None
    message: str | None
    analysis_mode: str  # the RESOLVED mode (auto resolved through the effective provider / ML)
    deep_investigation_enabled: bool
    deep_investigation_enabled_source: str


async def deep_investigation_gate(db: "AsyncSession") -> DeepGateDecision:
    """Decide whether a deep investigation may start. Pure read; uses ``db`` only."""
    from app.services.ai_config_resolver import SOURCE_ENV_DEFAULT, get_effective_ai_config
    from app.services.analysis_router import AnalysisMode, resolve_effective_analysis_mode

    cfg = await get_effective_ai_config(db=db)
    enabled = cfg.get("deep_investigation_enabled")
    if not isinstance(enabled, bool):  # a cache entry without the key: the env default
        enabled = bool(settings.DEEP_INVESTIGATION_ENABLED)
    source = str(cfg.get("deep_investigation_enabled_source") or SOURCE_ENV_DEFAULT)

    resolved, _reason, _configured = await resolve_effective_analysis_mode(db, config=cfg)

    if not enabled:
        return DeepGateDecision(False, REASON_DISABLED, MESSAGE_DISABLED, resolved, enabled, source)
    if resolved == AnalysisMode.RULES:
        return DeepGateDecision(False, REASON_RULES_MODE, MESSAGE_RULES_MODE, resolved, enabled, source)
    return DeepGateDecision(True, None, None, resolved, enabled, source)


async def require_deep_investigation(db: "AsyncSession") -> None:
    """Raise ``HTTPException(422, detail=message)`` when the gate refuses."""
    decision = await deep_investigation_gate(db)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=decision.message,
        )
