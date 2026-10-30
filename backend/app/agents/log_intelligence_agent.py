"""
Log Intelligence Agent.
Runs distributed trace reconstruction and log rate anomaly detection for a failure cluster.
Operates as a specialist sub-agent called by DeepRootCauseAgent.
"""
import json
import uuid
from typing import Any, Literal

import structlog

from app.agents.base import BaseAgent
from app.agents.evidence import EvidenceRef
from app.models.agent_contracts import (
    LogIntelligenceAgentOutput,
    degraded_contract,
    validate_agent_contract,
)
from app.services import prometheus_vocab as pv
from app.tools.detect_log_anomaly import detect_log_rate_anomaly
from app.tools.fetch_app_metrics import fetch_app_metrics
from app.tools.reconstruct_trace import reconstruct_distributed_trace
from app.services.evidence_sanitizer import (
    sanitize_persistence_payload,
    sanitize_reference_text,
)
from app.services.workflow_step_context import tool_allowed

logger = structlog.get_logger("agents.log_intelligence")

#: W23d AGT-15: the failure window the stage asks the metrics tool for (the window before it is the baseline).
METRICS_WINDOW_MINUTES = 15
#: The tool answered something the stage cannot read (a bug, never a Prometheus answer).
METRICS_LOOKUP_FAILED = "metrics unavailable: the metrics lookup failed"


def _metrics_block(outcome: str, sentence: str, service: str = "", series: list | None = None) -> dict:
    """One cluster's ``app_metrics``: numbers, template ids / labels, the regex-checked service and code-built
    sentences only (no Prometheus string survives -- contract §2.2 M-5)."""
    return {"outcome": outcome, "sentence": sentence[:500], "service": service[:128], "series": series or []}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _metrics_block_from_answer(answer: Any) -> dict:
    """The tool's JSON answer → the stored block (≤ 4 series × ≤ 120 points; typed, bounded)."""
    if not isinstance(answer, dict) or "error" in answer:
        # The tool refused the input -- here only an unusable failure time (the stage never sends templates).
        return _metrics_block("no_context", pv.OUTCOME_SENTENCES["no_context"])
    outcome = str(answer.get("outcome") or "")
    if outcome not in pv.OUTCOMES:
        return _metrics_block("http_error", METRICS_LOOKUP_FAILED)
    series: list[dict] = []
    for item in (answer.get("series") or [])[: pv.TEMPLATES_PER_CALL_MAX]:
        if not isinstance(item, dict):
            continue
        points = []
        for pair in (item.get("points") or [])[: pv.POINTS_MAX]:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and _number(pair[0]) is not None:
                points.append([_number(pair[0]), _number(pair[1])])
        series.append({
            "template_id": str(item.get("template_id") or "")[:40],
            "label": str(item.get("label") or "")[: pv.TEMPLATE_LABEL_MAX],
            "unit": str(item.get("unit") or "count")[:20],
            "points": points,
            "baseline_mean": _number(item.get("baseline_mean")),
            "window_mean": _number(item.get("window_mean")),
            "window_max": _number(item.get("window_max")),
            "ratio": _number(item.get("ratio")),
            "rose": item.get("rose") is True,
            "sentence": str(item.get("sentence") or "")[:300],
        })
    return _metrics_block(outcome, str(answer.get("sentence") or ""), str(answer.get("service") or ""), series)


def _metric_evidence(block: dict) -> list[dict]:
    """``EvidenceRef(source="prometheus")`` per ``ok`` series that compared with a baseline: ratio ≥ 3 strong/70,
    a rise (≥ RATIO_ROSE, or from 0) medium/50, a steady metric weak/15 (it weakly rules a cause out)."""
    if block.get("outcome") != "ok":
        return []
    refs: list[dict] = []
    for item in block.get("series") or []:
        ratio = item.get("ratio")
        if ratio is None and not item.get("rose"):
            continue  # no baseline: nothing compared, nothing claimed
        strength: Literal["weak", "medium", "strong"]
        if ratio is not None and ratio >= 3.0:
            strength, contribution = "strong", 70
        elif item.get("rose"):
            strength, contribution = "medium", 50
        else:
            strength, contribution = "weak", 15
        refs.append(EvidenceRef(
            source="prometheus",
            ref_id=f"{block.get('service')}:{item.get('template_id')}",
            excerpt=str(item.get("sentence") or ""),
            strength=strength,
            contribution=contribution,
        ).as_legacy_dict())
    return refs


