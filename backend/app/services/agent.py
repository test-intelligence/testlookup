"""
LangChain ReAct agent service.
Runs a Thought → Action → Observation loop using 5 investigation tools
to produce a structured root-cause analysis for any test failure.

Includes:
  - Fast-path classifier (single LLM call)
  - Token budget management (auto-truncation before LLM calls)
  - Redis-based analysis caching (identical failures skip LLM)
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
from app.services.resilience import (
    compute_analysis_cache_key,
    truncate_to_token_budget,
)

logger = logging.getLogger(__name__)
_tracer = get_tracer("services.agent")

SYSTEM_PROMPT = """You are an expert Software Quality Assurance Architect and Site Reliability Engineer.
Your objective is to analyse failed automated test cases, identify the root cause, and produce
a clear, structured, actionable defect analysis.

You have access to five investigation tools:
{tools}

STRICT RULES:
1. Base ALL conclusions strictly on data returned by your tools. NEVER guess or hallucinate root causes.
2. If tool responses are empty or inconclusive, state: "Insufficient telemetry to determine root cause."
3. Always check for infrastructure/environment issues BEFORE assuming the application code is broken.
4. Check for flakiness history before classifying a failure as a product bug.
5. You MUST use at least the stack trace tool before forming any conclusion.

After completing your investigation, return ONLY a valid JSON object with this exact schema:
{{
  "root_cause_summary": "string (2-4 sentences explaining the root cause in plain English)",
  "failure_category": "PRODUCT_BUG | INFRASTRUCTURE | TEST_DATA | AUTOMATION_DEFECT | FLAKY",
  "backend_error_found": true | false,
  "pod_issue_found": true | false,
  "is_flaky": true | false,
  "confidence_score": integer 0-100,
  "recommended_actions": ["action 1", "action 2", "action 3"],
  "role_actions": {{
    "qa": "1 sentence: what the QA engineer should do next",
    "developer": "1 sentence: what the developer responsible for this code should do",
    "sre": "1 sentence: what the SRE/platform team should investigate or monitor",
    "release_manager": "1 sentence: release gate recommendation (hold / proceed with conditions / clear to release)"
  }},
  "evidence_references": [
    {{"source": "stacktrace | splunk | ocp_events | flakiness", "reference_id": "...", "excerpt": "..."}}
  ]
}}

Available tools: {tool_names}

Use the following format:
Thought: your reasoning about what to investigate next
Action: the tool name to use
Action Input: the input to the tool
Observation: the tool's output
... (repeat Thought/Action/Observation as needed)
Thought: I now have enough information to form a conclusion
Final Answer: {{valid JSON object as specified above}}

Begin!

