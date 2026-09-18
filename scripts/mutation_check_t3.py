"""Prove T3's focused tests kill trigger-attribution and SoD regressions."""
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
    "--basetemp=backend/.pytest-tmp-t3-mutation",
    "backend/tests/test_migration_0188_pipeline_requested_by.py",
    "backend/tests/test_agent_configs.py",
    "backend/tests/test_agent_invocations.py",
    "backend/tests/test_pipeline_retry_scheduling.py",
    "backend/tests/test_finalize_creates_review_request.py",
    "backend/tests/test_reviews_api.py",
)

# (file, correct source, wrong behavior, exact replacement count)
MUTATIONS = (
    (
        "backend/app/routers/agents.py",
        "requested_by=str(current_user.id),",
        "requested_by=None,",
        2,
    ),
    (
        "backend/app/agents/workflow.py",
        "requested_by=uuid.UUID(str(requested_by)) if requested_by else None,",
        "requested_by=None,",
        1,
    ),
    (
        "backend/app/worker/tasks.py",
        "requested_by=requested_by,",
        "requested_by=None,",
        2,
    ),
    (
        "backend/app/worker/tasks.py",
        '"requested_by": str(invocation.requested_by) if invocation.requested_by else None,',
        '"requested_by": None,',
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        "requested_by=run.requested_by,",
        "requested_by=None,",
        1,
    ),
    (
        "backend/app/services/review_request_service.py",
        "if same_user and await _run_proposes_act_actions(db, review):",
        "if same_user and False:",
        1,
    ),
    (
        "backend/app/services/review_request_service.py",
        'if mode == "act":',
        'if mode == "suggest":',
        1,
    ),
    (
        "backend/app/services/review_request_service.py",
        "if not isinstance(agent_id, str) or not agent_id:\n            return True",
        "if not isinstance(agent_id, str) or not agent_id:\n            return False",
        1,
    ),
    (
        "backend/migrations/versions/0188_pipeline_requested_by.py",
        'ondelete="SET NULL",',
        'ondelete="CASCADE",',
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
            raise AssertionError(
                f"mutation did not change source: {relative}: {good!r}"
            )
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
    print(f"T3 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
