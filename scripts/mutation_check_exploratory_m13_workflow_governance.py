"""Prove M13 workflow-governance regressions kill their unsafe behavior."""
from __future__ import annotations

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
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
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
