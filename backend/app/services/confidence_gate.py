"""Confidence gate (US-15.2) — the single source of truth for "is this AI
output trustworthy enough for an automation to act on it?".

What this is
------------
One configurable threshold (``ai_confidence_threshold``) plus one recorded
**threshold check** per decision. Automations ask this module instead of
hard-coding a number, so an operator can move the line in one place
(``/settings/ai``) and every gated automation follows.

Resolution precedence (mirrors ``ai_config_resolver``)
-----------------------------------------------------
1. ``app_settings["ai_config"]["ai_confidence_threshold"]``  → source ``"ai_config"``
2. ``settings.AI_CONFIDENCE_THRESHOLD`` (env / config default) → source ``"env_default"``

Boundary semantics — deliberately ``>=``
----------------------------------------
``passed = observed_confidence >= threshold``. A score *exactly at* the
threshold PASSES. This is not arbitrary: every pre-existing gate in the
codebase was written as ``confidence < THRESHOLD -> needs review``
(``analysis_agent._validate_confidence``, ``services.agent``,
``ml.classifier``), which is the same boundary. Centralising on ``>=`` keeps
those call sites byte-identical in behaviour at the boundary.

An **absent** confidence (``None``) never passes — we cannot gate on a number
we do not have, and silently treating "unknown" as "good enough" is exactly
the failure mode this story exists to prevent.

HONESTY NOTE — this is a policy knob, not a calibration
-------------------------------------------------------
The confidences being compared are **self-declared estimates**, not measured
probabilities. The rules engine emits ``confidence_basis="heuristic_estimate"``
bands (see ``services/confidence_bands.py``, whose own docstring says there is
no labelled corpus to calibrate against); human corrections pin a hard-coded
95. So "confidence >= 80" means "the engine claimed at least 80", NOT "this is
right 80% of the time". Do not describe the threshold as calibrated in UI copy
or docs. It is a risk-appetite dial over self-reported numbers.
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from app.core.config import settings

logger = structlog.get_logger("services.confidence_gate")

# Provenance values for ``threshold_check.source``.
SOURCE_AI_CONFIG = "ai_config"       # an operator override is stored in app_settings
SOURCE_ENV_DEFAULT = "env_default"   # nothing stored — the config/env default is in force

# Values for the response-contract marker. Plain strings, not a Literal: a
# strict enum over a widening vocabulary 422s the whole response.
GATE_ABOVE = "above_threshold"
GATE_BELOW = "below_threshold"
GATE_NOT_EVALUATED = "not_evaluated"   # no confidence available / gate not run


def env_default_threshold() -> int:
    """The configured default, used when no stored override exists."""
    return int(settings.AI_CONFIDENCE_THRESHOLD)


def normalize_threshold(raw: Any) -> Optional[int]:
    """Coerce a stored override to a valid 0-100 int, or ``None`` if unusable.

    A junk value in the JSONB settings blob must not brick every gate, so an
    unparseable / out-of-range override is dropped and the caller falls back
    to the env default rather than raising.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 100:
        return None
    return value


def passes(observed_confidence: Optional[int], threshold: int) -> bool:
    """``observed >= threshold``. ``None`` never passes. See module docstring."""
    if observed_confidence is None:
        return False
    try:
        return int(observed_confidence) >= int(threshold)
    except (TypeError, ValueError):
        return False


async def resolve_confidence_threshold() -> tuple[int, str]:
    """Return ``(threshold, source)`` for the effective confidence gate.

    Reads through ``get_effective_ai_config()`` so it shares that resolver's
    Redis cache (60s) and its DB-failure fallback. Never raises — a settings
    outage degrades to the env default rather than opening or closing every
    gate unpredictably.
    """
    try:
        from app.services.ai_config_resolver import get_effective_ai_config

        config = await get_effective_ai_config()
        value = normalize_threshold(config.get("confidence_threshold"))
        source = config.get("confidence_threshold_source")
        if value is not None and source in (SOURCE_AI_CONFIG, SOURCE_ENV_DEFAULT):
            return value, source
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("confidence_threshold_resolve_failed", error=str(exc))
    return env_default_threshold(), SOURCE_ENV_DEFAULT


def build_threshold_check(
    observed_confidence: Optional[int],
    threshold: int,
    source: str,
) -> dict[str, Any]:
    """The decision-trail record of one gate evaluation.

    Exactly four keys, stable shape — this dict is persisted verbatim into
    ``AIAnalysis.routing_metadata["threshold_check"]`` and into
    ``Defect.policy_evaluation["threshold_check"]``, and is surfaced by the
    decision trail. Do not add keys without extending the schemas that read it.
    """
    observed = None
    if observed_confidence is not None:
        try:
            observed = int(observed_confidence)
        except (TypeError, ValueError):
            observed = None
    return {
        "threshold": int(threshold),
        "observed_confidence": observed,
        "passed": passes(observed, threshold),
        "source": source,
    }


async def check_confidence(observed_confidence: Optional[int]) -> dict[str, Any]:
    """Resolve the effective threshold and evaluate ``observed`` against it."""
    threshold, source = await resolve_confidence_threshold()
    return build_threshold_check(observed_confidence, threshold, source)


def gate_status(threshold_check: Optional[dict[str, Any]]) -> str:
    """Map a threshold-check record to the response-contract marker.

    ``GATE_NOT_EVALUATED`` when there is no check at all, or when the check
    ran without an observed confidence — "we did not judge" is a distinct,
    honest answer from "we judged it low".
    """
    if not threshold_check:
        return GATE_NOT_EVALUATED
    if threshold_check.get("observed_confidence") is None:
        return GATE_NOT_EVALUATED
    return GATE_ABOVE if threshold_check.get("passed") else GATE_BELOW


def is_low_confidence(threshold_check: Optional[dict[str, Any]]) -> bool:
    """True only when the gate actually ran and the output failed it.

    An un-evaluated output is NOT marked low-confidence — the UI would be
    asserting something the backend never checked.
    """
    return gate_status(threshold_check) == GATE_BELOW
