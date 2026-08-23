"""
LangChain ReAct agent service.
Runs a Thought → Action → Observation loop using 6 investigation tools
to produce a structured root-cause analysis for any test failure.

Includes:
  - Fast-path classifier (single LLM call)
  - Token budget management (auto-truncation before LLM calls)
  - Redis-based analysis caching (identical failures skip LLM)
  - Memory recall (AI-F3): prior corrections/analyses surfaced via the
    project-scoped recall_similar_failures tool
"""
import importlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

from app.core.config import settings
from app.core.tracing import get_tracer
from app.db.mongo import Collections, get_mongo_db
from app.services.input_sanitizer import (
    sanitize_error_message,
    sanitize_free_text,
    sanitize_service_name,
    sanitize_stack_trace,
)
from app.services.llm_factory import get_llm
from app.services.pipeline_event_log import emit_event as _emit_event
from app.services.prompt_registry import get_prompt_text
from app.services.resilience import (
    compute_analysis_cache_key,
    truncate_to_token_budget,
)

logger = logging.getLogger(__name__)
_tracer = get_tracer("services.agent")

# Versioned in the prompt registry (AI-F2): edits happen THERE, with a manifest
# bump + eval-gate attestation. v2 teaches the loop the memory recall tool.
SYSTEM_PROMPT = get_prompt_text("react_triage")


def _get_tools():
    from app.tools.analyze_ocp import analyze_openshift_pod_events
    from app.tools.check_flakiness import check_test_flakiness
    from app.tools.fetch_rest_payload import fetch_rest_api_payload
    from app.tools.fetch_stacktrace import fetch_allure_stacktrace
    from app.tools.query_splunk import query_splunk_logs
    from app.tools.recall_memory import recall_similar_failures

    return [
        fetch_allure_stacktrace,
        fetch_rest_api_payload,
        query_splunk_logs,
        check_test_flakiness,
        analyze_openshift_pod_events,
        recall_similar_failures,
    ]


