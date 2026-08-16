"""Confidence validation — the one place a raw engine score becomes a published one.

This logic lived as ``AnalysisAgent._validate_confidence`` and was therefore
reachable only from the batch pipeline. ``POST /api/v1/analyze`` classifies the
same test through the same router and persisted the engine's **raw** number, so
the product published two different confidences for one test and one body of
evidence — measured on the homelab 2026-08-16:

    pipeline-stored : confidence=50  evidence=0  requires_human_review=True
    POST /analyze   : confidence=95  evidence=0  requires_human_review=False

and, because ``POST /analyze`` upserts the same ``AIAnalysis`` row, the stored
50 was **overwritten with 95**. Opening a finding in the UI promoted it above
the confidence gate and cleared its human-review flag, with no new evidence.

The caps are the product's stated policy about what a claim is worth, not a
pipeline implementation detail. Anything that persists a confidence has to
apply them, so they live here where every path can reach them.

Idempotent by design: ``evidence_multiplier_bonus`` adds +5, so validating
twice would inflate a score. A dict already carrying ``confidence_validated``
is returned untouched.
"""
from __future__ import annotations

import structlog

from app.core.config import settings

logger = structlog.get_logger("services.confidence_validation")

UNKNOWN_CATEGORY = "UNKNOWN"


def stringify_value(value) -> str:
    """Coerce an enum-like or scalar category value to a plain string."""
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def engine_label(analysis: dict) -> str:
    """Name the engine that actually produced this analysis.

    ``validate_confidence`` post-processes the output of *every* engine, but its
    adjustment reasons were written as though an LLM had always run. On the
    rules path that put "LLM returned no evidence_references" into
    ``routing_metadata.confidence_adjustments`` of a record whose own
    ``llm_provider`` is ``none`` and ``analysis_mode`` is ``rules`` — a
    provenance record asserting a model call that never happened.

    Falls back to the neutral "analysis" rather than guessing, so an
    unrecognised mode never re-introduces a false claim.
    """
    mode = ((analysis.get("_routing") or {}).get("mode_resolved") or "").lower()
    return {
        "llm": "LLM",
        "ml": "ML model",
        "rules": "rules engine",
    }.get(mode, "analysis")


