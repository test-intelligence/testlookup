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
import time
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.base import BaseAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AIAnalysis, TestCase, TestStatus
from app.services.agent import run_triage_agent
from app.services.artifact_store import store_artifact
from app.services.category_normalizer import normalize_category_in_analysis

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


class AnalysisAgent(BaseAgent):
    stage_name = "root_cause_analysis"
    UNKNOWN_CATEGORY = "UNKNOWN"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        project_id = state["project_id"]
        failed_ids: list[str] = state.get("failed_test_ids", [])

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            project_id,
            {
                "status": "running",
                "message": f"Running root-cause analysis on {len(failed_ids)} failed test(s)...",
            },
        )

        if not failed_ids:
            await self.mark_stage_done(pipeline_run_id, result_data={"analysed": 0})
            return {
                "analyses": {},
                "completed_stages": ["root_cause_analysis"],
                "errors": [],
                "current_stage": "summary",
            }

        # Fetch enriched test metadata (error_message, severity, flakiness history)
        test_meta = await self._fetch_test_metadata(failed_ids)

        # Sort by priority: blockers/critical first, then by severity
        prioritized_ids = self._prioritize_tests(failed_ids, test_meta)

        semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT_ANALYSES)
        tasks = [
            self._analyse_with_retry(semaphore, tc_id, test_meta.get(tc_id, {}), state)
            for tc_id in prioritized_ids
        ]

        try:
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as gather_exc:
            logger.error("asyncio_gather_failed", error=str(gather_exc))
            results_list = [gather_exc] * len(prioritized_ids)

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
            },
            analysis_mode=dominant_mode,
            fallback_reason=stage_fallback_reason,
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

        return {
            "analyses": analyses,
            "completed_stages": ["root_cause_analysis"],
            "errors": errors,
            "stage_errors": stage_errors,
            "stage_quality": stage_quality,
            "low_confidence_count": low_confidence,
            "current_stage": "summary",
        }

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

    async def _analyse_with_retry(
        self,
        semaphore: asyncio.Semaphore,
        tc_id: str,
        meta: dict,
        state: dict,
    ) -> dict:
        """Run analysis with retry for low-confidence results."""
        result = await self._analyse_one(semaphore, tc_id, meta, state)

        # P2-5: Smart retry — only retry on LLM failures (low confidence from
        # actual analysis), not when data is missing (no error_message, no stack_trace).
        # Missing data retries waste tokens since the LLM gets the same empty inputs.
        has_input_data = bool(meta.get("error_message") or meta.get("stack_trace"))
        if (
            result.get("confidence_score", 0) < _RETRY_CONFIDENCE_THRESHOLD
            and not result.get("timed_out")
            and not result.get("error")
            and has_input_data
            and _MAX_ANALYSIS_RETRIES > 0
        ):
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

            # Check analysis mode — dispatch to ML/Rules if LLM is disabled
            from app.services.analysis_router import AnalysisMode, get_analysis_mode
            mode = get_analysis_mode()

            # Record the routing decision so downstream consumers (UI, reports)
            # can see *which* engine ran and why, without scraping logs.
            await self.log_decision(
                pipeline_run_id,
                decision_point="route_analysis_mode",
                chosen=mode,
                rationale=(
                    "configured ANALYSIS_MODE resolved via get_analysis_mode()"
                    if mode != AnalysisMode.AUTO
                    else "auto resolution: ML→LLM→Rules availability chain"
                ),
                test_case_id=tc_id,
                context={"severity": meta.get("severity")},
            )

            if mode in (AnalysisMode.ML, AnalysisMode.RULES):
                # Non-LLM path: use analysis_router (no timeout needed, <5ms)
                from app.services.analysis_router import classify_test
                run_data = state.get("test_run_data") or {}
                analysis = await classify_test(
                    test_case={
                        "test_case_id": tc_id,
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
            analysis["_audit"] = {
                "analysis_duration_seconds": round(elapsed, 3),
                "test_severity": meta.get("severity"),
                "had_error_message": bool(meta.get("error_message")),
                "had_stack_trace": bool(meta.get("stack_trace")),
                "historical_failure_count": meta.get("historical_failure_count", 0),
                "analysis_mode": routing.get("mode_used") or mode,
                "mode_requested": routing.get("mode_requested") or mode,
                "fallback_from": routing.get("fallback_from"),
                "fallback_reason": routing.get("fallback_reason"),
                "confidence_adjustments": analysis.pop("_confidence_adjustments", []),
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
                        "stack_trace": None,  # fetched from MongoDB if available
                    }
                    if row.test_fingerprint:
                        fingerprints[str(row.id)] = row.test_fingerprint

                # Fetch historical failure counts for prioritization
                if fingerprints:
                    await self._enrich_historical_counts(db, meta, fingerprints)

                # Fetch stack traces from MongoDB (best-effort)
                await self._enrich_stack_traces(meta, tc_ids)

                return meta
        except Exception as db_exc:
            logger.error("fetch_test_metadata_failed", error=str(db_exc))
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
        return {
            "root_cause_summary": f"Analysis failed: {exc}. Manual review required.",
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
            "error": str(exc),
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
