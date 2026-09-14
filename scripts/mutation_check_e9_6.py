"""Prove E9.6's focused tests kill each wrong-behaviour mutation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "backend/tests/services/test_online_drift_service.py",
    "backend/tests/services/test_tier_comparison_service.py",
    "backend/tests/test_agent_config_resolver.py",
    "backend/tests/regression/test_agent_eval_runs_on_a_schedule.py",
)
MUTATIONS = (
    (
        "backend/app/services/online_drift_service.py",
        "disjoint = current_ci[1] < previous_ci[0] or current_ci[0] > previous_ci[1]",
        "disjoint = False",
    ),
    (
        "backend/app/services/online_drift_service.py",
        "current_total >= minimum_samples\n        and previous_total >= minimum_samples",
        "current_total > 0\n        and previous_total > 0",
    ),
    (
        "backend/app/services/online_drift_service.py",
        'ReviewRequest.state == "pending_review",',
        'ReviewRequest.state == "accepted",',
    ),
    (
        "backend/app/services/agent_config_resolver.py",
        "config.review.auto_reviewer = False",
        "config.review.auto_reviewer = True",
    ),
    (
        "backend/app/services/tier_comparison_service.py",
        "if await has_active_drift_pin(db, project_id, agent_id):",
        "if False and await has_active_drift_pin(db, project_id, agent_id):",
    ),
    (
        "backend/app/services/online_drift_service.py",
        'if report["drift"]:',
        'if False and report["drift"]:',
    ),
    (
        "backend/app/worker/celery_app.py",
        'crontab(hour=6, minute=0, day_of_week="monday")',
        'crontab(hour=6, minute=0, day_of_week="sunday")',
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    for relative, good, bad in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        if original.count(good) != 1:
            raise AssertionError(f"mutation did not apply exactly once: {relative}: {good!r}")
        path.write_text(original.replace(good, bad), encoding="utf-8")
        try:
            run = subprocess.run(
                [str(python), "-m", "pytest", "-q", "-p", "no:testlookup", *TESTS],
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
    print(f"E9.6 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