def _metrics_summary(blocks: list[dict]) -> str:
    return "; ".join(dict.fromkeys(b["sentence"] for b in blocks if b.get("sentence")))[:500]


def _grade_anomaly_evidence(anomaly_data: dict) -> tuple[str, int]:
    """Map a log-rate-anomaly result to (strength, contribution) from the ACTUAL
    spike magnitude, not a fixed ``medium``/80.

    A *detected* spike is graded by its ratio (current/baseline); a "no anomaly"
    result is WEAK evidence — the absence of a log-rate spike rules a cause out,
    it is not medium-strength support for one. Grading these identically was
    cosmetic: it made the evidence-weighting machinery inert. Degrades to weak
    when the fields are absent (e.g. Splunk disabled)."""
    if not anomaly_data.get("anomaly_detected"):
        return "weak", 15
    levels = anomaly_data.get("levels") or {}
    max_ratio = max(
        (float(v.get("ratio", 0) or 0) for v in levels.values() if isinstance(v, dict)),
        default=0.0,
    )
    if max_ratio >= 6.0:
        return "strong", 85
    return "medium", 60


def _grade_trace_evidence(trace_data: dict) -> tuple[str, int]:
    """Map a reconstructed trace to (strength, contribution) by whether it
    surfaced an actual error chain.

    An error-bearing trace (ERROR/FATAL steps) is real causal evidence and
    scales with the number of error events; a trace with only context steps (no
    errors) is weak, and an empty trace weaker still. Previously every trace was
    emitted at a fixed ``medium``/80 regardless of whether it found anything."""
    steps = trace_data.get("trace_steps") or []
    error_steps = [
        s for s in steps
        if isinstance(s, dict) and s.get("level") in ("ERROR", "FATAL")
    ]
    if error_steps:
        return ("strong" if len(error_steps) >= 3 else "medium"), min(90, 60 + len(error_steps) * 10)
    if steps:
        return "weak", 20  # context only — no error chain
    return "weak", 10      # empty trace