Question: {input}
Thought: {agent_scratchpad}"""


def _get_tools():
    from app.tools.analyze_ocp import analyze_openshift_pod_events
    from app.tools.check_flakiness import check_test_flakiness
    from app.tools.fetch_rest_payload import fetch_rest_api_payload
    from app.tools.fetch_stacktrace import fetch_allure_stacktrace
    from app.tools.query_splunk import query_splunk_logs

    return [
        fetch_allure_stacktrace,
        fetch_rest_api_payload,
        query_splunk_logs,
        check_test_flakiness,
        analyze_openshift_pod_events,
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
) -> dict:
    """
    Execute the LangChain ReAct triage agent for a failed test case.

    Fast path: tries the FastClassifier first (single LLM call, ~50ms).
    If the classifier is confident enough (>= CLASSIFIER_CONFIDENCE_THRESHOLD),
    returns immediately without running the full ReAct agent.

    Slow path: falls through to the full ReAct agent with 5 investigation tools.

    Stores full audit trail to MongoDB in both cases.
    Returns structured analysis dict.
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
    cached = await _check_analysis_cache(test_name, error_message or "", stack_trace or "")
    if cached is not None:
        logger.info("Cache hit for test '%s' — returning cached analysis", test_name)
        cached["cache_hit"] = True
        await _store_audit_trail(test_case_id, f"cache_hit:{test_name}", cached, [])
        await _emit_event("", "cache_hit", test_case_id=test_case_id, detail={"type": "redis_exact", "test_name": test_name[:100]})
        return cached

    # ── Semantic cache: skip LLM for similar failures ────────────────────
    try:
        from app.services.semantic_cache import semantic_cache_lookup
        sem_cached = await semantic_cache_lookup(test_name, error_message or "", stack_trace or "")
        if sem_cached is not None:
            await _store_audit_trail(test_case_id, f"semantic_cache_hit:{test_name}", sem_cached, [])
            await _emit_event("", "cache_hit", test_case_id=test_case_id, detail={
                "type": "semantic_chromadb",
                "test_name": test_name[:100],
                "similarity": sem_cached.get("semantic_similarity", 0),
            })
            return sem_cached
    except Exception as sem_exc:
        logger.debug("Semantic cache skipped: %s", sem_exc)

    # ── Fast path: single-call classifier ────────────────────────────────────
    if error_message or stack_trace:
        try:
            from app.services.training.classifier import FastClassifier
            quick = await FastClassifier.classify(
                test_name=test_name,
                error_message=error_message or "",
                stack_trace=stack_trace or "",
            )
            if quick is not None:
                quick["tools_used"] = []  # Fast classifier uses no tools — honest empty list
                await _store_audit_trail(test_case_id, f"fast_classifier:{test_name}", quick, [])
                await _store_analysis_cache(test_name, error_message or "", stack_trace or "", quick)
                return quick
        except Exception as fc_exc:
            logger.debug("FastClassifier skipped: %s", fc_exc)

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
            analysis["llm_provider"] = settings.LLM_PROVIDER
            analysis["llm_model"] = settings.LLM_MODEL
            analysis["requires_human_review"] = analysis.get("confidence_score", 0) < settings.AI_CONFIDENCE_THRESHOLD
            # Record which tools were actually invoked so the frontend can show honest stage progress.
            tools_used = _extract_tools_used(intermediate_steps)
            analysis["tools_used"] = tools_used

            # Record tool call details as OTEL span events and pipeline events
            _record_tool_spans(triage_span, intermediate_steps)
            await _emit_event("", "llm_called", test_case_id=test_case_id, detail={
                "provider": settings.LLM_PROVIDER,
                "model": settings.LLM_MODEL,
                "tools_used": tools_used,
                "iterations": len(intermediate_steps),
                "confidence": analysis.get("confidence_score", 0),
                "category": analysis.get("failure_category", "UNKNOWN"),
            })

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
                    analysis["llm_unavailable_reason"] = (
                        f"Model '{settings.LLM_MODEL}' is not installed. "
                        f"Run: docker compose exec ollama ollama pull {settings.LLM_MODEL}"
                    )
                except Exception as rules_exc:
                    logger.error("Rules engine fallback also failed: %s", rules_exc)
                    analysis = _fallback_analysis(
                        f"Model '{settings.LLM_MODEL}' not installed. "
                        f"Pull it with: docker compose exec ollama ollama pull {settings.LLM_MODEL}"
                    )

            # ── Token limit ───────────────────────────────────────────────────
            elif any(kw in error_str for kw in ("token", "context length", "maximum context", "too long")):
                logger.warning("Token limit exceeded — returning fallback with truncation hint")
                analysis = _fallback_analysis(
                    f"Input exceeded LLM context window. Error: {str(e)[:200]}. "
                    "Consider reducing stack trace length or enabling a model with larger context."
                )
            else:
                analysis = _fallback_analysis(str(e))
            intermediate_steps = []

    # Store full audit trail to MongoDB
    await _store_audit_trail(test_case_id, user_question, analysis, intermediate_steps)

    # Cache successful analyses for future identical AND similar failures
    if analysis.get("confidence_score", 0) > 0 and error_message:
        await _store_analysis_cache(test_name, error_message or "", stack_trace or "", analysis)
        try:
            from app.services.semantic_cache import semantic_cache_store
            await semantic_cache_store(test_name, error_message or "", stack_trace or "", analysis)
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


def _record_tool_spans(parent_span: Any, intermediate_steps: list) -> None:
    """Record each ReAct tool invocation as an OTEL span event on the parent span."""
    for i, step in enumerate(intermediate_steps):
        try:
            action = step[0] if isinstance(step, (tuple, list)) else step
            observation = step[1] if isinstance(step, (tuple, list)) and len(step) > 1 else ""
            tool_name = getattr(action, "tool", "unknown")
            tool_input = str(getattr(action, "tool_input", ""))[:300]
            obs_preview = str(observation)[:300]

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
    # Try to extract JSON block from output
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return cast(dict[Any, Any], json.loads(raw))
        except json.JSONDecodeError:
            pass

    # Try to find JSON within the output
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return cast(dict[Any, Any], json.loads(match.group()))
        except json.JSONDecodeError:
            pass

    return _fallback_analysis("Could not parse structured output from agent")


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
    """Persist full agent reasoning trace to MongoDB for auditing."""
    db = get_mongo_db()
    await db[Collections.AI_ANALYSIS_PAYLOADS].update_one(
        {"test_case_id": test_case_id},
        {"$set": {
            "test_case_id": test_case_id,
            "prompt": prompt,
            "analysis": analysis,
            "intermediate_steps": [str(s) for s in steps],
            "llm_provider": settings.LLM_PROVIDER,
            "llm_model": settings.LLM_MODEL,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ── Redis-based AI analysis cache ───────────────────────────────────────────

async def _check_analysis_cache(
    test_name: str, error_message: str, stack_trace: str
) -> Optional[dict]:
    """Return cached analysis for identical failure, or None on miss."""
    if not error_message and not stack_trace:
        return None
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cache_key = compute_analysis_cache_key(test_name, error_message, stack_trace)
        raw = await redis.get(cache_key)
        if raw:
            return cast(dict[Any, Any], json.loads(raw))
    except Exception as exc:
        logger.debug("Analysis cache lookup failed (non-critical): %s", exc)
    return None


async def _store_analysis_cache(
    test_name: str, error_message: str, stack_trace: str, analysis: dict
) -> None:
    """Cache an analysis result in Redis with TTL."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cache_key = compute_analysis_cache_key(test_name, error_message, stack_trace)
        # Store a clean copy without transient fields
        cacheable = {k: v for k, v in analysis.items() if k not in ("cache_hit",)}
        await redis.set(cache_key, json.dumps(cacheable, default=str), ex=settings.AI_ANALYSIS_CACHE_TTL)
    except Exception as exc:
        logger.debug("Analysis cache store failed (non-critical): %s", exc)
