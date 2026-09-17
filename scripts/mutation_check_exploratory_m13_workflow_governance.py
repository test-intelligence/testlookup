"""Prove M13 workflow-governance regressions kill their unsafe behavior."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "workflow-executor-parity",
        "backend/app/agents/workflow_compiler.py",
        "        elif stage not in WORKFLOW_ELIGIBLE:\n",
        "        elif False and stage not in WORKFLOW_ELIGIBLE:\n",
        "backend/tests/test_workflow_compiler.py::test_validation_rejects_registered_capability_without_workflow_executor",
    ),
    Mutation(
        "missing-condition-is-false",
        "backend/app/agents/workflow_compiler.py",
        "        except (TypeError, ValueError):\n",
        "        except ZeroDivisionError:\n",
        "backend/tests/test_workflow_compiler.py::test_relational_condition_treats_missing_runtime_fact_as_false",
    ),
    Mutation(
        "published-evaluation-immutability",
        "backend/app/routers/workflows.py",
        '    if row.status == "published":\n        raise _conflict(svc.WorkflowConflict("published workflow versions are immutable"))\n',
        "",
        "backend/tests/test_workflows_router.py::test_evaluate_refuses_to_mutate_a_published_version",
    ),
    Mutation(
        "compare-and-publish-digest",
        "backend/app/routers/workflows.py",
        "    if svc.definition_checksum(row.definition) != body.definition_sha256:\n",
        "    if False and svc.definition_checksum(row.definition) != body.definition_sha256:\n",
        "backend/tests/test_workflows_router.py::test_publish_is_bound_to_selected_version_digest_and_exact_repeats_are_read_only",
    ),
    Mutation(
        "frozen-runtime-authority",
        "backend/app/agents/workflow.py",
        "        and _workflow_runtime_authority_valid(metadata)\n",
        "        and True\n",
        "backend/tests/test_e33_workflow_runtime.py::test_resume_authority_rejects_definition_plan_and_version_mutations",
    ),
    Mutation(
        "custom-step-tool-narrowing",
        "backend/app/agents/workflow.py",
        '    if workflow_id.startswith("wf."):\n',
        '    if False and workflow_id.startswith("wf."):\n',
        "backend/tests/test_e33_workflow_runtime.py::test_custom_step_tools_narrow_the_frozen_project_allowlist",
    ),
    Mutation(
        "blocking-review-check",
        "backend/app/models/agent_contracts.py",
        '        if self.verdict in {"pass", "pass_with_flags"} and any(\n'
        '            not check.passed\n',
        '        if False and self.verdict in {"pass", "pass_with_flags"} and any(\n'
        '            not check.passed\n',
        "backend/tests/agents/test_reviewer_agent.py::test_pass_with_flags_cannot_override_a_blocking_deterministic_failure",
    ),
    Mutation(
        "review-rejection-stops-graph",
        "backend/app/agents/workflow.py",
        '                if isinstance(supervisor, dict) and supervisor.get("route") == "finalize":\n',
        '                if False and isinstance(supervisor, dict) and supervisor.get("route") == "finalize":\n',
        "backend/tests/test_e33_workflow_runtime.py::test_runtime_reviewer_applies_loop_count_and_stops_on_final_rejection",
    ),
    Mutation(
        "topology-replay-honesty",
        "backend/app/services/workflow_evaluation_service.py",
        "    topology_measured = not (\n",
        "    topology_measured = True or not (\n",
        "backend/tests/services/test_workflow_evaluation_service.py::test_replay_never_claims_to_measure_control_flow_from_cached_step_outputs",
    ),
    Mutation(
        "low-model-agreement-flags",
        "backend/app/models/agent_contracts.py",
        '            self.verdict == "pass"\n'
        '            and self.second_model is not None\n',
        '            self.verdict in {"pass", "pass_with_flags"}\n'
        '            and self.second_model is not None\n',
        "backend/tests/agents/test_reviewer_agent.py::test_low_second_model_agreement_continues_only_with_flags_and_human_review",
    ),
    Mutation(
        "single-canonical-review-loop",
        "backend/app/agents/workflow_compiler.py",
        "        if len(retry_loops) != 1 or len(source_loops) != 1:\n",
        "        if len(retry_loops) != 1:\n",
        "backend/tests/test_workflow_compiler.py::test_reviewer_requires_one_bounded_supervisor_retry_loop",
    ),
    Mutation(
        "checkpoint-runtime-authority",
        "backend/app/agents/workflow.py",
        '        and metadata.get("workflow_runtime_authority_sha256")\n'
        "        == workflow_runtime_authority_sha256\n",
        "        and True\n",
        "backend/tests/test_e33_workflow_runtime.py::test_checkpoint_restore_requires_exact_workflow_authority",
    ),
    Mutation(
        "runtime-config-state",
        "backend/app/agents/workflow.py",
        '        "workflow_agent_configs": setup.get("workflow_agent_configs") or {},\n',
        '        "workflow_agent_configs": {},\n',
        "backend/tests/test_e33_workflow_runtime.py::test_runtime_state_carries_frozen_tool_and_model_authority",
    ),
    Mutation(
        "reviewer-instance-and-model-provenance",
        "backend/app/agents/workflow.py",
        '                        "step_name": target_id,\n',
        '                        "step_name": target.agent_id.removeprefix("agent.").removesuffix(".v1"),\n',
        "backend/tests/test_e33_workflow_runtime.py::test_runtime_reviewer_binds_declared_output_and_frozen_policy",
    ),
    Mutation(
        "reviewer-human-gate",
        "backend/app/agents/workflow.py",
        "                and not reviewer_requires_human\n",
        "                and reviewer_requires_human\n",
        "backend/tests/test_finalize_creates_review_request.py::test_reviewer_flag_forces_review_even_without_a_report_stage",
    ),
    Mutation(
        "reviewer-evidence-binding",
        "backend/app/agents/workflow.py",
        "                                or review_request_service.reviewer_evidence_hash_from(final_state)\n",
        "                                or None\n",
        "backend/tests/test_finalize_creates_review_request.py::test_reviewer_flag_forces_review_even_without_a_report_stage",
    ),
    Mutation(
        "named-step-result-identity",
        "backend/app/agents/workflow.py",
        "    if step_id == capability_stage:\n",
        "    if True:\n",
        "backend/tests/test_e33_workflow_runtime.py::test_named_step_outputs_use_instance_identity_for_verification_and_contracts",
    ),
    Mutation(
        "named-pipeline-bound-checkpoint",
        "backend/app/agents/workflow.py",
        "                if capability_stage in _PIPELINE_BOUND_CHECKPOINT_STAGES:\n",
        "                if stage.stage_name in _PIPELINE_BOUND_CHECKPOINT_STAGES:\n",
        "backend/tests/test_decision_evidence_checkpoint_resume.py::test_named_pipeline_bound_stage_is_never_restored_cross_pipeline",
    ),
    Mutation(
        "checkpoint-runtime-version",
        "backend/app/agents/workflow.py",
        "    if runtime_versions != _runtime_version_snapshot():\n",
        "    if False and runtime_versions != _runtime_version_snapshot():\n",
        "backend/tests/test_decision_evidence_checkpoint_resume.py::test_checkpoint_from_a_different_runtime_is_not_restored",
    ),
    Mutation(
        "reviewer-retry-tier-override",
        "backend/app/services/model_router.py",
        '        tier_override or (DEFAULT_TIERS[stage] if configured == "auto" else configured),\n',
        '        DEFAULT_TIERS[stage] if configured == "auto" else configured,\n',
        "backend/tests/services/test_model_router.py::test_supervisor_retry_override_selects_llm_over_configured_slm",
    ),
    Mutation(
        "review-checks-preserve-shared-budget",
        "backend/app/agents/reviewer_agent.py",
        '                    if endpoint is None:\n'
        '                        budget_blocked = True\n',
        '                    if endpoint is None or not budget.consume("review_self_consistency"):\n'
        '                        budget_blocked = True\n',
        "backend/tests/agents/test_reviewer_model_supervisor.py::test_model_review_checks_do_not_consume_escalation_and_retry_budget",
    ),
    Mutation(
        "runtime-applies-reviewer-tier-override",
        "backend/app/agents/workflow.py",
        '                    result["_workflow_tier_overrides"] = {retry_target: "llm"}\n',
        '                    result["_workflow_tier_overrides"] = {}\n',
        "backend/tests/test_e33_workflow_runtime.py::test_runtime_shares_step_budget_and_applies_reviewer_tier_override",
    ),
    Mutation(
        "g4-execution-authority-input",
        "backend/app/services/workflow_evaluation_service.py",
        '        "resolved_agent_config": resolved_agent_configs.get(agent_id),\n',
        '        "resolved_agent_config": None,\n',
        "backend/tests/services/test_workflow_evaluation_service.py::test_step_authority_covers_every_behavior_input[config]",
    ),
    Mutation(
        "g4-cross-workflow-corpus-lock",
        "backend/app/services/workflow_evaluation_service.py",
        '    return f"workflow-replay-corpus:{project_id}:{base}"\n',
        '    return f"workflow-evaluation:{project_id}:per-workflow"\n',
        "backend/tests/services/test_workflow_evaluation_service.py::test_evaluation_uses_project_base_corpus_lock",
    ),
    Mutation(
        "g4-publish-reevaluates",
        "backend/app/routers/workflows.py",
        "    # Evaluation evidence is mutable authority: configs, prompts, runtime, and\n"
        "    # the replay corpus can change while the draft definition does not. Always\n"
        "    # re-evaluate under the publication lock instead of trusting denormalized\n"
        "    # verdict fields copied onto the draft by an earlier request.\n"
        "    result = await eval_svc.evaluate_definition(\n",
        "    # UNSAFE: reuse stale denormalized evidence.\n"
        "    result = await (eval_svc.evaluate_definition if row.eval_verdict is None else _reuse_stale_eval)(\n",
        "backend/tests/test_workflows_router.py::test_publish_reevaluates_existing_verdict_before_enforcement",
    ),
    Mutation(
        "g4-recorded-input-identity",
        "backend/app/services/workflow_evaluation_service.py",
        '        "input_checksum_sha256": input_checksum_sha256,\n',
        '        "input_checksum_sha256": "0" * 64,\n',
        "backend/tests/services/test_workflow_evaluation_service.py::test_replay_hash_uses_recorded_input_not_test_run_identity",
    ),
    Mutation(
        "g4-receipt-output-integrity",
        "backend/app/services/workflow_evaluation_service.py",
        "        or _checksum(checkpoint) != output_checksum\n",
        "        or False\n",
        "backend/tests/services/test_workflow_evaluation_service.py::test_replay_receipt_is_required_and_tamper_evident",
    ),
    Mutation(
        "g4-terminal-attempt-selection",
        "backend/app/services/workflow_evaluation_service.py",
        "        int(terminal),\n",
        "        0,\n",
        "backend/tests/services/test_workflow_evaluation_service.py::test_terminal_latest_stage_attempt_wins_deterministically",
    ),
    Mutation(
        "g4-manifest-binds-evidence",
        "backend/app/services/workflow_evaluation_service.py",
        '        "corpus_evidence_sha256": _corpus_evidence(cases),\n',
        '        "corpus_evidence_sha256": "0" * 64,\n',
        "backend/tests/services/test_workflow_evaluation_service.py::test_manifest_checksum_binds_replay_evidence",
    ),
    Mutation(
        "g4-acceptance-manifest-binding",
        "backend/app/routers/workflows.py",
        "        and body.eval_manifest_checksum != result[\"manifest_checksum\"]\n",
        "        and False\n",
        "backend/tests/test_workflows_router.py::test_publish_refuses_acceptance_for_a_stale_eval_manifest",
    ),
    Mutation(
        "g4-authoritative-publish-window",
        "backend/app/routers/workflows.py",
        "        sample_limit=eval_svc.PUBLISH_REPLAY_RUNS,\n",
        "        sample_limit=eval_svc.MIN_REPLAY_RUNS,\n",
        "backend/tests/test_workflows_router.py::test_publish_uses_the_full_authoritative_replay_window",
    ),
    Mutation(
        "g4-reject-ignored-model-override",
        "backend/app/agents/workflow_compiler.py",
        "        if step.model is not None:\n",
        "        if False and step.model is not None:\n",
        "backend/tests/test_workflow_compiler.py::test_semantic_validation_rejects_ignored_step_model_override",
    ),
    Mutation(
        "g4-config-snapshot-lock",
        "backend/app/services/agent_config_service.py",
        "    await lock_agent_config_authority(db, project_id)\n",
        "    # UNSAFE: configuration may change while G4 snapshots it.\n",
        "backend/tests/test_agent_configs.py::test_put_is_one_upsert_that_bumps_the_version",
    ),
    Mutation(
        "g4-endpoint-identity",
        "backend/app/services/agent_config_resolver.py",
        '            "base_url_sha256": (\n',
        '            "base_url_sha256": None if True else (\n',
        "backend/tests/test_agent_config_resolver.py::test_endpoint_authority_fingerprint_binds_url_without_exposing_it",
    ),
    Mutation(
        "g4-terminal-preserves-endpoint-authority",
        "backend/app/agents/workflow.py",
        '                    "endpoint_authority_fingerprints": prior_metadata.get(\n'
        '                        "endpoint_authority_fingerprints"\n'
        '                    ) or {},\n',
        '                    "endpoint_authority_fingerprints": {},\n',
        "backend/tests/services/test_pipeline_public_status.py::test_finalize_preserves_the_invocation_config_authority",
    ),
    Mutation(
        "g4-terminal-preserves-start-prompt-authority",
        "backend/app/agents/workflow.py",
        '                    "prompt_versions": prior_metadata.get("prompt_versions") or {},\n',
        '                    "prompt_versions": _prompt_registry_versions(),\n',
        "backend/tests/services/test_pipeline_public_status.py::test_finalize_preserves_the_invocation_config_authority",
    ),
    Mutation(
        "g4-terminal-preserves-behavior-plan-authority",
        "backend/app/agents/workflow.py",
        '                    "workflow_behavior_plan_sha256": prior_metadata.get(\n'
        '                        "workflow_behavior_plan_sha256"\n'
        "                    ),\n",
        '                    "workflow_behavior_plan_sha256": None,\n',
        "backend/tests/services/test_pipeline_public_status.py::test_finalize_preserves_the_invocation_config_authority",
    ),
    Mutation(
        "g4-behavior-plan-excludes-workflow-identity",
        "backend/app/services/agent_planner.py",
        '        "workflow_id",\n        "workflow_version",\n        "workflow_ref",\n        "name",\n        "description",\n',
        "",
        "backend/tests/services/test_workflow_evaluation_service.py::test_behavior_identical_workflow_identity_reuses_replay_authority",
    ),
    Mutation(
        "g4-authority-lock-order",
        "backend/app/services/agent_authority_lock.py",
        "    await lock_global_agent_authority(db)\n    await lock_project_agent_authority(db, project_id)\n",
        "    await lock_project_agent_authority(db, project_id)\n",
        "backend/tests/services/test_agent_authority_lock.py::test_authority_snapshot_locks_global_then_project",
    ),
    Mutation(
        "g4-runtime-authority-snapshot-lock",
        "backend/app/agents/workflow.py",
        "        await lock_agent_authority_snapshot(db, authority_project_id)\n",
        "        # UNSAFE: runtime authority may combine concurrent writes.\n",
        "backend/tests/test_agent_configs.py::test_a_pipeline_run_freezes_the_projects_config_versions",
    ),
    Mutation(
        "g4-fresh-feature-authority",
        "backend/app/services/feature_flags.py",
        "    redis_flag = None if fresh else await _load_from_redis(key)\n",
        "    redis_flag = await _load_from_redis(key)\n",
        "backend/tests/services/test_feature_flags_service.py::test_fresh_authority_read_bypasses_shared_cache",
    ),
    Mutation(
        "g4-feature-writer-authority-lock",
        "backend/app/services/feature_flags.py",
        "    rollout_percent: int,\n    actor: User,\n) -> FeatureFlag:\n    await lock_global_agent_authority(db)\n",
        "    rollout_percent: int,\n    actor: User,\n) -> FeatureFlag:\n",
        "backend/tests/services/test_feature_flags_service.py::test_feature_flag_writers_share_the_global_authority_lock",
    ),
    Mutation(
        "g4-ai-config-writer-authority-lock",
        "backend/app/routers/app_settings.py",
        "    await lock_global_agent_authority(db)\n    existing = await _load_ai_config(db)\n",
        "    existing = await _load_ai_config(db)\n",
        "backend/tests/regression/test_knowledge_rag_single_gate.py::test_flag_caches_are_dropped_after_the_commit_not_before",
    ),
    Mutation(
        "g4-single-global-config-snapshot",
        "backend/app/services/agent_config_resolver.py",
        "        global_ai_config=ai_config,\n",
        "        global_ai_config=await get_effective_ai_config(),\n",
        "backend/tests/test_agent_config_resolver.py::test_project_resolution_uses_the_supplied_global_snapshot",
    ),
    Mutation(
        "g4-fresh-global-config-authority",
        "backend/app/services/ai_config_resolver.py",
        "    if not fresh:\n        try:\n            from app.db.redis_client import get_redis\n            redis = get_redis()\n            cached = await redis.get(_CACHE_KEY)\n",
        "    if True:\n        try:\n            from app.db.redis_client import get_redis\n            redis = get_redis()\n            cached = await redis.get(_CACHE_KEY)\n",
        "backend/tests/services/test_ai_config_resolver.py::test_fresh_authority_snapshot_uses_callers_db_and_bypasses_cache",
    ),
    Mutation(
        "g4-frontend-authoritative-evaluation-window",
        "frontend/src/services/workflowService.ts",
        "    sample_limit: 100,\n",
        "    sample_limit: 20,\n",
        "frontend/src/services/workflowService.test.ts",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    if test.startswith("frontend/"):
        npm = "npm.cmd" if os.name == "nt" else "npm"
        return subprocess.run(
            [npm, "run", "test", "--", test.removeprefix("frontend/")],
            cwd=ROOT / "frontend",
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m13-mutation-{suffix}",
            test,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        source = originals[path].decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation.test, mutation.name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with pytest exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            path.write_bytes(originals[path])
    print(f"M13 mutation check passed: {len(MUTATIONS)} unsafe changes were killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
