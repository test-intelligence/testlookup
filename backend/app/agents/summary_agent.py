"""
Test Summary Agent — Stage 4 of the offline pipeline.

Generates a 4-layer structured report from anomaly-detection and root-cause-analysis outputs:

  Layer 1 — 3-sentence executive summary (for release managers / stakeholders)
  Layer 2 — Structured incident view  (what failed / likely cause / scope / criticality / release impact)
  Layer 3 — Evidence pack             (logs, stack traces, flaky history, similar failures)
  Layer 4 — Action plan               (mitigation / fix recommendation / validation / rollback guidance)

All 4 layers are stored in MongoDB[run_summaries] under their own keys.
"""
import asyncio
import hashlib
import json
import structlog
import uuid
from datetime import datetime, timezone
from typing import Any

from app.agents.base import BaseAgent
from app.agents.consistency import (
    check_summary_consistency,
    log_consistency_failures,
)
from app.core.config import settings
from app.db.mongo import Collections, get_mongo_db
from app.models.agent_contracts import SummaryAgentOutput, validate_agent_contract
from app.models.llm_schemas import (
    ActionPlan,
    EvidencePack,
    IncidentView,
    validate_llm_output_with_error,
)
from app.services.llm_factory import get_llm
from app.services.llm_json_parser import parse_llm_json
from app.services.prompt_registry import get_prompt, get_prompt_text
from app.services.redaction_service import redact_text
from app.services.resilience import truncate_to_token_budget


def _prompt_version_tag(prompt_id: str) -> str:
    return get_prompt(prompt_id).version_tag

logger = structlog.get_logger("agents.summary")

