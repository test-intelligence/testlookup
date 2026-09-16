"""Prove E3.3 runtime-authority tests kill unsafe workflow mutations."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTEST = (
    sys.executable, "-m", "pytest", "-q", "-p", "no:testlookup", "-p", "no:randomly",
    "--basetemp=backend/.pytest-tmp-e33-mutation",
    "backend/tests/test_e33_workflow_runtime.py",
    "backend/tests/services/test_workflow_definition_service.py",
)

MUTATIONS = (
    (
        "backend/app/agents/workflow_compiler.py",
        "executor = node_executors.get(step.id) or node_executors.get(step.agent_id)",
        "executor = node_executors.get(step.agent_id)",
        1,
    ),
    (
        "backend/app/services/workflow_definition_service.py",
        'WorkflowDefinition.status == "published",',
        'WorkflowDefinition.status != "published",',
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        'and metadata.get("workflow_ref") == expected_ref',
        'and metadata.get("workflow_ref") != expected_ref',
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        'metadata.get("workflow_ref") == workflow_ref',
        'metadata.get("workflow_ref") != workflow_ref',
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
                f"mutation did not apply {expected} time(s): {relative}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                PYTEST, cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=180, check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {relative}: {bad!r}")
    print(f"E3.3 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
