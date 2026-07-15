"""
Root Cause Analysis Agent — Stage 3 of the offline pipeline.

For each failed test, runs the LangChain ReAct triage agent concurrently
(bounded by a semaphore to avoid LLM overload), then persists results to
the ai_analysis PostgreSQL table.

Large analysis payloads are stored in object storage (MinIO/S3) and only
URI references are passed through the pipeline state to keep memory low.

This agent wraps the existing run_triage_agent() from services/agent.py
so the single-test analysis path is unchanged.

Improvements over baseline:
  - Enhanced context gathering (error_message, severity, historical flakiness)
  - Evidence-based confidence score validation
  - Failure category sanitization against FailureCategory enum
  - Progressive 3-tier fallback on timeout/error
  - Retry logic for low-confidence analyses
  - Priority-based analysis ordering (critical/blocker failures first)
  - Structured audit logging with timing and decision rationale
"""
import asyncio
import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.base import BaseAgent
from app.agents.consistency import (
    check_analysis_consistency,
    log_consistency_failures,
)
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import AnalysisAgentOutput, validate_agent_contract
from app.models.postgres import AIAnalysis, TestCase, TestStatus
from app.services.agent import run_triage_agent
from app.services.artifact_store import store_artifact
from app.services.category_normalizer import normalize_category_in_analysis
from app.services.prompt_registry import prompt_versions_used as _prompt_versions_used

import structlog

logger = structlog.get_logger("agents.analysis")

# Priority weights for analysis ordering (higher = analyzed first)
_SEVERITY_PRIORITY = {
    "BLOCKER": 100,
    "CRITICAL": 80,
    "NORMAL": 50,
    "MINOR": 30,
    "TRIVIAL": 10,
}

# Minimum confidence to skip retry
_RETRY_CONFIDENCE_THRESHOLD = 40

# Maximum retries for low-confidence analyses
_MAX_ANALYSIS_RETRIES = 1

# Non-LLM engines and deterministic fallbacks are repeatable; retrying them
# just burns CPU/LLM fallback budget and returns the same low-confidence answer.
_DETERMINISTIC_ANALYSIS_ENGINES = {"rules", "ml", "blocked"}
_METADATA_CACHE_TTL_SECONDS = 30
_METADATA_CACHE_MAX_ENTRIES = 64
_METADATA_CACHE: dict[str, tuple[float, dict[str, dict]]] = {}
_ANALYSIS_LATENCY_EWMA_BY_PROVIDER: dict[str, float] = {}
_LOCAL_LLM_PROVIDERS = {"ollama", "lmstudio", "localai"}
_REMOTE_LLM_PROVIDERS = {"openai", "gemini", "anthropic"}
_HIGH_LATENCY_SECONDS = 20.0
_LOW_LATENCY_SECONDS = 4.0


