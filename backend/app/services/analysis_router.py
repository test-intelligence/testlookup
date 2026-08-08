"""
Analysis Router — dispatches test classification and summary generation
to the correct engine based on the configured ANALYSIS_MODE.

Modes:
  "llm"   — Full LangChain ReAct agent (existing behavior)
  "ml"    — scikit-learn ML classifier (no LLM dependency)
  "rules" — Pattern matching + statistical heuristics (zero dependencies)
  "auto"  — ML if trained, else LLM if reachable, else rules

All modes produce the same output shape (AIAnalysis-compatible dict + 4-layer
summary structure), so downstream consumers (frontend, reports, dashboards)
work identically regardless of mode.

Usage:
    from app.services.analysis_router import AnalysisMode, get_analysis_mode, classify_test, generate_summary

    mode = get_analysis_mode()
    result = await classify_test(test_case, history, run_context)
    summary = await generate_summary(run_data, classifications, anomalies)
"""
import structlog
from typing import Any

from app.core.config import settings

logger = structlog.get_logger("services.analysis_router")


class AnalysisMode:
    LLM = "llm"
    ML = "ml"
    RULES = "rules"
    AUTO = "auto"


_cached_mode: str | None = None
_cached_mode_ts: float = 0

# Ollama model availability probe result. None = not probed yet, True/False = last result.
# Refreshed asynchronously by refresh_analysis_mode_from_cache() to avoid blocking the
# event loop with a synchronous httpx.get() call inside async code paths.
_ollama_model_available: bool | None = None
_ollama_probe_ts: float = 0
_OLLAMA_PROBE_TTL_SECONDS = 60


async def _probe_ollama_model_async() -> bool | None:
    """Async, non-blocking probe for Ollama model availability.

    Returns True if the configured model is installed, False if Ollama is reachable
    but the model is missing, None if the probe itself could not run.
    """
    if settings.LLM_PROVIDER != "ollama":
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
        if resp.status_code != 200:
            return None
        installed = [m.get("name", "") for m in resp.json().get("models", [])]
        model_base = settings.LLM_MODEL.split(":")[0]
        return any(
            settings.LLM_MODEL in name or name.startswith(model_base)
            for name in installed
        )
    except Exception as exc:
        logger.debug("ollama_probe_failed", error=str(exc))
        return None


def get_analysis_mode() -> str:
    """Resolve the effective analysis mode. See
    :func:`resolve_analysis_mode_with_reason` — this discards the reason."""
    return resolve_analysis_mode_with_reason()[0]