async def run_triage_agent(
    test_case_id: str,
    test_name: str,
    service_name: Optional[str] = None,
    timestamp: Optional[str] = None,
    ocp_pod_name: Optional[str] = None,
    ocp_namespace: Optional[str] = None,
    error_message: Optional[str] = None,
    stack_trace: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    run_id: Optional[str] = None,
    project_id: Optional[str] = None,
    test_fingerprint: Optional[str] = None,
) -> dict:
    """
    Execute the LangChain ReAct triage agent for a failed test case.

    Fast path: tries the FastClassifier first (single LLM call, ~50ms).
    If the classifier is confident enough (>= CLASSIFIER_CONFIDENCE_THRESHOLD),
    returns immediately without running the full ReAct agent.

    Slow path: falls through to the full ReAct agent with 6 investigation tools.

    Stores full audit trail to MongoDB in both cases.
    Returns structured analysis dict.

    ``run_id`` / ``project_id`` are optional context. ``project_id`` scopes the
    analysis caches (exact Redis + semantic ChromaDB) so one tenant's cached
    analysis — which on the slow path embeds project-specific Splunk/OCP
    evidence — is never served to another tenant on an identical failure.
    ``project_id`` + ``test_fingerprint`` also feed the recall_similar_failures
    tool's server-side context (AI-F3) — recall is project-scoped by
    construction, never by LLM-provided input.
    """
    # ── Phase 4: Sanitise inputs before any LLM/tool interaction ────────────
    if service_name:
        service_name = sanitize_service_name(service_name)
    if error_message:
        error_message = sanitize_error_message(error_message)
    if stack_trace:
        stack_trace = sanitize_stack_trace(stack_trace)
    test_name = sanitize_free_text(test_name, max_length=500)

    # ── Cache lookup: skip LLM for identical failures ──────────────────────
    cached = (
        await _check_analysis_cache(
            test_name, error_message or "", stack_trace or "", project_id
        )
        if project_id
        else None
    )
    if cached is not None:
        cached["evidence_references"] = []
        logger.info("Cache hit for test '%s' — returning cached analysis", test_name)
        cached["cache_hit"] = True
        await _store_audit_trail(test_case_id, f"cache_hit:{test_name}", cached, [])
        await _emit_event(
            pipeline_run_id or "",
            "cache_hit",
            test_case_id=test_case_id,
            detail={"type": "redis_exact", "test_name": test_name[:100]},
        )
        return cached

    # ── Semantic cache: skip LLM for similar failures ────────────────────
    try:
        from app.services.semantic_cache import semantic_cache_lookup
        sem_cached = (
            await semantic_cache_lookup(
                test_name, error_message or "", stack_trace or "", project_id=project_id
            )
            if project_id
            else None
        )
        if sem_cached is not None:
            sem_cached["evidence_references"] = []
            await _store_audit_trail(test_case_id, f"semantic_cache_hit:{test_name}", sem_cached, [])
            await _emit_event(
                pipeline_run_id or "",
                "cache_hit",
                test_case_id=test_case_id,
                detail={
                    "type": "semantic_chromadb",
                    "test_name": test_name[:100],
                    "similarity": sem_cached.get("semantic_similarity", 0),
                },
            )
            return sem_cached
    except Exception as sem_exc:
        logger.debug("Semantic cache skipped: %s", sem_exc)

    # ── Fast path: single-call classifier ────────────────────────────────────
    # Recorded on the returned analysis so the stage can log it: a classifier
    # that emitted unreadable output must be distinguishable from one correctly
    # declining a hard case. Both used to return a bare None.
    classifier_outcome = "not_attempted"
    if error_message or stack_trace:
        try:
            from app.services.training.classifier import FastClassifier
            quick, classifier_outcome = await FastClassifier.classify_with_outcome(
                test_name=test_name,
                error_message=error_message or "",
                stack_trace=stack_trace or "",
            )
            if quick is not None:
                quick["tools_used"] = []  # Fast classifier uses no tools — honest empty list
                # F-17: no tools does not mean no evidence. This classifier saw
                # the error text and nothing else, so that text is the whole of
                # what supports its verdict — and citing it is what lets the
                # summary's evidence catalogue exist at all. Leaving this as []
                # is why coverage measured 0.06% on a deployment where every
                # analysis takes this path.
                quick["evidence_references"] = []
                # US-15.1: the fast classifier IS an LLM call, just a single-shot
                # one — say so rather than leaving provenance blank.
                quick["_routing"] = {
                    "mode_used": "llm",
                    "mode_requested": None,
                    "mode_resolved": "llm",
                    "fallback_from": None,
                    "fallback_reason": None,
                    "execution_path": "fast_classifier",
                }
                try:
                    from app.services.classifier_evidence import attach_classifier_evidence

                    attach_classifier_evidence(
                        quick,
                        {"test_name": test_name, "error_message": error_message or stack_trace},
                    )
                except Exception as ev_exc:  # pragma: no cover — provenance is best-effort
                    logger.debug("classifier evidence skipped: %s", ev_exc)
                # The denominator, attached HERE because this path returns
                # early -- it never reaches the assignment further down, which
                # only the ReAct path executes. Setting it there alone left
                # successes unrecorded exactly as before, and a source-level
                # test did not catch it because the source did contain the fix.
                quick["_classifier_outcome"] = classifier_outcome
                await _store_audit_trail(test_case_id, f"fast_classifier:{test_name}", quick, [])
                await _store_analysis_cache(test_name, error_message or "", stack_trace or "", quick, project_id)
                return quick
        except Exception as fc_exc:
            logger.debug("FastClassifier skipped: %s", fc_exc)
            classifier_outcome = "call_failed"

    # ── Token budget enforcement ─────────────────────────────────────────────
    # Truncate inputs that would blow the context window before the agent starts.
    max_input_tokens = settings.LLM_MAX_TOKENS - settings.PROMPT_OVERHEAD_TOKENS
    if error_message:
        error_message = truncate_to_token_budget(error_message, max_input_tokens // 4)
    if stack_trace:
        stack_trace = truncate_to_token_budget(stack_trace, max_input_tokens // 2)

    # ── Slow path: full ReAct agent ───────────────────────────────────────────
    from langchain_core.prompts import PromptTemplate

    langchain_agents = importlib.import_module("langchain.agents")
    create_react_agent = cast(Any, langchain_agents.create_react_agent)
    AgentExecutor = cast(Any, langchain_agents.AgentExecutor)

    llm = await get_llm()
    tools = _get_tools()

    prompt = PromptTemplate.from_template(SYSTEM_PROMPT)
    agent = create_react_agent(llm=llm, tools=tools, prompt=prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=settings.is_development,
        max_iterations=6,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
        max_execution_time=settings.AI_TIMEOUT_SECONDS,
        early_stopping_method="generate",
    )

    user_question = (
        f"Investigate why test '{test_name}' (ID: {test_case_id}) failed. "
        + (f"Backend service: '{service_name}'. " if service_name else "")
        + (f"Failure timestamp: {timestamp}. " if timestamp else "")
        + (f"Ran on OpenShift pod '{ocp_pod_name}' in namespace '{ocp_namespace}'." if ocp_pod_name else "")
    )

    logger.info("Starting AI triage for test: %s (provider: %s)", test_name, settings.LLM_PROVIDER)

    # US-15.1: the decision record for this call. Mirrors the shape
    # ``analysis_router.classify_test`` produces so both AI paths surface
    # provenance through the same ``AnalysisResponse.provenance`` block. The
    # LLM is what we are about to attempt; the except-branches below rewrite
    # ``mode_used`` when something else ends up producing the answer.
    routing: dict[str, Any] = {
        "mode_used": "llm",
        "mode_requested": None,
        "mode_resolved": "llm",
        "fallback_from": None,
        "fallback_reason": None,
        "execution_path": "react_agent",
    }

    # Bind the investigation identity for the recall_similar_failures tool
    # (AI-F3). Server-side ContextVar — the LLM never supplies identifiers, so
    # recall stays project-scoped even if the model passes garbage input.
    from app.tools.investigation_context import (
        InvestigationContext,
        reset_investigation_context,
        set_investigation_context,
    )
    from app.tools.recall_memory import reset_recall_context, set_recall_context
    _investigation_token = None
    if project_id and run_id:
        _investigation_token = set_investigation_context(InvestigationContext(
            project_id=str(project_id),
            run_id=str(run_id),
            test_case_id=str(test_case_id),
            test_name=test_name,
            test_fingerprint=test_fingerprint,
            service_name=service_name,
            timestamp=timestamp,
            ocp_pod_name=ocp_pod_name,
            ocp_namespace=ocp_namespace,
        ))
    _recall_token = set_recall_context(
        project_id=project_id,
        test_fingerprint=test_fingerprint,
        test_name=test_name,
        error_message=error_message,
    )
    try:
        with _tracer.start_as_current_span(
            "agent.react_triage",
            attributes={
                "agent.test_case_id": test_case_id,
                "agent.test_name": test_name[:200],
                "agent.llm_provider": settings.LLM_PROVIDER,
                "agent.llm_model": settings.LLM_MODEL,
            },
        ) as triage_span:
            try:
                result = await executor.ainvoke({"input": user_question})
                raw_output = result.get("output", "{}")
                intermediate_steps = result.get("intermediate_steps", [])

                # Parse JSON from agent output
                analysis = _parse_agent_output(raw_output)
                if analysis.get("schema_validated") is False:
                    await _emit_event(
                        pipeline_run_id or "",
                        "schema_validation_failed",
                        test_case_id=test_case_id,
                        detail={
                            "agent": "react_triage",
                            "schema": "RootCauseAnalysis",
                            "error": str(analysis.get("schema_validation_error") or "")[:500],
                        },
                    )
                analysis["llm_provider"] = settings.LLM_PROVIDER
                analysis["llm_model"] = settings.LLM_MODEL
                # requires_human_review is set once, at the single exit below,
                # from the US-15.2 confidence gate — not here. Setting it inline
                # meant only the happy path ever consulted a threshold.
                # Record which tools were actually invoked so the frontend can show honest stage progress.
                tools_used = _extract_tools_used(intermediate_steps)
                analysis["tools_used"] = tools_used
                # Model-produced citations are untrusted. Build the citation
                # candidates solely from tools that actually executed; the
                # terminal artifact service later binds these to tenant/run/test.
                analysis["evidence_references"] = _tool_observation_references(
                    intermediate_steps,
                    project_id=project_id,
                    run_id=run_id,
                    pipeline_run_id=pipeline_run_id,
                    test_case_id=test_case_id,
                )

                # Record tool call details as OTEL span events and pipeline events
                _record_tool_spans(triage_span, intermediate_steps)
                await _emit_event(
                    pipeline_run_id or "",
                    "llm_called",
                    test_case_id=test_case_id,
                    detail={
                        "provider": settings.LLM_PROVIDER,
                        "model": settings.LLM_MODEL,
                        "tools_used": tools_used,
                        "iterations": len(intermediate_steps),
                        "confidence": analysis.get("confidence_score", 0),
                        "category": analysis.get("failure_category", "UNKNOWN"),
                    },
                )

                triage_span.set_attribute("agent.tools_used", ",".join(tools_used))
                triage_span.set_attribute("agent.confidence_score", analysis.get("confidence_score", 0))
                triage_span.set_attribute("agent.failure_category", analysis.get("failure_category", "UNKNOWN"))
                triage_span.set_attribute("agent.iterations", len(intermediate_steps))

            except Exception as e:
                logger.error("Agent execution failed: %s", e, exc_info=True)
                triage_span.set_attribute("agent.error", str(e)[:500])
                error_str = str(e).lower()

                # ── Model not installed → fall back to rules engine silently ──────
                _model_not_found = (
                    "model" in error_str and "not found" in error_str
                ) or (
                    "404" in error_str and ("model" in error_str or "pull" in error_str)
                )
                if _model_not_found:
                    logger.warning(
                        "LLM model '%s' not available (%s) — falling back to rules engine",
                        settings.LLM_MODEL, str(e)[:120],
                    )
                    # Open the circuit breaker so subsequent tasks skip the LLM
                    try:
                        from app.streams.circuit_breaker import LLMCircuitBreaker
                        await LLMCircuitBreaker.record_failure()
                        await LLMCircuitBreaker.record_failure()
                        await LLMCircuitBreaker.record_failure()
                        await LLMCircuitBreaker.record_failure()
                        await LLMCircuitBreaker.record_failure()  # 5 failures → OPEN
                    except Exception:
                        pass
                    try:
                        from app.services.rules_engine import RulesEngine
                        analysis = RulesEngine.classify_test(
                            error_message=error_message,
                            test_name=test_name,
                            stack_trace=stack_trace,
                        )
                        analysis["llm_provider"] = settings.LLM_PROVIDER
                        analysis["llm_model"] = settings.LLM_MODEL
                        analysis["analysis_engine"] = "rules"
                        analysis["llm_unavailable_reason"] = _model_missing_hint(settings.LLM_MODEL)
                        # US-15.1: THE case this story exists for — the card
                        # must say "the LLM was unavailable, this is the rules
                        # engine" instead of presenting heuristics as model output.
                        routing["mode_used"] = "rules"
                        routing["fallback_from"] = "llm"
                        routing["fallback_reason"] = "llm_model_not_available"
                    except Exception as rules_exc:
                        logger.error("Rules engine fallback also failed: %s", rules_exc)
                        analysis = _fallback_analysis(_model_missing_hint(settings.LLM_MODEL))
                        # No engine produced this — a canned stub did. mode_used
                        # stays None so nothing claims authorship.
                        routing["mode_used"] = None
                        routing["fallback_from"] = "llm"
                        routing["fallback_reason"] = "llm_model_not_available_and_rules_failed"

                # ── Token limit ───────────────────────────────────────────────────
                elif any(kw in error_str for kw in ("token", "context length", "maximum context", "too long")):
                    logger.warning("Token limit exceeded — returning fallback with truncation hint")
                    analysis = _fallback_analysis(
                        f"Input exceeded LLM context window. Error: {str(e)[:200]}. "
                        "Consider reducing stack trace length or enabling a model with larger context."
                    )
                    routing["mode_used"] = None
                    routing["fallback_from"] = "llm"
                    routing["fallback_reason"] = "context_window_exceeded"
                else:
                    analysis = _fallback_analysis(str(e))
                    routing["mode_used"] = None
                    routing["fallback_from"] = "llm"
                    routing["fallback_reason"] = "llm_error"
                intermediate_steps = []
    finally:
        reset_recall_context(_recall_token)
        if _investigation_token is not None:
            reset_investigation_context(_investigation_token)

    # ── US-15.2: one confidence gate for every exit path ──────────────────────
    # Previously only the happy path compared confidence to a threshold, and it
    # read settings directly. Now every path (agent success, rules fallback,
    # canned stub) runs the same configurable gate and carries the explicit
    # markers, so no consumer re-derives the verdict from raw numbers.
    try:
        from app.services.confidence_gate import (
            check_confidence,
            gate_status,
            is_low_confidence,
        )

        gate_check = await check_confidence(analysis.get("confidence_score"))
        routing["threshold_check"] = gate_check
        analysis["requires_human_review"] = not gate_check["passed"]
        analysis["low_confidence"] = is_low_confidence(gate_check)
        analysis["confidence_gate_status"] = gate_status(gate_check)
    except Exception as gate_exc:  # pragma: no cover — never fail the analysis
        logger.warning("Confidence gate evaluation failed: %s", gate_exc)
        analysis.setdefault("requires_human_review", True)
    analysis["_routing"] = routing
    # F-4 follow-up: the fast classifier was the last LLM path whose failures
    # were counted nowhere. Carried on the analysis so AnalysisAgent can record
    # it in the stage decision log, which is where the baseline harness looks.
    #
    # `classified` is recorded too, and that is the point: without successes
    # there is no denominator, so "no entries" reads identically whether the
    # classifier ran perfectly or never ran at all. Measured 2026-08-23 against
    # 40 novel failures -- zero entries, and only the Mongo payload's
    # `classified_by` could tell the two apart. A metric that looks healthiest
    # when nothing happened is the same fail-open shape this instrumentation
    # was added to remove.
    #
    # `not_attempted` stays out: it is not a classifier call, and emitting one
    # per test case would bloat the decision log on every rules/ML run.
    if classifier_outcome != "not_attempted":
        analysis["_classifier_outcome"] = classifier_outcome

    # Store full audit trail to MongoDB
    await _store_audit_trail(test_case_id, user_question, analysis, intermediate_steps)

    # Cache successful analyses for future identical AND similar failures
    if analysis.get("confidence_score", 0) > 0 and error_message:
        await _store_analysis_cache(test_name, error_message or "", stack_trace or "", analysis, project_id)
        try:
            from app.services.semantic_cache import semantic_cache_store
            await semantic_cache_store(test_name, error_message or "", stack_trace or "", analysis, project_id=project_id)
        except Exception as sem_exc:
            logger.debug("Semantic cache store skipped: %s", sem_exc)

    return analysis


def _extract_tools_used(intermediate_steps: list) -> list[str]:
    """Extract deduplicated list of tool names actually invoked during ReAct execution."""
    seen: list[str] = []
    for step in intermediate_steps:
        # intermediate_steps is a list of (AgentAction, observation) tuples
        try:
            action = step[0] if isinstance(step, (tuple, list)) else step
            tool_name = getattr(action, "tool", None)
            if tool_name and tool_name not in seen:
                seen.append(tool_name)
        except Exception:
            pass
    return seen


def _tool_observation_references(
    intermediate_steps: list,
    *,
    project_id: str | None = None,
    run_id: str | None = None,
    pipeline_run_id: str | None = None,
    test_case_id: str | None = None,
) -> list[dict[str, str]]:
    """Create bounded citation candidates from actual tool observations."""
    from app.services.evidence_sanitizer import sanitize_reference_text

    references: list[dict[str, str]] = []
    for step in intermediate_steps[:100]:
        try:
            action = step[0] if isinstance(step, (tuple, list)) else step
            observation = (
                step[1]
                if isinstance(step, (tuple, list)) and len(step) > 1
                else ""
            )
            tool_name, _, _ = sanitize_reference_text(
                str(getattr(action, "tool", "unknown")), limit=100
            )
            excerpt, _, _ = sanitize_reference_text(str(observation), limit=500)
            normalized = excerpt.strip().lower()
            if not excerpt or any(marker in normalized for marker in (
                " lookup denied:",
                " lookup unavailable:",
                " query failed:",
                "no authorized investigation context",
                "no allure result found",
                "no rest api payload captured",
            )):
                continue
            reference = {
                "source": tool_name,
                "kind": "tool_observation",
                "excerpt": excerpt,
                "freshness": "current_run",
                "sensitivity": "restricted",
            }
            if all((project_id, run_id, pipeline_run_id, test_case_id)):
                from app.services.evidence_artifact_service import (
                    tool_observation_attestation,
                )
                reference["producer_attestation"] = tool_observation_attestation(
                    project_id=str(project_id),
                    run_id=str(run_id),
                    pipeline_id=str(pipeline_run_id),
                    test_case_id=str(test_case_id),
                    source=tool_name,
                    kind="tool_observation",
                    excerpt=excerpt,
                )
            references.append(reference)
        except Exception:
            continue
    return references


def _record_tool_spans(parent_span: Any, intermediate_steps: list) -> None:
    """Record each ReAct tool invocation as an OTEL span event on the parent span."""
    from app.services.evidence_sanitizer import sanitize_reference_text

    for i, step in enumerate(intermediate_steps):
        try:
            action = step[0] if isinstance(step, (tuple, list)) else step
            observation = step[1] if isinstance(step, (tuple, list)) and len(step) > 1 else ""
            tool_name = getattr(action, "tool", "unknown")
            tool_input, _, _ = sanitize_reference_text(
                str(getattr(action, "tool_input", "")), limit=300
            )
            obs_preview, _, _ = sanitize_reference_text(str(observation), limit=300)

            parent_span.add_event(
                f"tool_call.{tool_name}",
                attributes={
                    "tool.name": tool_name,
                    "tool.input_preview": tool_input,
                    "tool.output_preview": obs_preview,
                    "tool.output_length": len(str(observation)),
                    "tool.step_index": i,
                },
            )
        except Exception:
            pass


def _parse_agent_output(raw: str) -> dict:
    """Extract and parse JSON from agent final answer."""
    from app.models.llm_schemas import RootCauseAnalysis, validate_llm_output_with_error
    from app.services.llm_json_parser import parse_llm_json

    expected_keys = [
        "root_cause_summary",
        "failure_category",
        "backend_error_found",
        "pod_issue_found",
        "is_flaky",
        "confidence_score",
        "recommended_actions",
        "role_actions",
        "evidence_references",
    ]
    parsed, error = parse_llm_json(
        raw,
        expected_keys=expected_keys,
        context="react_triage_root_cause",
    )
    if error:
        fallback = _fallback_analysis(
            f"Could not parse structured output from agent ({error})"
        )
        fallback["schema_validation_error"] = error
        fallback["schema_validated"] = False
        return fallback

    validated, validation_error = validate_llm_output_with_error(
        RootCauseAnalysis,
        parsed,
        context="react_triage_root_cause",
    )
    validated["schema_validated"] = validation_error is None
    if validation_error:
        validated["schema_validation_error"] = validation_error
    return validated


def _model_missing_hint(model: str) -> str:
    """Build a runtime-aware "model not installed" hint.

    The previous hardcoded ``docker compose exec ollama ollama pull ...``
    message was wrong for K8s deployments — users on K3s / OpenShift saw
    a Docker Compose command and (correctly) wondered why it didn't work.
    The runtime-aware recipe now lives in ``model_status_service`` so the
    AI settings page shows an operator the same command this analysis
    tells them to run (US-13.2).
    """
    from app.services.model_status_service import ollama_pull_command

    pull_cmd = ollama_pull_command(model)
    return (
        f"Model '{model}' not installed on the Ollama instance. "
        f"Ask your admin to pull it: {pull_cmd}. "
        "If the Ollama pod has no internet egress (NordVPN / firewall blocking "
        "registry.ollama.ai), the pull will fail until that's resolved."
    )


def _fallback_analysis(error_msg: str) -> dict:
    return {
        "root_cause_summary": f"AI analysis could not complete. Reason: {error_msg}. Manual investigation required.",
        "failure_category": "UNKNOWN",
        "backend_error_found": False,
        "pod_issue_found": False,
        "is_flaky": False,
        "confidence_score": 0,
        "recommended_actions": ["Review stack trace manually", "Check application logs", "Re-run test to check for flakiness"],
        "role_actions": {
            "qa": "Manually inspect the stack trace and re-run the test to determine if the failure is reproducible.",
            "developer": "Review recent code changes for this module and check application logs for errors.",
            "sre": "Verify infrastructure health — check pod logs, resource limits, and recent deployment events.",
            "release_manager": "Hold release until the test failure is investigated and root cause confirmed.",
        },
        "evidence_references": [],
        "tools_used": [],
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "requires_human_review": True,
    }


async def _store_audit_trail(test_case_id: str, prompt: str, analysis: dict, steps: list) -> None:
    """Persist a bounded, sanitized execution audit without chain-of-thought."""
    from app.services.evidence_sanitizer import (
        sanitize_persistence_payload,
        sanitize_reference_text,
    )

    safe_prompt, _, _ = sanitize_reference_text(prompt, limit=2000)
    safe_analysis, stats = sanitize_persistence_payload(analysis)
    if stats.omitted_items or stats.truncated_strings:
        safe_analysis = {
            "failure_category": analysis.get("failure_category", "UNKNOWN"),
            "confidence_score": analysis.get("confidence_score", 0),
            "requires_human_review": True,
            "audit_truncated": True,
        }
    safe_steps: list[dict[str, str]] = []
    for step in steps[:100]:
        try:
            action = step[0] if isinstance(step, (tuple, list)) else step
            observation = step[1] if isinstance(step, (tuple, list)) and len(step) > 1 else ""
            safe_observation, _, _ = sanitize_reference_text(
                str(observation), limit=500
            )
            safe_steps.append({
                "tool": str(getattr(action, "tool", "unknown"))[:100],
                "observation": safe_observation,
            })
        except Exception:
            continue
    db = get_mongo_db()
    await db[Collections.AI_ANALYSIS_PAYLOADS].update_one(
        {"test_case_id": test_case_id},
        {"$set": {
            "test_case_id": test_case_id,
            "prompt": safe_prompt,
            "analysis": safe_analysis,
            "intermediate_steps": safe_steps,
            "llm_provider": settings.LLM_PROVIDER,
            "llm_model": settings.LLM_MODEL,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ── Redis-based AI analysis cache ───────────────────────────────────────────

def _scoped_cache_key(
    test_name: str, error_message: str, stack_trace: str, project_id: Optional[str],
) -> str:
    """Project-scoped analysis cache key.

    The cached slow-path analysis embeds project-specific evidence
    (Splunk/OCP excerpts), so the key MUST be tenant-scoped — otherwise an
    identical failure in another project gets served this project's evidence.
    """
    base = compute_analysis_cache_key(test_name, error_message, stack_trace)
    return f"{base}:proj:{project_id}" if project_id else base


async def _check_analysis_cache(
    test_name: str, error_message: str, stack_trace: str,
    project_id: Optional[str] = None,
) -> Optional[dict]:
    """Return cached analysis for identical failure, or None on miss."""
    if not error_message and not stack_trace:
        return None
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cache_key = _scoped_cache_key(test_name, error_message, stack_trace, project_id)
        raw = await redis.get(cache_key)
        if raw:
            return cast(dict[Any, Any], json.loads(raw))
    except Exception as exc:
        logger.debug("Analysis cache lookup failed (non-critical): %s", exc)
    return None


async def _store_analysis_cache(
    test_name: str, error_message: str, stack_trace: str, analysis: dict,
    project_id: Optional[str] = None,
) -> None:
    """Cache an analysis result in Redis with TTL."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cache_key = _scoped_cache_key(test_name, error_message, stack_trace, project_id)
        # Store a clean copy without transient fields
        from app.services.evidence_sanitizer import sanitize_persistence_payload

        cacheable = {
            k: v for k, v in analysis.items()
            if k not in ("cache_hit", "evidence_references")
        }
        cacheable, stats = sanitize_persistence_payload(cacheable)
        if stats.omitted_items or stats.truncated_strings:
            return
        await redis.set(cache_key, json.dumps(cacheable, default=str), ex=settings.AI_ANALYSIS_CACHE_TTL)
    except Exception as exc:
        logger.debug("Analysis cache store failed (non-critical): %s", exc)