def _hash_text(value: object) -> str | None:
    """Return a stable SHA-256 hash for audit fingerprints without raw text."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _metadata_cache_key(tc_ids: list[str]) -> str:
    return _hash_json({"test_case_ids": sorted(str(tc_id) for tc_id in tc_ids)})


def _metadata_cache_get(cache_key: str) -> dict[str, dict] | None:
    cached = _METADATA_CACHE.get(cache_key)
    if not cached:
        return None
    cached_at, payload = cached
    if time.monotonic() - cached_at > _METADATA_CACHE_TTL_SECONDS:
        _METADATA_CACHE.pop(cache_key, None)
        return None
    return copy.deepcopy(payload)


def _metadata_cache_set(cache_key: str, payload: dict[str, dict]) -> None:
    if len(_METADATA_CACHE) >= _METADATA_CACHE_MAX_ENTRIES:
        oldest_key = min(_METADATA_CACHE, key=lambda key: _METADATA_CACHE[key][0])
        _METADATA_CACHE.pop(oldest_key, None)
    _METADATA_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(payload))


class AnalysisAgent(BaseAgent):
    stage_name = "root_cause_analysis"
    UNKNOWN_CATEGORY = "UNKNOWN"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        project_id = state["project_id"]
        failed_ids: list[str] = state.get("failed_test_ids", [])

        await self.mark_stage_running(pipeline_run_id)

        # Tier 1 item 2 — LLM cost budget enforcement. Evaluate the quota
        # BEFORE the first classification so every test in this stage sees
        # the same mode. A downgrade here is recorded as a decision log
        # entry so the Run Intelligence "Decision Trail" drawer explains
        # why the AI ran in reduced mode without operators having to grep
        # Prometheus.
        from app.services.llm_cost_budget import check_and_apply_cap
        cap_decision = await check_and_apply_cap(project_id)
        if cap_decision.is_capped():
            await self.log_decision(
                pipeline_run_id,
                decision_point="llm_cost_budget_cap",
                chosen=cap_decision.action,
                rationale=cap_decision.rationale,
                context={
                    "utilization_pct": cap_decision.utilization_pct,
                    "mode_override": cap_decision.mode_override,
                    "block": cap_decision.block,
                },
            )
        # Stash on state so _analyse_one can see it without a second DB hit.
        state["_cost_budget_mode_override"] = cap_decision.mode_override
        state["_cost_budget_block"] = cap_decision.block

        await self.broadcast_progress(
            project_id,
            {
                "status": "running",
                "message": f"Running root-cause analysis on {len(failed_ids)} failed test(s)...",
            },
        )

        if not failed_ids:
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={"analysed": 0},
                project_id=project_id,
            )
            return validate_agent_contract(
                AnalysisAgentOutput,
                {
                    "analyses": {},
                    "completed_stages": ["root_cause_analysis"],
                    "errors": [],
                    "current_stage": "summary",
                },
                agent_name=self.stage_name,
                confidence=100,
                decision_reason="no_failed_tests",
            )

        # Hard block short-circuits the entire stage. Return an empty result
        # set with a stage_quality marker so the summary agent can explain
        # the gap instead of pretending all tests passed.
        if cap_decision.block:
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "analysed": 0,
                    "stage_quality": "cost_budget_blocked",
                    "blocked_reason": cap_decision.rationale,
                },
                analysis_mode="blocked",
                fallback_reason=cap_decision.rationale[:200],
                project_id=project_id,
            )
            return validate_agent_contract(
                AnalysisAgentOutput,
                {
                    "analyses": {},
                    "completed_stages": ["root_cause_analysis"],
                    "errors": [],
                    "stage_errors": {"root_cause_analysis": [cap_decision.rationale]},
                    "stage_quality": "cost_budget_blocked",
                    "current_stage": "summary",
                },
                agent_name=self.stage_name,
                fallback_used=True,
                confidence=0,
                decision_reason="cost_budget_blocked",
            )

        # Fetch enriched test metadata (error_message, severity, flakiness history)
        test_meta = await self._fetch_test_metadata(failed_ids)

        # Learning loop: fetch human corrections FRESH each run (not via the
        # cached metadata) so a just-submitted correction takes effect
        # immediately. Stashed on state; _analyse_one applies it by fingerprint,
        # bypassing the known-wrong AI path. Best-effort, never blocks analysis.
        state["_human_corrections"] = await self._fetch_human_corrections(project_id, test_meta)

        # Sort by priority: blockers/critical first, then by severity
        prioritized_ids = self._prioritize_tests(failed_ids, test_meta)

        concurrency_policy = await self._resolve_adaptive_concurrency(state, len(prioritized_ids))
        concurrency = concurrency_policy["concurrency"]
        semaphore = asyncio.Semaphore(concurrency)
        await self.log_decision(
            pipeline_run_id,
            decision_point="analysis_concurrency_policy",
            chosen=str(concurrency),
            rationale=concurrency_policy["rationale"],
            context=concurrency_policy,
        )
        results_list = []
        for start in range(0, len(prioritized_ids), concurrency):
            batch_ids = prioritized_ids[start:start + concurrency]
            batch_tasks = [
                self._analyse_with_retry(semaphore, tc_id, test_meta.get(tc_id, {}), state)
                for tc_id in batch_ids
            ]
            try:
                results_list.extend(await asyncio.gather(*batch_tasks, return_exceptions=True))
            except Exception as gather_exc:
                logger.error("asyncio_gather_failed", error=str(gather_exc))
                results_list.extend([gather_exc] * len(batch_ids))

        analyses: dict[str, dict] = {}
        errors: list[str] = []
        timed_out = 0
        retried = 0
        low_confidence = 0
        error_count = 0
        from app.services.privacy_service import sanitize_for_logging
        for tc_id, result in zip(prioritized_ids, results_list):
            if isinstance(result, BaseException):
                error_count += 1
                # Exception messages may contain stack traces, payload snippets,
                # URLs, or raw customer data. Strip PII before persisting the
                # error on state — it later flows through logs and any LLM-
                # assisted debugging view.
                safe_msg = sanitize_for_logging(str(result))
                errors.append(f"Analysis failed for {tc_id}: {safe_msg}")
                analyses[tc_id] = {"error": safe_msg, "confidence_score": 0}
            else:
                analyses[tc_id] = result
                if result.get("timed_out"):
                    timed_out += 1
                    error_count += 1
                if result.get("retry_count", 0) > 0:
                    retried += 1
                if result.get("confidence_score", 0) < settings.AI_CONFIDENCE_THRESHOLD:
                    low_confidence += 1

        # P2-4: Explicit error propagation — determine stage quality
        total_analysed = len(prioritized_ids)
        error_ratio = error_count / max(total_analysed, 1)
        stage_quality = "degraded" if error_ratio > 0.3 else "normal"
        stage_errors: dict[str, list[str]] = {}
        if errors:
            stage_errors["root_cause_analysis"] = errors

        # Batch persist all analyses in chunked upserts (single session)
        await self._batch_upsert_analyses(analyses)

        # Summarise routing across all analyses — the dominant mode is what
        # ran for the majority; fallbacks are tallied separately so degradation
        # is visible on the stage row without joining the event log.
        mode_counts: dict[str, int] = {}
        fallback_count = 0
        for a in analyses.values():
            audit = a.get("_audit") or {}
            m = audit.get("analysis_mode") or "unknown"
            mode_counts[m] = mode_counts.get(m, 0) + 1
            if audit.get("fallback_from"):
                fallback_count += 1
        dominant_mode = max(mode_counts, key=mode_counts.get) if mode_counts else None
        stage_fallback_reason = (
            f"{fallback_count}/{total_analysed} tests fell back from requested engine"
            if fallback_count else None
        )
        self._record_latency_feedback(analyses)

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "analysed": len(analyses),
                "errors": len(errors),
                "timed_out": timed_out,
                "retried": retried,
                "low_confidence": low_confidence,
                "stage_quality": stage_quality,
                "error_ratio": round(error_ratio, 3),
                "mode_distribution": mode_counts,
                "fallback_count": fallback_count,
                "adaptive_concurrency": concurrency_policy,
            },
            analysis_mode=dominant_mode,
            fallback_reason=stage_fallback_reason,
            project_id=project_id,
        )

        quality_msg = ""
        if stage_quality == "degraded":
            quality_msg = f" (quality: DEGRADED — {error_count} of {total_analysed} tests had insufficient data)"
        await self.broadcast_progress(
            project_id,
            {
                "status": "completed",
                "message": f"Root-cause analysis complete: {len(analyses)} test(s) analysed"
                + (f", {timed_out} timed out" if timed_out else "")
                + (f", {retried} retried" if retried else "")
                + quality_msg,
            },
        )

        confidence_values = [
            int(result.get("confidence_score") or 0)
            for result in analyses.values()
            if isinstance(result.get("confidence_score"), (int, float))
        ]
        evidence_refs = [
            {"type": "analysis", "id": str(test_id)}
            for test_id in sorted(analyses.keys())
        ]
        consistency_report = check_analysis_consistency(analyses, failed_ids)
        log_consistency_failures(consistency_report, pipeline_run_id=pipeline_run_id)
        evidence_refs.append(consistency_report.evidence_ref())

        return validate_agent_contract(
            AnalysisAgentOutput,
            {
                "analyses": analyses,
                "completed_stages": ["root_cause_analysis"],
                "errors": errors,
                "stage_errors": stage_errors,
                "stage_quality": stage_quality,
                "low_confidence_count": low_confidence,
                "current_stage": "summary",
            },
            agent_name=self.stage_name,
            fallback_used=bool(fallback_count or errors),
            confidence=(
                int(sum(confidence_values) / len(confidence_values))
                if confidence_values else 0
            ),
            evidence_refs=evidence_refs,
            decision_reason=(
                "root_cause_analysis_completed"
                if stage_quality == "normal" else f"root_cause_analysis_{stage_quality}"
            ) + consistency_report.decision_suffix(),
        )

    def _prioritize_tests(
        self, failed_ids: list[str], test_meta: dict[str, dict]
    ) -> list[str]:
        """Sort failed test IDs by priority: severity, then historical failure count."""

        def _priority_key(tc_id: str) -> tuple[int, int]:
            meta = test_meta.get(tc_id, {})
            severity = (meta.get("severity") or "NORMAL").upper()
            severity_score = _SEVERITY_PRIORITY.get(severity, 50)
            # Higher historical failure count = higher priority
            hist_failures = meta.get("historical_failure_count", 0)
            return (-severity_score, -hist_failures)

        return sorted(failed_ids, key=_priority_key)

    async def _resolve_adaptive_concurrency(self, state: dict, total_tests: int) -> dict:
        """Choose per-run analysis fan-out from engine, provider, latency, and circuit state."""
        base = max(1, int(settings.LLM_MAX_CONCURRENT_ANALYSES or 1))
        provider = str(settings.LLM_PROVIDER or "unknown").lower()
        mode = str(state.get("analysis_mode_resolved") or "auto").lower()
        if state.get("_cost_budget_mode_override") in ("ml", "rules"):
            mode = str(state["_cost_budget_mode_override"])

        circuit_status: dict = {}
        if mode not in _DETERMINISTIC_ANALYSIS_ENGINES:
            try:
                from app.streams.circuit_breaker import LLMCircuitBreaker
                circuit_status = await LLMCircuitBreaker.get_status()
            except Exception as exc:
                circuit_status = {"state": "unknown", "error": str(exc)[:200]}

        concurrency = base
        reasons = [f"base={base}"]
        if mode in {"ml", "rules"}:
            concurrency = min(max(base, base * 4), 16)
            reasons.append(f"deterministic_mode={mode}")
        elif provider in _REMOTE_LLM_PROVIDERS:
            concurrency = min(max(base, base * 2), 8)
            reasons.append(f"remote_provider={provider}")
        elif provider in _LOCAL_LLM_PROVIDERS:
            concurrency = min(base, 3)
            reasons.append(f"local_provider={provider}")

        circuit_state = str(circuit_status.get("state") or "").upper()
        if circuit_state in {"OPEN", "HALF_OPEN"}:
            concurrency = 1
            reasons.append(f"circuit={circuit_state}")

        latency_ewma = _ANALYSIS_LATENCY_EWMA_BY_PROVIDER.get(provider)
        if latency_ewma is not None:
            if latency_ewma >= _HIGH_LATENCY_SECONDS:
                concurrency = max(1, concurrency // 2)
                reasons.append(f"high_latency_ewma={latency_ewma:.3f}s")
            elif latency_ewma <= _LOW_LATENCY_SECONDS and provider in _REMOTE_LLM_PROVIDERS:
                concurrency = min(concurrency + 1, 8)
                reasons.append(f"low_latency_ewma={latency_ewma:.3f}s")

        concurrency = max(1, min(int(concurrency), max(total_tests, 1)))
        return {
            "concurrency": concurrency,
            "base_concurrency": base,
            "analysis_mode": mode,
            "provider": provider,
            "latency_ewma_seconds": latency_ewma,
            "circuit_state": circuit_status.get("state"),
            "circuit_failures": circuit_status.get("failure_count_in_window"),
            "rationale": "; ".join(reasons),
        }

    def _record_latency_feedback(self, analyses: dict[str, dict]) -> None:
        """Update provider latency EWMA from completed per-test audit metadata."""
        durations = [
            float((analysis.get("_audit") or {}).get("analysis_duration_seconds"))
            for analysis in analyses.values()
            if isinstance((analysis.get("_audit") or {}).get("analysis_duration_seconds"), (int, float))
        ]
        if not durations:
            return
        provider = str(settings.LLM_PROVIDER or "unknown").lower()
        observed = sum(durations) / len(durations)
        previous = _ANALYSIS_LATENCY_EWMA_BY_PROVIDER.get(provider)
        _ANALYSIS_LATENCY_EWMA_BY_PROVIDER[provider] = (
            observed if previous is None else (previous * 0.7) + (observed * 0.3)
        )

    async def _analyse_with_retry(
        self,
        semaphore: asyncio.Semaphore,
        tc_id: str,
        meta: dict,
        state: dict,
    ) -> dict:
        """Run analysis with retry for low-confidence results."""
        result = await self._analyse_one(semaphore, tc_id, meta, state)

        if self._should_retry_analysis(result, meta):
            logger.info(
                "low_confidence_retry",
                confidence_score=result.get("confidence_score", 0),
                test_case_id=tc_id,
            )
            retry_result = await self._analyse_one(semaphore, tc_id, meta, state)
            # Keep the result with higher confidence
            if retry_result.get("confidence_score", 0) > result.get("confidence_score", 0):
                retry_result["retry_count"] = 1
                retry_result["previous_confidence"] = result.get("confidence_score", 0)
                return retry_result
            result["retry_count"] = 1

        return result

    def _should_retry_analysis(self, result: dict, meta: dict) -> bool:
        """Return True only when another LLM attempt can plausibly improve output."""
        if _MAX_ANALYSIS_RETRIES <= 0:
            return False
        if result.get("confidence_score", 0) >= _RETRY_CONFIDENCE_THRESHOLD:
            return False
        if result.get("timed_out") or result.get("error"):
            return False
        if not (meta.get("error_message") or meta.get("stack_trace")):
            return False

        audit = result.get("_audit") or {}
        analysis_mode = str(audit.get("analysis_mode") or result.get("analysis_engine") or "").lower()
        if analysis_mode in _DETERMINISTIC_ANALYSIS_ENGINES:
            return False
        if result.get("classified_by") in {"rules_engine", "pattern_heuristic"}:
            return False
        if result.get("cache_hit") or result.get("semantic_similarity") is not None:
            return False
        if result.get("fallback_tier"):
            return False

        # Retry LLM parse/schema failures and under-evidenced LLM answers. These
        # are the cases where a second provider call can produce better evidence.
        if result.get("schema_validated") is False or result.get("schema_validation_error"):
            return True
        # ``auto`` is the DEFAULT mode — when it reaches here every
        # deterministic / cached / fallback path has already returned
        # False above, so an ``auto`` result this far down was produced
        # by the LLM and is just as retryable as an explicit ``llm`` one.
        # Excluding it silently disabled low-confidence retry for the
        # default configuration. (Empty string = mode not recorded ⇒
        # treat as LLM, the historical default.)
        return analysis_mode in {"llm", "auto", ""}

    async def _analyse_one(
        self,
        semaphore: asyncio.Semaphore,
        tc_id: str,
        meta: dict,
        state: dict,
    ) -> dict:
        start_time = time.perf_counter()
        pipeline_run_id = state.get("pipeline_run_id", "")
        async with semaphore:
            logger.info(
                "Analysing test case",
                test_case_id=tc_id,
                test_name=meta.get("test_name", ""),
            )

            # Check analysis mode — dispatch to ML/Rules if LLM is disabled.
            # If the LLM cost budget (checked once at the top of run()) forced
            # a downgrade, it wins over the configured mode — every test in
            # this stage runs under the downgraded engine.
            from app.services.analysis_router import AnalysisMode
            budget_override = state.get("_cost_budget_mode_override")
            if budget_override in ("ml", "rules"):
                mode = budget_override
            else:
                mode = state.get("analysis_mode_resolved") or "auto"

            # Record the routing decision so downstream consumers (UI, reports)
            # can see *which* engine ran and why, without scraping logs.
            await self.log_decision(
                pipeline_run_id,
                decision_point="route_analysis_mode",
                chosen=mode,
                rationale=(
                    "LLM cost budget override forced this engine"
                    if budget_override in ("ml", "rules")
                    else "pipeline-start analysis mode snapshot"
                ),
                test_case_id=tc_id,
                context={
                    "severity": meta.get("severity"),
                    "mode_requested": state.get("analysis_mode_requested"),
                    "mode_resolved": state.get("analysis_mode_resolved"),
                    "mode_resolution": state.get("analysis_mode_resolution"),
                },
            )

            # Learning-loop short-circuit: if a human previously corrected this
            # logical test's classification, apply it instead of re-running the
            # (known-wrong) AI path. Flows through the same post-processing +
            # audit below so provenance is recorded like any other verdict.
            analysis = None
            correction = (state.get("_human_corrections") or {}).get(
                meta.get("test_fingerprint")
            )
            if correction:
                from app.services.analysis_corrections import build_corrected_analysis
                analysis = build_corrected_analysis(correction)
                await self.log_decision(
                    pipeline_run_id,
                    decision_point="apply_human_correction",
                    chosen=correction["corrected_category"],
                    rationale=(
                        "A prior human review corrected this test's classification; "
                        "applying it instead of re-running analysis."
                    ),
                    test_case_id=tc_id,
                    context={"feedback_id": correction.get("feedback_id")},
                )

            if analysis is not None:
                pass  # human correction applied — skip the classifier dispatch
            elif mode in (AnalysisMode.ML, AnalysisMode.RULES):
                # Non-LLM path: use analysis_router (no timeout needed, <5ms)
                from app.services.analysis_router import classify_test
                run_data = state.get("test_run_data") or {}
                analysis = await classify_test(
                    test_case={
                        "test_case_id": tc_id,
                        "pipeline_run_id": pipeline_run_id,
                        "test_name": meta.get("test_name", tc_id),
                        "suite_name": meta.get("suite_name"),
                        "error_message": meta.get("error_message"),
                        "stack_trace": meta.get("stack_trace"),
                        "duration_ms": meta.get("duration_ms"),
                        "severity": meta.get("severity"),
                    },
                    history=meta.get("flakiness_data"),
                    run_context={
                        "pass_rate": run_data.get("pass_rate", 0),
                        "failed_tests": run_data.get("failed_tests", 0),
                    },
                    mode=mode,
                )
            else:
                # LLM path: existing ReAct agent with timeout + fallback
                try:
                    analysis = await asyncio.wait_for(
                        run_triage_agent(
                            test_case_id=tc_id,
                            test_name=meta.get("test_name", tc_id),
                            service_name=meta.get("suite_name"),
                            timestamp=None,
                            ocp_pod_name=state.get("test_run_data", {}).get("ocp_pod_name"),
                            ocp_namespace=state.get("test_run_data", {}).get("ocp_namespace"),
                            error_message=meta.get("error_message"),
                            stack_trace=meta.get("stack_trace"),
                            pipeline_run_id=pipeline_run_id,
                            # Scope the analysis caches to this tenant.
                            project_id=str(state.get("project_id")) if state.get("project_id") else None,
                            # Feeds the recall_similar_failures tool's
                            # server-side, project-scoped context (AI-F3).
                            test_fingerprint=meta.get("test_fingerprint"),
                        ),
                        timeout=settings.AI_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "react_agent_timeout",
                        timeout_seconds=settings.AI_TIMEOUT_SECONDS,
                        test_case_id=tc_id,
                        fallback="progressive",
                    )
                    analysis = await self._build_progressive_fallback(tc_id, meta)
                except Exception as exc:
                    logger.error(
                        "react_agent_failed",
                        test_case_id=tc_id,
                        error=str(exc),
                    )
                    analysis = self._build_error_analysis(exc)

            # Attach flakiness data from historical enrichment for P2-6 validation
            if "flakiness_data" in meta:
                analysis["flakiness_data"] = meta["flakiness_data"]

            # Post-process: validate confidence and sanitize category
            analysis = self._validate_confidence(analysis)
            analysis = self._sanitize_category(analysis)

            # Record audit metadata. Includes the routing decision returned
            # by the router so callers can see which engine ran (and, on
            # fallback, which engine was requested and why it was swapped).
            elapsed = time.perf_counter() - start_time
            routing = analysis.get("_routing") or {}
            fingerprint_context = {
                "test_case_id": tc_id,
                "test_name": meta.get("test_name", tc_id),
                "suite_name": meta.get("suite_name"),
                "error_message_hash": _hash_text(meta.get("error_message")),
                "stack_trace_hash": _hash_text(meta.get("stack_trace")),
                "severity": meta.get("severity"),
                "mode_requested": state.get("analysis_mode_requested"),
                "mode_resolved": state.get("analysis_mode_resolved") or mode,
            }
            analysis["_audit"] = {
                "analysis_duration_seconds": round(elapsed, 3),
                "test_severity": meta.get("severity"),
                "had_error_message": bool(meta.get("error_message")),
                "had_stack_trace": bool(meta.get("stack_trace")),
                "historical_failure_count": meta.get("historical_failure_count", 0),
                "analysis_mode": routing.get("mode_used") or mode,
                "mode_requested": state.get("analysis_mode_requested") or routing.get("mode_requested") or mode,
                "mode_resolved_at_pipeline_start": state.get("analysis_mode_resolved"),
                "mode_resolution": state.get("analysis_mode_resolution"),
                "fallback_from": routing.get("fallback_from"),
                "fallback_reason": routing.get("fallback_reason"),
                "input_fingerprints": {
                    "test_name_sha256": _hash_text(meta.get("test_name", tc_id)),
                    "suite_name_sha256": _hash_text(meta.get("suite_name")),
                    "error_message_sha256": _hash_text(meta.get("error_message")),
                    "stack_trace_sha256": _hash_text(meta.get("stack_trace")),
                    "classification_context_sha256": _hash_json(fingerprint_context),
                },
                # Registry-derived version tags (AI-F2): v<version>:<hash12>
                # of the exact prompt bytes in use, plus the code-level agent
                # version label.
                "prompt_versions": {
                    **_prompt_versions_used(
                        "react_triage", "fast_classifier_system",
                    ),
                    "analysis_agent": "agents.analysis_agent:v2",
                },
                "model_config_snapshot": {
                    "provider": settings.LLM_PROVIDER,
                    "model": settings.LLM_MODEL,
                    "temperature": settings.LLM_TEMPERATURE,
                    "max_tokens": settings.LLM_MAX_TOKENS,
                },
                "confidence_adjustments": analysis.pop("_confidence_adjustments", []),
                # AI-F4: basis of the confidence number — "heuristic_estimate"
                # (rules-engine band, no empirical calibration) vs "empirical".
                # None for LLM/ML paths that don't set it. Persisted via
                # AIAnalysis.routing_metadata (JSONB) — no schema migration.
                "confidence_basis": analysis.get("confidence_basis"),
                "confidence_rule_id": analysis.get("confidence_rule_id"),
            }

            # If the router recorded a fallback, surface it as a decision entry
            # so the stage-level decision_log reflects per-test anomalies.
            if routing.get("fallback_from") and pipeline_run_id:
                await self.log_decision(
                    pipeline_run_id,
                    decision_point="analysis_engine_fallback",
                    chosen=routing.get("mode_used") or "rules",
                    rationale=routing.get("fallback_reason") or "unspecified",
                    alternatives=[routing.get("fallback_from")],
                    test_case_id=tc_id,
                )

            # Store full analysis payload to object storage for large payloads
            pipeline_run_id = state.get("pipeline_run_id", "")
            if pipeline_run_id and len(str(analysis)) > 2000:
                artifact_uri = await store_artifact(
                    pipeline_run_id, "root_cause_analysis", tc_id, analysis,
                )
                if artifact_uri:
                    analysis["_artifact_uri"] = artifact_uri

            return analysis

    async def _fetch_test_metadata(self, tc_ids: list[str]) -> dict[str, dict]:
        """Fetch enriched test metadata including error details and flakiness history."""
        cache_key = _metadata_cache_key(tc_ids)
        cached = _metadata_cache_get(cache_key)
        if cached is not None:
            logger.debug("analysis_metadata_cache_hit", test_count=len(tc_ids))
            return cached

        try:
            async with AsyncSessionLocal() as db:
                # Fetch core test case data + error info
                result = await db.execute(
                    select(
                        TestCase.id,
                        TestCase.test_name,
                        TestCase.suite_name,
                        TestCase.class_name,
                        TestCase.error_message,
                        TestCase.severity,
                        TestCase.test_fingerprint,
                    ).where(TestCase.id.in_(tc_ids))
                )
                rows = result.all()

                meta: dict[str, dict] = {}
                fingerprints: dict[str, str] = {}  # tc_id -> fingerprint
                for row in rows:
                    meta[str(row.id)] = {
                        "test_name": row.test_name,
                        "suite_name": row.suite_name,
                        "class_name": row.class_name,
                        "error_message": row.error_message,
                        "severity": row.severity,
                        "test_fingerprint": row.test_fingerprint,
                        "stack_trace": None,  # fetched from MongoDB if available
                    }
                    if row.test_fingerprint:
                        fingerprints[str(row.id)] = row.test_fingerprint

                # Fetch historical failure counts for prioritization
                if fingerprints:
                    await self._enrich_historical_counts(db, meta, fingerprints)

                # Fetch stack traces from MongoDB (best-effort)
                await self._enrich_stack_traces(meta, tc_ids)

                _metadata_cache_set(cache_key, meta)
                return meta
        except Exception as db_exc:
            logger.error("fetch_test_metadata_failed", error=str(db_exc))
            return {}

    async def _fetch_human_corrections(
        self, project_id, test_meta: dict[str, dict]
    ) -> dict[str, dict]:
        """Return ``{test_fingerprint: correction}`` for the failing tests that a
        human previously reclassified. Best-effort: on any error (or no
        project), returns an empty map so analysis proceeds normally."""
        try:
            fingerprints = [
                m.get("test_fingerprint")
                for m in test_meta.values()
                if m.get("test_fingerprint")
            ]
            if not project_id or not fingerprints:
                return {}
            from app.services.analysis_corrections import get_corrections_for_fingerprints
            async with AsyncSessionLocal() as db:
                return await get_corrections_for_fingerprints(db, project_id, fingerprints)
        except Exception as exc:  # pragma: no cover — best-effort enrichment
            logger.debug("human_corrections_fetch_failed", error=str(exc))
            return {}

    async def _enrich_historical_counts(
        self, db, meta: dict[str, dict], fingerprints: dict[str, str]
    ) -> None:
        """Add historical failure counts and pass/fail breakdown for flakiness detection."""
        try:
            fp_values = list(set(fingerprints.values()))
            # Fetch both failure and total counts per fingerprint for flakiness detection (P2-6)
            result = await db.execute(
                select(
                    TestCase.test_fingerprint,
                    TestCase.status,
                    func.count(TestCase.id).label("count"),
                )
                .where(TestCase.test_fingerprint.in_(fp_values))
                .group_by(TestCase.test_fingerprint, TestCase.status)
            )
            # Build pass/fail breakdown per fingerprint
            fp_stats: dict[str, dict[str, int]] = {}
            for row in result.all():
                fp_stats.setdefault(row.test_fingerprint, {"pass_count": 0, "fail_count": 0})
                if row.status in (TestStatus.FAILED.value, TestStatus.BROKEN.value):
                    fp_stats[row.test_fingerprint]["fail_count"] += row.count
                elif row.status == TestStatus.PASSED.value:
                    fp_stats[row.test_fingerprint]["pass_count"] += row.count

            for tc_id, fp in fingerprints.items():
                if tc_id in meta:
                    stats = fp_stats.get(fp, {"pass_count": 0, "fail_count": 0})
                    meta[tc_id]["historical_failure_count"] = stats["fail_count"]
                    meta[tc_id]["flakiness_data"] = stats
        except Exception as exc:
            logger.debug("historical_count_enrichment_failed", error=str(exc))

    async def _enrich_stack_traces(
        self, meta: dict[str, dict], tc_ids: list[str]
    ) -> None:
        """Fetch stack traces from MongoDB for test cases that have them."""
        try:
            from app.db.mongo import Collections, get_mongo_db
            mongo_db = get_mongo_db()
            cursor = mongo_db[Collections.AI_ANALYSIS_PAYLOADS].find(
                {"test_case_id": {"$in": tc_ids}},
                {"test_case_id": 1, "analysis.stack_trace": 1},
            )
            async for doc in cursor:
                tc_id = doc.get("test_case_id")
                if tc_id and tc_id in meta:
                    trace = (doc.get("analysis") or {}).get("stack_trace")
                    if trace:
                        meta[tc_id]["stack_trace"] = trace
        except Exception as exc:
            logger.debug("stack_trace_enrichment_failed", error=str(exc))

    def _validate_confidence(self, analysis: dict) -> dict:
        """
        Validate and adjust confidence score based on evidence quality.

        Rules:
        - No evidence references → cap at 50
        - No root_cause_summary or very short → cap at 30
        - Has error + evidence + detailed summary → trust the score
        - Confidence clamped to 0-100
        """
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

        # Penalty: no evidence references at all
        if not evidence and confidence > 50:
            adjustments.append({
                "rule": "no_evidence_references",
                "from": confidence,
                "to": 50,
                "reason": "LLM returned no evidence_references",
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
                "reason": "LLM reached conclusion without invoking any tools",
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
            self._stringify_value(analysis.get("failure_category")).upper()
            == self.UNKNOWN_CATEGORY
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
        analysis["requires_human_review"] = confidence < settings.AI_CONFIDENCE_THRESHOLD
        analysis["confidence_validated"] = True
        return analysis

    @staticmethod
    def _stringify_value(value) -> str:
        """Coerce an enum-like or scalar category value to a plain string."""
        if value is None:
            return ""
        if hasattr(value, "value"):
            return str(value.value)
        return str(value)

    def _sanitize_category(self, analysis: dict) -> dict:
        """Validate failure_category against the FailureCategory enum.

        Delegates to the unified category_normalizer service which uses
        a deterministic alias map (no fuzzy matching).
        """
        return normalize_category_in_analysis(analysis)

    async def _build_progressive_fallback(
        self, tc_id: str, meta: dict
    ) -> dict:
        """
        3-tier progressive fallback when the full ReAct agent fails:
          Tier 1: Try FastClassifier (single LLM call, ~200ms)
          Tier 2: Pattern-based heuristic from error message
          Tier 3: Generic unknown fallback
        """
        error_msg = meta.get("error_message") or ""
        test_name = meta.get("test_name") or tc_id

        # Tier 1: Fast classifier
        try:
            from app.services.training.classifier import FastClassifier
            quick = await asyncio.wait_for(
                FastClassifier.classify(
                    test_name=test_name,
                    error_message=error_msg,
                    stack_trace=meta.get("stack_trace") or "",
                ),
                timeout=min(30, settings.AI_TIMEOUT_SECONDS // 2),
            )
            if quick is not None:
                quick["fallback_tier"] = 1
                quick["fallback_reason"] = "timeout_fast_classifier"
                logger.info("progressive_fallback_tier1_ok", test_case_id=tc_id)
                return quick
        except Exception as exc:
            logger.debug(
                "progressive_fallback_tier1_failed",
                test_case_id=tc_id,
                error=str(exc),
            )

        # Tier 2: Pattern-based heuristic
        heuristic = self._pattern_based_analysis(error_msg, test_name)
        if heuristic["failure_category"] != self.UNKNOWN_CATEGORY:
            heuristic["fallback_tier"] = 2
            heuristic["fallback_reason"] = "timeout_pattern_match"
            logger.info("progressive_fallback_tier2_ok", test_case_id=tc_id)
            return heuristic

        # Tier 3: Generic fallback
        fallback = self._build_timeout_analysis()
        fallback["fallback_tier"] = 3
        fallback["fallback_reason"] = "timeout_generic"
        return fallback

    def _pattern_based_analysis(self, error_message: str, test_name: str) -> dict:
        """
        Heuristic analysis based on error message patterns.
        Returns a best-effort categorization without LLM.
        """
        error_lower = (error_message or "").lower()

        patterns = [
            # (keywords_list, category, summary_template, confidence)
            (
                ["oomkilled", "out of memory", "memory limit", "oom"],
                "INFRASTRUCTURE",
                "Pod/container ran out of memory (OOMKilled). Check resource limits.",
                65,
            ),
            (
                ["connection refused", "connection reset", "econnrefused", "econnreset"],
                "INFRASTRUCTURE",
                "Network connectivity issue: connection refused/reset. Check service health.",
                60,
            ),
            (
                ["timeout", "timed out", "deadline exceeded", "socket timeout"],
                "INFRASTRUCTURE",
                "Request or operation timed out. Check service latency and timeouts.",
                55,
            ),
            (
                ["502 bad gateway", "503 service unavailable", "504 gateway timeout"],
                "INFRASTRUCTURE",
                "HTTP server error (5xx). Check upstream service health.",
                60,
            ),
            (
                ["404 not found", "resource not found", "no such file"],
                "TEST_DATA",
                "Resource not found (404). Check test data setup and environment state.",
                55,
            ),
            (
                ["setup failed", "before method", "beforeeach", "beforeall", "fixture"],
                "TEST_DATA",
                "Test setup/fixture failed. Check test data prerequisites.",
                50,
            ),
            (
                ["nullpointerexception", "undefined is not", "cannot read propert", "typeerror"],
                "AUTOMATION_DEFECT",
                "Null/undefined reference in test code. Review test implementation.",
                55,
            ),
            (
                ["element not found", "no such element", "locator", "selector"],
                "AUTOMATION_DEFECT",
                "UI element locator failed. Check for UI changes or timing issues.",
                55,
            ),
            (
                ["flaky", "intermittent", "race condition", "eventually"],
                "FLAKY",
                "Failure matches flaky test patterns. Verify with re-run.",
                45,
            ),
            (
                ["assertion", "expected", "but was", "assertequals", "assertthat"],
                "PRODUCT_BUG",
                "Assertion failure detected. Likely a product bug or expected behavior change.",
                45,
            ),
        ]

        for keywords, category, summary, confidence in patterns:
            if any(kw in error_lower for kw in keywords):
                return {
                    "root_cause_summary": summary,
                    "failure_category": category,
                    "backend_error_found": category == "INFRASTRUCTURE",
                    "pod_issue_found": "oom" in error_lower or "pod" in error_lower,
                    "is_flaky": category == "FLAKY",
                    "confidence_score": confidence,
                    "recommended_actions": [
                        "Review the error message and stack trace",
                        "Check recent code changes",
                        "Re-run test to verify reproducibility",
                    ],
                    "evidence_references": [],
                    "requires_human_review": True,
                    "classified_by": "pattern_heuristic",
                    "tools_used": [],
                    "llm_provider": "none",
                    "llm_model": "pattern_heuristic",
                }

        # No pattern matched — return UNKNOWN
        return {
            "root_cause_summary": "Pattern analysis inconclusive. Manual review required.",
            "failure_category": self.UNKNOWN_CATEGORY,
            "backend_error_found": False,
            "pod_issue_found": False,
            "is_flaky": False,
            "confidence_score": 0,
            "recommended_actions": ["Review stack trace manually"],
            "evidence_references": [],
            "requires_human_review": True,
            "classified_by": "pattern_heuristic",
            "tools_used": [],
        }

    def _build_timeout_analysis(self) -> dict:
        timeout = settings.AI_TIMEOUT_SECONDS
        return {
            "root_cause_summary": f"Analysis timed out after {timeout}s. Manual review required.",
            "failure_category": self.UNKNOWN_CATEGORY,
            "backend_error_found": False,
            "pod_issue_found": False,
            "is_flaky": False,
            "confidence_score": 0,
            "recommended_actions": [
                "Re-run analysis with a faster LLM or higher timeout",
                "Review stack trace manually",
            ],
            "evidence_references": [],
            "requires_human_review": True,
            "timed_out": True,
            "tools_used": [],
        }

    def _build_error_analysis(self, exc: Exception) -> dict:
        # This dict is persisted to AIAnalysis (root_cause_summary/error), so the
        # raw exception — which can carry DSNs, internal hostnames, or PII from the
        # failure text — must be sanitized before it crosses the persistence boundary.
        from app.services.privacy_service import sanitize_for_persistence

        safe_error = sanitize_for_persistence(str(exc))
        return {
            "root_cause_summary": "Analysis failed — manual review required.",
            "failure_category": self.UNKNOWN_CATEGORY,
            "backend_error_found": False,
            "pod_issue_found": False,
            "is_flaky": False,
            "confidence_score": 0,
            "recommended_actions": [
                "Review stack trace manually",
                "Check LLM service connectivity",
            ],
            "evidence_references": [],
            "requires_human_review": True,
            "error": safe_error,
            "tools_used": [],
        }

    async def _batch_upsert_analyses(
        self, analyses: dict[str, dict], chunk_size: int = 50
    ) -> None:
        """Batch upsert all analysis results in chunked commits (single session per chunk)."""
        items = list(analyses.items())
        if not items:
            return

        now = datetime.now(timezone.utc)
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            try:
                async with AsyncSessionLocal() as db:
                    for tc_id, analysis in chunk:
                        if analysis.get("error") and not analysis.get("root_cause_summary"):
                            continue  # Skip error-only entries with no useful data
                        # Persist the per-test decision audit so the trail UI
                        # can show "why did the AI route this test to engine X"
                        # without hitting the Mongo event log.
                        routing_metadata = analysis.get("_audit") or None
                        stmt = pg_insert(AIAnalysis).values(
                            test_case_id=tc_id,
                            root_cause_summary=analysis.get("root_cause_summary"),
                            failure_category=analysis.get("failure_category", "UNKNOWN"),
                            backend_error_found=analysis.get("backend_error_found", False),
                            pod_issue_found=analysis.get("pod_issue_found", False),
                            is_flaky=analysis.get("is_flaky", False),
                            confidence_score=analysis.get("confidence_score", 0),
                            recommended_actions=analysis.get("recommended_actions", []),
                            evidence_references=analysis.get("evidence_references", []),
                            tools_used=analysis.get("tools_used"),
                            role_actions=analysis.get("role_actions"),
                            llm_provider=analysis.get("llm_provider"),
                            llm_model=analysis.get("llm_model"),
                            requires_human_review=analysis.get("requires_human_review", True),
                            routing_metadata=routing_metadata,
                            created_at=now,
                        ).on_conflict_do_update(
                            index_elements=["test_case_id"],
                            set_={
                                "root_cause_summary": analysis.get("root_cause_summary"),
                                "failure_category": analysis.get("failure_category", self.UNKNOWN_CATEGORY),
                                "backend_error_found": analysis.get("backend_error_found", False),
                                "pod_issue_found": analysis.get("pod_issue_found", False),
                                "is_flaky": analysis.get("is_flaky", False),
                                "confidence_score": analysis.get("confidence_score", 0),
                                "recommended_actions": analysis.get("recommended_actions", []),
                                "evidence_references": analysis.get("evidence_references", []),
                                "tools_used": analysis.get("tools_used"),
                                "role_actions": analysis.get("role_actions"),
                                "requires_human_review": analysis.get("requires_human_review", True),
                                "routing_metadata": routing_metadata,
                            },
                        )
                        await db.execute(stmt)
                    await db.commit()
            except Exception as exc:
                logger.error(
                    "batch_upsert_failed",
                    chunk_start=i,
                    chunk_end=i + len(chunk),
                    error=str(exc),
                )