# Max tokens for context sent to LLM (reserve overhead for prompt template)
_MAX_CONTEXT_TOKENS = max(512, settings.LLM_MAX_TOKENS - 1500)
# Timeout for individual LLM layer calls
_LAYER_TIMEOUT_SECONDS = min(120, settings.AI_TIMEOUT_SECONDS)
# Semantic enrichment is useful but non-critical; keep summary generation moving
# if vector search or embedding infrastructure is slow.
_SIMILAR_FAILURES_TIMEOUT_SECONDS = min(10, max(2, settings.AI_TIMEOUT_SECONDS // 30))
# Registry-derived version tags (AI-F2): v<version>:<hash12> of the exact
# prompt bytes in use — recorded in summary provenance.
_SUMMARY_PROMPT_VERSIONS = {
    "system": _prompt_version_tag("summary_system"),
    "executive_summary": _prompt_version_tag("summary_executive"),
    "incident_view": _prompt_version_tag("summary_incident_view"),
    "evidence_pack": _prompt_version_tag("summary_evidence_pack"),
    "action_plan": _prompt_version_tag("summary_action_plan"),
}


# Prompt texts live in the prompt registry (AI-F2) — edit there, with a
# manifest bump + eval-gate attestation.
_SYSTEM_PROMPT = get_prompt_text("summary_system")

# ── Layer prompts ─────────────────────────────────────────────────────────────

_EXEC_SUMMARY_PROMPT = get_prompt_text("summary_executive")

_INCIDENT_VIEW_PROMPT = get_prompt_text("summary_incident_view")

_EVIDENCE_PACK_PROMPT = get_prompt_text("summary_evidence_pack")

_ACTION_PLAN_PROMPT = get_prompt_text("summary_action_plan")


def _hash_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


class SummaryAgent(BaseAgent):
    stage_name = "summary"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        test_run_id = state["test_run_id"]
        project_id = state["project_id"]

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Generating structured test run summary…"},
        )

        try:
            run_data = state.get("test_run_data") or {}
            anomalies = state.get("anomalies") or []
            analyses = state.get("analyses") or {}
            anomaly_summary = state.get("anomaly_summary") or ""
            stage_errors = state.get("stage_errors") or {}
            stage_quality = state.get("stage_quality") or "normal"

            # Fetch similar historical failures BEFORE LLM call to enrich context
            similar_failures = await self._fetch_similar_failures(
                run_data=run_data,
                analyses=analyses,
                state=state,
            )
            summary_provenance = self._build_summary_provenance(
                run_data=run_data,
                anomaly_summary=anomaly_summary,
                anomalies=anomalies,
                analyses=analyses,
                similar_failures=similar_failures,
                stage_quality=stage_quality,
                stage_errors=stage_errors,
            )

            fallback_reason: str | None = None
            try:
                structured = await self._generate_structured_report(
                    run_data=run_data,
                    anomaly_summary=anomaly_summary,
                    anomalies=anomalies,
                    analyses=analyses,
                    similar_failures=similar_failures,
                    stage_quality=stage_quality,
                    stage_errors=stage_errors,
                    pipeline_run_id=pipeline_run_id,
                )
            except Exception as exc:
                fallback_reason = str(exc)
                logger.warning(
                    "Summary LLM unavailable; generating deterministic fallback summary instead: %s",
                    exc,
                )
                structured = self._build_fallback_structured_report(
                    run_data=run_data,
                    anomaly_summary=anomaly_summary,
                    anomalies=anomalies,
                    analyses=analyses,
                    error_message=fallback_reason,
                    similar_failures=similar_failures,
                )
            summary_provenance["fallback_used"] = bool(fallback_reason)
            summary_provenance["fallback_reason_sha256"] = _hash_text(fallback_reason)
            structured["_provenance"] = summary_provenance

            # Persist all 4 layers to MongoDB
            await self._store_summary(test_run_id, structured, state)

            # Legacy fields kept for backward compatibility
            executive_summary = structured["layer1_executive_summary"]
            markdown_report = self._build_markdown(structured)

            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "summary_length": len(markdown_report),
                    "layers": 4,
                    "fallback_used": bool(fallback_reason),
                    "fallback_reason": fallback_reason,
                    "summary_provenance": summary_provenance,
                },
            )
            await self.broadcast_progress(
                project_id,
                {
                    "status": "completed",
                    "message": (
                        "Structured test run summary generated"
                        if not fallback_reason
                        else "Summary generated in fallback mode because the configured LLM was unavailable"
                    ),
                },
            )

            consistency_report = check_summary_consistency(
                structured=structured,
                run_data=run_data,
                failed_test_ids=state.get("failed_test_ids") or [],
                analyses=analyses,
            )
            log_consistency_failures(consistency_report, pipeline_run_id=pipeline_run_id)

            summary_decision_reason = (
                "deterministic_summary_fallback"
                if fallback_reason else "structured_summary_generated"
            ) + consistency_report.decision_suffix()

            return validate_agent_contract(
                SummaryAgentOutput,
                {
                    "executive_summary": executive_summary,
                    "summary_markdown": markdown_report,
                    "structured_summary": structured,
                    "summary_provenance": summary_provenance,
                    "completed_stages": ["summary"],
                    "errors": [],
                    "current_stage": "triage",
                },
                agent_name=self.stage_name,
                fallback_used=bool(fallback_reason),
                confidence=70 if fallback_reason else 90,
                evidence_refs=[
                    {
                        "type": "summary_provenance",
                        "id": summary_provenance.get("context_sha256"),
                    },
                    {
                        "type": "ordered_analysis_ids",
                        "id": summary_provenance.get("ordered_analysis_ids_sha256"),
                    },
                    consistency_report.evidence_ref(),
                ],
                decision_reason=summary_decision_reason,
            )

        except Exception as exc:
            error_msg = f"Summary agent error: {exc}"
            logger.error(error_msg, exc_info=True)
            await self.mark_stage_done(pipeline_run_id, error=error_msg)
            return validate_agent_contract(
                SummaryAgentOutput,
                {
                    "executive_summary": None,
                    "summary_markdown": None,
                    "structured_summary": None,
                    "errors": [error_msg],
                    "completed_stages": ["summary"],
                    "current_stage": "triage",
                },
                agent_name=self.stage_name,
                fallback_used=True,
                confidence=0,
                decision_reason="summary_agent_exception",
            )

    # ── Context builder ───────────────────────────────────────────────────────

    @staticmethod
    def _sorted_analyses(analyses: dict[str, dict]) -> list[tuple[str, dict]]:
        """Return analyses in a stable, high-signal order for prompts/reports."""
        def sort_key(item: tuple[str, dict]) -> tuple[int, str, str]:
            test_id, analysis = item
            try:
                confidence = int(analysis.get("confidence_score") or 0)
            except (TypeError, ValueError):
                confidence = 0
            category = str(analysis.get("failure_category") or "UNKNOWN")
            return (-confidence, category, str(test_id))

        return sorted(analyses.items(), key=sort_key)

    def _build_context(
        self,
        run_data: dict,
        anomaly_summary: str,
        anomalies: list[dict],
        analyses: dict[str, dict],
        similar_failures: list[dict] | None = None,
        stage_quality: str = "normal",
        stage_errors: dict[str, list[str]] | None = None,
    ) -> str:
        pass_rate = run_data.get("pass_rate", 0)
        total = run_data.get("total_tests", 0)
        failed = run_data.get("failed_tests", 0)
        build = run_data.get("build_number", "?")
        branch = run_data.get("branch", "?")

        analysis_bullets = []
        sorted_analyses = self._sorted_analyses(analyses)
        for _tc_id, analysis in sorted_analyses[:15]:
            if analysis.get("confidence_score", 0) >= 50:
                cat = analysis.get("failure_category", "UNKNOWN")
                conf = analysis.get("confidence_score", 0)
                summary = analysis.get("root_cause_summary", "")[:200]
                is_flaky = " [FLAKY]" if analysis.get("is_flaky") else ""
                analysis_bullets.append(f"- [{cat}]{is_flaky} conf={conf}%: {summary}")

        evidence_excerpts = []
        for _tc_id, analysis in sorted_analyses[:5]:
            for ev in analysis.get("evidence_references", [])[:2]:
                evidence_excerpts.append(
                    f"  [{ev.get('source', '?')}] {ev.get('excerpt', '')[:150]}"
                )

        # Include similar historical failures if available
        similar_section = ""
        if similar_failures:
            names = [sf.get("test_name", "") for sf in similar_failures[:3] if sf.get("test_name")]
            if names:
                similar_section = (
                    f"\n\nSimilar historical failures ({len(similar_failures)}):\n"
                    + "\n".join(f"  - {n}" for n in names)
                )

        # Surface analysis quality indicator from upstream stages
        quality_note = ""
        stage_errors = stage_errors or {}
        if stage_quality == "degraded":
            error_stages = ", ".join(stage_errors.keys()) if stage_errors else "unknown"
            quality_note = (
                f"\n\nANALYSIS QUALITY: DEGRADED — some upstream stages ({error_stages}) "
                "had errors or timeouts. Data below may be incomplete.\n"
            )

        raw_context = (
            f"Build: {build} | Branch: {branch}\n"
            f"Results: {total} tests, {failed} failures, {pass_rate:.1f}% pass rate\n"
            + quality_note
            + f"\nAnomalies ({len(anomalies)} detected):\n{anomaly_summary or 'None detected.'}\n\n"
            f"Top failure root causes ({len(analysis_bullets)} above threshold):\n"
            + ("\n".join(analysis_bullets) or "No analyses available.")
            + (("\n\nEvidence excerpts:\n" + "\n".join(evidence_excerpts)) if evidence_excerpts else "")
            + similar_section
        )
        return redact_text(raw_context)

    def _build_summary_provenance(
        self,
        run_data: dict,
        anomaly_summary: str,
        anomalies: list[dict],
        analyses: dict[str, dict],
        similar_failures: list[dict] | None = None,
        stage_quality: str = "normal",
        stage_errors: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Build deterministic fingerprints for replaying summary generation."""
        context = self._build_context(
            run_data,
            anomaly_summary,
            anomalies,
            analyses,
            similar_failures=similar_failures or [],
            stage_quality=stage_quality,
            stage_errors=stage_errors or {},
        )
        safe_context = truncate_to_token_budget(context, _MAX_CONTEXT_TOKENS)
        sorted_analysis_ids = [test_id for test_id, _analysis in self._sorted_analyses(analyses)]
        prompt_templates = {
            "system": _SYSTEM_PROMPT,
            "executive_summary": _EXEC_SUMMARY_PROMPT,
            "incident_view": _INCIDENT_VIEW_PROMPT,
            "evidence_pack": _EVIDENCE_PACK_PROMPT,
            "action_plan": _ACTION_PLAN_PROMPT,
        }
        return {
            "schema_version": 1,
            "generated_by": "agents.summary_agent",
            "context_sha256": _hash_text(context),
            "safe_context_sha256": _hash_text(safe_context),
            "input_fingerprints": {
                "run_data_sha256": _hash_json(run_data),
                "anomaly_summary_sha256": _hash_text(anomaly_summary),
                "anomalies_sha256": _hash_json(anomalies),
                "analyses_sha256": _hash_json(analyses),
                "similar_failures_sha256": _hash_json(similar_failures or []),
                "ordered_analysis_ids_sha256": _hash_json(sorted_analysis_ids),
            },
            "prompt_versions": dict(_SUMMARY_PROMPT_VERSIONS),
            "prompt_template_hashes": {
                key: _hash_text(template) for key, template in prompt_templates.items()
            },
            "model_config_snapshot": {
                "provider": settings.LLM_PROVIDER,
                "model": settings.LLM_MODEL,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens": settings.LLM_MAX_TOKENS,
                "context_token_budget": _MAX_CONTEXT_TOKENS,
                "layer_timeout_seconds": _LAYER_TIMEOUT_SECONDS,
                "similar_failures_timeout_seconds": _SIMILAR_FAILURES_TIMEOUT_SECONDS,
            },
            "stage_quality": stage_quality,
            "stage_errors_sha256": _hash_json(stage_errors or {}),
        }

    # ── LLM calls ─────────────────────────────────────────────────────────────

    async def _generate_structured_report(
        self,
        run_data: dict,
        anomaly_summary: str,
        anomalies: list[dict],
        analyses: dict[str, dict],
        similar_failures: list[dict] | None = None,
        stage_quality: str = "normal",
        stage_errors: dict[str, list[str]] | None = None,
        pipeline_run_id: str | None = None,
    ) -> dict:
        context = self._build_context(
            run_data, anomaly_summary, anomalies, analyses,
            similar_failures=similar_failures or [],
            stage_quality=stage_quality,
            stage_errors=stage_errors or {},
        )
        llm = await get_llm()

        # Layer 1: executive summary (plain text) with timeout
        safe_context = truncate_to_token_budget(context, _MAX_CONTEXT_TOKENS)
        try:
            exec_resp = await asyncio.wait_for(
                llm.ainvoke(
                    _EXEC_SUMMARY_PROMPT.format(system=_SYSTEM_PROMPT, context=safe_context)
                ),
                timeout=_LAYER_TIMEOUT_SECONDS,
            )
            layer1 = _extract_text(exec_resp)
        except asyncio.TimeoutError:
            logger.warning("Executive summary LLM call timed out", timeout=_LAYER_TIMEOUT_SECONDS)
            layer1 = "Executive summary generation timed out. Please refer to the detailed breakdown below."
        except Exception as exc:
            logger.warning("Executive summary LLM call failed", error=str(exc))
            layer1 = "Executive summary generation failed. Please refer to the detailed breakdown below."

        # Layers 2, 3, 4: JSON responses (run in sequence to respect LLM rate limits)
        layer2 = await self._call_json_layer(
            llm, _INCIDENT_VIEW_PROMPT, context,
            expected_keys=["what_failed", "likely_cause", "criticality", "release_impact"],
            layer_name="incident_view",
            pipeline_run_id=pipeline_run_id,
        )
        layer3 = await self._call_json_layer(
            llm, _EVIDENCE_PACK_PROMPT, context,
            expected_keys=["top_stack_traces", "log_anomalies", "data_sources_used"],
            layer_name="evidence_pack",
            pipeline_run_id=pipeline_run_id,
        )
        layer4 = await self._call_json_layer(
            llm, _ACTION_PLAN_PROMPT, context,
            expected_keys=["immediate_mitigation", "fix_recommendations", "validation_steps"],
            layer_name="action_plan",
            pipeline_run_id=pipeline_run_id,
        )

        # Post-processing: attach similar_failures to layer3 and extract citations
        if isinstance(layer3, dict):
            if similar_failures:
                layer3["similar_historical_failures"] = [
                    str(sf.get("test_name", ""))[:100] for sf in similar_failures[:5]
                ]
            # Extract citations from evidence snippets
            from app.services.summary_assembler import extract_citations  # noqa: PLC0415
            evidence_snippets: list[dict] = []
            for tc_id, analysis in self._sorted_analyses(analyses)[:10]:
                for ev in (analysis.get("evidence_references") or [])[:2]:
                    evidence_snippets.append({
                        "source": str(ev.get("source") or ""),
                        "excerpt": str(ev.get("excerpt") or "")[:400],
                        "test_id": str(tc_id),
                    })
            citations = extract_citations(json.dumps(layer3), evidence_snippets)
            layer3["citations"] = citations

        # ── Executive panel (deterministic, never LLM-generated) ─────────
        from app.services.executive_panel_builder import build_executive_panel  # noqa: PLC0415

        category_counts: dict[str, int] = {}
        flaky_count = 0
        ep_actions: list[str] = []
        for _tc_id, analysis in self._sorted_analyses(analyses):
            cat = self._stringify_value(analysis.get("failure_category")) or "UNKNOWN"
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if analysis.get("is_flaky"):
                flaky_count += 1
            for a in (analysis.get("recommended_actions") or [])[:2]:
                a_text = str(a).strip()
                if a_text and a_text not in ep_actions:
                    ep_actions.append(a_text)

        ep_release_impact = "CONDITIONAL_GO"
        if isinstance(layer2, dict):
            ep_release_impact = layer2.get("release_impact", "CONDITIONAL_GO")

        executive_panel = build_executive_panel(
            run_data=run_data,
            category_counts=category_counts,
            flaky_count=flaky_count,
            anomaly_count=len(anomalies),
            cluster_count=0,
            release_impact=ep_release_impact,
            recommended_actions=ep_actions[:3],
        )

        return {
            "executive_panel": executive_panel,
            "layer1_executive_summary": layer1,
            "layer2_incident_view": layer2,
            "layer3_evidence_pack": layer3,
            "layer4_action_plan": layer4,
        }

    # Map layer names to Pydantic schemas for structured validation
    _LAYER_SCHEMAS: dict[str, type] = {
        "incident_view": IncidentView,
        "evidence_pack": EvidencePack,
        "action_plan": ActionPlan,
    }

    async def _call_json_layer(
        self,
        llm,
        prompt_template: str,
        context: str,
        expected_keys: list[str] | None = None,
        layer_name: str = "unknown",
        pipeline_run_id: str | None = None,
    ) -> dict:
        """Call LLM with a JSON-requesting prompt. Returns parsed dict or error stub."""
        # Truncate context to token budget
        safe_context = truncate_to_token_budget(context, _MAX_CONTEXT_TOKENS)
        try:
            resp = await asyncio.wait_for(
                llm.ainvoke(
                    prompt_template.format(system=_SYSTEM_PROMPT, context=safe_context)
                ),
                timeout=_LAYER_TIMEOUT_SECONDS,
            )
            raw = _extract_text(resp)
            parsed, error = parse_llm_json(
                raw,
                expected_keys=expected_keys,
                context=f"summary_{layer_name}",
            )
            if error:
                logger.warning("JSON layer parse issue", layer=layer_name, reason=error)
                if pipeline_run_id:
                    await self.log_decision(
                        pipeline_run_id,
                        decision_point="summary_schema_validation",
                        chosen="parse_fallback",
                        rationale=error,
                        context={
                            "layer": layer_name,
                            "schema": self._LAYER_SCHEMAS.get(layer_name).__name__
                            if self._LAYER_SCHEMAS.get(layer_name) else None,
                            "expected_keys": expected_keys or [],
                        },
                    )
            # Validate through Pydantic schema if available
            schema = self._LAYER_SCHEMAS.get(layer_name)
            if schema:
                parsed, validation_error = validate_llm_output_with_error(
                    schema, parsed, context=f"summary_{layer_name}"
                )
                if validation_error and pipeline_run_id:
                    await self.log_decision(
                        pipeline_run_id,
                        decision_point="summary_schema_validation",
                        chosen="schema_defaults",
                        rationale=validation_error,
                        context={
                            "layer": layer_name,
                            "schema": schema.__name__,
                            "expected_keys": expected_keys or [],
                        },
                    )
            return parsed
        except asyncio.TimeoutError:
            logger.warning("JSON layer call timed out", layer=layer_name, timeout=_LAYER_TIMEOUT_SECONDS)
            return {}
        except Exception as exc:
            logger.warning("JSON layer call failed", layer=layer_name, error=str(exc))
        return {}

    async def _fetch_similar_failures(
        self,
        run_data: dict,
        analyses: dict,
        state: dict,
    ) -> list[dict]:
        """Retrieve similar historical failures via semantic search (blocks before LLM)."""
        top_error = ""
        for _tc_id, analysis in self._sorted_analyses(analyses)[:5]:
            root_cause = str(analysis.get("root_cause_summary") or "").strip()
            if root_cause and len(root_cause) > 20:
                top_error = root_cause[:200]
                break

        if not top_error:
            return []

        try:
            from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
            from app.models.postgres import TestStatus  # noqa: PLC0415
            from app.services.agent_memory_service import recall_similar  # noqa: PLC0415
            from app.services.semantic_search import semantic_search  # noqa: PLC0415

            async with AsyncSessionLocal() as db:
                project_id = state.get("project_id")
                if project_id:
                    try:
                        memory_matches = await asyncio.wait_for(
                            recall_similar(
                                db=db,
                                project_id=uuid.UUID(str(project_id)),
                                error_signature=top_error,
                                entity_type="analysis",
                                limit=5,
                            ),
                            timeout=_SIMILAR_FAILURES_TIMEOUT_SECONDS,
                        )
                    except (TypeError, ValueError):
                        memory_matches = []
                    if memory_matches:
                        return self._format_memory_recall_items(memory_matches)

                items, _total, _pages = await asyncio.wait_for(
                    semantic_search(
                        db=db,
                        q=top_error,
                        page=1,
                        size=5,
                        project_id=state.get("project_id"),
                        status=TestStatus.FAILED.value,
                    ),
                    timeout=_SIMILAR_FAILURES_TIMEOUT_SECONDS,
                )
            return items
        except asyncio.TimeoutError:
            logger.debug(
                "Similar failures retrieval timed out after %ss",
                _SIMILAR_FAILURES_TIMEOUT_SECONDS,
            )
            return []
        except Exception as exc:
            logger.debug(
                "Similar failures retrieval failed (non-blocking): %s", exc
            )
            return []

    @staticmethod
    def _format_memory_recall_items(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert audited memory recall results into summary-context items."""
        items: list[dict[str, Any]] = []
        for match in sorted(
            matches,
            key=lambda item: (
                -float(item.get("similarity") or 0),
                str(getattr(item.get("memory"), "id", "")),
            ),
        ):
            memory = match.get("memory")
            if not memory:
                continue
            payload = getattr(memory, "payload", None) or {}
            items.append({
                "test_case_id": str(getattr(memory, "entity_id", "")),
                "test_name": (
                    str(payload.get("test_name") or getattr(memory, "entity_id", ""))
                ),
                "failure_category": getattr(memory, "failure_category", None),
                "root_cause_summary": getattr(memory, "root_cause_summary", None),
                "relevance_score": match.get("similarity"),
                "source_mode_used": "agent_memory",
                "retrieval_audit": match.get("retrieval_audit"),
                "memory_reference": match.get("memory_reference"),
            })
        return items[:5]

    def _build_fallback_structured_report(
        self,
        run_data: dict[str, Any],
        anomaly_summary: str,
        anomalies: list[dict[str, Any]],
        analyses: dict[str, dict[str, Any]],
        error_message: str | None = None,
        similar_failures: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        total = int(run_data.get("total_tests") or 0)
        failed = int(run_data.get("failed_tests") or 0)
        pass_rate = float(run_data.get("pass_rate") or 0.0)
        build = run_data.get("build_number") or "unknown"
        branch = run_data.get("branch") or "unknown"

        category_counts: dict[str, int] = {}
        recommended_actions: list[str] = []
        stack_traces: list[str] = []
        flaky_test_ids: list[str] = []
        data_sources: set[str] = set()

        for test_id, analysis in self._sorted_analyses(analyses):
            category = self._stringify_value(analysis.get("failure_category")) or "UNKNOWN"
            category_counts[category] = category_counts.get(category, 0) + 1

            if analysis.get("is_flaky"):
                flaky_test_ids.append(str(test_id))

            for action in analysis.get("recommended_actions") or []:
                action_text = str(action).strip()
                if action_text and action_text not in recommended_actions:
                    recommended_actions.append(action_text)

            for evidence in analysis.get("evidence_references") or []:
                source = str(evidence.get("source") or "").strip()
                excerpt = str(evidence.get("excerpt") or "").strip()
                if source:
                    data_sources.add(source)
                if excerpt and len(stack_traces) < 3:
                    stack_traces.append(excerpt[:300])

        top_category = (
            max(category_counts, key=lambda category: category_counts[category])
            if category_counts
            else "UNKNOWN"
        )
        if failed == 0:
            release_impact = "GO"
            criticality = "LOW"
        elif pass_rate >= 90:
            release_impact = "CONDITIONAL_GO"
            criticality = "MEDIUM"
        elif pass_rate >= 75:
            release_impact = "CONDITIONAL_GO"
            criticality = "HIGH"
        else:
            release_impact = "NO_GO"
            criticality = "CRITICAL"

        summary_sentence = (
            f"Build {build} on branch {branch} completed with {failed} failing tests out of {total} and a {pass_rate:.1f}% pass rate. "
            f"The dominant failure category was {top_category.replace('_', ' ').lower()}, and the current release signal is {release_impact.replace('_', ' ')}. "
            "This summary was generated from stored pipeline evidence because the configured LLM was unavailable."
        )

        log_anomalies = [str(item.get("summary") or item.get("message") or item) for item in anomalies[:5]]
        if anomaly_summary and not log_anomalies:
            log_anomalies = [anomaly_summary]

        likely_cause = "Insufficient evidence to determine root cause."
        for analysis in analyses.values():
            root_cause_summary = str(analysis.get("root_cause_summary") or "").strip()
            if root_cause_summary:
                likely_cause = root_cause_summary
                break

        immediate_mitigation = (
            "Verify the configured local LLM model is available, then re-run the summary stage."
            if error_message
            else "Review the failing suites and rerun the affected tests."
        )

        fallback_fix_recommendations = recommended_actions[:5] or [
            "Review the top failing tests and confirm whether the issue is product, environment, or test-data related.",
            "Restore or retarget the configured Ollama model before re-running AI-assisted summary generation.",
        ]

        # ── Executive panel (structured, scannable) ────────────────────
        from app.services.executive_panel_builder import build_executive_panel

        executive_panel = build_executive_panel(
            run_data=run_data,
            category_counts=category_counts,
            flaky_count=len(flaky_test_ids),
            anomaly_count=len(anomalies),
            cluster_count=0,
            release_impact=release_impact,
            recommended_actions=fallback_fix_recommendations[:3],
        )

        return {
            "executive_panel": executive_panel,
            "layer1_executive_summary": summary_sentence,
            "layer2_incident_view": {
                "what_failed": anomaly_summary or f"{failed} tests failed in build {build}",
                "likely_cause": likely_cause,
                "scope": run_data.get("jenkins_job") or run_data.get("ocp_namespace") or "test run scope not recorded",
                "criticality": criticality,
                "release_impact": release_impact,
                "failure_breakdown": {
                    "product_bugs": category_counts.get("PRODUCT_BUG", 0),
                    "infrastructure": category_counts.get("INFRASTRUCTURE", 0),
                    "test_data": category_counts.get("TEST_DATA", 0),
                    "automation_defect": category_counts.get("AUTOMATION_DEFECT", 0),
                    "flaky": category_counts.get("FLAKY", 0),
                    "unknown": category_counts.get("UNKNOWN", 0),
                },
            },
            "layer3_evidence_pack": {
                "top_stack_traces": stack_traces,
                "log_anomalies": [item for item in log_anomalies if item],
                "flaky_test_ids": flaky_test_ids[:10],
                "similar_historical_failures": [
                    str(sf.get("test_name", ""))[:100]
                    for sf in (similar_failures or [])[:5]
                ],
                "data_sources_used": sorted(data_sources),
                "citations": [],
            },
            "layer4_action_plan": {
                "immediate_mitigation": immediate_mitigation,
                "fix_recommendations": fallback_fix_recommendations,
                "validation_steps": [
                    "Re-run the failed suites after addressing the top suspected cause.",
                    "Confirm the configured summary model is present before retrying the AI pipeline.",
                ],
                "rollback_guidance": "Rollback the last relevant environment or application change if the same failures persist after remediation.",
                "owner_hints": {
                    "qa": "Validate the failing scenarios and capture any repeatable evidence.",
                    "developer": "Investigate the dominant failure category and remediate the highest-impact root cause first.",
                    "sre": "Check environment health, service dependencies, and local model availability for the AI stack.",
                    "release_manager": f"Treat this run as {release_impact.replace('_', ' ')} until the failures and AI model availability are resolved.",
                },
            },
        }

    @staticmethod
    def _stringify_value(value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "value"):
            return str(value.value)
        return str(value)

    # ── Markdown builder (backward-compat legacy field) ────────────────────────

    def _build_markdown(self, structured: dict) -> str:
        layer1 = structured.get("layer1_executive_summary", "")
        layer2 = structured.get("layer2_incident_view", {})
        layer3 = structured.get("layer3_evidence_pack", {})
        layer4 = structured.get("layer4_action_plan", {})

        sections: list[str] = []
        sections.append(f"## Executive Summary\n{layer1}")

        if layer2:
            sections.append(
                "## Incident View\n"
                f"**What failed:** {layer2.get('what_failed', '—')}\n"
                f"**Likely cause:** {layer2.get('likely_cause', '—')}\n"
                f"**Scope:** {layer2.get('scope', '—')}\n"
                f"**Criticality:** {layer2.get('criticality', '—')}\n"
                f"**Release impact:** {layer2.get('release_impact', '—')}"
            )

        if layer3:
            sources = ", ".join(layer3.get("data_sources_used", []))
            flaky = ", ".join(layer3.get("flaky_test_ids", []))
            anomalies_text = "\n".join(f"- {a}" for a in layer3.get("log_anomalies", []))
            sections.append(
                "## Evidence Pack\n"
                f"**Data sources:** {sources or '—'}\n"
                f"**Flaky tests:** {flaky or 'None'}\n"
                + (f"**Log anomalies:**\n{anomalies_text}" if anomalies_text else "")
            )

        if layer4:
            fix_recs = "\n".join(f"- {r}" for r in layer4.get("fix_recommendations", []))
            val_steps = "\n".join(f"- {s}" for s in layer4.get("validation_steps", []))
            sections.append(
                "## Action Plan\n"
                f"**Immediate mitigation:** {layer4.get('immediate_mitigation', '—')}\n\n"
                f"**Fix recommendations:**\n{fix_recs or '—'}\n\n"
                f"**Validation steps:**\n{val_steps or '—'}\n\n"
                f"**Rollback guidance:** {layer4.get('rollback_guidance', '—')}"
            )

        return "\n\n".join(sections)

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _store_summary(
        self,
        test_run_id: str,
        structured: dict,
        state: dict,
    ) -> None:
        db = get_mongo_db()
        now = datetime.now(timezone.utc)

        # Extract citations from layer3 if present
        layer3 = structured.get("layer3_evidence_pack") or {}
        citations = layer3.get("citations", []) if isinstance(layer3, dict) else []
        fallback_used = structured.get("_fallback_used", False)
        data_sources = (
            layer3.get("data_sources_used", []) if isinstance(layer3, dict) else []
        )
        summary_provenance = structured.get("_provenance") or {}

        await db[Collections.RUN_SUMMARIES].update_one(
            {"test_run_id": test_run_id},
            {
                "$set": {
                    "test_run_id": test_run_id,
                    "project_id": state.get("project_id"),
                    "build_number": state.get("build_number"),
                    # Layer 1: backward-compat field
                    "executive_summary": structured.get("layer1_executive_summary"),
                    # Legacy markdown report built from all layers
                    "markdown_report": self._build_markdown(structured),
                    # All 4 layers stored independently for structured API access
                    "layer1_executive_summary": structured.get("layer1_executive_summary"),
                    "layer2_incident_view": structured.get("layer2_incident_view"),
                    "layer3_evidence_pack": structured.get("layer3_evidence_pack"),
                    "layer4_action_plan": structured.get("layer4_action_plan"),
                    # Structured executive panel (ES-2)
                    "executive_panel": structured.get("executive_panel"),
                    # Pipeline metadata
                    "anomaly_count": len(state.get("anomalies") or []),
                    "is_regression": state.get("is_regression", False),
                    "analysis_count": len(state.get("analyses") or {}),
                    "generated_at": now,
                    "schema_version": 4,  # v4 = v3 + executive_panel
                    # Epic 6 additions
                    "fallback_used": fallback_used,
                    "citations": citations,
                    "summary_provenance": summary_provenance,
                    "provenance": {
                        "schema_version": 4,
                        "data_sources_used": data_sources,
                        "fallback_used": fallback_used,
                        "generated_at": now.isoformat(),
                        "summary_provenance": summary_provenance,
                    },
                }
            },
            upsert=True,
        )


def _extract_text(response) -> str:
    raw = response.content if hasattr(response, "content") else str(response)
    return str(raw).strip()
