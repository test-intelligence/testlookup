"""Prove T22's focused tests kill each live-contract regression."""
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
    "backend/tests/test_agent_catalog.py",
    "backend/tests/test_agentic_live_dod.py",
    "backend/tests/test_intelligence_export_review_envelope.py",
    "backend/tests/services/test_pipeline_public_status.py",
)

# (file, correct source, wrong behavior, exact replacement count)
MUTATIONS = (
    (
        "backend/app/services/agent_catalog.py",
        '"invokable": invocation_workflow_type(spec.stage_name) is not None,',
        '"invokable": True,',
        1,
    ),
    (
        "scripts/verify_agentic_live_dod.py",
        'if entry.get("invokable") is True',
        'if entry.get("invokable") is not True',
        1,
    ),
    (
        "scripts/verify_agentic_live_dod.py",
        'return "completed" if entry.get("produces_report") is True else "passed"',
        'return "passed" if entry.get("produces_report") is True else "completed"',
        1,
    ),
    (
        "scripts/verify_agentic_live_dod.py",
        "if initial_http != 202:",
        "if initial_http != 200:",
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        "query = query.where(AgentPipelineRun.fencing_token == fencing_token)",
        "query = query.where(AgentPipelineRun.fencing_token != fencing_token)",
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        "result = await db.execute(query.with_for_update())",
        "result = await db.execute(query)",
        1,
    ),
    (
        "backend/app/routers/run_intelligence.py",
        "if not distribution.allowed:",
        "if distribution.allowed:",
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
    print(f"T22 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
