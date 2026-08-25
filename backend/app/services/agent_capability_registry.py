"""Canonical capability metadata for the existing agent workflow stages.

Every entry declares how it is executed via ``execution``:

* ``planned``       — a workflow type lists it in its stage order (the default);
* ``child_spawned`` — dispatched per cluster, never top-level (``cluster_investigation``);
* ``on_demand``     — reachable only through its own endpoint (``defect_commander``,
  ``POST /api/v1/agents/defect-command``);
* ``runtime``       — a pseudo-capability for runtime bookkeeping (``workflow``).

The ``agents.capability-has-executor`` gate holds the two halves together: a
``planned`` capability must appear in a stage list, and one that appears in a
stage list must not claim to be anything else. Before it existed,
``defect_commander`` read as a mutating pipeline stage depending on
``root_cause_analysis`` while having no executor at all -- zero
``agent_stage_results`` rows in the entire history of the deployment.
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
    _capability("flaky_sentinel", inputs="RunEvidenceBundleV1", output="FlakySentinelOutput", dependencies=("ingestion",), evidence=("historical_results",)),
    _capability("test_health", inputs="RunEvidenceBundleV1", output="TestHealthOutput", dependencies=("ingestion",), evidence=("test_history",)),
    _capability("release_risk", inputs="MetricSnapshotV1", output="ReleaseRiskOutput", dependencies=("flaky_sentinel", "test_health"), evidence=("metric_snapshot",), fallback="deterministic_release_policy"),
    _capability("decision_report", inputs="DecisionEvidenceSnapshotV3", output="DecisionReportV1", dependencies=("release_risk",), evidence=("decision_evidence_snapshot",), cost_usd=0.02),
    _capability("decision_report_critic", inputs="DecisionReportV1", output="DecisionReportVerificationV1", dependencies=("decision_report",), evidence=("signed_decision_snapshot",), cost_usd=0, fallback="reject_publication", concurrency_class="verification"),
    _capability("regression_watchman", inputs="RunEvidenceBundleV1", output="RegressionWatchmanOutput", dependencies=("ingestion",), evidence=("historical_results",)),
    _capability("change_ownership", inputs="RunEvidenceBundleV1", output="ChangeOwnershipAgentOutput", dependencies=("ingestion",), evidence=("baseline_diff", "ownership_resolutions"), latency_ms=10_000, cost_usd=0, timeout_seconds=90, fallback="not_enough_evidence", concurrency_class="domain_evidence", permission="read_only"),
    _capability("defect_commander", execution="on_demand", inputs="AnalysisAgentOutput", output="DefectCommanderOutput", dependencies=("root_cause_analysis",), evidence=("analysis_findings",), concurrency_class="action_proposal", permission="mutating"),
    _capability("investigator_plan", inputs="InvestigationRequestV1", output="InvestigationPlanV1", latency_ms=1_000, cost_usd=0, fallback="bounded_static_hypothesis_plan", concurrency_class="investigation"),
    _capability("hypothesis_infra", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("infrastructure_observations",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_commit", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("change_history",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_environment", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("environment_observations",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_known_flaky", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("historical_results",), concurrency_class="investigation_hypothesis"),
    _capability("hypothesis_regression", inputs="InvestigationPlanV1", output="AgentFindingV1", dependencies=("investigator_plan",), evidence=("failed_test_results",), concurrency_class="investigation_hypothesis"),
    _capability("investigator_synthesis", inputs="AgentFindingV1[]", output="InvestigationVerdictV1", dependencies=("hypothesis_infra", "hypothesis_commit", "hypothesis_environment", "hypothesis_known_flaky", "hypothesis_regression"), evidence=("investigation_findings",), concurrency_class="investigation"),
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
