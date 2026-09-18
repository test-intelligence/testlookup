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
        "            if blockers:\n                raise run_deletion_service.RunDeletionBlocked(run.id, blockers)",
        "            if False and blockers:\n                raise run_deletion_service.RunDeletionBlocked(run.id, blockers)",
        "test_single_delete_behavior_stops_before_every_side_effect",
    ),
    Mutation(
        "criteria-delete-skips-worker-preflight",
        "backend/app/worker/tasks.py",
        "                    if blockers:\n                        raise run_deletion_service.RunDeletionBlocked(\n                            run.id, blockers\n                        )",
        "                    if False and blockers:\n                        raise run_deletion_service.RunDeletionBlocked(\n                            run.id, blockers\n                        )",
        "test_criteria_delete_behavior_retains_a_newly_protected_run",
    ),
    Mutation(
        "queued-hash-drift-accepted",
        "backend/app/services/deletion_job_service.py",
        "    if job.candidate_hash != candidate_hash(resolved):\n        raise FrozenSetRejected(\n            409,\n            \"the frozen candidate set no longer matches its hash — refusing \"",
        "    if False and job.candidate_hash != candidate_hash(resolved):\n        raise FrozenSetRejected(\n            409,\n            \"the frozen candidate set no longer matches its hash — refusing \"",
        "test_queued_hash_drift_is_refused_without_starting",
    ),
    Mutation(
        "job-row-lock-removed",
        "backend/app/services/deletion_job_service.py",
        "        statement = statement.with_for_update()",
        "        statement = statement.execution_options()",
        "test_get_job_emits_a_real_row_lock_when_requested",
    ),
    Mutation(
        "report-subject-share-lock-removed",
        "backend/app/services/decision_report_service.py",
        "                .with_for_update(read=True)",
        "                .execution_options()",
        "test_subject_lock_emits_postgres_for_share",
    ),
    Mutation(
        "queued-relay-reads-previewed-jobs",
        "backend/app/services/deletion_job_service.py",
        "                        DeletionJob.status == QUEUED,",
        "                        DeletionJob.status == PREVIEWED,",
        "test_queued_jobs_are_a_durable_relay_source",
    ),
    Mutation(
        "terminal-close-loses-compare-and-set",
        "backend/app/services/deletion_job_service.py",
        "                statement = statement.where(DeletionJob.status == expected_status)",
        "                statement = statement.where(DeletionJob.id == job_id)",
        "test_close_job_compare_and_set_refuses_a_stale_writer",
    ),
)


def run_test(mutation: Mutation) -> subprocess.CompletedProcess[str]:
    if "preflight" in mutation.name or "protection" in mutation.name:
        test_file = "backend/tests/test_delete_run_task.py"
    elif "report-subject" in mutation.name:
        test_file = "backend/tests/services/test_decision_report_service.py"
    elif "relay" in mutation.name or "terminal-close" in mutation.name:
        test_file = "backend/tests/test_deletion_jobs.py"
    else:
        test_file = "backend/tests/test_criteria_deletion_routes.py"
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
