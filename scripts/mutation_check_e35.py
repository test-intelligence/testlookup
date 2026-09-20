"""Prove E3.5 guards and topology tests kill built-in workflow drift."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUALITY_COMMAND = (
    sys.executable,
    "scripts/quality_gate.py",
    "--only",
    "workflows.builtins-match-compiled",
)
BACKEND_COMMAND = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "-p",
    "no:randomly",
    "backend/tests/test_workflow_compiler.py",
    "-k",
    "builtin_definitions_compile_to_the_live_graph_topology",
)

MUTATIONS = (
    (
        "backend/tests/test_workflow_compiler.py",
        '("live", workflow._build_live_graph),',
        "",
        1,
        QUALITY_COMMAND,
    ),
    (
        "backend/tests/test_workflow_compiler.py",
        "assert _topology(compiled.graph) == _topology(legacy_builder())",
        "assert set(compiled.graph.nodes) == set(legacy_builder().nodes)",
        1,
        QUALITY_COMMAND,
    ),
    (
        "backend/app/services/workflow_definition_service.py",
        '{"from": "summary", "to": "__end__"}',
        '{"from": "ingestion", "to": "__end__"}',
        1,
        BACKEND_COMMAND,
    ),
    (
        "backend/app/agents/workflow.py",
        'graph.add_edge("summary", END)',
        'graph.add_edge("ingestion", END)',
        1,
        BACKEND_COMMAND,
    ),
)


def main() -> int:
    env = os.environ.copy()
    backend = str(ROOT / "backend")
    env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
    for relative, good, bad, expected, command in MUTATIONS:
        path = ROOT / relative
        original = path.read_bytes()
        good_bytes = good.encode("utf-8")
        bad_bytes = bad.encode("utf-8")
        count = original.count(good_bytes)
        if count != expected:
            raise AssertionError(
                f"mutation did not apply {expected} time(s): {relative}: {good!r} (found {count})"
            )
        mutated = original.replace(good_bytes, bad_bytes)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_bytes(mutated)
        try:
            run = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        finally:
            path.write_bytes(original)
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {relative}: {bad!r}")
    print(f"E3.5 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
