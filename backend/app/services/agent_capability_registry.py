"""Canonical capability metadata for the existing agent workflow stages.

Every entry declares how it is executed via ``execution``:

* ``planned``       — a workflow type lists it in its stage order (the default);
* ``child_spawned`` — dispatched per cluster, never top-level (``cluster_investigation``);
* ``on_demand``     — reachable only through its own endpoint (none today;
  ``defect_commander`` was the example until it was wired into the deep
  pipeline behind a default-off flag, and it still serves
  ``POST /api/v1/agents/defect-command`` as well);
* ``runtime``       — a pseudo-capability for runtime bookkeeping (``workflow``).

The ``agents.capability-has-executor`` gate holds the two halves together: a
``planned`` capability must appear in a stage list, and one that appears in a
stage list must not claim to be anything else. It is what forced this entry to
change when ``defect_commander`` moved into ``_DEEP_STAGES``: leaving it as
``on_demand`` fails the gate with "the declaration contradicts the planner".
"""
from __future__ import annotations

from app.models.agentic_runtime import CapabilitySpecV1


def _capability(
    stage: str,
    *,
    inputs: str,
    output: str,
    dependencies: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    latency_ms: int = 5_000,
    cost_usd: float = 0.01,
    timeout_seconds: int = 60,
    fallback: str = "deterministic_degraded_output",
    concurrency_class: str = "analysis",
    permission: str = "read_only",
    max_retries: int = 0,
    execution: str = "planned",
) -> CapabilitySpecV1:
    return CapabilitySpecV1(
        capability_id=f"agent.{stage}.v1",
        stage_name=stage,
        input_schema=inputs,
        output_schema=output,
        required_evidence=evidence,
        dependencies=dependencies,
        expected_latency_ms=latency_ms,
        expected_cost_usd=cost_usd,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        fallback=fallback,
        concurrency_class=concurrency_class,
        permission=permission,
        execution=execution,
    )


