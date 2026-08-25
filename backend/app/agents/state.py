"""
LangGraph workflow state shared across all pipeline agents.
Each agent reads from and writes to this state as it moves through stages.
"""
from typing import Annotated, Optional
from typing_extensions import TypedDict


def _merge_dicts(a: dict, b: dict) -> dict:
    """Reducer: merge two dicts (used for parallel analysis fan-out)."""
    return {**a, **b}


def _concat_lists(a: list, b: list) -> list:
    """Reducer: concatenate lists across parallel nodes."""
    return a + b


def _dedup_concat_lists(a: list, b: list) -> list:
    """Reducer: concatenate lists, then deduplicate preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in a + b:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _merge_error_dicts(a: dict[str, list[str]], b: dict[str, list[str]]) -> dict[str, list[str]]:
    """Reducer: merge stage_errors dicts (stage_name -> list of error strings)."""
    merged = dict(a)
    for key, errors in b.items():
        merged.setdefault(key, [])
        merged[key].extend(errors)
    return merged


def _last_str(a: str, b: str) -> str:
    """Reducer: last writer wins (for scalar fields updated by parallel nodes)."""
    return b


def _last_bool(a: bool, b: bool) -> bool:
    """Reducer: last writer wins for booleans (True wins over False via OR)."""
    return a or b


def _last_int(a: int, b: int) -> int:
    """Reducer: sum integers from parallel nodes."""
    return a + b


def _last_optional_str(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Reducer: last non-None writer wins."""
    return b if b is not None else a


