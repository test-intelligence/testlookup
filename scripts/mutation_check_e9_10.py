"""Prove E9.10's focused tests kill each wrong-behaviour mutation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv311" / "Scripts" / "python.exe"
TESTS = (
    "backend/tests/services/test_eval_label_leakage.py",
    "backend/tests/services/test_online_drift_service.py",
    "backend/tests/test_migration_0186_eval_label_manifest_provenance.py",
)
COMMAND = (
    str(PYTHON),
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=backend/.pytest-tmp-e910-mutation",
    *TESTS,
)
MUTATIONS = (
    (
        "backend/app/services/eval_label_provenance.py",
        "if source_checksum == candidate or created_at > cutoff:",
        "if created_at > cutoff:",
    ),
    (
        "backend/app/services/eval_label_provenance.py",
        "if source_checksum == candidate or created_at > cutoff:",
        "if source_checksum == candidate or created_at < cutoff:",
    ),
    (
        "backend/app/services/eval_label_provenance.py",
        "if source_checksum is None or created_at is None:\n            continue",
        "if False:\n            continue",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "AIFeedback.created_at <= cutoff,",
        "AIFeedback.created_at >= cutoff,",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        "AIFeedback.eval_manifest_checksum.isnot(None),",
        "AIFeedback.eval_manifest_checksum.is_(None),",
    ),
    (
        "backend/app/services/ai_eval_service.py",
        '"eval_manifest_checksum": feedback.eval_manifest_checksum,',
        '"eval_manifest_checksum": None,',
    ),
    (
        "backend/app/services/eval_gate_service.py",
        'gate_manifest_checksum=manifest["manifest_checksum_sha256"],',
        "gate_manifest_checksum=None,",
    ),
    (
        "backend/app/services/review_request_service.py",
        "eval_manifest_checksum=checksum_from_execution_metadata(\n            getattr(run, \"execution_metadata\", None)\n        ),",
        "eval_manifest_checksum=None,",
    ),
    (
        "backend/app/services/online_drift_service.py",
        "eval_manifest_checksum=current_eval_manifest_checksum(),",
        "eval_manifest_checksum=None,",
    ),
    (
        "backend/app/agents/workflow.py",
        '"eval_manifest_checksum": str(pipeline_setup.get("eval_manifest_checksum") or ""),',
        '"eval_manifest_checksum": "",',
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    for relative, good, bad in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        count = original.count(good)
        expected = 2 if relative.endswith("agents/workflow.py") else 1
        if count != expected:
            raise AssertionError(
                f"mutation did not apply expected times ({expected}): {relative}: {good!r}"
            )
        path.write_text(original.replace(good, bad), encoding="utf-8")
        try:
            run = subprocess.run(
                COMMAND,
                cwd=ROOT,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {relative}: {bad!r}")
        finally:
            path.write_text(original, encoding="utf-8")
    print(f"E9.10 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
