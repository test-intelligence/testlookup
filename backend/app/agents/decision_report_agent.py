"""Terminal synthesis for the deep test-intelligence workflow.

The existing SummaryAgent intentionally runs early so triage can consume a
compact briefing.  A deep run, however, continues through flaky, test-health,
and release-risk specialists.  This agent runs after those specialists and
publishes the decision-grade view without asking an LLM to recompute facts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agents.base import BaseAgent
from app.models.agent_contracts import (
    DecisionClaimV1,
    DecisionReportAgentOutput,
    ProposedActionV1,
    validate_agent_contract,
)
from app.services.canonical_json import stable_json_sha256
from app.services.decision_evidence_snapshot import persist_decision_evidence_snapshot
from app.services.evidence_sanitizer import sanitize_persistence_payload
from app.services.evidence_artifact_service import capture_authorized_evidence_artifacts
from app.services.run_evidence_bundle import build_run_evidence_bundle

logger = structlog.get_logger("agents.decision_report")

DECISION_REPORT_SCHEMA_VERSION = 1
SUMMARY_DOCUMENT_SCHEMA_VERSION = 5
DECISION_EVIDENCE_FIELDS = (
    "schema_version",
    "status",
    "metrics",
    "metric_snapshot",
    "run_evidence_bundle_sha256",
    "failure_clusters",
    "deep_findings",
    "flaky_findings",
    "test_health_findings",
    "contract_findings",
    "log_findings",
    "regression_classification",
    "change_ownership_findings",
    "cluster_investigation_results",
    "release_decision",
    "quality_review",
    "source_stages",
)


def _stable_hash(value: Any) -> str:
    return stable_json_sha256(value)


def compute_decision_evidence_hash(decision: dict[str, Any]) -> str:
    """Fingerprint only evidence-bearing fields, excluding timestamps/review metadata."""
    return _stable_hash(decision_evidence_projection(decision))


def decision_evidence_projection(decision: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical payload the critic compares and fingerprints."""
    return {field: deepcopy(decision.get(field)) for field in DECISION_EVIDENCE_FIELDS}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]



_METRIC_REF = {
    "type": "metric",
    "id": "metric_snapshot",
    "definition_version": "run_metrics_v1",
    "freshness": "run",
}


