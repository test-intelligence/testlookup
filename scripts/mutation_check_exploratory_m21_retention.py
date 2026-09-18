"""Prove M21 deletion claim and stale-protection regressions are detected."""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "preview-claim-does-not-queue",
        "backend/app/services/deletion_job_service.py",
        "    job.status = QUEUED\n    return [uuid.UUID(str(r)) for r in resolved]",
        "    job.status = PREVIEWED\n    return [uuid.UUID(str(r)) for r in resolved]",
        "test_a_matching_hash_is_accepted",
    ),
    Mutation(
        "queued-claim-does-not-start",
        "backend/app/services/deletion_job_service.py",
        "    job.status = RUNNING\n    job.started_at = datetime.now(timezone.utc)",
        "    job.status = QUEUED\n    job.started_at = datetime.now(timezone.utc)",
        "test_a_queued_set_can_be_started_once",
    ),
    Mutation(
        "queue-transition-not-committed-before-dispatch",
        "backend/app/routers/retention.py",
        "    await db.commit()\n    try:\n        execute_criteria_deletion_task.delay(",
        "    # unsafe: dispatch before the queued transition is durable\n    try:\n        execute_criteria_deletion_task.delay(",
        "test_execute_claim_is_committed_before_dispatch",
    ),
    Mutation(
        "in-progress-protection-not-rechecked",
        "backend/app/services/run_deletion_service.py",
        "    if status_blocks_deletion(run.status):\n        reasons.append(\"run is still executing\")",
        "    if False and status_blocks_deletion(run.status):\n        reasons.append(\"run is still executing\")",
        "test_execution_rechecks_mutable_protections",
    ),
    Mutation(
        "single-delete-skips-worker-preflight",
        "backend/app/worker/tasks.py",
        "            blockers = await run_deletion_service.execution_blockers(\n                db, run=run, mongo=mongo\n            )\n            if blockers:",
        "            blockers = []\n            if blockers:",
        "delete_run_everywhere",
    ),
    Mutation(
        "criteria-delete-skips-worker-preflight",
        "backend/app/worker/tasks.py",
        "                    blockers = await run_deletion_service.execution_blockers(\n                        db, run=run, mongo=mongo\n                    )\n                    if blockers:",
        "                    blockers = []\n                    if blockers:",
        "execute_criteria_deletion_task",
    ),
)


def run_test(mutation: Mutation) -> subprocess.CompletedProcess[str]:
    test_file = (
        "backend/tests/test_delete_run_task.py"
        if "protection" in mutation.name or "preflight" in mutation.name
        else "backend/tests/test_criteria_deletion_routes.py"
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            test_file,
            "-k",
            mutation.test,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def restore(path: Path, content: bytes) -> None:
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        original = path.read_bytes()
        source = original.decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")

        baseline = run_test(mutation)
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation)
            if mutated.returncode == 0:
                raise AssertionError(
                    f"{mutation.name} survived\n{mutated.stdout}{mutated.stderr}"
                )
        finally:
            restore(path, original)

    print(f"M21 retention mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
