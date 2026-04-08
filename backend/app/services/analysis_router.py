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


def get_analysis_mode() -> str:
    """Resolve the effective analysis mode.

    Priority: Redis cache (set by Settings UI) → env var → "auto".
    For "auto": checks ML model availability, then LLM availability,
    then falls back to rules.
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
        return configured

    # Auto resolution: ML → LLM → Rules
    try:
        from app.services.ml.classifier import MLClassifier
        if MLClassifier.is_available():
            logger.debug("Auto mode: ML model available — using ML")
            return AnalysisMode.ML
    except Exception:
        pass

    # Check if LLM is likely reachable (heuristic: provider is configured)
    if settings.LLM_PROVIDER and settings.LLM_PROVIDER != "none":
        # Check circuit breaker first — if OPEN, skip LLM entirely
        try:
            from app.streams.circuit_breaker import LLMCircuitBreaker
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't await here; fall through to LLM which the breaker will guard
                pass
        except Exception:
            pass

        # For Ollama: verify the configured model is actually installed.
        # A 404 "model not found" from Ollama is not a transient error — it means
        # the model was never pulled.  Detect it here so auto mode degrades to rules
        # immediately instead of burning a LLM attempt on every task.
        if settings.LLM_PROVIDER == "ollama":
            try:
                import httpx
                resp = httpx.get(
                    f"{settings.OLLAMA_BASE_URL}/api/tags",
                    timeout=3.0,
                )
                if resp.status_code == 200:
                    installed = [m.get("name", "") for m in resp.json().get("models", [])]
                    # Match "qwen2.5:7b" against full tag names like "qwen2.5:7b" or "qwen2.5:latest"
                    model_base = settings.LLM_MODEL.split(":")[0]
                    model_available = any(
                        settings.LLM_MODEL in name or name.startswith(model_base)
                        for name in installed
                    )
                    if not model_available:
                        logger.warning(
                            "Auto mode: Ollama model '%s' not installed (available: %s) — using rules",
                            settings.LLM_MODEL,
                            installed or "none",
                        )
                        return AnalysisMode.RULES
            except Exception as probe_exc:
                logger.debug("Auto mode: Ollama probe failed (%s) — attempting LLM anyway", probe_exc)

        logger.debug("Auto mode: LLM provider configured — using LLM")
        return AnalysisMode.LLM

    logger.debug("Auto mode: no ML model or LLM — using rules")
    return AnalysisMode.RULES


async def refresh_analysis_mode_from_cache() -> str:
    """Read analysis_mode from Redis (set by Settings UI) and update in-process cache.

    Called at the start of pipeline tasks so the worker picks up UI changes.
    """
    global _cached_mode, _cached_mode_ts
    import time
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
    """
    effective_mode = mode or get_analysis_mode()

    if effective_mode == AnalysisMode.ML:
        return _classify_ml(test_case, history, run_context)

    if effective_mode == AnalysisMode.RULES:
        return _classify_rules(test_case, history, run_context)

    # LLM mode — delegate to existing triage agent
    return await _classify_llm(test_case, history, run_context)


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
    test_case: dict, history: dict | None, run_context: dict | None,
) -> dict:
    try:
        from app.services.ml.feature_extractor import extract_features
        from app.services.ml.classifier import MLClassifier

        features = extract_features(test_case, history, run_context)
        return MLClassifier.classify(features)
    except RuntimeError:
        # Model not available — fall back to rules
        logger.warning("ML model unavailable — falling back to rules engine")
        return _classify_rules(test_case, history, run_context)
    except Exception as exc:
        logger.error("ML classification failed: %s — falling back to rules", exc)
        return _classify_rules(test_case, history, run_context)


async def _classify_llm(
    test_case: dict, history: dict | None, run_context: dict | None,
) -> dict:
    """Delegate to the existing LLM-based triage agent."""
    try:
        from app.services.agent import run_triage_agent
        return await run_triage_agent(
            test_case_id=test_case.get("test_case_id", ""),
            test_name=test_case.get("test_name", ""),
            service_name=test_case.get("suite_name"),
            error_message=test_case.get("error_message"),
            stack_trace=test_case.get("stack_trace"),
        )
    except Exception as exc:
        logger.warning("LLM triage failed: %s — falling back to rules", exc)
        return _classify_rules(test_case, history, run_context)
