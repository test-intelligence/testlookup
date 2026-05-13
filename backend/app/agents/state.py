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

    # ── Stage 5: Defect Triage Agent ─────────────────────────────
    triage_results: list[dict]          # [{test_case_id, ticket_key, action: created|updated|skipped}]

    # ── Stage 2b: Failure Clustering (deep workflow only) ────────
    failure_clusters: list[dict]        # [{cluster_id, label, member_test_ids, representative_error, size}]
    cluster_map: dict[str, str]         # test_case_id -> cluster_id

    # ── Stage 3 deep: Deep Root-Cause per cluster ─────────────────
    deep_findings: Annotated[dict[str, dict], _merge_dicts]  # cluster_id -> DeepFinding dict

    # ── Stage: Flaky Sentinel ─────────────────────────────────────
    flaky_findings: list[dict]          # [{test_case_id, test_name, flaky_since_build, recommendation}]

    # ── Stage: Test Health ────────────────────────────────────────
    test_health_findings: list[dict]    # [{test_case_id, health_score, violations, recommendation}]

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
    schema_version: int            # pipeline state schema version (increment on breaking changes)

    # ── Phase 6: Per-Stage Observability ─────────────────────────
    # Accumulated by BaseAgent.mark_stage_done() — keyed by stage_name
    stage_metrics: Annotated[dict[str, dict], _merge_dicts]  # stage_name -> {tokens, cost, latency, ...}