class WorkflowState(TypedDict):
    # ── Required Inputs ───────────────────────────────────────────
    pipeline_run_id: str        # AgentPipelineRun.id (UUID string)
    test_run_id: str            # TestRun.id that triggered this pipeline
    project_id: str             # Project.id
    build_number: str
    workflow_type: str          # WorkflowType enum value: "offline" | "deep" | "live"
    # ``time.monotonic()`` instant after which no new stage may START. Set once
    # at pipeline start from AI_PIPELINE_DEADLINE_SECONDS and never advanced, so
    # a resume gets a fresh budget while a single attempt cannot outrun the
    # Celery soft limit. 0.0 = unbounded (the budget is disabled).
    pipeline_deadline_ts: float

    # ── Stage 1: Ingestion Agent ──────────────────────────────────
    test_run_data: Optional[dict]        # Serialized TestRun summary
    branch: Optional[str]                # Current run branch, duplicated for branch-aware agents
    failed_test_ids: list[str]           # IDs of FAILED / BROKEN tests
    total_tests: int
    pass_rate: float
    ingestion_enriched: bool

    # ── Stage 2: Anomaly Detection Agent ─────────────────────────
    anomalies: list[dict]               # [{type, severity, description, test_ids}]
    is_regression: bool                 # Pass rate dropped significantly vs baseline
    regression_tests: list[str]         # Tests that newly failed this run
    anomaly_summary: Optional[str]      # Short human-readable summary

    # ── Stage 3: Root Cause Analysis Agent (parallel fan-out) ─────
    # Reducer merges results from parallel analysis nodes
    analyses: Annotated[dict[str, dict], _merge_dicts]   # test_case_id -> analysis dict

    # ── Stage 4: Summary Agent ────────────────────────────────────
    executive_summary: Optional[str]    # Layer 1: 3-sentence executive summary
    summary_markdown: Optional[str]     # Full markdown report (built from all 4 layers)
    structured_summary: Optional[dict]  # All 4 layers: layer1..layer4 keys
    summary_provenance: Optional[dict]  # Hashes, prompt versions, and model config for summary replay
    decision_intelligence: Optional[dict]  # Terminal deep-report synthesis after all read specialists
    decision_evidence_snapshot: Optional[dict]  # Durable immutable authority metadata
    decision_report_verification: Optional[dict]  # Independent terminal critic checks and repairs
    authorized_evidence_artifacts: list[dict]  # Server-owned artifact projections for signed citations
    evidence_authorization_errors: list[str]  # Fail-closed resolver errors

    # ── Stage 5: Defect Triage Agent ─────────────────────────────
    triage_results: list[dict]          # [{test_case_id, ticket_key, action: created|updated|skipped}]

    # ── Stage 2b: Failure Clustering (deep workflow only) ────────
    failure_clusters: list[dict]        # [{cluster_id, label, member_test_ids, representative_error, size}]
    cluster_map: dict[str, str]         # test_case_id -> cluster_id
    cluster_child_settings: dict        # frozen feature/policy/budget gate
    contract_agent_enabled: bool       # frozen project feature-flag snapshot
    contract_findings: Optional[dict]  # Contract Agent output contract
    log_intelligence_enabled: bool       # frozen project feature-flag snapshot
    log_findings: Optional[dict]        # Log Intelligence output contract
    defect_commander_enabled: bool     # frozen project feature-flag snapshot (MUTATING stage)
    defect_promotion: Optional[dict]   # DefectCommander output, None when off or nothing to promote
    regression_watchman_enabled: bool  # frozen project feature-flag snapshot
    regression_classification: Optional[dict]  # RegressionWatchman output contract
    change_ownership_enabled: bool      # frozen project feature-flag snapshot
    change_ownership_findings: Optional[dict]  # baseline + ownership output contract
    cluster_investigation_plan: Optional[dict]  # hashed selected/skipped cluster tasks
    cluster_investigation_results: Optional[dict]  # bounded join/result projection

    # ── Stage 3 deep: Deep Root-Cause per cluster ─────────────────
    deep_findings: Annotated[dict[str, dict], _merge_dicts]  # cluster_id -> DeepFinding dict

    # ── Stage: Flaky Sentinel ─────────────────────────────────────
    flaky_findings: list[dict]          # [{test_case_id, test_name, flaky_since_build, recommendation}]

    # ── Stage: Test Health ────────────────────────────────────────
    test_health_findings: list[dict]    # [{test_case_id, health_score, violations, recommendation}]

    # ── Stage: Gap Detection (deep workflow, AIQ-P4, optional) ────
    gap_report: Optional[dict]          # coverage/integrity gap report (last-writer-wins)

    # ── Stage: Report Refinement (deep workflow, AIQ-P4, optional) ─
    refined_report: Optional[dict]      # dedup/contradiction reconciliation report (last-writer-wins)

    # ── Stage 6: Release Risk Agent ──────────────────────────────
    release_decision: Optional[dict]    # {recommendation, risk_score, blocking_issues, reasoning}

    # ── Error / Progress Tracking ─────────────────────────────────
    errors: Annotated[list[str], _concat_lists]
    completed_stages: Annotated[list[str], _dedup_concat_lists]
    current_stage: Annotated[str, _last_str]
    # Per-stage structured errors for downstream agents to inspect
    stage_errors: Annotated[dict[str, list[str]], _merge_error_dicts]
    # Quality indicator set by analysis agent when >30% of analyses fail
    stage_quality: Annotated[Optional[str], _last_optional_str]  # "normal" | "degraded"
    low_confidence_count: Annotated[int, _last_int]              # count of analyses below confidence threshold

    # ── Provenance / Execution Tracking ──────────────────────────
    # Annotated with _concat_lists so parallel nodes (analysis + cluster) can both append
    skipped_stages: Annotated[list[str], _concat_lists]  # stages bypassed and why
    execution_path: Annotated[str, _last_str]    # ExecutionPath enum value for the overall run
    fallback_used: Annotated[bool, _last_bool]   # any stage used deterministic fallback instead of LLM
    tools_used: Annotated[list[str], _concat_lists]      # LangChain tools invoked (parallel-safe)
    analysis_mode_requested: str  # configured value at pipeline start (env/UI)
    analysis_mode_resolved: str   # effective engine frozen for this pipeline
    analysis_mode_resolution: dict  # probe/config snapshot for audit replay
    _workflow_route_decisions: list[dict]  # sync router decisions persisted in pipeline metadata
    _checkpoint_stages: list[str]  # stage outputs restored from a previous authorized checkpoint
    _checkpoint_replay_metadata: dict[str, dict]  # replay hashes/version breadcrumbs for restored stages
    workflow_plan: dict  # deterministic planner output for expected stage path
    initial_workflow_plan: dict  # immutable planner snapshot captured before execution
    workflow_verification: dict  # verifier checks comparing final state to plan
    agent_contracts: Annotated[dict[str, dict], _merge_dicts]  # agent_name -> versioned output contract metadata
    schema_version: int            # pipeline state schema version (increment on breaking changes)

    # ── Phase 6: Per-Stage Observability ─────────────────────────
    # Accumulated by BaseAgent.mark_stage_done() — keyed by stage_name
    stage_metrics: Annotated[dict[str, dict], _merge_dicts]  # stage_name -> {tokens, cost, latency, ...}
