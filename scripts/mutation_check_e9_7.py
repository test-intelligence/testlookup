"""Prove E9.7's focused tests kill each wrong-behaviour mutation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv311" / "Scripts" / "python.exe"
BACKEND_TESTS = (
    "backend/tests/services/test_eval_provenance_service.py",
    "backend/tests/services/test_pipeline_public_status.py",
    "backend/tests/test_agent_configs.py",
)
BACKEND_COMMAND = (
    str(PYTHON),
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    *BACKEND_TESTS,
)
FRONTEND_COMMAND = (
    "npm.cmd",
    "run",
    "test",
    "--",
    "--run",
    "src/pages/settings/AIEvalDashboardPage.test.tsx",
)
MUTATIONS = (
    (
        "backend/app/services/eval_provenance_service.py",
        'payload.pop("eval_manifest_checksum", None)',
        'payload.pop("ignored_checksum", None)',
        BACKEND_COMMAND,
        ROOT,
    ),
    (
        "backend/app/services/eval_provenance_service.py",
        'if eval_manifest_checksum(manifest) != checksum:\n        raise EvalManifestError("eval manifest archive content does not match its checksum")',
        'if False and eval_manifest_checksum(manifest) != checksum:\n        raise EvalManifestError("eval manifest archive content does not match its checksum")',
        BACKEND_COMMAND,
        ROOT,
    ),
    (
        "backend/app/services/eval_provenance_service.py",
        '"has_unresolvable_checksums": unresolved_run_count > 0,',
        '"has_unresolvable_checksums": False,',
        BACKEND_COMMAND,
        ROOT,
    ),
    (
        "backend/app/agents/workflow.py",
        '"eval_manifest_checksum": current_eval_manifest_checksum(),',
        '"eval_manifest_checksum": "0" * 64,',
        BACKEND_COMMAND,
        ROOT,
    ),
    (
        "backend/app/agents/workflow.py",
        '"eval_manifest_checksum": prior_metadata.get(\n                        "eval_manifest_checksum"\n                    ),',
        '"eval_manifest_checksum": None,',
        BACKEND_COMMAND,
        ROOT,
    ),
    (
        "backend/app/agents/investigator/workflow.py",
        '"eval_manifest_checksum": current_eval_manifest_checksum(),',
        '"eval_manifest_checksum": "0" * 64,',
        BACKEND_COMMAND,
        ROOT,
    ),
    (
        "frontend/src/pages/settings/AIEvalDashboardPage.tsx",
        "? 'UNRESOLVED' : 'RESOLVED'",
        "? 'RESOLVED' : 'UNRESOLVED'",
        FRONTEND_COMMAND,
        ROOT / "frontend",
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    for relative, good, bad, command, cwd in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        if original.count(good) != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {relative}: {good!r}"
            )
        path.write_text(original.replace(good, bad), encoding="utf-8")
        try:
            run = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {relative}: {bad!r}")
        finally:
            path.write_text(original, encoding="utf-8")
    print(f"E9.7 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
