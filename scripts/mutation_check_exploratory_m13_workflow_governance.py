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
        '        if self.verdict in {"pass", "pass_with_flags"} and any(\n',
        '        if False and self.verdict in {"pass", "pass_with_flags"} and any(\n',
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
