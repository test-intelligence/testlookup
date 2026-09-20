"""Prove E3.2 compiler tests kill semantic and policy regressions."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTEST = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "-p",
    "no:randomly",
    "backend/tests/test_workflow_compiler.py",
    "backend/tests/test_workflows_router.py",
    "backend/tests/test_architectural_agent_contracts.py",
)

# (file, correct source, wrong behavior, exact replacement count)
MUTATIONS = (
    (
        "backend/app/agents/workflow_compiler.py",
        "if visited != len(step_ids):",
        "if False and visited != len(step_ids):",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "if config.enabled and not mode_permits(config.mode, spec.permission):",
        "if False and config.enabled and not mode_permits(config.mode, spec.permission):",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "elif tool not in config.tools.allowlist:",
        "elif False and tool not in config.tools.allowlist:",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "if depth > MAX_CONDITION_DEPTH:",
        "if False and depth > MAX_CONDITION_DEPTH:",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "if leaves > MAX_CONDITION_LEAVES:",
        "if False and leaves > MAX_CONDITION_LEAVES:",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "if else_count != 1:",
        "if False and else_count != 1:",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "if int(counters.get(label, 0)) >= loop_limit:",
        "if False and int(counters.get(label, 0)) >= loop_limit:",
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        'elif sources[0] in loop_sources:',
        'elif False and sources[0] in loop_sources:',
        1,
    ),
    (
        "backend/app/agents/workflow_compiler.py",
        "if not alternatives or not any(guaranteed(item) for item in alternatives):",
        "if False:",
        1,
    ),
    (
        "backend/app/routers/workflows.py",
        "await _require_semantic_validity(db, project_id, row)",
        "if False: await _require_semantic_validity(db, project_id, row)",
        2,
    ),
    (
        "backend/app/services/workflow_definition_service.py",
        '{"from": "decision_report_critic", "to": "__end__"},',
        '{"from": "decision_report", "to": "__end__"},',
        1,
    ),
    (
        "backend/tests/test_architectural_agent_contracts.py",
        '    "workflow_compiler.py",',
        '    "workflow_compiler_MUTATED.py",',
        1,
    ),
)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    for relative, good, bad, expected in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        count = original.count(good)
        if count != expected:
            raise AssertionError(
                f"mutation did not apply {expected} time(s): {relative}: "
                f"{good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                PYTEST,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {relative}: {bad!r}")
    print(f"E3.2 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
