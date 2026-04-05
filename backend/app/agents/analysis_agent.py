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
from difflib import get_close_matches
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.base import BaseAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AIAnalysis, FailureCategory, TestCase, TestStatus
from app.services.agent import run_triage_agent
from app.services.artifact_store import store_artifact

import structlog

logger = structlog.get_logger("agents.analysis")

# Valid failure categories from the enum
VALID_CATEGORIES = {cat.value for cat in FailureCategory}

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
            logger.error("asyncio.gather failed unexpectedly: %s", gather_exc)
            results_list = [gather_exc] * len(prioritized_ids)

        analyses: dict[str, dict] = {}
        errors: list[str] = []
        timed_out = 0
        retried = 0
        low_confidence = 0
        for tc_id, result in zip(prioritized_ids, results_list):
            if isinstance(result, BaseException):
                errors.append(f"Analysis failed for {tc_id}: {result}")
                analyses[tc_id] = {"error": str(result), "confidence_score": 0}
            else:
                analyses[tc_id] = result
                if result.get("timed_out"):
                    timed_out += 1
                if result.get("retry_count", 0) > 0:
                    retried += 1
                if result.get("confidence_score", 0) < settings.AI_CONFIDENCE_THRESHOLD:
                    low_confidence += 1

        # Batch persist all analyses in chunked upserts (single session)
        await self._batch_upsert_analyses(analyses)

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "analysed": len(analyses),
                "errors": len(errors),
                "timed_out": timed_out,
                "retried": retried,
                "low_confidence": low_confidence,
            },
        )
        await self.broadcast_progress(
            project_id,
            {
                "status": "completed",
                "message": f"Root-cause analysis complete: {len(analyses)} test(s) analysed"
                + (f", {timed_out} timed out" if timed_out else "")
                + (f", {retried} retried" if retried else ""),
            },
        )

        return {
            "analyses": analyses,
            "completed_stages": ["root_cause_analysis"],
            "errors": errors,
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

        # Retry once if confidence is very low and it wasn't a timeout/error
        if (
            result.get("confidence_score", 0) < _RETRY_CONFIDENCE_THRESHOLD
            and not result.get("timed_out")
            and not result.get("error")
            and _MAX_ANALYSIS_RETRIES > 0
        ):
            logger.info(
                "Low confidence (%d) for %s — retrying analysis",
                result.get("confidence_score", 0),
                tc_id,
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
        async with semaphore:
            logger.info("Analysing test case %s: %s", tc_id, meta.get("test_name", ""))
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
                    "ReAct agent timed out after %ds for test %s — using progressive fallback",
                    settings.AI_TIMEOUT_SECONDS, tc_id,
                )
                analysis = await self._build_progressive_fallback(tc_id, meta)
            except Exception as exc:
                logger.error("ReAct agent failed for %s: %s", tc_id, exc)
                analysis = self._build_error_analysis(exc)

            # Post-process: validate confidence and sanitize category
            analysis = self._validate_confidence(analysis)
            analysis = self._sanitize_category(analysis)

            # Record audit metadata
            elapsed = time.perf_counter() - start_time
            analysis["_audit"] = {
                "analysis_duration_seconds": round(elapsed, 3),
                "test_severity": meta.get("severity"),
                "had_error_message": bool(meta.get("error_message")),
                "had_stack_trace": bool(meta.get("stack_trace")),
                "historical_failure_count": meta.get("historical_failure_count", 0),
            }

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
            logger.error("Failed to fetch test metadata: %s", db_exc)
            return {}

    async def _enrich_historical_counts(
        self, db, meta: dict[str, dict], fingerprints: dict[str, str]
    ) -> None:
        """Add historical failure counts to metadata for priority ordering."""
        try:
            fp_values = list(set(fingerprints.values()))
            result = await db.execute(
                select(
                    TestCase.test_fingerprint,
                    func.count(TestCase.id).label("failure_count"),
                )
                .where(
                    TestCase.test_fingerprint.in_(fp_values),
                    TestCase.status.in_([TestStatus.FAILED.value, TestStatus.BROKEN.value]),
                )
                .group_by(TestCase.test_fingerprint)
            )
            counts = {row.test_fingerprint: row.failure_count for row in result.all()}
            for tc_id, fp in fingerprints.items():
                if tc_id in meta:
                    meta[tc_id]["historical_failure_count"] = counts.get(fp, 0)
        except Exception as exc:
            logger.debug("Historical count enrichment failed (non-critical): %s", exc)

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
            logger.debug("Stack trace enrichment from MongoDB failed (non-critical): %s", exc)

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

        # Penalty: no evidence references at all
        if not evidence and confidence > 50:
            logger.debug(
                "Confidence capped 50 (was %d): no evidence references", confidence,
            )
            confidence = min(confidence, 50)

        # Penalty: very short or generic summary
        if len(summary) < 30 and confidence > 30:
            logger.debug(
                "Confidence capped 30 (was %d): summary too short (%d chars)",
                confidence, len(summary),
            )
            confidence = min(confidence, 30)

        # Penalty: no tools used and not a cache hit (suspicious high confidence)
        if not has_tools and not analysis.get("cache_hit") and not analysis.get("classified_by") and confidence > 60:
            logger.debug(
                "Confidence capped 60 (was %d): no tools used and not cached", confidence,
            )
            confidence = min(confidence, 60)

        # Bonus: multiple evidence sources increase trustworthiness
        if len(evidence) >= 3 and confidence < 90:
            confidence = min(confidence + 5, 100)

        analysis["confidence_score"] = confidence
        analysis["requires_human_review"] = confidence < settings.AI_CONFIDENCE_THRESHOLD
        analysis["confidence_validated"] = True
        return analysis

    def _sanitize_category(self, analysis: dict) -> dict:
        """
        Validate failure_category against the FailureCategory enum.
        Uses fuzzy matching for near-misses, falls back to UNKNOWN.
        """
        raw_category = (analysis.get("failure_category") or "").strip().upper()

        if raw_category in VALID_CATEGORIES:
            analysis["failure_category"] = raw_category
            return analysis

        # Try fuzzy matching for close misspellings
        matches = get_close_matches(raw_category, VALID_CATEGORIES, n=1, cutoff=0.7)
        if matches:
            corrected = matches[0]
            logger.info(
                "Category sanitized: '%s' -> '%s' (fuzzy match)", raw_category, corrected,
            )
            analysis["failure_category"] = corrected
            analysis["_category_corrected_from"] = raw_category
            return analysis

        # Common LLM aliases
        _CATEGORY_ALIASES = {
            "BUG": "PRODUCT_BUG",
            "PRODUCT": "PRODUCT_BUG",
            "CODE_BUG": "PRODUCT_BUG",
            "APPLICATION_BUG": "PRODUCT_BUG",
            "INFRA": "INFRASTRUCTURE",
            "ENV": "INFRASTRUCTURE",
            "ENVIRONMENT": "INFRASTRUCTURE",
            "NETWORK": "INFRASTRUCTURE",
            "DATA": "TEST_DATA",
            "DATA_ISSUE": "TEST_DATA",
            "SETUP": "TEST_DATA",
            "TEST_CODE": "AUTOMATION_DEFECT",
            "AUTOMATION": "AUTOMATION_DEFECT",
            "TEST_BUG": "AUTOMATION_DEFECT",
            "INTERMITTENT": "FLAKY",
            "RACE_CONDITION": "FLAKY",
            "NONDETERMINISTIC": "FLAKY",
            "NON_DETERMINISTIC": "FLAKY",
        }
        if raw_category in _CATEGORY_ALIASES:
            corrected = _CATEGORY_ALIASES[raw_category]
            logger.info(
                "Category sanitized: '%s' -> '%s' (alias match)", raw_category, corrected,
            )
            analysis["failure_category"] = corrected
            analysis["_category_corrected_from"] = raw_category
            return analysis

        # Fallback to UNKNOWN
        if raw_category and raw_category != self.UNKNOWN_CATEGORY:
            logger.warning(
                "Unrecognized failure category '%s' — falling back to UNKNOWN", raw_category,
            )
            analysis["_category_corrected_from"] = raw_category
        analysis["failure_category"] = self.UNKNOWN_CATEGORY
        return analysis

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
                logger.info("Progressive fallback tier 1 succeeded for %s", tc_id)
                return quick
        except Exception as exc:
            logger.debug("Progressive fallback tier 1 failed for %s: %s", tc_id, exc)

        # Tier 2: Pattern-based heuristic
        heuristic = self._pattern_based_analysis(error_msg, test_name)
        if heuristic["failure_category"] != self.UNKNOWN_CATEGORY:
            heuristic["fallback_tier"] = 2
            heuristic["fallback_reason"] = "timeout_pattern_match"
            logger.info("Progressive fallback tier 2 succeeded for %s", tc_id)
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
                    "Batch upsert failed for chunk %d-%d: %s", i, i + len(chunk), exc
                )