def resolve_analysis_mode_with_reason() -> tuple[str, str]:
    """Resolve the effective analysis mode **and why**.

    Priority: Redis cache (set by Settings UI) → env var → "auto".
    For "auto": checks ML model availability, then LLM availability,
    then falls back to rules.

    The reason used to exist only as a log line, so ``routing_metadata`` recorded
    ``mode_resolved: "rules"`` with ``fallback_reason: null`` and the decision
    trail — which calls itself authoritative — could not answer the first
    question an operator asks when AI analysis looks thin: *why didn't the LLM
    run?* Returning it alongside the mode lets the caller persist it.
    """
    global _cached_mode, _cached_mode_ts
    import time

    # In-process cache: re-read from Redis at most once per 30 seconds
    now = time.monotonic()
    if _cached_mode and (now - _cached_mode_ts) < 30:
        configured = _cached_mode
    else:
        configured = settings.ANALYSIS_MODE.lower()
        _cached_mode = configured
        _cached_mode_ts = now

    if configured != AnalysisMode.AUTO:
        return configured, f"mode explicitly set to '{configured}' — no auto resolution"

    # Auto resolution: ML → LLM → Rules
    try:
        from app.services.ml.classifier import MLClassifier
        if MLClassifier.is_available():
            logger.debug("Auto mode: ML model available — using ML")
            return AnalysisMode.ML, "auto: trained ML model available — preferred over LLM"
    except Exception:
        pass

    # Check if LLM is likely reachable (heuristic: provider is configured)
    if settings.LLM_PROVIDER and settings.LLM_PROVIDER != "none":
        # Ollama probe result is refreshed asynchronously by refresh_analysis_mode_from_cache().
        # Only degrade to rules when we have a *definitive* False result — unknown (None)
        # means we haven't probed yet; attempt LLM and let _classify_llm fall back on error.
        if settings.LLM_PROVIDER == "ollama" and _ollama_model_available is False:
            # structlog's BoundLogger doesn't accept printf-style positional
            # interpolation args — pass the model name as a kwarg instead.
            logger.warning(
                "auto_mode_ollama_model_unavailable",
                model=settings.LLM_MODEL,
                detail="cached probe definitively False — falling back to rules",
            )
            return AnalysisMode.RULES, (
                f"auto: no trained ML model, and the cached probe reports "
                f"ollama model '{settings.LLM_MODEL}' unavailable — fell back to rules"
            )

        logger.debug("Auto mode: LLM provider configured — using LLM")
        return AnalysisMode.LLM, (
            f"auto: no trained ML model; LLM provider "
            f"'{settings.LLM_PROVIDER}' configured and not known-unavailable"
        )

    logger.debug("Auto mode: no ML model or LLM — using rules")
    return AnalysisMode.RULES, (
        "auto: no trained ML model and no LLM provider configured — fell back to rules"
    )


async def refresh_analysis_mode_from_cache() -> str:
    """Read analysis_mode from Redis (set by Settings UI) and update in-process cache.

    Also refreshes the Ollama model availability probe using an async HTTP client, so
    the synchronous get_analysis_mode() can consult a cached result instead of blocking
    the event loop.

    Called at the start of pipeline tasks so the worker picks up UI changes.
    """
    global _cached_mode, _cached_mode_ts, _ollama_model_available, _ollama_probe_ts
    import time

    # Refresh Ollama probe (throttled to TTL) — never raises.
    now = time.monotonic()
    if (now - _ollama_probe_ts) > _OLLAMA_PROBE_TTL_SECONDS:
        _ollama_model_available = await _probe_ollama_model_async()
        _ollama_probe_ts = now

    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cached = await redis.get("config:analysis_mode")
        if cached and cached in ("llm", "ml", "rules", "auto"):
            _cached_mode = cached
            _cached_mode_ts = time.monotonic()
            return cached
    except Exception:
        pass
    return get_analysis_mode()