def _claim_evidence(
    evidence_sha: str, supporting: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A provenance anchor plus the evidence that supports THIS claim.

    The anchor (the signed decision-evidence hash) is legitimately common to
    every claim: it says *which bundle this report was computed from*. What
    follows it must differ per claim, because that is the part a reader treats
    as support.

    Every claim used to carry one identical array -- the anchor, the first five
    authorized artifacts, and the metric snapshot -- so the claim-evidence
    drawer showed bundle-level provenance as though it were claim-level.
    Measured on the homelab 2026-08-22: 10 of 10 published reports had every
    claim sharing a byte-identical evidence list. Coverage read 100% while no
    claim cited evidence chosen for it.
    """
    return [
        {"type": "decision_evidence", "id": evidence_sha, "freshness": "run"},
        *supporting,
    ]


def _artifact_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "artifact",
        "id": item.get("artifact_id") or item.get("evidence_id"),
        "evidence_id": item.get("evidence_id"),
        "source": item.get("source"),
        "kind": item.get("kind"),
        "excerpt": item.get("excerpt"),
        "checksum_sha256": item.get("checksum_sha256"),
        "scope": item.get("scope"),
        "freshness": item.get("freshness"),
        "sensitivity": item.get("sensitivity"),
    }


def _build_typed_claims(
    metrics: dict[str, Any],
    release: dict[str, Any],
    quality: dict[str, Any],
    source_stages: list[str],
    evidence_sha: str,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Derive bounded, deterministic facts/inferences/unknowns.

    Each claim cites what actually supports it. Authorized tool-observation
    artifacts are NOT attached to the aggregate claims below -- none of them is
    about a specific test, so listing five arbitrary observations under "N of M
    tests failed" was noise wearing the costume of evidence. They get their own
    claim instead, which is a statement those artifacts genuinely support.
    """
    artifacts = [_artifact_ref(i) for i in (evidence_refs or [])[:5] if isinstance(i, dict)]
    claims: list[dict[str, Any]] = []

    total = metrics.get("total_tests")
    failed = metrics.get("failed_tests")
    if isinstance(total, (int, float)) and isinstance(failed, (int, float)):
        claims.append(DecisionClaimV1(
            claim_id="fact.metrics.test_outcome",
            kind="fact",
            text=f"{int(failed)} of {int(total)} tests failed.",
            confidence=1.0,
            confidence_basis="deterministic_run_metrics",
            # The metric snapshot IS the evidence for a metric claim.
            evidence=_claim_evidence(evidence_sha, [_METRIC_REF]),
            source_stage="deterministic_run_metrics",
            freshness="run",
        ).model_dump())

    recommendation = release.get("recommendation")
    if recommendation:
        blockers = release.get("blocking_issues") or []
        claims.append(DecisionClaimV1(
            claim_id="inference.release.recommendation",
            kind="inference",
            text=f"Release recommendation is {recommendation}.",
            confidence=0.95 if not quality.get("contradictions") else 0.65,
            confidence_basis="release_risk_policy_evaluation",
            # A policy verdict is supported by the policy and the metrics it
            # evaluated -- not by whichever tool observations happened to be
            # captured first.
            evidence=_claim_evidence(evidence_sha, [
                _METRIC_REF,
                {
                    "type": "policy",
                    "id": "release_risk_policy",
                    "source_stage": "release_risk",
                    "freshness": "run",
                },
            ]),
            counter_evidence=[{"type": "contradiction", "count": len(quality.get("contradictions") or [])}] if quality.get("contradictions") else [],
            source_stage="release_risk",
            freshness="run",
            hypothesis=bool(quality.get("contradictions")),
        ).model_dump())
        if blockers:
            claims.append(DecisionClaimV1(
                claim_id="fact.release.blockers",
                kind="fact",
                text=f"{len(blockers)} release blocker(s) were recorded.",
                confidence=1.0,
                confidence_basis="release_risk_policy_evaluation",
                # Cite the blockers themselves, so a reader can see WHICH.
                evidence=_claim_evidence(evidence_sha, [
                    {
                        "type": "release_blocker",
                        "id": f"release_blocker.{index}",
                        "excerpt": str(blocker)[:200],
                        "source_stage": "release_risk",
                        "freshness": "run",
                    }
                    for index, blocker in enumerate(blockers[:5])
                ]),
                source_stage="release_risk",
                freshness="run",
            ).model_dump())

    missing = quality.get("missing_or_failed_specialists") or []
    if missing:
        claims.append(DecisionClaimV1(
            claim_id="unknown.specialists.missing",
            kind="unknown",
            text="Some specialist evidence is unavailable, so the report is degraded.",
            confidence=1.0,
            confidence_basis="terminal_completeness_check",
            # Name the absent stages. "Some evidence is unavailable" with a
            # generic bundle attached is unactionable; naming them is not.
            evidence=_claim_evidence(evidence_sha, [
                {
                    "type": "missing_specialist",
                    "id": str(name),
                    "source_stage": "decision_report",
                    "freshness": "run",
                }
                for name in list(missing)[:8]
            ]),
            source_stage="decision_report",
            freshness="run",
        ).model_dump())

    if artifacts:
        # The artifacts get a claim they actually support, rather than being
        # stapled to claims they do not.
        claims.append(DecisionClaimV1(
            claim_id="fact.evidence.captured",
            kind="fact",
            text=f"{len(artifacts)} authorized evidence artifact(s) were captured for this run.",
            confidence=1.0,
            confidence_basis="authorized_evidence_capture",
            evidence=_claim_evidence(evidence_sha, artifacts),
            source_stage="decision_report",
            freshness="run",
        ).model_dump())

    return claims[:20]


def _build_proposed_actions(
    release: dict[str, Any],
    claims: list[dict[str, Any]],
    evidence_sha: str,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for index, text in enumerate((release.get("conditions_for_go") or [])[:5]):
        actions.append(ProposedActionV1(
            action_id=f"release.condition.{index + 1}",
            title="Resolve release condition",
            owner="release_owner",
            rationale=str(text)[:500],
            evidence=[{"type": "decision_evidence", "id": evidence_sha}],
            risk="medium",
            required_permission="release_review",
            idempotency_key=f"release-condition:{evidence_sha}:{index + 1}",
        ).model_dump())
    for index, text in enumerate((release.get("blocking_issues") or [])[:5]):
        actions.append(ProposedActionV1(
            action_id=f"release.blocker.{index + 1}",
            title="Investigate release blocker",
            owner="release_owner",
            rationale=str(text)[:500],
            evidence=[{"type": "decision_evidence", "id": evidence_sha}],
            risk="high",
            required_permission="release_review",
            idempotency_key=f"release-blocker:{evidence_sha}:{index + 1}",
        ).model_dump())
    return actions[:10]
def build_decision_intelligence(state: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic terminal decision section from specialist state."""
    redacted_strings = 0
    truncated_strings = 0
    omitted_items = 0

    def sanitized(value: Any) -> Any:
        nonlocal redacted_strings, truncated_strings, omitted_items
        safe, stats = sanitize_persistence_payload(value)
        redacted_strings += stats.redacted_strings
        truncated_strings += stats.truncated_strings
        omitted_items += stats.omitted_items
        return safe

    # The draft must never share mutable evidence with workflow state. The
    # terminal critic rebuilds its authority from that state, so aliasing here
    # would let draft mutation silently rewrite the critic's source of truth.
    flaky = _as_dict_list(sanitized(state.get("flaky_findings") or []))
    health = _as_dict_list(sanitized(state.get("test_health_findings") or []))
    contract_findings = _as_dict(sanitized(state.get("contract_findings") or {}))
    log_findings = _as_dict(sanitized(state.get("log_findings") or {}))
    regression_classification = _as_dict(sanitized(state.get("regression_classification") or {}))
    change_ownership = _as_dict(sanitized(state.get("change_ownership_findings") or {}))
    cluster_investigations = _as_dict(
        sanitized(state.get("cluster_investigation_results") or {})
    )
    clusters = _as_dict_list(sanitized(state.get("failure_clusters") or []))
    deep_findings = _as_dict(sanitized(state.get("deep_findings") or {}))
    if clusters and not deep_findings:
        # The legacy persistence adapter runs after LangGraph completes. Build
        # the same honest cluster aggregates here so terminal synthesis can
        # consume them; the adapter remains responsible for relational writes.
        from app.agents.deep_persistence import synthesize_deep_findings

        deep_findings = _as_dict(sanitized(synthesize_deep_findings(state)))
    release = _as_dict(sanitized(state.get("release_decision") or {}))
    refined = _as_dict(sanitized(state.get("refined_report") or {}))
    gap = _as_dict(sanitized(state.get("gap_report") or {}))
    stage_errors = _as_dict(state.get("stage_errors"))

    selected_sources = [
        "deterministic_run_metrics",
        "failure_clustering",
        "flaky_sentinel",
        "test_health",
        "release_risk",
    ]
    if contract_findings:
        selected_sources.append("contract_validation")
    if log_findings:
        selected_sources.append("log_intelligence")
    if regression_classification:
        selected_sources.append("regression_watchman")
    if change_ownership:
        selected_sources.append("change_ownership")
    if cluster_investigations:
        selected_sources.append("cluster_investigation_join")
    if gap:
        selected_sources.append("gap_detection")
    if refined:
        selected_sources.append("report_refinement")

    missing: list[str] = []
    if not release:
        missing.append("release_risk")
    if state.get("failed_test_ids") and not clusters:
        missing.append("failure_clustering")
    if (
        cluster_investigations
        and cluster_investigations.get("status") in {"degraded", "pending"}
    ):
        missing.append("cluster_investigation")
    if (
        state.get("contract_agent_enabled")
        and state.get("failed_test_ids")
        and contract_findings.get("status") == "failed"
    ):
        missing.append("contract_validation")
    if (state.get("log_intelligence_enabled") and state.get("failed_test_ids") and log_findings.get("status") == "failed"):
        missing.append("log_intelligence")
    if (state.get("regression_watchman_enabled") and state.get("failure_clusters") and not regression_classification):
        missing.append("regression_watchman")
    if state.get("change_ownership_enabled") and state.get("failed_test_ids") and change_ownership.get("status") == "failed":
        missing.append("change_ownership")
    if stage_errors:
        missing.extend(sorted(str(name) for name in stage_errors))
    missing = list(dict.fromkeys(missing))

    preliminary = _as_dict(state.get("structured_summary"))
    preliminary_incident = _as_dict(preliminary.get("layer2_incident_view"))
    contradictions: list[dict[str, Any]] = []
    preliminary_release = preliminary_incident.get("release_impact")
    final_release = release.get("recommendation")
    if preliminary_release and final_release and preliminary_release != final_release:
        contradictions.append({
            "type": "preliminary_vs_policy_release_decision",
            "preliminary": preliminary_release,
            "final": final_release,
            "resolution": "prefer_final_release_policy",
        })

    generated_at = datetime.now(timezone.utc).isoformat()
    run_evidence_bundle = build_run_evidence_bundle(state)
    metric_snapshot = deepcopy(run_evidence_bundle["metric_snapshot"])
    metrics = deepcopy(metric_snapshot["values"])
    data_quality_flags = deepcopy(run_evidence_bundle["quality_flags"])
    evidence_sha = run_evidence_bundle["content_sha256"]
    if redacted_strings:
        data_quality_flags.append({
            "code": "canonical_payload_redacted",
            "severity": "warning",
            "detail": f"Sensitive patterns were redacted from {redacted_strings} persisted strings",
        })
    if truncated_strings or omitted_items:
        data_quality_flags.append({
            "code": "canonical_payload_truncated",
            "severity": "error",
            "detail": (
                f"Persistence bounds truncated {truncated_strings} strings and omitted "
                f"{omitted_items} payload items"
            ),
        })
    has_quality_errors = any(item.get("severity") == "error" for item in data_quality_flags)
    report_status = "degraded" if missing or has_quality_errors else "complete"

    claims = _build_typed_claims(metrics, release, {"contradictions": contradictions, "missing_or_failed_specialists": missing}, selected_sources, evidence_sha, _as_dict_list(run_evidence_bundle.get("evidence_refs")))
    proposed_actions = _build_proposed_actions(release, claims, evidence_sha)

    decision = {
        "schema_version": DECISION_REPORT_SCHEMA_VERSION,
        "status": report_status,
        "generated_at": generated_at,
        "metrics": metrics,
        "metric_snapshot": metric_snapshot,
        "run_evidence_bundle_sha256": run_evidence_bundle["content_sha256"],
        "failure_clusters": clusters,
        "deep_findings": deep_findings,
        "flaky_findings": flaky,
        "test_health_findings": health,
        "contract_findings": contract_findings or None,
        "log_findings": log_findings or None,
        "regression_classification": regression_classification or None,
        "change_ownership_findings": change_ownership or None,
        "cluster_investigation_results": cluster_investigations or None,
        "release_decision": release or None,
        "quality_review": {
            "missing_or_failed_specialists": missing,
            "contradictions": contradictions,
            "gap_report": gap or None,
            "refined_report": refined or None,
            "data_quality_flags": data_quality_flags,
            "requires_human_review": bool(missing or contradictions or data_quality_flags),
        },
        "source_stages": selected_sources,
        "claims": claims,
        "proposed_actions": proposed_actions,
        "verification": {"status": "pending", "checks": [], "repairs": []},
    }
    decision["evidence_bundle_sha256"] = compute_decision_evidence_hash(decision)
    return decision


def _coverage_line(quality: dict) -> str:
    """How much of the failure set this recommendation actually rests on.

    ``gap_detection_agent`` computes this for every deep workflow and it was
    threaded into ``quality_review.gap_report`` and then read by nothing --
    not this markdown, not ``report_status``, not ``requires_human_review``,
    not the UI. A release decision resting on 1 analysed failure out of 50
    rendered identically to one resting on all 50.

    Absent gap data reports "not recorded" rather than a fabricated 0/0:
    reports generated before the field was published carry none, and that is
    not the same as full coverage.
    """
    gap = quality.get("gap_report")
    if not isinstance(gap, dict) or not gap:
        return "**Analysis coverage:** not recorded"
    try:
        failed = int(gap.get("failed_count") or 0)
        analyzed = int(gap.get("analyzed_count") or 0)
        skipped = int(gap.get("skipped_count") or 0)
        errored = int(gap.get("errored_count") or 0)
    except (TypeError, ValueError):
        return "**Analysis coverage:** not recorded"
    if gap.get("integrity_ok") is False:
        return (
            "**Analysis coverage:** DID NOT RECONCILE -- "
            f"{analyzed} analysed + {skipped + errored} unanalysed does not "
            f"account for {failed} failure(s); treat coverage as unknown"
        )
    uncovered = skipped + errored
    if uncovered:
        return (
            f"**Analysis coverage:** {analyzed}/{failed} failures analysed; "
            f"{uncovered} never analysed"
        )
    return f"**Analysis coverage:** {analyzed}/{failed} failures analysed"


def render_decision_markdown(decision: dict[str, Any]) -> str:
    metrics = _as_dict(decision.get("metrics"))
    release = _as_dict(decision.get("release_decision"))
    quality = _as_dict(decision.get("quality_review"))
    recommendation = release.get("recommendation") or "UNAVAILABLE"
    risk_score = release.get("risk_score")
    risk_text = f"{recommendation} (risk {risk_score}/100)" if risk_score is not None else recommendation
    missing = quality.get("missing_or_failed_specialists") or []
    verification = _as_dict(decision.get("verification"))
    verification_status = str(verification.get("status") or "pending").upper()
    repairs = verification.get("repairs") or []
    unresolved = verification.get("unresolved_failures") or []
    return "\\n\\n".join([
        "## Final Decision Intelligence",
        f"**Report status:** {str(decision.get('status') or 'degraded').upper()}",
        f"**Release recommendation:** {risk_text}",
        (
            "**Specialist findings:** "
            f"{metrics.get('failure_cluster_count', 0)} failure clusters, "
            f"{metrics.get('flaky_finding_count', 0)} flaky findings, and "
            f"{metrics.get('test_health_finding_count', 0)} test-health findings."
        ),
        "**Missing or failed specialists:** " + (", ".join(map(str, missing)) if missing else "None"),
        _coverage_line(quality),
        f"**Independent verification:** {verification_status}",
        "**Verification repairs:** " + (", ".join(map(str, repairs)) if repairs else "None"),
        "**Unresolved verification failures:** "
        + (", ".join(map(str, unresolved)) if unresolved else "None"),
        f"**Evidence bundle:** `{decision.get('evidence_bundle_sha256')}`",
    ])


class DecisionReportAgent(BaseAgent):
    """Publish the final, specialist-complete report for a deep workflow."""

    stage_name = "decision_report"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        pipeline_run_id = str(state["pipeline_run_id"])
        project_id = str(state["project_id"])
        await self.mark_stage_running(pipeline_run_id, input_keys=list(state.keys()))
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Synthesizing final decision report…"},
        )

        try:
            authorized_artifacts, authorization_errors = (
                await capture_authorized_evidence_artifacts(state)
            )
            evidence_state = {
                **state,
                "authorized_evidence_artifacts": authorized_artifacts,
                "evidence_authorization_errors": authorization_errors,
            }
            decision = build_decision_intelligence(evidence_state)
            quality_review = _as_dict(decision.get("quality_review"))
            structured = deepcopy(_as_dict(state.get("structured_summary")))
            structured["decision_intelligence"] = decision
            preliminary_markdown = str(state.get("summary_markdown") or "").strip()
            final_markdown = "\\n\\n".join(
                part for part in (preliminary_markdown, render_decision_markdown(decision)) if part
            )
            confidence = 70 if decision["status"] == "degraded" else 95
            contracted = validate_agent_contract(
                DecisionReportAgentOutput,
                {
                    "structured_summary": structured,
                    "summary_markdown": final_markdown,
                    "decision_intelligence": decision,
                    "decision_evidence_snapshot": {"status": "pending"},
                    "completed_stages": [self.stage_name],
                    "errors": [],
                    "current_stage": "decision_report_critic",
                },
                agent_name=self.stage_name,
                confidence=confidence,
                evidence_refs=[
                    {"type": "decision_evidence_bundle", "id": decision["evidence_bundle_sha256"]},
                    {"type": "release_decision", "id": str(state.get("test_run_id"))},
                ],
                fallback_used=decision["status"] == "degraded",
                decision_reason=f"terminal_specialist_synthesis_{decision['status']}",
            )
            decision_contract = _as_dict(contracted.get("agent_contracts")).get(
                self.stage_name
            )
            if not isinstance(decision_contract, dict):
                raise RuntimeError("decision report contract validation did not produce metadata")
            snapshot_state = {
                **evidence_state,
                "agent_contracts": {
                    **deepcopy(_as_dict(state.get("agent_contracts"))),
                    self.stage_name: deepcopy(decision_contract),
                },
            }
            snapshot = await persist_decision_evidence_snapshot(
                snapshot_state,
                decision_evidence_projection(decision),
            )
            contracted["decision_evidence_snapshot"] = snapshot
            await self.log_decision(
                pipeline_run_id,
                "terminal_report_status",
                str(decision["status"]),
                (
                    "all required specialist outputs available"
                    if decision["status"] == "complete"
                    else "report published with explicit missing or failed specialist outputs"
                ),
                alternatives=["complete", "degraded"],
                context={
                    "missing_or_failed_specialists": quality_review.get(
                        "missing_or_failed_specialists", []
                    ),
                    "contradiction_count": len(quality_review.get("contradictions") or []),
                },
            )
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "report_status": decision["status"],
                    "evidence_bundle_sha256": decision["evidence_bundle_sha256"],
                    "evidence_snapshot_sha256": snapshot["content_sha256"],
                    "source_stages": decision["source_stages"],
                    "authorized_artifact_count": len(authorized_artifacts),
                },
                confidence_score=confidence,
                evidence_count=len(decision["source_stages"]),
            )
            await self.broadcast_progress(
                project_id,
                {"status": "completed", "message": "Decision report draft ready for verification"},
            )
            return contracted
        except Exception as exc:
            error = f"Decision report agent error: {exc}"
            logger.error("decision_report_failed", error=str(exc), exc_info=True)
            await self.mark_stage_done(pipeline_run_id, error=error)
            return validate_agent_contract(
                DecisionReportAgentOutput,
                {
                    "decision_intelligence": None,
                    "completed_stages": [self.stage_name],
                    "errors": [error],
                    "current_stage": "decision_report_critic",
                },
                agent_name=self.stage_name,
                confidence=0,
                fallback_used=True,
                decision_reason="terminal_synthesis_exception",
            )