class LogIntelligenceAgent(BaseAgent):
    """
    Specialist for log-based evidence gathering.
    Called per cluster with context about the failing service and timestamp.

    Subclasses ``BaseAgent`` so the stage lands in the standard tables. It
    did not until 2026-08-24, which is why ``log_intelligence`` wrote no
    ``stage_started`` / ``stage_completed`` event at all: 0 of 6 on the
    deployment, against 6 of 6 for the compliant ``regression_watchman``.
    """

    stage_name = "log_intelligence"

    async def investigate(
        self,
        service_name: str,
        timestamp_utc: str,
        correlation_id: str = "",
        related_services: list[str] | None = None,
    ) -> dict:
        """
        Run trace reconstruction + log anomaly detection for a service/timestamp pair.
        Returns aggregated log evidence dict.
        """
        evidence = {}

        safe_service, _, _ = sanitize_reference_text(service_name, limit=200)
        safe_timestamp, _, _ = sanitize_reference_text(timestamp_utc, limit=80)
        safe_correlation, _, _ = sanitize_reference_text(correlation_id, limit=200)
        safe_related = []
        for item in related_services or [service_name]:
            safe_item, _, _ = sanitize_reference_text(item, limit=200)
            safe_related.append(safe_item)

        # 1. Reconstruct distributed trace
        try:
            if not tool_allowed("reconstruct_distributed_trace"):
                raise PermissionError("tool_not_allowed: reconstruct_distributed_trace")
            trace_json = await reconstruct_distributed_trace.ainvoke({
                "params_json": json.dumps({
                    "correlation_id": safe_correlation,
                    "timestamp_utc": safe_timestamp,
                    "services": safe_related,
                    "window_seconds": 45,
                })
            })
            trace_data = json.loads(trace_json)
            evidence["distributed_trace"] = sanitize_persistence_payload(trace_data)[0]
        except Exception as exc:
            logger.debug("trace_reconstruction_failed", error_type=type(exc).__name__)
            evidence["distributed_trace"] = {"error": type(exc).__name__}

        # 2. Detect log rate anomaly for primary service
        try:
            if not tool_allowed("detect_log_rate_anomaly"):
                raise PermissionError("tool_not_allowed: detect_log_rate_anomaly")
            anomaly_json = await detect_log_rate_anomaly.ainvoke({
                "params_json": json.dumps({
                    "service_name": safe_service,
                    "timestamp_utc": safe_timestamp,
                    "window_minutes": 10,
                    "baseline_days": 7,
                })
            })
            anomaly_data = json.loads(anomaly_json)
            evidence["log_anomaly"] = sanitize_persistence_payload(anomaly_data)[0]
        except Exception as exc:
            logger.debug("log_anomaly_detection_failed", error_type=type(exc).__name__)
            evidence["log_anomaly"] = {"error": type(exc).__name__}

        # Build a summary for the calling agent
        trace_summary = evidence.get("distributed_trace", {}).get("causal_summary", "Trace unavailable.")
        anomaly_assessment = evidence.get("log_anomaly", {}).get("assessment", "Anomaly check unavailable.")
        safe_trace_summary, _, _ = sanitize_reference_text(trace_summary, limit=500)
        safe_anomaly_assessment, _, _ = sanitize_reference_text(anomaly_assessment, limit=500)
        evidence["log_summary"] = (
            f"Trace: {safe_trace_summary} | Anomaly: {safe_anomaly_assessment}"
        )

        trace_ok = "error" not in evidence.get("distributed_trace", {})
        anomaly_ok = "error" not in evidence.get("log_anomaly", {})
        structured_evidence: list[EvidenceRef] = []
        if trace_ok:
            trace_strength, trace_contribution = _grade_trace_evidence(
                evidence.get("distributed_trace", {})
            )
            structured_evidence.append(EvidenceRef(
                source="distributed_trace",
                ref_id=safe_service,
                excerpt=safe_trace_summary,
                strength=trace_strength,
                contribution=trace_contribution,
            ))
        if anomaly_ok:
            anomaly_strength, anomaly_contribution = _grade_anomaly_evidence(
                evidence.get("log_anomaly", {})
            )
            structured_evidence.append(EvidenceRef(
                source="log_anomaly",
                ref_id=safe_service,
                excerpt=safe_anomaly_assessment,
                strength=anomaly_strength,
                contribution=anomaly_contribution,
            ))
        fallback_used = not (trace_ok and anomaly_ok)
        evidence["status"] = "complete" if not fallback_used else "failed"
        return validate_agent_contract(
            LogIntelligenceAgentOutput, evidence, agent_name="log_intelligence",
            agent_version="v1", fallback_used=fallback_used,
            structured_evidence=structured_evidence,
            decision_reason="log_evidence_gathered" if not fallback_used else "partial_log_evidence",
        )

    # ── Stage entry point ────────────────────────────────────────────────

    @staticmethod
    def _context(analysis: dict, run_data: dict) -> tuple[str, str, str, list[str]] | None:
        """Pull (service, timestamp, correlation, related) out of one analysis.

        Returns None when the two required fields are absent — log evidence is
        meaningless without a service and a failure timestamp to scope it to.
        """
        service = (
            analysis.get("service_name")
            or analysis.get("affected_service")
            or run_data.get("service_name")
        )
        timestamp = (
            analysis.get("timestamp_utc")
            or analysis.get("failed_at")
            or run_data.get("completed_at")
        )
        correlation = analysis.get("correlation_id") or analysis.get("trace_id") or ""
        related = analysis.get("related_services") or analysis.get("affected_services") or []
        if not service or not timestamp:
            return None
        safe_related = [str(item) for item in related] if isinstance(related, list) else []
        return str(service), str(timestamp), str(correlation), safe_related

    def _select_contexts(self, state: dict) -> list[tuple[str, tuple[str, str, str, list[str]]]]:
        """Pick at most five deterministic cluster representatives.

        The first implementation used the first analysis in the run, which could
        attribute one service's trace to every failure. Clusters are sorted by
        id and de-duplicated by (service, timestamp, correlation) so the same
        evidence is not gathered twice.
        """
        analyses = state.get("analyses") or {}
        first = next((item for item in analyses.values() if isinstance(item, dict)), {})
        run_data = state.get("test_run_data") or {}

        contexts: list[tuple[str, tuple[str, str, str, list[str]]]] = []
        seen: set[tuple[str, str, str]] = set()
        clusters = state.get("failure_clusters") or []
        candidates = sorted(
            (item for item in clusters if isinstance(item, dict)),
            key=lambda item: str(item.get("cluster_id") or ""),
        ) if isinstance(clusters, list) else []

        for index, cluster in enumerate(candidates[:5]):
            members = cluster.get("member_test_ids") or cluster.get("test_ids") or []
            members = sorted({str(item) for item in members}) if isinstance(members, list) else []
            representative = next(
                (analyses.get(str(m)) for m in members if isinstance(analyses.get(str(m)), dict)),
                first,
            )
            context = self._context(
                representative if isinstance(representative, dict) else {}, run_data
            )
            if context is None:
                continue
            key = (context[0], context[1], context[2])
            if key in seen:
                continue
            seen.add(key)
            contexts.append((str(cluster.get("cluster_id") or f"cluster_{index + 1}"), context))

        if not contexts:
            context = self._context(first, run_data)
            if context is not None:
                contexts.append(("run", context))
        return contexts

    # ── W23d AGT-15: application metrics (deterministic -- no model decides; no prompt) ──

    @staticmethod
    async def _metrics_for(service: str, timestamp: str, runtime_cfg: Any) -> dict:
        """One cluster's ``app_metrics``: the tool when the frozen config allows it, else ``tool_not_allowed``
        with no call; an unreadable integration config is ``config_unavailable`` (the tool is not asked to
        resolve it again). The outcome never changes the cluster's status."""
        if not tool_allowed("fetch_app_metrics"):
            return _metrics_block("tool_not_allowed", pv.OUTCOME_SENTENCES["tool_not_allowed"])
        if runtime_cfg is None:
            return _metrics_block("config_unavailable", pv.OUTCOME_SENTENCES["config_unavailable"])
        try:
            raw = await fetch_app_metrics.ainvoke({"params_json": json.dumps({
                "service_name": service, "timestamp_utc": timestamp, "window_minutes": METRICS_WINDOW_MINUTES,
            })})
            return _metrics_block_from_answer(json.loads(raw))
        except Exception as exc:  # noqa: BLE001 - a metrics lookup never fails the stage
            logger.debug("app_metrics_lookup_failed", error_type=type(exc).__name__)
            return _metrics_block("http_error", METRICS_LOOKUP_FAILED)

    @staticmethod
    async def _metric_targets_from_cases(state: dict) -> list[tuple[str, str, str]]:
        """M-4a: (cluster id, service, timestamp) from the failures' stored ``TestCase.service_name`` when no
        analysis named a service. ONE bounded select on a short session, closed before any HTTP; at most
        ``CLUSTERS_QUERIED_MAX`` targets (one per cluster representative, else one for the run)."""
        failed = [str(item) for item in (state.get("failed_test_ids") or [])][:200]
        ids: list[uuid.UUID] = []
        for item in failed:
            try:
                ids.append(uuid.UUID(item))
            except ValueError:
                continue
        try:
            run_uuid = uuid.UUID(str(state.get("test_run_id") or ""))
        except ValueError:
            return []
        if not ids:
            return []
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import TestCase

        try:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(TestCase.id, TestCase.service_name, TestCase.finished_at).where(
                        TestCase.test_run_id == run_uuid, TestCase.id.in_(ids), TestCase.service_name.is_not(None),
                    )
                )).all()
        except Exception as exc:  # noqa: BLE001 - no target is "no_context", never a failed stage
            logger.debug("app_metrics_targets_failed", error_type=type(exc).__name__)
            return []
        completed_at = (state.get("test_run_data") or {}).get("completed_at")
        by_id: dict[str, tuple[str, str]] = {}
        for case_id, service, finished_at in rows:
            stamp = finished_at.isoformat() if finished_at is not None else completed_at
            if service and stamp:
                by_id[str(case_id)] = (str(service), str(stamp))

        targets: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        clusters = state.get("failure_clusters") or []
        candidates = sorted((c for c in clusters if isinstance(c, dict)),
                            key=lambda c: str(c.get("cluster_id") or "")) if isinstance(clusters, list) else []
        for index, cluster in enumerate(candidates[:5]):
            members = cluster.get("member_test_ids") or cluster.get("test_ids") or []
            members = sorted({str(m) for m in members}) if isinstance(members, list) else []
            found = next((by_id[m] for m in members if m in by_id), None)
            if found is None or found in seen:
                continue
            seen.add(found)
            targets.append((str(cluster.get("cluster_id") or f"cluster_{index + 1}"), *found))
        if not targets:
            first = next((by_id[m] for m in failed if m in by_id), None)
            if first is not None:
                targets.append(("run", *first))
        return targets[: pv.CLUSTERS_QUERIED_MAX]

    async def _metrics_without_context(self, state: dict) -> tuple[dict, str, list[dict]]:
        """The no-context branch: (first block, summary, evidence refs). No failure names a service → the
        ``no_context`` sentence with no session and no call."""
        targets = await self._metric_targets_from_cases(state)
        if not targets:
            block = _metrics_block("no_context", pv.OUTCOME_SENTENCES["no_context"])
            return block, block["sentence"], []
        from app.services.integration_config_service import runtime_integrations_for
        from app.tools.investigation_context import bind_runtime_integrations, reset_runtime_integrations

        runtime_cfg = await runtime_integrations_for(state.get("project_id"))
        token = bind_runtime_integrations(runtime_cfg)
        blocks: list[dict] = []
        try:
            for _cluster_id, service, timestamp in targets:
                blocks.append(await self._metrics_for(service, timestamp, runtime_cfg))
        finally:
            reset_runtime_integrations(token)
        refs = [ref for block in blocks for ref in _metric_evidence(block)][:20]
        return blocks[0], _metrics_summary(blocks), refs

    async def run(self, state: dict) -> dict:
        """Stage entry point: lifecycle around per-cluster log investigation.

        Returns the workflow delta the ``log_intelligence`` node used to build
        inline. The lifecycle calls are what put the stage in the pipeline
        timeline, the OTEL trace and the Prometheus histograms; before this the
        stage produced findings while recording that it never ran.
        """
        pipeline_run_id = str(state.get("pipeline_run_id") or "")
        await self.mark_stage_running(pipeline_run_id, input_keys=sorted(state))

        contexts = self._select_contexts(state)

        if not contexts:
            # Ran, looked, found nothing to look at. That is a result, and it
            # has to be stated -- an absent contract reads as a lost record and
            # fails the decision critic closed (#842).
            findings: dict[str, Any] = {
                "status": "not_enough_evidence",
                "distributed_trace": {},
                "log_anomaly": {},
                "cluster_findings": [],
                "log_summary": "Log evidence requires a scoped service and failure timestamp.",
            }
            # W23d AGT-15 (M-4a): the metrics context is separate -- a failure's stored service name still
            # scopes a Prometheus lookup; the stage's status stays the trace / anomaly verdict.
            app_metrics, metrics_summary, metric_refs = await self._metrics_without_context(state)
            findings["app_metrics"] = app_metrics
            findings["metrics_summary"] = metrics_summary
            if metric_refs:
                findings["evidence_refs"] = metric_refs
            await self.log_decision(
                pipeline_run_id,
                decision_point="log_evidence_scope",
                chosen="not_enough_evidence",
                rationale="no analysis carried both a service name and a failure timestamp",
            )
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={"status": "not_enough_evidence", "cluster_count": 0},
                confidence_score=0,
                evidence_count=0,
                fallback_reason="no_log_context",
            )
            contracted = degraded_contract(
                LogIntelligenceAgentOutput,
                {"log_findings": findings},
                agent_name=self.stage_name,
                decision_reason="no_log_context",
            )
            return {
                "log_findings": contracted.get("log_findings", findings),
                "agent_contracts": contracted.get("agent_contracts", {}),
                "completed_stages": [self.stage_name],
                "current_stage": "gap_detection",
                "errors": [],
            }

        # W10 INT-01 / INT-03: the two Splunk tools below run server-side with
        # no InvestigationContext, so the Splunk settings saved in the UI (and
        # the project's index) are bound for them on a standalone ContextVar,
        # resolved once here (a bound config, the 30 s cache, or one short
        # session closed before any HTTP). Tokens stay in process memory: the
        # config never enters workflow state, so no checkpoint carries it.
        from app.services.integration_config_service import runtime_integrations_for
        from app.tools.investigation_context import (
            bind_runtime_integrations,
            reset_runtime_integrations,
        )

        runtime_cfg = await runtime_integrations_for(state.get("project_id"))
        integrations_token = bind_runtime_integrations(runtime_cfg)
        cluster_results: list[dict] = []
        try:
            for cluster_id, (service, timestamp, correlation, related) in contexts:
                try:
                    result = await self.investigate(service, timestamp, correlation, related)
                except Exception as exc:  # noqa: BLE001 - preserve cluster-level degradation
                    result = {
                        "status": "failed",
                        "distributed_trace": {"error": type(exc).__name__},
                        "log_anomaly": {},
                        "log_summary": "Log specialist failed for this cluster.",
                    }
                safe_result, _ = sanitize_persistence_payload(result if isinstance(result, dict) else {})
                safe_cluster_id, _, _ = sanitize_reference_text(cluster_id, limit=64)
                cluster_results.append({
                    "cluster_id": safe_cluster_id,
                    "status": safe_result.get("status", "failed"),
                    "distributed_trace": safe_result.get("distributed_trace") or {},
                    "log_anomaly": safe_result.get("log_anomaly") or {},
                    "log_summary": str(safe_result.get("log_summary") or "")[:500],
                    "evidence_refs": list(safe_result.get("evidence_refs") or [])[:20],
                })
            # W23d AGT-15 (M-4): application metrics for the first CLUSTERS_QUERIED_MAX contexts, called by code
            # (small models skip tools), with the same (service, timestamp) and the same bound config.
            for index, (_cluster_id, (service, timestamp, _corr, _rel)) in enumerate(
                contexts[: pv.CLUSTERS_QUERIED_MAX]
            ):
                block = await self._metrics_for(service, timestamp, runtime_cfg)
                cluster_results[index]["app_metrics"] = block
                cluster_results[index]["evidence_refs"] = (
                    cluster_results[index]["evidence_refs"] + _metric_evidence(block)
                )[:20]
        finally:
            reset_runtime_integrations(integrations_token)

        # Reuse the first cluster result for the legacy top-level fields without
        # issuing a duplicate provider/tool call.
        result = dict(cluster_results[0])
        result["cluster_findings"] = [dict(item) for item in cluster_results]
        result["cluster_count"] = len(cluster_results)
        result["evidence_refs"] = [
            ref for item in cluster_results for ref in item.get("evidence_refs", [])
        ][:20]
        statuses = [item["status"] for item in cluster_results]
        if all(item == "complete" for item in statuses):
            result["status"] = "complete"
        elif all(item == "not_enough_evidence" for item in statuses):
            result["status"] = "not_enough_evidence"
        else:
            result["status"] = "failed"
        result["log_summary"] = " | ".join(
            item["log_summary"] for item in cluster_results if item["log_summary"]
        )[:1000]
        # W23d AGT-15: ``app_metrics`` (copied above from the first cluster -- always queried) and the distinct
        # metric sentences. They never change ``status``.
        result["metrics_summary"] = _metrics_summary(
            [item["app_metrics"] for item in cluster_results if "app_metrics" in item]
        )

        complete = result.get("status") == "complete"
        evidence_refs = list(result.get("evidence_refs") or [])[:20]
        reason = "log_evidence_evaluated" if result.get("log_summary") else "no_log_evidence"

        await self.log_decision(
            pipeline_run_id,
            decision_point="log_evidence_scope",
            chosen=str(result.get("status") or "unknown"),
            rationale=f"investigated {len(cluster_results)} cluster representative(s)",
            context={"evidence_refs": len(evidence_refs)},
        )
        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "status": result.get("status"),
                "cluster_count": len(cluster_results),
            },
            confidence_score=70 if complete else 0,
            evidence_count=len(evidence_refs),
            fallback_reason=None if complete else reason,
        )

        contracted = validate_agent_contract(
            LogIntelligenceAgentOutput,
            {"log_findings": result},
            agent_name=self.stage_name,
            fallback_used=not complete,
            confidence=70 if complete else 0,
            evidence_refs=evidence_refs,
            decision_reason=reason,
        )
        return {
            "log_findings": contracted.get("log_findings", result),
            "agent_contracts": contracted.get("agent_contracts", {}),
            "completed_stages": [self.stage_name],
            "current_stage": "gap_detection",
            "errors": [],
        }

    def disabled_delta(self) -> dict:
        """Delta for the flag-disabled branch — a skip, not a run.

        No lifecycle call: the stage genuinely did not execute, so it must not
        appear in the timeline as if it had. It still emits a contract (#842).
        """
        findings = {
            "status": "not_enough_evidence",
            "distributed_trace": {},
            "log_anomaly": {},
            "cluster_findings": [],
            "log_summary": "Log Intelligence feature flag is disabled.",
        }
        contracted = degraded_contract(
            LogIntelligenceAgentOutput,
            {"log_findings": findings},
            agent_name=self.stage_name,
            decision_reason="no_log_intelligence_flag_disabled",
        )
        return {
            "log_findings": contracted.get("log_findings", findings),
            "agent_contracts": contracted.get("agent_contracts", {}),
            "skipped_stages": [self.stage_name],
            "completed_stages": [self.stage_name],
            "current_stage": "gap_detection",
            "errors": [],
        }
