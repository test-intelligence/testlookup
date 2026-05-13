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
    for node in module.body:
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

    assert "parse_llm_json" in fn
    assert "RootCauseAnalysis" in fn
    assert "validate_llm_output" in fn
    assert "schema_validated" in fn
    assert "schema_validation_error" in fn
    assert "re.search" not in fn


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
    ]:
        assert heading in plan