async def classify_test(
    test_case: dict[str, Any],
    history: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Classify a single test failure using the configured analysis engine.

    Args:
        test_case: Dict with error_message, duration_ms, severity, test_name, etc.
        history: Historical stats (pass_count, fail_count, etc.)
        run_context: Run-level context (pass_rate, failed_tests, etc.)
        mode: Override analysis mode (defaults to configured mode).

    Returns:
        Dict matching AIAnalysis shape with failure_category, confidence_score, etc.
        Always includes a ``_routing`` dict describing which engine ran and why
        (mode_requested, mode_resolved, mode_used, fallback_from, fallback_reason,
        auto_probe, prompt_versions, threshold_check). This is the authoritative
        decision record for the call — downstream code should read it instead of
        re-deriving the decision.

        US-15.2: ``_routing["threshold_check"]`` records the confidence-gate
        evaluation ({threshold, observed_confidence, passed, source}) and the
        result carries the explicit ``low_confidence`` / ``confidence_gate_status``
        markers so no consumer has to compare numbers itself.
    """
    requested = mode
    resolved = mode or get_analysis_mode()

    routing: dict[str, Any] = {
        "mode_requested": requested,
        "mode_resolved": resolved,
        "mode_used": resolved,
        "fallback_from": None,
        "fallback_reason": None,
        "auto_probe": {
            "ollama_model_available": _ollama_model_available,
            "probe_age_seconds": None,
        },
    }
    if _ollama_probe_ts:
        import time as _time
        routing["auto_probe"]["probe_age_seconds"] = round(
            _time.monotonic() - _ollama_probe_ts, 1,
        )

    logger.debug(
        "classify_test dispatching",
        mode_requested=requested,
        mode_resolved=resolved,
        test_case_id=test_case.get("test_case_id"),
    )

    if resolved == AnalysisMode.ML:
        result = _classify_ml(test_case, history, run_context, routing=routing)
    elif resolved == AnalysisMode.RULES:
        result = _classify_rules(test_case, history, run_context)
    else:
        result = await _classify_llm(test_case, history, run_context, routing=routing)

    # Attach the routing record. Private dispatchers may have updated routing
    # in-place (e.g., ML fallback to rules) — reflect the final state here.
    # AI-F2: stamp the registry version tags of the prompts the engine that
    # actually ran depends on (empty for the prompt-free rules/ML engines),
    # so the decision record traces back to exact prompt bytes.
    try:
        from app.services.prompt_registry import prompt_versions_used

        routing["prompt_versions"] = (
            prompt_versions_used("react_triage", "fast_classifier_system")
            if routing.get("mode_used") == AnalysisMode.LLM
            else {}
        )
    except Exception:  # pragma: no cover — stamping must never break routing
        routing["prompt_versions"] = {}

    # US-15.2: record the confidence-gate evaluation into the same decision
    # record, so the threshold check lands in AIAnalysis.routing_metadata and
    # surfaces in the decision trail without a second write path. Four keys:
    # threshold / observed_confidence / passed / source.
    #
    # NOTE: this observes the confidence AS RETURNED BY THE ENGINE. Downstream
    # post-processing (AnalysisAgent._validate_confidence caps/adjusts scores)
    # re-evaluates the gate against the FINAL number and rewrites this entry —
    # it deliberately reuses the threshold + source recorded here so the two
    # can never disagree about which policy was in force.
    try:
        from app.services.confidence_gate import check_confidence, gate_status, is_low_confidence

        check = await check_confidence(result.get("confidence_score"))
        routing["threshold_check"] = check
        # Explicit markers on the result so consumers never re-derive the
        # verdict by comparing numbers themselves.
        result["low_confidence"] = is_low_confidence(check)
        result["confidence_gate_status"] = gate_status(check)
        # ``requires_human_review`` is the field that is actually PERSISTED
        # (ai_analysis has no low_confidence column), served by the run
        # intelligence read path, and counted as "needs_review" in analytics.
        # It used to keep whatever the engine set — and rules_engine sets it
        # from a hardcoded ``confidence < 70``, independent of the
        # admin-configured threshold this gate just evaluated. Deriving it here
        # mirrors what services/agent.py already does, so the configured
        # threshold is authoritative on every path rather than only one.
        result["requires_human_review"] = not check["passed"]
    except Exception:  # pragma: no cover — gating must never break routing
        routing["threshold_check"] = None

    result["_routing"] = routing
    return result


async def generate_summary(
    run_data: dict[str, Any],
    classifications: dict[str, dict[str, Any]],
    anomalies: list[dict] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Generate a 4-layer structured summary using the configured engine.

    Returns dict with layer1_executive_summary, layer2_incident_view,
    layer3_evidence_pack, layer4_action_plan keys.
    """
    effective_mode = mode or get_analysis_mode()

    if effective_mode == AnalysisMode.ML:
        from app.services.ml.summary_generator import MLSummaryGenerator
        from app.services.ml.classifier import MLClassifier
        info = MLClassifier.get_model_info()
        return MLSummaryGenerator.generate(
            run_data, classifications, anomalies,
            model_version=info.get("version"),
        )

    if effective_mode == AnalysisMode.RULES:
        from app.services.rules_engine import RulesEngine
        return RulesEngine.generate_summary(run_data, classifications, anomalies)

    # LLM mode — return None to signal caller should use existing SummaryAgent
    return {}


# ── Private dispatchers ─────────────────────────────────────────────────────


def _classify_rules(
    test_case: dict, history: dict | None, run_context: dict | None,
) -> dict:
    from app.services.rules_engine import RulesEngine
    return RulesEngine.classify_test(
        error_message=test_case.get("error_message"),
        test_name=test_case.get("test_name"),
        duration_ms=test_case.get("duration_ms"),
        severity=test_case.get("severity"),
        history=history,
        run_context=run_context,
    )


def _classify_ml(
    test_case: dict,
    history: dict | None,
    run_context: dict | None,
    *,
    routing: dict | None = None,
) -> dict:
    try:
        from app.services.ml.feature_extractor import extract_features
        from app.services.ml.classifier import MLClassifier

        features = extract_features(test_case, history, run_context)
        result = MLClassifier.classify(features)
        if routing is not None:
            # AI-F1 honesty: record whether this model actually learned from
            # human labels or is still imitating LLM pseudo-labels
            # ("bootstrap_llm_imitating" below ML_HUMAN_LABEL_FLOOR human
            # labels in its training composition).
            try:
                from app.services.ml.label_provenance import (
                    model_maturity_from_metadata,
                )
                routing["ml_maturity"] = model_maturity_from_metadata(
                    MLClassifier.get_model_info(),
                    settings.ML_HUMAN_LABEL_FLOOR,
                )
            except Exception:  # noqa: BLE001 — honesty tag is best-effort
                routing["ml_maturity"] = None
        return result
    except RuntimeError as exc:
        # Model not available — fall back to rules
        reason = f"ml_model_unavailable: {exc}"
        logger.warning(
            "ML classification fell back to rules",
            reason=reason,
            test_case_id=test_case.get("test_case_id"),
        )
        if routing is not None:
            routing["mode_used"] = AnalysisMode.RULES
            routing["fallback_from"] = AnalysisMode.ML
            routing["fallback_reason"] = reason[:200]
        return _classify_rules(test_case, history, run_context)
    except Exception as exc:  # noqa: BLE001
        reason = f"ml_error: {type(exc).__name__}: {exc}"
        logger.error(
            "ML classification failed, falling back to rules",
            reason=reason,
            test_case_id=test_case.get("test_case_id"),
        )
        if routing is not None:
            routing["mode_used"] = AnalysisMode.RULES
            routing["fallback_from"] = AnalysisMode.ML
            routing["fallback_reason"] = reason[:200]
        return _classify_rules(test_case, history, run_context)


async def _classify_llm(
    test_case: dict,
    history: dict | None,
    run_context: dict | None,
    *,
    routing: dict | None = None,
) -> dict:
    """Delegate to the existing LLM-based triage agent."""
    try:
        from app.services.agent import run_triage_agent
        # Scope the analysis caches to this tenant (avoids cross-project
        # evidence leak on identical failures). project_id may be carried on
        # the test_case or the run-level context dict.
        _pid = test_case.get("project_id") or (run_context or {}).get("project_id")
        return await run_triage_agent(
            test_case_id=test_case.get("test_case_id", ""),
            test_name=test_case.get("test_name", ""),
            service_name=test_case.get("suite_name"),
            error_message=test_case.get("error_message"),
            stack_trace=test_case.get("stack_trace"),
            pipeline_run_id=test_case.get("pipeline_run_id"),
            project_id=str(_pid) if _pid else None,
        )
    except Exception as exc:  # noqa: BLE001
        reason = f"llm_error: {type(exc).__name__}: {exc}"
        logger.warning(
            "LLM triage failed, falling back to rules",
            reason=reason,
            test_case_id=test_case.get("test_case_id"),
        )
        if routing is not None:
            routing["mode_used"] = AnalysisMode.RULES
            routing["fallback_from"] = AnalysisMode.LLM
            routing["fallback_reason"] = reason[:200]
        return _classify_rules(test_case, history, run_context)
