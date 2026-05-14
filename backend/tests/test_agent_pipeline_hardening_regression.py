"""Regression tests for deterministic and auditable agent pipeline hardening.

These tests intentionally inspect source contracts instead of importing the
full app stack. That keeps them runnable in lightweight environments while
still catching regressions in the wiring that makes pipeline outputs
reproducible and auditable.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _function_source(relative: str, name: str) -> str:
    source = _read(relative)
    module = ast.parse(source)
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found in {relative}")


def _class_source(relative: str, name: str) -> str:
    source = _read(relative)
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found in {relative}")


def test_triage_events_are_correlated_to_pipeline_run_id():
    source = _read("app/services/agent.py")
    fn = _function_source("app/services/agent.py", "run_triage_agent")

    assert "pipeline_run_id: Optional[str] = None" in fn
    assert 'pipeline_run_id or ""' in fn
    assert 'await _emit_event("", "cache_hit"' not in source
    assert 'await _emit_event("", "llm_called"' not in source


def test_root_cause_parser_uses_shared_json_parser_and_schema():
    fn = _function_source("app/services/agent.py", "_parse_agent_output")
    run = _function_source("app/services/agent.py", "run_triage_agent")

    assert "parse_llm_json" in fn
    assert "RootCauseAnalysis" in fn
    assert "validate_llm_output_with_error" in fn
    assert "schema_validated" in fn
    assert "schema_validation_error" in fn
    assert "re.search" not in fn
    assert "schema_validation_failed" in run
    assert '"schema": "RootCauseAnalysis"' in run


def test_root_cause_schema_exists_and_normalizes_categories():
    cls = _class_source("app/models/llm_schemas.py", "RootCauseAnalysis")

    for field in [
        "root_cause_summary",
        "failure_category",
        "backend_error_found",
        "pod_issue_found",
        "is_flaky",
        "confidence_score",
        "recommended_actions",
        "role_actions",
        "evidence_references",
    ]:
        assert field in cls
    for category in [
        "PRODUCT_BUG",
        "INFRASTRUCTURE",
        "TEST_DATA",
        "AUTOMATION_DEFECT",
        "FLAKY",
        "UNKNOWN",
    ]:
        assert category in cls


def test_pipeline_analysis_mode_is_frozen_and_persisted():
    workflow = _read("app/agents/workflow.py")
    snapshot_fn = _function_source("app/agents/workflow.py", "_resolve_analysis_mode_snapshot")

    assert "refresh_analysis_mode_from_cache" in snapshot_fn
    assert "get_analysis_mode" in snapshot_fn
    assert '"requested"' in snapshot_fn
    assert '"resolved"' in snapshot_fn
    assert workflow.count('"analysis_mode_requested": mode_snapshot["requested"]') == 2
    assert workflow.count('"analysis_mode_resolved": mode_snapshot["resolved"]') == 2
    assert '"analysis_mode_resolution": mode_snapshot' in workflow
    assert '"analysis_mode_requested": final_state.get("analysis_mode_requested")' in workflow
    assert '"analysis_mode_resolved": final_state.get("analysis_mode_resolved")' in workflow
    assert '"analysis_mode_resolution": final_state.get("analysis_mode_resolution", {})' in workflow


def test_analysis_agent_consumes_frozen_mode_and_persists_fingerprints():
    source = _read("app/agents/analysis_agent.py")

    assert 'state.get("analysis_mode_resolved") or "auto"' in source
    assert "get_analysis_mode()" not in source
    assert '"mode_resolved_at_pipeline_start": state.get("analysis_mode_resolved")' in source
    for key in [
        "input_fingerprints",
        "error_message_sha256",
        "stack_trace_sha256",
        "classification_context_sha256",
        "prompt_versions",
        "model_config_snapshot",
    ]:
        assert key in source


def test_checkpoint_restores_are_visible_in_stage_results():
    workflow = _read("app/agents/workflow.py")

    assert "checkpoint_restored" in workflow
    assert "_mark_stage_restored" in workflow
    assert '"restored_from_checkpoint": True' in workflow
    assert "Restored from previous pipeline checkpoint" in workflow
    assert '"checkpoint_stages": final_state.get("_checkpoint_stages", [])' in workflow


def test_pipeline_dedup_allows_same_owner_retry_and_releases_on_failure():
    tasks = _read("app/worker/tasks.py")
    is_duplicate = _function_source("app/worker/tasks.py", "_is_duplicate")
    release_lock = _function_source("app/worker/tasks.py", "_release_duplicate_lock")
    run_pipeline = _function_source("app/worker/tasks.py", "run_agent_pipeline")

    assert "owner: str | None = None" in is_duplicate
    assert "existing == owner" in is_duplicate
    assert "await redis.expire(key, ttl)" in is_duplicate
    assert "await redis.delete(key)" in release_lock
    assert "dedup_owner = str(self.request.id)" in run_pipeline
    assert "_is_duplicate(dedup_key, ttl=7200, owner=dedup_owner)" in tasks
    assert "_release_duplicate_lock(dedup_key, dedup_owner)" in run_pipeline


def test_deterministic_ordering_for_clusters_and_summary_context():
    cluster = _read("app/agents/cluster_agent.py")
    summary = _read("app/agents/summary_agent.py")

    assert "test_ids = sorted(test_id_to_error.keys())" in cluster
    assert "def _sorted_analyses" in summary
    assert "return (-confidence, category, str(test_id))" in summary
    assert "sorted_analyses = self._sorted_analyses(analyses)" in summary
    assert "list(analyses.items())[:15]" not in summary
    assert "list(analyses.items())[:10]" not in summary
    assert "list(analyses.items())[:5]" not in summary


def test_hardening_plan_tracks_remaining_phases():
    plan = _read("../docs/AGENT_PIPELINE_HARDENING_PLAN.md")

    for heading in [
        "Phase 1: Correctness And Audit Correlation",
        "Phase 2: Deterministic Inputs And Reproducibility",
        "Phase 3: Structured Output Enforcement",
        "Phase 4: Performance And Backpressure",
        "Phase 5: Full Audit Replay",
        "Refactor R2: Planner And Verifier",
        "Refactor R4: Safety And HITL Enforcement",
        "Refactor R5: Evaluation Release Gate",
        "Refactor R6: Observability And Cost Control",
    ]:
        assert heading in plan


def test_low_confidence_retry_skips_deterministic_and_cached_results():
    source = _read("app/agents/analysis_agent.py")
    retry_gate = _function_source("app/agents/analysis_agent.py", "_should_retry_analysis")
    retry_wrapper = _function_source("app/agents/analysis_agent.py", "_analyse_with_retry")

    assert "_DETERMINISTIC_ANALYSIS_ENGINES" in source
    for engine in ['"rules"', '"ml"', '"blocked"']:
        assert engine in source
    assert "self._should_retry_analysis(result, meta)" in retry_wrapper
    assert 'analysis_mode in _DETERMINISTIC_ANALYSIS_ENGINES' in retry_gate
    assert 'result.get("classified_by") in {"rules_engine", "pattern_heuristic"}' in retry_gate
    assert 'result.get("cache_hit")' in retry_gate
    assert 'result.get("fallback_tier")' in retry_gate
    assert 'result.get("schema_validated") is False' in retry_gate
    assert 'return analysis_mode in {"llm", ""}' in retry_gate


def test_analysis_agent_uses_adaptive_concurrency_policy():
    source = _read("app/agents/analysis_agent.py")
    run = _function_source("app/agents/analysis_agent.py", "run")
    resolver = _function_source("app/agents/analysis_agent.py", "_resolve_adaptive_concurrency")
    feedback = _function_source("app/agents/analysis_agent.py", "_record_latency_feedback")

    assert "_ANALYSIS_LATENCY_EWMA_BY_PROVIDER" in source
    assert "_REMOTE_LLM_PROVIDERS" in source
    assert "_LOCAL_LLM_PROVIDERS" in source
    assert "concurrency_policy = await self._resolve_adaptive_concurrency" in run
    assert "asyncio.Semaphore(concurrency)" in run
    assert "analysis_concurrency_policy" in run
    assert '"adaptive_concurrency": concurrency_policy' in run
    assert "LLMCircuitBreaker.get_status()" in resolver
    assert 'circuit_state in {"OPEN", "HALF_OPEN"}' in resolver
    assert "_HIGH_LATENCY_SECONDS" in resolver
    assert "_LOW_LATENCY_SECONDS" in resolver
    assert "_ANALYSIS_LATENCY_EWMA_BY_PROVIDER[provider]" in feedback


def test_analysis_metadata_enrichment_has_bounded_ttl_cache():
    source = _read("app/agents/analysis_agent.py")
    fetch_meta = _function_source("app/agents/analysis_agent.py", "_fetch_test_metadata")
    cache_get = _function_source("app/agents/analysis_agent.py", "_metadata_cache_get")
    cache_set = _function_source("app/agents/analysis_agent.py", "_metadata_cache_set")

    assert "_METADATA_CACHE_TTL_SECONDS = 30" in source
    assert "_METADATA_CACHE_MAX_ENTRIES = 64" in source
    assert "def _metadata_cache_key" in source
    assert "copy.deepcopy(payload)" in cache_get
    assert "time.monotonic() - cached_at > _METADATA_CACHE_TTL_SECONDS" in cache_get
    assert "oldest_key = min(_METADATA_CACHE" in cache_set
    assert "cached = _metadata_cache_get(cache_key)" in fetch_meta
    assert "analysis_metadata_cache_hit" in fetch_meta
    assert "_metadata_cache_set(cache_key, meta)" in fetch_meta


def test_summary_semantic_enrichment_has_deadline_and_fallback():
    source = _read("app/agents/summary_agent.py")
    fetch_similar = _function_source("app/agents/summary_agent.py", "_fetch_similar_failures")

    assert "_SIMILAR_FAILURES_TIMEOUT_SECONDS" in source
    assert "asyncio.wait_for" in fetch_similar
    assert "timeout=_SIMILAR_FAILURES_TIMEOUT_SECONDS" in fetch_similar
    assert "except asyncio.TimeoutError" in fetch_similar
    assert "return []" in fetch_similar


def test_summary_schema_failures_are_decision_logged():
    source = _read("app/agents/summary_agent.py")
    generate = _function_source("app/agents/summary_agent.py", "_generate_structured_report")
    call_layer = _function_source("app/agents/summary_agent.py", "_call_json_layer")

    assert "validate_llm_output_with_error" in source
    assert "pipeline_run_id: str | None = None" in generate
    assert "pipeline_run_id=pipeline_run_id" in generate
    assert "summary_schema_validation" in call_layer
    assert 'chosen="parse_fallback"' in call_layer
    assert 'chosen="schema_defaults"' in call_layer
    assert "await self.log_decision" in call_layer
    assert "validation_error" in call_layer


def test_summary_stage_persists_context_hashes_and_prompt_versions():
    source = _read("app/agents/summary_agent.py")
    provenance = _function_source("app/agents/summary_agent.py", "_build_summary_provenance")
    run = _function_source("app/agents/summary_agent.py", "run")
    store = _function_source("app/agents/summary_agent.py", "_store_summary")
    state = _read("app/agents/state.py")
    workflow = _read("app/agents/workflow.py")

    assert "_SUMMARY_PROMPT_VERSIONS" in source
    assert "def _hash_text" in source
    assert "def _hash_json" in source
    for key in [
        "context_sha256",
        "safe_context_sha256",
        "input_fingerprints",
        "run_data_sha256",
        "anomaly_summary_sha256",
        "anomalies_sha256",
        "analyses_sha256",
        "similar_failures_sha256",
        "ordered_analysis_ids_sha256",
        "prompt_versions",
        "prompt_template_hashes",
        "model_config_snapshot",
    ]:
        assert key in provenance
    assert 'structured["_provenance"] = summary_provenance' in run
    assert '"summary_provenance": summary_provenance' in run
    assert '"summary_provenance": summary_provenance' in store
    assert "summary_provenance: Optional[dict]" in state
    assert '"summary_provenance": None' in workflow


def test_workflow_route_decisions_have_durable_metadata_mirror():
    workflow = _read("app/agents/workflow.py")
    state = _read("app/agents/state.py")
    emit_route = _function_source("app/agents/workflow.py", "_emit_route_decision")
    append_route = _function_source("app/agents/workflow.py", "_append_route_decision")
    mark_done = _function_source("app/agents/workflow.py", "_mark_pipeline_done")

    assert "_workflow_route_decisions" in state
    assert "state.setdefault(\"_workflow_route_decisions\", [])" in append_route
    assert "_append_route_decision(state, payload)" in emit_route
    assert "asyncio.create_task" in emit_route
    assert '"workflow_route_decisions": final_state.get("_workflow_route_decisions", [])' in mark_done


def test_stage_replay_checksums_and_runtime_versions_are_persisted():
    workflow = _read("app/agents/workflow.py")
    checkpoint_stage = _function_source("app/agents/workflow.py", "_checkpoint_stage")
    wrapper = _function_source("app/agents/workflow.py", "_make_checkpointed_node")
    mark_done = _function_source("app/agents/workflow.py", "_mark_pipeline_done")

    assert "def _canonical_checksum" in workflow
    assert "def _runtime_version_snapshot" in workflow
    assert "hashlib.sha256" in workflow
    assert "input_checksum_sha256" in checkpoint_stage
    assert "output_checksum_sha256" in checkpoint_stage
    assert '"runtime_versions": _runtime_version_snapshot()' in checkpoint_stage
    assert "input_checksum = _canonical_checksum(state)" in wrapper
    assert '"final_state_checksum_sha256": _canonical_checksum(final_state)' in mark_done


def test_event_log_has_retry_and_dead_letter_accounting():
    event_log = _read("app/services/pipeline_event_log.py")
    emit_event = _function_source("app/services/pipeline_event_log.py", "emit_event")

    assert "_MAX_WRITE_ATTEMPTS" in event_log
    assert "_DEAD_LETTER_EVENTS" in event_log
    assert "def _record_dead_letter" in event_log
    assert "def get_event_log_health" in event_log
    assert "for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1)" in emit_event
    assert "await asyncio.sleep(0)" in emit_event
    assert "_record_dead_letter(event, last_error)" in emit_event


def test_decision_trail_falls_back_to_postgres_route_decisions():
    service = _read("app/services/decision_trail_service.py")
    load_events = _function_source("app/services/decision_trail_service.py", "_load_workflow_events")

    assert "workflow_route_decisions" in service
    assert "fallback_events = list(metadata.get(\"workflow_route_decisions\") or [])" in load_events
    assert "return events or fallback_events" in load_events
    assert "return fallback_events" in load_events


def test_pipeline_replay_service_reconstructs_deterministically():
    replay = _read("app/services/pipeline_replay_service.py")
    build_replay = _function_source("app/services/pipeline_replay_service.py", "build_pipeline_replay")
    build_summary = _function_source("app/services/pipeline_replay_service.py", "build_replay_integrity_summary")
    integrity = _function_source("app/services/pipeline_replay_service.py", "_integrity_report")

    assert "def _event_sort_key" in replay
    assert "def _stage_sort_key" in replay
    assert "def _workflow_route_decisions" in replay
    assert "def _synthesized_route_events" in replay
    assert "sorted(stage_result.scalars().all(), key=_stage_sort_key)" in build_replay
    assert "events = sorted(events, key=_event_sort_key)" in build_replay
    assert '"route_decisions": _workflow_route_decisions(pipeline)' in build_replay
    assert '"stage_replay": [_stage_summary(stage) for stage in stages]' in build_replay
    assert '"event_counts": _event_counts(events)' in build_replay
    assert "missing_start_events" in integrity
    assert "missing_terminal_events" in integrity
    assert "missing_replay_checksums" in integrity
    assert "missing_final_state_checksum" in integrity
    assert "_integrity_report(pipeline, stages, normalized_events)" in build_summary


def test_agents_router_exposes_pipeline_replay_endpoint():
    router = _read("app/routers/agents.py")
    endpoint = _function_source("app/routers/agents.py", "get_pipeline_replay")

    assert "PipelineReplayResponse" in router
    assert '@router.get("/pipelines/{pipeline_id}/replay", response_model=PipelineReplayResponse)' in router
    assert "build_pipeline_replay" in endpoint
    assert 'HTTPException(404, detail="Pipeline run not found")' in endpoint


def test_agents_router_exposes_event_log_health_and_timeline_integrity():
    router = _read("app/routers/agents.py")
    timeline = _function_source("app/routers/agents.py", "get_pipeline_timeline")
    health = _function_source("app/routers/agents.py", "get_pipeline_event_log_health")

    assert "PipelineEventLogHealthResponse" in router
    assert '@router.get("/event-log/health", response_model=PipelineEventLogHealthResponse)' in router
    assert "require_role(UserRole.QA_LEAD)" in health
    assert "get_event_log_health" in health
    assert '"status": status' in health
    assert "build_replay_integrity_summary" in timeline
    assert '"replay_integrity": replay_integrity' in timeline


def test_pipeline_replay_response_schema_is_typed():
    schemas = _read("app/models/schemas.py")
    response = _class_source("app/models/schemas.py", "PipelineReplayResponse")

    for cls in [
        "PipelineReplayEventResponse",
        "PipelineReplayStageResponse",
        "PipelineReplayAuditGaps",
        "PipelineReplayIntegritySummary",
        "PipelineReplayResponse",
        "PipelineEventLogHealthResponse",
    ]:
        assert f"class {cls}" in schemas
    for field in [
        "pipeline_run_id: uuid.UUID",
        "test_run_id: uuid.UUID",
        "analysis_mode_resolution: Dict[str, Any]",
        "stage_replay: List[PipelineReplayStageResponse]",
        "events: List[PipelineReplayEventResponse]",
        "event_counts: Dict[str, int]",
        "audit_gaps: PipelineReplayAuditGaps",
    ]:
        assert field in response
    timeline = _class_source("app/models/schemas.py", "PipelineTimelineResponse")
    event_health = _class_source("app/models/schemas.py", "PipelineEventLogHealthResponse")
    assert "replay_integrity: PipelineReplayIntegritySummary" in timeline
    assert 'status: str = "healthy"' in event_health
    assert "recent_dead_letters: List[Dict[str, Any]]" in event_health


def test_agent_contract_models_and_helper_exist():
    contracts = _read("app/models/agent_contracts.py")
    helper = _function_source("app/models/agent_contracts.py", "validate_agent_contract")

    for cls in [
        "AgentContractMetadata",
        "ContractedAgentOutput",
        "IngestionAgentOutput",
        "ClusterAgentOutput",
        "AnomalyDetectionAgentOutput",
        "SummaryAgentOutput",
        "DefectTriageAgentOutput",
        "ReleaseRiskAgentOutput",
    ]:
        assert f"class {cls}" in contracts
    for field in [
        "schema_version",
        "agent_name",
        "agent_version",
        "fallback_used",
        "confidence",
        "evidence_refs",
        "decision_reason",
        "output_keys",
    ]:
        assert field in contracts
    assert "schema.model_validate" in helper
    assert '"agent_contracts"' in helper
    assert '"contract_validation_error"' in helper


def test_workflow_carries_agent_contract_metadata():
    state = _read("app/agents/state.py")
    workflow = _read("app/agents/workflow.py")
    mark_done = _function_source("app/agents/workflow.py", "_mark_pipeline_done")

    assert "agent_contracts: Annotated[dict[str, dict], _merge_dicts]" in state
    assert '"agent_contracts": {}' in workflow
    assert '"agent_contracts": final_state.get("agent_contracts", {})' in mark_done


def test_first_agents_validate_outputs_against_contracts():
    ingestion = _read("app/agents/ingestion_agent.py")
    cluster = _read("app/agents/cluster_agent.py")
    release = _read("app/agents/release_risk_agent.py")

    assert "IngestionAgentOutput" in ingestion
    assert "validate_agent_contract(" in ingestion
    assert "agent_name=self.stage_name" in ingestion
    assert "ClusterAgentOutput" in cluster
    assert "validate_agent_contract(" in cluster
    assert "evidence_refs=" in cluster
    assert "ReleaseRiskAgentOutput" in release
    assert "validate_agent_contract(" in release
    assert "score_model" in release


def test_next_agents_validate_outputs_against_contracts():
    anomaly = _read("app/agents/anomaly_agent.py")
    summary = _read("app/agents/summary_agent.py")
    triage = _read("app/agents/triage_agent.py")

    assert "AnomalyDetectionAgentOutput" in anomaly
    assert "validate_agent_contract(" in anomaly
    assert "decision_reason=" in anomaly
    assert "SummaryAgentOutput" in summary
    assert "validate_agent_contract(" in summary
    assert "summary_provenance" in summary
    assert "DefectTriageAgentOutput" in triage
    assert "validate_agent_contract(" in triage
    assert "triage_result" in triage


def test_r2_planner_and_verifier_service_is_deterministic():
    planner = _read("app/services/agent_planner.py")
    build_plan = _function_source("app/services/agent_planner.py", "build_workflow_plan")
    verifier = _function_source("app/services/agent_planner.py", "verify_workflow_execution")
    attach = _function_source("app/services/agent_planner.py", "attach_workflow_plan_and_verification")

    assert "PLANNER_VERSION" in planner
    assert "VERIFIER_VERSION" in planner
    assert "_OFFLINE_STAGES" in planner
    assert "_DEEP_STAGES" in planner
    assert "_LIVE_STAGES" in planner
    assert "_as_sorted_strings" in planner
    assert "sorted(str(value)" in planner
    assert "all-green run has no failed tests" in build_plan
    assert "triageable_test_ids" in build_plan
    assert "required_planned_stages_completed" in verifier
    assert "unplanned_stages_not_executed" in verifier
    assert "route_rationale_persisted" in verifier
    assert "all_green_skips_analysis_work" in verifier
    assert 'state["workflow_plan"] = plan' in attach
    assert 'state["workflow_verification"] = verify_workflow_execution(plan, state)' in attach


def test_workflow_persists_r2_plan_and_verification():
    workflow = _read("app/agents/workflow.py")
    state = _read("app/agents/state.py")
    mark_done = _function_source("app/agents/workflow.py", "_mark_pipeline_done")
    run_offline = _function_source("app/agents/workflow.py", "run_offline_pipeline")
    run_deep = _function_source("app/agents/workflow.py", "run_deep_pipeline")

    assert "workflow_plan: dict" in state
    assert "workflow_verification: dict" in state
    assert "build_workflow_plan(workflow_type=workflow_type)" in run_offline
    assert "build_workflow_plan(workflow_type=\"deep\")" in run_deep
    assert "attach_workflow_plan_and_verification" in run_offline
    assert "attach_workflow_plan_and_verification" in run_deep
    assert '"workflow_verified"' in workflow
    assert '"workflow_plan": final_state.get("workflow_plan", {})' in mark_done
    assert '"workflow_verification": final_state.get("workflow_verification", {})' in mark_done


def test_replay_exposes_r2_plan_and_verification_contract():
    replay = _read("app/services/pipeline_replay_service.py")
    schemas = _class_source("app/models/schemas.py", "PipelineReplayResponse")
    build_replay = _function_source("app/services/pipeline_replay_service.py", "build_pipeline_replay")

    assert '"workflow_plan": metadata.get("workflow_plan") or {}' in build_replay
    assert '"workflow_verification": metadata.get("workflow_verification") or {}' in build_replay
    assert "workflow_plan: Dict[str, Any]" in schemas
    assert "workflow_verification: Dict[str, Any]" in schemas


def test_r4_triage_gates_jira_creation_with_action_policy():
    triage = _read("app/agents/triage_agent.py")
    triage_one = _function_source("app/agents/triage_agent.py", "_triage_one")
    policy = _function_source("app/services/action_policy.py", "check_jira_ticket_creation_policy")

    assert "check_jira_ticket_creation_policy" in triage
    assert "ActionStatus.PENDING_REVIEW" in triage_one
    assert "_mark_defect_pending_review" in triage
    assert '"mutating_action": "jira_ticket_creation"' in triage_one
    assert '"requires_approval": True' in triage_one
    assert triage_one.index("check_jira_ticket_creation_policy") < triage_one.index("create_jira_issue")
    assert "ActionType.JIRA_TICKET_CREATION" in policy
    assert "Jira ticket creation requires human approval" in policy


def test_r5_agent_stack_release_gate_manifest_is_auditable():
    service = _read("app/services/eval_gate_service.py")
    manifest = _function_source("app/services/eval_gate_service.py", "build_agent_stack_gate_manifest")
    release_gate = _function_source("app/services/eval_gate_service.py", "evaluate_agent_stack_release_gate")
    router = _read("app/routers/ai_evaluation.py")

    assert "DEFAULT_AGENT_STACK_GATES" in service
    assert "_manifest_checksum" in service
    assert "prompt_versions" in manifest
    assert "model_versions" in manifest
    assert "routing_versions" in manifest
    assert '"manifest_checksum_sha256"' in manifest
    assert "_BLOCKING_GATE_STATUSES" in service
    assert "evaluate_pre_release_gate" in release_gate
    assert '"blocking_gates"' in release_gate
    assert '"version_changes"' in release_gate
    assert "AgentStackReleaseGateRequest" in router
    assert '@router.post("/agent-stack-release-gate")' in router


def test_r6_timeline_exposes_agent_observability_summary():
    service = _read("app/services/agent_cost_service.py")
    summary = _function_source("app/services/agent_cost_service.py", "build_agent_observability_summary")
    router = _function_source("app/routers/agents.py", "get_pipeline_timeline")
    timeline_schema = _class_source("app/models/schemas.py", "PipelineTimelineResponse")

    assert "build_agent_observability_summary" in service
    for key in [
        '"latency"',
        '"tokens"',
        '"cost"',
        '"fallback"',
        '"errors"',
        '"quality"',
        '"alerts"',
        '"per_agent"',
    ]:
        assert key in summary
    assert "route_rationale" in summary
    assert "build_agent_observability_summary" in router
    assert '"agent_observability": agent_observability' in router
    assert "agent_observability: Dict[str, Any]" in timeline_schema