_SPECS = (
    _capability("ingestion", inputs="TestRun", output="RunEvidenceBundleV1", latency_ms=2_000, cost_usd=0, fallback="fail_closed"),
    _capability("anomaly_detection", inputs="RunEvidenceBundleV1", output="AnomalyAgentOutput", dependencies=("ingestion",), evidence=("metric_snapshot",)),
    _capability("failure_clustering", inputs="RunEvidenceBundleV1", output="FailureClusterOutput", dependencies=("ingestion",), evidence=("failed_test_results",)),
    _capability("cluster_investigation_dispatch", inputs="AuthoritativeFailureClusterV1[]", output="ClusterInvestigationExpansionPlanV1", dependencies=("failure_clustering",), evidence=("failed_test_results",), latency_ms=1_000, cost_usd=0, timeout_seconds=30, fallback="skip_cluster_children_fail_closed", concurrency_class="orchestration", permission="read_only"),
    _capability("cluster_investigation_join", inputs="ClusterInvestigationExpansionPlanV1", output="ClusterInvestigationJoinV1", dependencies=("cluster_investigation_dispatch", "cluster_investigation"), evidence=("cluster_investigation_results",), latency_ms=1_000, cost_usd=0, timeout_seconds=30, fallback="continue_without_cluster_children", concurrency_class="orchestration", permission="read_only"),
    _capability("cluster_investigation", execution="child_spawned", inputs="ClusterScopedEvidenceBundleV1", output="InvestigationVerdictV1", dependencies=("cluster_investigation_dispatch",), evidence=("cluster_scoped_verified_evidence",), latency_ms=30_000, cost_usd=0.05, timeout_seconds=300, fallback="deterministic_cluster_summary", concurrency_class="investigation_child", permission="read_only"),
    _capability("contract_validation", inputs="RunEvidenceBundleV1", output="ContractAgentOutput", dependencies=("root_cause_analysis",), evidence=("scoped_rest_contract",), latency_ms=15_000, cost_usd=0, timeout_seconds=90, fallback="not_enough_evidence", concurrency_class="domain_evidence", permission="read_only"),
    _capability("log_intelligence", inputs="RunEvidenceBundleV1", output="LogIntelligenceAgentOutput", dependencies=("root_cause_analysis",), evidence=("scoped_log_evidence",), latency_ms=20_000, cost_usd=0, timeout_seconds=120, fallback="not_enough_evidence", concurrency_class="domain_evidence", permission="read_only"),
    _capability("root_cause_analysis", inputs="RunEvidenceBundleV1", output="AnalysisAgentOutput", dependencies=("ingestion",), evidence=("failed_test_results",), latency_ms=20_000, cost_usd=0.05, timeout_seconds=120),
    _capability("summary", inputs="RunEvidenceBundleV1", output="PreliminarySummary", dependencies=("ingestion",), latency_ms=10_000, cost_usd=0.02),
    _capability("triage", inputs="AnalysisAgentOutput", output="TriageOutput", dependencies=("root_cause_analysis",), evidence=("analysis_findings",), permission="propose_action"),
    _capability("gap_detection", inputs="PreliminarySummary", output="GapReport", dependencies=("summary",), cost_usd=0.02),
    _capability("report_refinement", inputs="GapReport", output="RefinedReport", dependencies=("gap_detection",), cost_usd=0.02),
    _capability("flaky_sentinel", inputs="RunEvidenceBundleV1", output="FlakySentinelOutput", dependencies=("ingestion",), evidence=("historical_results",), cost_usd=0),
    _capability("test_health", inputs="RunEvidenceBundleV1", output="TestHealthOutput", dependencies=("ingestion",), evidence=("test_history",), cost_usd=0),
    _capability("release_risk", inputs="MetricSnapshotV1", output="ReleaseRiskOutput", dependencies=("flaky_sentinel", "test_health"), evidence=("metric_snapshot",), fallback="deterministic_release_policy"),
    _capability("decision_report", inputs="DecisionEvidenceSnapshotV3", output="DecisionReportV1", dependencies=("release_risk",), evidence=("decision_evidence_snapshot",), cost_usd=0.02),
    _capability("decision_report_critic", inputs="DecisionReportV1", output="DecisionReportVerificationV1", dependencies=("decision_report",), evidence=("signed_decision_snapshot",), cost_usd=0, fallback="reject_publication", concurrency_class="verification"),
    _capability("regression_watchman", inputs="RunEvidenceBundleV1", output="RegressionWatchmanOutput", dependencies=("ingestion",), evidence=("historical_results",)),
    _capability("change_ownership", inputs="RunEvidenceBundleV1", output="ChangeOwnershipAgentOutput", dependencies=("ingestion",), evidence=("baseline_diff", "ownership_resolutions"), latency_ms=10_000, cost_usd=0, timeout_seconds=90, fallback="not_enough_evidence", concurrency_class="domain_evidence", permission="read_only"),
    _capability("defect_commander", inputs="AnalysisAgentOutput", output="DefectCommanderOutput", dependencies=("root_cause_analysis", "failure_clustering"), evidence=("analysis_findings",), concurrency_class="action_proposal", permission="mutating"),
    _capability("investigator_plan", inputs="InvestigationRequestV1", output="InvestigationPlanV1", latency_ms=1_000, cost_usd=0, fallback="bounded_static_hypothesis_plan", concurrency_class="investigation"),
    _capability("hypothesis_infra", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("infrastructure_observations",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_commit", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("change_history",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_environment", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("environment_observations",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_known_flaky", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("historical_results",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_regression", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("failed_test_results",), concurrency_class="investigation_hypothesis"),
    _capability("investigator_synthesis", inputs="AgentFindingV1[]", output="InvestigationVerdictV1", dependencies=("hypothesis_infra", "hypothesis_commit", "hypothesis_environment", "hypothesis_known_flaky", "hypothesis_regression"), evidence=("investigation_findings",), concurrency_class="investigation"),
    _capability("reviewer", execution="on_demand", inputs="ReviewerInputV1", output="ReviewVerdictV1", evidence=("reviewed_step_outputs", "workflow_state"), latency_ms=10_000, cost_usd=0.02, timeout_seconds=30, fallback="reject", concurrency_class="verification"),
    _capability("workflow", execution="runtime", inputs="AgenticRunV1", output="WorkflowFailure", cost_usd=0, fallback="persist_terminal_failure", concurrency_class="runtime"),
)

CAPABILITY_REGISTRY: dict[str, CapabilitySpecV1] = {spec.stage_name: spec for spec in _SPECS}


def get_capability(stage_name: str) -> CapabilitySpecV1:
    try:
        return CAPABILITY_REGISTRY[stage_name]
    except KeyError as exc:
        raise ValueError(f"unregistered workflow capability: {stage_name}") from exc


def capability_registry_snapshot() -> list[dict]:
    return [CAPABILITY_REGISTRY[name].model_dump(mode="json") for name in sorted(CAPABILITY_REGISTRY)]


# ── Report-producing capabilities (architecture E7.5, section 7.1) ───────────
# A pipeline run that produced a report must be reviewed by a human before it
# can be ``passed`` (E8); one that produced none has nothing to review and
# settles ``completed -> passed`` at finalize. "Report" is decided by the
# capability's OUTPUT CONTRACT, not by its stage name, so a new stage that
# emits one of these schemas is reviewed without anyone remembering to add it
# to a list of names.
REPORT_OUTPUT_SCHEMAS: frozenset[str] = frozenset({
    "PreliminarySummary",   # summary
    "AnalysisAgentOutput",  # root_cause_analysis
    "DecisionReportV1",     # decision_report
    "RefinedReport",        # report_refinement
})


def is_report_producing(stage_name: str) -> bool:
    """True when ``stage_name``'s declared output is a report contract.

    An unregistered stage is treated as report-producing. That is the
    fail-closed direction: a run wrongly held for review is visible and a
    human can settle it, while a report wrongly auto-passed would reach
    distribution never having been looked at.
    """
    try:
        spec = get_capability(stage_name)
    except Exception:  # noqa: BLE001 -- unknown stage: require review
        return True
    return spec.output_schema in REPORT_OUTPUT_SCHEMAS


# -- Sync-eligible capabilities (architecture E1.1, section 3.1) -----------------
# A capability may be invoked synchronously only when it is deterministic and
# cheap: no model call and an expected latency of 5 s or less. Everything else
# is async (202 plus a poll URL). Declared, not derived from ``cost_usd``: most
# capabilities inherit the registry's 0.01 default whether or not they call a
# model, so cost says nothing about it.
SYNC_ELIGIBLE: frozenset[str] = frozenset({
    "ingestion",
    "flaky_sentinel",
    "test_health",
    "release_risk",
    "cluster_investigation_dispatch",
    "cluster_investigation_join",
})

# Model routing metadata is deliberately kept out of CapabilitySpecV1. Frozen
# workflow-plan snapshots serialize that public model, so adding these fields
# there would change retry authority for every existing run (T6 / E5.1).
#
# Defaults reflect the implementation on main. Planned downgrades such as
# Tier promotions require the E9.3 gate before this map changes. Summary moved
# to SLM in E5.2 after E9.3 shipped. Deterministic capabilities that optionally
# add an LLM narrative keep
# a positive expected_cost_usd so their existing budget reservation remains.
DEFAULT_TIERS: dict[str, str] = {
    "ingestion": "deterministic",
    "anomaly_detection": "deterministic",
    "failure_clustering": "deterministic",
    "cluster_investigation_dispatch": "deterministic",
    "cluster_investigation_join": "deterministic",
    "cluster_investigation": "llm",
    "contract_validation": "deterministic",
    "log_intelligence": "deterministic",
    "root_cause_analysis": "slm",
    "summary": "slm",
    "triage": "llm",
    "gap_detection": "deterministic",
    "report_refinement": "deterministic",
    "flaky_sentinel": "deterministic",
    "test_health": "deterministic",
    "release_risk": "deterministic",
    "decision_report": "deterministic",
    "decision_report_critic": "deterministic",
    "regression_watchman": "deterministic",
    "change_ownership": "deterministic",
    "defect_commander": "llm",
    "investigator_plan": "deterministic",
    "hypothesis_infra": "llm",
    "hypothesis_commit": "llm",
    "hypothesis_environment": "llm",
    "hypothesis_known_flaky": "llm",
    "hypothesis_regression": "llm",
    "investigator_synthesis": "llm",
    "reviewer": "deterministic",
    "workflow": "deterministic",
}

ESCALATION_TRIGGERS: dict[str, frozenset[str]] = {
    name: frozenset() for name in CAPABILITY_REGISTRY
}
ESCALATION_TRIGGERS.update({
    "summary": frozenset({"validation_failure"}),
    "root_cause_analysis": frozenset({"low_confidence", "multi_artifact_evidence"}),
    "contract_validation": frozenset({"not_enough_evidence"}),
    "log_intelligence": frozenset({"not_enough_evidence"}),
    "change_ownership": frozenset({"not_enough_evidence"}),
    "report_refinement": frozenset({"contradictions"}),
})

# Capabilities whose SLM endpoint may be replaced by a promoted classifier.
# The map is separate because CapabilitySpecV1 has no stable `kind` field.
CLASSIFY_CAPABILITIES: frozenset[str] = frozenset({
    "root_cause_analysis",
    "regression_watchman",
})


def is_sync_eligible(stage_name: str) -> bool:
    """True when ``stage_name`` may be invoked with ``mode=sync``."""
    return stage_name in SYNC_ELIGIBLE
