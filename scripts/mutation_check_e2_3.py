"""Prove E2.3's replay and positive alert tests kill unsafe mutations."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=backend/.pytest-tmp-e23-mutation",
    "backend/tests/services/test_agent_operational_metrics.py",
    "backend/tests/integration/test_agent_dlq_replay.py",
)

# (file, correct source, mutation, exact replacement count). The count check is
# part of the harness: a refactor may never turn an unapplied mutation green.
MUTATIONS = (
    (
        "backend/app/services/ingestion_dlq.py",
        'decoded.get("source") != "celery"',
        'decoded.get("source") == "celery"',
        1,
    ),
    (
        "backend/app/services/ingestion_dlq.py",
        "task_name not in REPLAYABLE_CELERY_TASKS",
        "task_name in REPLAYABLE_CELERY_TASKS",
        1,
    ),
    (
        "backend/app/services/ingestion_dlq.py",
        "await redis.xdel(DLQ_STREAM, entry_id)",
        "await redis.xlen(DLQ_STREAM)",
        1,
    ),
    (
        "backend/app/worker/tasks.py",
        '"test_run_id": test_run_id,\n                "project_id": project_id,',
        '"test_run_id": test_run_id,',
        1,
    ),
    (
        "backend/app/services/agent_operational_metrics.py",
        "_age_seconds(oldest_run, observed_at) - deadline - grace",
        "_age_seconds(oldest_run, observed_at) - deadline + grace",
        1,
    ),
    (
        "backend/app/services/agent_operational_metrics.py",
        "persist_depth + stream_depth",
        "stream_depth",
        1,
    ),
    (
        "infra/monitoring/prometheus-rules/testlookup-alerts.yml",
        "expr: testlookup_agent_in_progress_overdue_seconds > 0",
        "expr: testlookup_agent_in_progress_overdue_seconds > 1",
        1,
    ),
    (
        "infra/monitoring/prometheus-rules/testlookup-alerts.yml",
        "expr: testlookup_agent_dlq_depth > 0",
        "expr: testlookup_agent_dlq_depth > 1",
        1,
    ),
    (
        "infra/monitoring/prometheus-rules/testlookup-alerts.yml",
        "expr: testlookup_pending_review_oldest_age_seconds > 86400",
        "expr: testlookup_pending_review_oldest_age_seconds > 86401",
        1,
    ),
    (
        "backend/app/bootstrap.py",
        "await refresh_agent_operational_metrics()",
        "await _refresh_celery_queue_depths()",
        1,
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    for relative, good, bad, count in MUTATIONS:
        path = ROOT / relative
        original = path.read_bytes()
        good_bytes = good.encode("utf-8")
        if original.count(good_bytes) != count:
            raise AssertionError(
                f"mutation did not apply {count} time(s): {relative}: {good!r}"
            )
        mutated = original.replace(good_bytes, bad.encode("utf-8"))
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_bytes(mutated)
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
            path.write_bytes(original)
    print(f"E2.3 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
