"""Prove M11 configuration-authority regressions kill unsafe behavior."""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def restore(path: Path, content: bytes) -> None:
    """Restore a mutation target despite transient Windows file locks."""
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "retry-agent-config",
        "backend/app/routers/agent_invoke.py",
        "if await _invocation_config_changed(db, invocation):",
        "if False and await _invocation_config_changed(db, invocation):",
        "backend/tests/test_agent_invocation_retry_cancel.py::test_retry_is_refused_when_the_frozen_agent_config_changed",
    ),
    Mutation(
        "review-project",
        "backend/app/services/agent_action_ledger_service.py",
        "ReviewRequest.project_id == action.project_id,",
        "ReviewRequest.project_id == uuid.uuid4(),",
        "backend/tests/services/test_agent_action_ledger_service.py::test_executor_denies_an_action_whose_proposing_run_is_not_accepted",
    ),
    Mutation(
        "review-kind",
        "backend/app/services/agent_action_ledger_service.py",
        'ReviewRequest.kind == "report",',
        'ReviewRequest.kind == "evaluation",',
        "backend/tests/services/test_agent_action_ledger_service.py::test_executor_denies_an_action_whose_proposing_run_is_not_accepted",
    ),
    Mutation(
        "review-subject-type",
        "backend/app/services/agent_action_ledger_service.py",
        'ReviewRequest.subject_type == "pipeline_run",',
        'ReviewRequest.subject_type == "capability",',
        "backend/tests/services/test_agent_action_ledger_service.py::test_executor_denies_an_action_whose_proposing_run_is_not_accepted",
    ),
    Mutation(
        "review-subject-id",
        "backend/app/services/agent_action_ledger_service.py",
        "ReviewRequest.subject_id == str(pipeline_run_id),",
        'ReviewRequest.subject_id == "wrong-subject",',
        "backend/tests/services/test_agent_action_ledger_service.py::test_executor_denies_an_action_whose_proposing_run_is_not_accepted",
    ),
    Mutation(
        "canonical-proposer",
        "backend/app/services/agent_action_ledger_service.py",
        '"proposing_agent_id": "agent.decision_report.v1",',
        '"proposing_agent_id": "decision_report",',
        "backend/tests/services/test_agent_action_ledger_service.py::test_report_proposals_are_bounded_and_approval_gated",
    ),
    Mutation(
        "frozen-workflow-config",
        "backend/app/services/agent_config_resolver.py",
        "if isinstance(frozen_config, dict):",
        "if False and isinstance(frozen_config, dict):",
        "backend/tests/test_agent_config_resolver.py::test_an_ordinary_pipeline_uses_its_frozen_workflow_config",
    ),
    Mutation(
        "trace-tool-authority",
        "backend/app/agents/log_intelligence_agent.py",
        'if not tool_allowed("reconstruct_distributed_trace"):',
        'if False and not tool_allowed("reconstruct_distributed_trace"):',
        "backend/tests/test_exploratory_m11_config_authority.py::test_log_agent_does_not_call_tools_removed_by_the_frozen_allowlist",
    ),
    Mutation(
        "anomaly-tool-authority",
        "backend/app/agents/log_intelligence_agent.py",
        'if not tool_allowed("detect_log_rate_anomaly"):',
        'if False and not tool_allowed("detect_log_rate_anomaly"):',
        "backend/tests/test_exploratory_m11_config_authority.py::test_log_agent_does_not_call_tools_removed_by_the_frozen_allowlist",
    ),
    Mutation(
        "config-if-match-precheck",
        "backend/app/routers/agent_configs.py",
        "if current_version != expected_version:",
        "if False and current_version != expected_version:",
        "backend/tests/test_agent_configs.py::test_put_requires_a_fresh_if_match_version",
    ),
    Mutation(
        "config-atomic-version-check",
        "backend/app/services/agent_config_service.py",
        "if expected_version is not None:\n        conflict_kwargs",
        "if False and expected_version is not None:\n        conflict_kwargs",
        "backend/tests/test_agent_configs.py::test_put_is_one_upsert_that_bumps_the_version",
    ),
    Mutation(
        "clustering-tool-authority",
        "backend/app/agents/cluster_agent.py",
        'if not tool_allowed("embed_and_cluster"):',
        'if False and not tool_allowed("embed_and_cluster"):',
        "backend/tests/test_exploratory_m11_config_authority.py::test_cluster_agent_does_not_call_a_tool_removed_by_the_frozen_allowlist",
    ),
    Mutation(
        "retired-fixer-body-validation",
        "backend/app/routers/fixer.py",
        "async def put_fixer_config(\n    project_id: uuid.UUID,\n    db:",
        "async def put_fixer_config(\n    project_id: uuid.UUID,\n    body: dict[str, Any],\n    db:",
        "backend/tests/test_exploratory_m11_config_authority.py::test_retired_config_puts_return_405_before_parsing_any_body[fixer-/api/v1/projects/00000000-0000-0000-0000-000000000001/fixer/config-/api/v1/projects/{project_id}/fixer/config]",
    ),
    Mutation(
        "retired-investigator-body-validation",
        "backend/app/routers/agent_investigations.py",
        "async def update_agent_policy(\n    project_id: uuid.UUID,\n    agent_id: str,\n    db:",
        "async def update_agent_policy(\n    project_id: uuid.UUID,\n    agent_id: str,\n    body: dict[str, Any],\n    db:",
        "backend/tests/test_exploratory_m11_config_authority.py::test_retired_config_puts_return_405_before_parsing_any_body[investigator-/api/v1/projects/00000000-0000-0000-0000-000000000001/agent-policies/unknown-/api/v1/projects/{project_id}/agent-policies/{agent_id}]",
    ),
    Mutation(
        "fixer-config-version-header",
        "frontend/src/services/fixerService.ts",
        "headers: { 'If-Match': `\"${view.config_version}\"` },",
        "headers: {},",
        "frontend/src/services/fixerService.test.ts",
    ),
    Mutation(
        "root-cause-tool-authority",
        "backend/app/services/agent.py",
        "if tool_allowed(tool.name)",
        "if True or tool_allowed(tool.name)",
        "backend/tests/test_exploratory_m11_config_authority.py::test_root_cause_react_tools_follow_the_frozen_allowlist",
    ),
    Mutation(
        "root-cause-cache-authority",
        "backend/app/services/agent.py",
        "return all(tool_allowed(name) for name in _REACT_TOOL_NAMES)",
        "return True",
        "backend/tests/test_exploratory_m11_config_authority.py::test_root_cause_react_tools_follow_the_frozen_allowlist",
    ),
    Mutation(
        "root-cause-failure-limit",
        "backend/app/agents/analysis_agent.py",
        "all_prioritized_ids[:failure_limit]",
        "all_prioritized_ids",
        "backend/tests/test_analysis_agent.py::TestAnalysisAgentRun::test_frozen_max_failures_limits_the_prioritized_analysis_scope",
    ),
    Mutation(
        "contract-tool-authority",
        "backend/app/agents/contract_agent.py",
        'if ids and not tool_allowed("validate_api_contract"):',
        'if False and ids and not tool_allowed("validate_api_contract"):',
        "backend/tests/test_exploratory_m11_config_authority.py::test_contract_agent_does_not_call_a_tool_removed_by_the_frozen_allowlist",
    ),
    Mutation(
        "frozen-drift-clamp",
        "backend/app/services/agent_config_resolver.py",
        "stored=frozen.config,\n        config_version=frozen.config_version,\n        drift_pin_active=drift_pin_active,",
        "stored=frozen.config,\n        config_version=frozen.config_version,\n        drift_pin_active=False,",
        "backend/tests/test_agent_config_resolver.py::test_frozen_restore_reapplies_a_new_eval_drift_pin",
    ),
    Mutation(
        "frozen-endpoint-clamp-sanitization",
        "backend/app/services/agent_config_resolver.py",
        'field=f"model.{tier}.endpoint",',
        'field=f"model.{tier}.base_url",',
        "backend/tests/test_agent_config_resolver.py::test_a_refused_endpoint_clamp_is_sanitized_before_freezing",
    ),
    Mutation(
        "reviewer-config-version",
        "backend/app/services/reviewer_quality_service.py",
        "expected_version=int(config_row.config_version),",
        "expected_version=None,",
        "backend/tests/services/test_reviewer_quality_service.py::test_auto_disable_uses_the_single_agent_config_writer",
    ),
    Mutation(
        "reviewer-config-row-lock",
        "backend/app/services/reviewer_quality_service.py",
        ").with_for_update()",
        ")",
        "backend/tests/services/test_reviewer_quality_service.py::test_auto_disable_uses_the_single_agent_config_writer",
    ),
    Mutation(
        "reviewer-config-conflict",
        "backend/app/services/reviewer_quality_service.py",
        "except ConfigVersionConflict:\n        return False",
        "except ConfigVersionConflict:\n        raise",
        "backend/tests/services/test_reviewer_quality_service.py::test_auto_disable_loses_a_concurrent_config_update_without_overwriting_it",
    ),
    Mutation(
        "mcp-frozen-config",
        "mcp/tools/agents.py",
        'snapshot = data.get("config_snapshot")',
        "snapshot = None",
        "mcp/tests/test_mcp_agent_catalog.py::test_the_invocation_view_shows_the_sanitized_frozen_config",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    if test.startswith("frontend/"):
        npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
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
            f".pytest-tmp-exploratory-m11-mutation-{suffix}",
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
        text = originals[path].decode("utf-8")
        if text.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                text.replace(mutation.safe, mutation.unsafe, 1),
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
            restore(path, originals[path])
    for path, original in originals.items():
        if path.read_bytes() != original:
            raise AssertionError(f"mutation harness did not restore {path}")
    print(f"M11 configuration-authority mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