def validate_confidence(analysis: dict) -> dict:
    """
    Validate and adjust confidence score based on evidence quality.

    Rules:
    - No evidence references → cap at 50
    - No root_cause_summary or very short → cap at 30
    - Has error + evidence + detailed summary → trust the score
    - Confidence clamped to 0-100

    Also re-evaluates the confidence gate against the FINAL number and sets
    ``requires_human_review`` / ``low_confidence`` / ``confidence_gate_status``
    accordingly, so no consumer has to re-derive the verdict by comparing
    numbers itself.
    """
    # Already validated — see the module docstring: the evidence bonus is not
    # idempotent, so a second pass would raise a score that nothing re-earned.
    if analysis.get("confidence_validated"):
        return analysis

    raw_confidence = analysis.get("confidence_score", 0)

    # Ensure integer in valid range
    try:
        confidence = max(0, min(100, int(raw_confidence)))
    except (TypeError, ValueError):
        confidence = 0

    # Capture the LLM's ORIGINAL signals BEFORE any cap/correction below
    # mutates them. Two self-consistency rules (flaky_contradicts_history,
    # evidence_count_vs_confidence) reason about what the LLM *claimed*, not
    # about the already-corrected state — otherwise earlier caps mask them
    # and they become dead code.
    raw_is_flaky = analysis.get("is_flaky") is True
    raw_confidence_clamped = confidence

    evidence = analysis.get("evidence_references") or []
    summary = analysis.get("root_cause_summary") or ""
    has_tools = bool(analysis.get("tools_used"))

    # Record every confidence adjustment so the per-test _audit block can
    # explain the final number — ops can see "LLM said 85, we capped to 50
    # because no evidence" without reading debug logs.
    adjustments: list[dict] = []
    # Whoever actually ran — these reasons are provenance, not prose.
    engine = engine_label(analysis)

    # Penalty: no evidence references at all
    if not evidence and confidence > 50:
        adjustments.append({
            "rule": "no_evidence_references",
            "from": confidence,
            "to": 50,
            "reason": f"{engine} returned no evidence_references",
        })
        confidence = min(confidence, 50)

    # Penalty: very short or generic summary
    if len(summary) < 30 and confidence > 30:
        adjustments.append({
            "rule": "summary_too_short",
            "from": confidence,
            "to": 30,
            "reason": f"root_cause_summary only {len(summary)} chars",
        })
        confidence = min(confidence, 30)

    # P2-6: Validate flakiness using actual historical data instead of LLM guess.
    # A test is flaky only if it has both passes AND failures historically,
    # with a pass rate between 10-90% (indicating non-deterministic behavior).
    flakiness_data = analysis.get("flakiness_data") or {}
    if flakiness_data:
        hist_passes = flakiness_data.get("pass_count", 0)
        hist_failures = flakiness_data.get("fail_count", 0)
        total_hist = hist_passes + hist_failures
        if total_hist > 0:
            hist_pass_rate = (hist_passes / total_hist) * 100
            is_actually_flaky = (
                hist_passes > 0
                and hist_failures > 0
                and 10 <= hist_pass_rate <= 90
            )
            analysis["is_flaky"] = is_actually_flaky
            if not is_actually_flaky and hist_failures > 0 and hist_passes == 0:
                # Never passed — this is broken, not flaky
                analysis["is_flaky"] = False

    # Penalty: no tools used and not a cache hit (suspicious high confidence)
    if not has_tools and not analysis.get("cache_hit") and not analysis.get("classified_by") and confidence > 60:
        adjustments.append({
            "rule": "no_tools_no_cache",
            "from": confidence,
            "to": 60,
            "reason": f"{engine} reached conclusion without invoking any tools",
        })
        confidence = min(confidence, 60)

    # Bonus: multiple evidence sources increase trustworthiness
    if len(evidence) >= 3 and confidence < 90:
        bonus_from = confidence
        confidence = min(confidence + 5, 100)
        adjustments.append({
            "rule": "evidence_multiplier_bonus",
            "from": bonus_from,
            "to": confidence,
            "reason": f"{len(evidence)} evidence references — +5 trust bonus",
        })

    # AIQ-P2 C1: self-consistency corrections (only ever LOWER/correct).
    # Cap: an UNKNOWN category cannot carry high confidence.
    if (
        stringify_value(analysis.get("failure_category")).upper() == UNKNOWN_CATEGORY
        and confidence > 40
    ):
        adjustments.append({
            "rule": "category_unknown_high_confidence",
            "from": confidence,
            "to": 40,
            "reason": "UNKNOWN failure_category cannot carry high confidence",
        })
        confidence = min(confidence, 40)

    # Correction: a never-passing history contradicts a flaky verdict.
    # Reason about the LLM's ORIGINAL flaky claim (``raw_is_flaky``): the
    # P2-6 block above may have already cleared the flag, but the desync
    # between what the LLM asserted and what history shows is exactly what
    # this rule must record. Ensure the corrected state is False.
    if (
        flakiness_data
        and flakiness_data.get("pass_count", 0) == 0
        and flakiness_data.get("fail_count", 0) > 0
        and raw_is_flaky
    ):
        analysis["is_flaky"] = False
        adjustments.append({
            "rule": "flaky_contradicts_history",
            "from": confidence,
            "to": confidence,
            "reason": "history has only failures — cleared flaky flag",
        })

    # Floor: an analysis carrying an error cannot assert any confidence.
    if analysis.get("error") and confidence > 0:
        adjustments.append({
            "rule": "error_present_zero_confidence_floor",
            "from": confidence,
            "to": 0,
            "reason": "analysis carries an error — forced confidence to 0",
        })
        confidence = 0

    # Cap: a RAW 60-80 LLM confidence with zero evidence and no tools is
    # unjustified. Reason about ``raw_confidence_clamped`` — the LLM's
    # ORIGINAL number — not the live ``confidence`` (the no_evidence_references
    # cap above already lowered it to 50 in exactly this case, which is what
    # previously made this rule dead code). Record the desync, and only ever
    # LOWER the final number (never raise it back to 60).
    if (
        60 < raw_confidence_clamped <= 80
        and not evidence
        and not has_tools
    ):
        capped_to = min(confidence, 60)
        adjustments.append({
            "rule": "evidence_count_vs_confidence",
            "from": raw_confidence_clamped,
            "to": capped_to,
            "reason": "raw confidence 60-80 with no evidence and no tools",
        })
        confidence = capped_to

    if adjustments:
        # Stashed under a private key; _analyse_one pops it into _audit so
        # we don't double-persist the list on the AIAnalysis row.
        analysis["_confidence_adjustments"] = adjustments
        logger.debug(
            "confidence_adjusted",
            raw=raw_confidence,
            final=confidence,
            adjustment_count=len(adjustments),
        )

    analysis["confidence_score"] = confidence
    # US-15.2: the gate is re-evaluated here against the FINAL (possibly
    # capped) confidence, reusing the threshold + source the router already
    # resolved, so routing_metadata never records a check against a number
    # that was subsequently adjusted. No stored check (legacy path, direct
    # ReAct calls) → fall back to the env default, preserving the previous
    # behaviour exactly.
    routing = analysis.get("_routing")
    prior_check = (routing or {}).get("threshold_check") or {}
    from app.services.confidence_gate import (
        SOURCE_ENV_DEFAULT,
        build_threshold_check,
        gate_status,
        is_low_confidence,
    )

    threshold = prior_check.get("threshold")
    source = prior_check.get("source")
    if not isinstance(threshold, int) or source not in ("ai_config", "env_default"):
        threshold = settings.AI_CONFIDENCE_THRESHOLD
        source = SOURCE_ENV_DEFAULT
    check = build_threshold_check(confidence, threshold, source)
    if isinstance(routing, dict):
        routing["threshold_check"] = check
    analysis["requires_human_review"] = not check["passed"]
    analysis["low_confidence"] = is_low_confidence(check)
    analysis["confidence_gate_status"] = gate_status(check)
    analysis["confidence_validated"] = True
    return analysis
